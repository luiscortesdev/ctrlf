"""
Push-to-talk voice input for Ctrl+F for Your Room.

Press SPACE in the video window, hear a "tink", ask your question. It stops by itself
when you stop talking (or press SPACE again), plays a "pop", and turns the speech into
text with Whisper running on the Mac's GPU (mlx-whisper). No Wi-Fi needed.

The first run downloads the Whisper model (~480 MB). macOS asks Terminal for microphone
permission the first time; approve it.
"""

import os
import subprocess
import threading
import time
import wave

import numpy as np

MODEL = "mlx-community/whisper-small.en-mlx"  # 0.25 s per question; base.en is faster, sloppier
RATE = 16000
BLOCK = RATE // 10      # read the mic in 0.1 s blocks
MAX_SECS = 7.0          # longest question
SILENCE_SECS = 1.0      # this much quiet after speech = done talking (people pause mid-question)
CUE_SECS = 0.6          # the tink plays while the mic is already on; don't mistake it for speech
# Mic loudness (RMS). Someone talking into the MacBook mic measured 0.03-0.12; chatter
# across the room 0.01-0.02. Re-check with a USB mic.
SPEECH_LEVEL = 0.025
TAIL_FRACTION = 0.2     # after speech, below 20% of its loudest = quiet (ignores chatter)
START_SOUND = "/System/Library/Sounds/Tink.aiff"
STOP_SOUND = "/System/Library/Sounds/Pop.aiff"

# Set CTRLF_VOICE_LOG=/some/folder to save each question as a .wav there, for tuning.
LOG_DIR = os.environ.get("CTRLF_VOICE_LOG")

# Whisper "hears" these in silence or noise; treat them as nothing.
NOT_SPEECH = {"", "you", "thank you", "thanks for watching", "bye", "okay", "uh", "um"}


class Endpointer:
    """Fed the mic 0.1 s at a time: has someone spoken into it, and have they stopped?"""

    def __init__(self):
        self.levels, self.peak, self.heard, self.quiet = [], 0.0, False, 0.0

    def add(self, block):
        """Returns True when the speaker has finished."""
        level = float(np.sqrt(np.mean(block ** 2)))
        self.levels.append(level)
        # The room's noise level is the quietest moment so far.
        speaking = level > max(SPEECH_LEVEL, 3 * min(self.levels), TAIL_FRACTION * self.peak)
        if speaking and len(self.levels) * len(block) / RATE > CUE_SECS:  # skip the tink
            self.heard, self.quiet = True, 0.0
            self.peak = max(self.peak, level)
        elif self.heard:
            self.quiet += len(block) / RATE
        return self.heard and self.quiet >= SILENCE_SECS


class Listener:
    """state: "loading" -> "idle" -> "listening" -> "thinking" -> "idle".
    on_text(text) is called from a background thread with the question, or None if
    nothing understandable was heard."""

    def __init__(self, on_text):
        self.on_text = on_text
        self.state = "loading"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _transcribe(self, audio):
        import mlx_whisper

        with self._lock:
            return mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL, language="en",
                                          condition_on_previous_text=False)["text"].strip()

    def _warm_up(self):
        try:
            self._transcribe(np.zeros(RATE, np.float32))  # loads (and downloads) the model
            self.state = "idle"
        except Exception as e:
            print(f"\n(voice off: {e!r})")
            self.state = "off"

    def toggle(self):
        """The push-to-talk button: start listening, or stop early if already listening."""
        if self.state == "listening":
            self._stop.set()
        elif self.state == "idle":
            self.state = "listening"
            self._stop.clear()
            threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        try:
            audio, heard = self._record()
            self.state = "thinking"
            text = self._transcribe(audio) if heard else ""
        except Exception as e:
            print(f"\n(voice error: {e!r})")
            text = ""
        self.state = "idle"
        words = text.lower().strip(" .!?,")
        self.on_text(None if words in NOT_SPEECH else text)

    def _record(self):
        """Record until the speaker goes quiet, MAX_SECS pass, or toggle() is pressed again.
        Returns (audio, heard anything louder than the room?)."""
        import sounddevice as sd

        blocks, ears = [], Endpointer()
        # Mic first, then the tink: people start talking the moment they press the button.
        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK) as mic:
            subprocess.Popen(["afplay", START_SOUND])
            t0 = time.time()  # opening can wait on the permission prompt the first time
            why = "time limit"
            while time.time() - t0 < MAX_SECS:
                if self._stop.is_set():
                    why = "button"
                    break
                blocks.append(mic.read(BLOCK)[0][:, 0])
                if ears.add(blocks[-1]):
                    why = "silence"
                    break
        subprocess.Popen(["afplay", STOP_SOUND])
        audio = np.concatenate(blocks) if blocks else np.zeros(BLOCK, np.float32)
        if LOG_DIR:
            path = os.path.join(LOG_DIR, time.strftime("question_%H%M%S.wav"))
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
            print(f"\n(voice: {len(audio) / RATE:.1f} s, stopped by {why}, "
                  f"loudest {ears.peak:.3f}, heard speech: {ears.heard}; saved {path})")
        return audio, ears.heard
