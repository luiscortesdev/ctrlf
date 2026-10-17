"""
Push-to-talk voice input for Ctrl+F for Your Room.

Press SPACE in the video window, hear a "tink", ask your question. It stops by itself
when you stop talking (or press SPACE again), plays a "pop", and turns the speech into
text with Whisper running on the Mac's GPU (mlx-whisper). No Wi-Fi needed.

The first run downloads the Whisper model (~480 MB). macOS asks Terminal for microphone
permission the first time; approve it.
"""

import subprocess
import threading
import time

import numpy as np

MODEL = "mlx-community/whisper-small.en-mlx"  # 0.25 s per question; base.en is faster, sloppier
RATE = 16000
BLOCK = RATE // 10      # read the mic in 0.1 s blocks
MAX_SECS = 6.0          # longest question
SILENCE_SECS = 0.9      # this much quiet after speech = done talking
# Mic loudness (RMS). Someone talking into the MacBook mic measured 0.03-0.12; chatter
# across the room 0.01-0.02. Re-check with a USB mic.
SPEECH_LEVEL = 0.025
TAIL_FRACTION = 0.3     # after speech, below 30% of its loudest = quiet (ignores chatter)
START_SOUND = "/System/Library/Sounds/Tink.aiff"
STOP_SOUND = "/System/Library/Sounds/Pop.aiff"

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
        # The room's noise level is the quietest of the first few blocks.
        speaking = level > max(SPEECH_LEVEL, 3 * min(self.levels[:5]), TAIL_FRACTION * self.peak)
        if speaking and len(self.levels) > 2:  # skip the first 0.2 s (mic warm-up)
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

        subprocess.run(["afplay", START_SOUND])  # finish the tink before the mic opens
        blocks, ears = [], Endpointer()
        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK) as mic:
            t0 = time.time()  # opening can wait on the permission prompt the first time
            while not self._stop.is_set() and time.time() - t0 < MAX_SECS:
                blocks.append(mic.read(BLOCK)[0][:, 0])
                if ears.add(blocks[-1]):
                    break
        subprocess.Popen(["afplay", STOP_SOUND])
        return (np.concatenate(blocks) if blocks else np.zeros(BLOCK, np.float32)), ears.heard
