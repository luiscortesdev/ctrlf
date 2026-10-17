"""
Push-to-talk voice for Ctrl+F for Your Room: listening, and talking back.

Press SPACE in the video window, hear a "tink", ask your question. It stops by itself
when you stop talking (or press SPACE again) and plays a "pop". The recording goes to
Gemini (gemini.py), which hears it and works out what you want: "did you see my AirPods?",
"and my wallet?", "nah, wrong one". Meanwhile Whisper turns it into text on the Mac's GPU
(mlx-whisper); that's used instead if Gemini doesn't answer in time (no Wi-Fi, no key).

Speaker says the answers in Gemini's voice, or with the Mac's `say` without it.

The first run downloads the Whisper model (~480 MB). macOS asks Terminal for microphone
permission the first time; approve it.
"""

import hashlib
import os
import queue
import re
import subprocess
import threading
import time
import wave
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

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

# Gemini, after you stop talking. In tests (Oct 17) answers took 0.9-1.5 s, but about 1 in 8
# took 11-15 s: so ask again alongside after GEMINI_HEDGE, and after GEMINI_WAIT use
# Whisper's text. Two misses in a row (Wi-Fi down?): Whisper alone for GEMINI_REST seconds.
GEMINI_WAIT = 4.5
GEMINI_HEDGE = 1.6
GEMINI_REST = 60
# Speaking: Gemini's voice must start within SPEECH_WAIT (it took 0.75 s), or the Mac's
# says it. SAY_VOICE: the Mac's voice, None = the system one. A Premium voice sounds much
# better: download e.g. "Ava (Premium)" in System Settings > Accessibility > Spoken
# Content > System Voice > Manage Voices, then put its name here.
SPEECH_WAIT = 2.5
SAY_VOICE = None
# Gemini's speech for answers without numbers ("Okay, never mind.") is kept here, so it
# plays instantly next time.
SPEECH_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
                            "speech_cache")

# Set CTRLF_VOICE_LOG=/some/folder to save each question as a .wav there, for tuning.
LOG_DIR = os.environ.get("CTRLF_VOICE_LOG")

# Whisper "hears" these in silence or noise; treat them as nothing.
NOT_SPEECH = {"", "you", "thank you", "thanks for watching", "bye", "okay", "uh", "um"}

POOL = ThreadPoolExecutor(max_workers=4)  # Gemini requests (a slow one is left to finish)


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
    on_heard(text, meaning) is called from a background thread: meaning is Gemini's answer
    (see gemini.understand) and text what it heard; or meaning is None and text is
    Whisper's (None if nothing understandable was heard). context() returns what Gemini
    needs to know (see gemini.describe_context); without it, only Whisper is used."""

    def __init__(self, on_heard, context=None):
        self.on_heard = on_heard
        self.context = context
        self.state = "loading"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._misses, self._rest_until = 0, 0.0
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
        text, meaning = "", None
        try:
            audio, heard = self._record()
            self.state = "thinking"
            if heard:
                text, meaning = self._understand(audio)
        except Exception as e:
            print(f"\n(voice error: {e!r})")
        self.state = "idle"
        if meaning is None and text.lower().strip(" .!?,") in NOT_SPEECH:
            text = None
        self.on_heard(text, meaning)

    def _understand(self, audio):
        """(what was said, Gemini's answer), or (Whisper's text, None) if Gemini doesn't
        answer in time."""
        from src import gemini

        asks, ask = [], None
        if self.context and time.time() > self._rest_until and gemini.client():
            try:
                wav, context = gemini.to_wav(audio, RATE), self.context()
                ask = lambda: POOL.submit(gemini.understand, wav, context)  # noqa: E731
                asks.append(ask())
            except Exception as e:
                asks, ask = [], None
                print(f"\n(Gemini skipped: {e!r})")
        start = time.time()
        text = self._transcribe(audio)  # meanwhile, in case Gemini doesn't answer
        why, hedged = None, False
        while asks:
            now = time.time()
            if now >= start + GEMINI_WAIT:
                why = f"no answer in {GEMINI_WAIT:.0f} s"
                break
            if now >= start + GEMINI_HEDGE and not hedged:
                hedged = True
                asks.append(ask())
            until = start + (GEMINI_WAIT if hedged else GEMINI_HEDGE)
            done, _ = wait(asks, timeout=max(0.01, until - now), return_when=FIRST_COMPLETED)
            for f in done:
                asks.remove(f)
                if f.exception() is None:
                    self._misses = 0
                    meaning = f.result()
                    return meaning.get("heard") or text, meaning
                why = repr(f.exception())[:160]
        if ask:
            self._misses += 1
            if self._misses >= 2:
                self._misses, self._rest_until = 0, time.time() + GEMINI_REST
                why += f"; Whisper only for {GEMINI_REST} s"
            print(f"\n(Gemini: {why}. Using Whisper.)")
        return text, None

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


class Speaker:
    """Says things one at a time, in Gemini's voice (streamed), or with the Mac's `say` if
    there's no Gemini or it doesn't start within SPEECH_WAIT. stop() cuts it off and drops
    what's waiting (pressing SPACE to talk does that, so it doesn't talk over you)."""

    def __init__(self, use_gemini=True):
        from src import gemini

        self.use_gemini = use_gemini and gemini.client() is not None
        self.queue = queue.Queue()
        self.turn = 0  # stop() starts a new turn: lines from before it aren't said
        self.proc = None
        self.used_up_told = False
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text):
        self.queue.put((self.turn, text))

    def stop(self):
        self.turn += 1
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()

    def _run(self):
        from src import gemini

        while True:
            turn, text = self.queue.get()
            if turn != self.turn:
                continue
            try:
                if self.use_gemini and self._gemini(text, turn):
                    continue
            except Exception as e:
                if not isinstance(e, gemini.VoiceUsedUp):
                    print(f"\n(Gemini voice: {e!r:.160}. Using the Mac's.)")
                elif not self.used_up_told:  # say it once, not with every answer
                    self.used_up_told = True
                    print("\n(Gemini voice: the free quota (10 answers a day) is used up and the "
                          "key has no paid credits. Using the Mac's voice until it resets.)")
            if turn == self.turn:
                self.proc = subprocess.Popen(["say"] + (["-v", SAY_VOICE] if SAY_VOICE else []) + [text])
                self.proc.wait()

    def _gemini(self, text, turn):
        """Says text in Gemini's voice. False if it didn't start in time."""
        from src import gemini

        key = f"{gemini.TTS_MODEL} {gemini.TTS_VOICE} {text}"
        path = os.path.join(SPEECH_CACHE, hashlib.sha1(key.encode()).hexdigest() + ".pcm")
        if os.path.exists(path):
            with open(path, "rb") as f:
                self._play([f.read()], turn)
            return True
        chunks = queue.Queue()

        def fetch():
            try:
                for chunk in gemini.speak(text):
                    chunks.put(chunk)
            except Exception as e:
                chunks.put(e)
            chunks.put(None)

        threading.Thread(target=fetch, daemon=True).start()
        try:
            first = chunks.get(timeout=SPEECH_WAIT)
        except queue.Empty:
            print(f"\n(Gemini voice: nothing after {SPEECH_WAIT} s. Using the Mac's.)")
            return False
        if isinstance(first, Exception):
            raise first
        if first is None:
            return False

        def rest():
            yield first
            while True:
                chunk = chunks.get(timeout=5)
                if chunk is None:
                    return
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        audio = self._play(rest(), turn)
        if audio and not re.search(r"\d", text):
            os.makedirs(SPEECH_CACHE, exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(audio)
            os.replace(path + ".tmp", path)
        return True

    def _play(self, chunks, turn):
        """Plays 16-bit PCM chunks as they come. Returns all of it, or None if stop() cut
        it off."""
        from src import gemini
        import sounddevice as sd

        step = gemini.TTS_RATE // 10 * 2  # 0.1 s at a time, so stop() is quick
        played, pending = bytearray(), b""
        with sd.RawOutputStream(samplerate=gemini.TTS_RATE, channels=1, dtype="int16") as out:
            for chunk in chunks:
                pending += chunk
                while len(pending) >= step:
                    if turn != self.turn:
                        out.abort()
                        return None
                    out.write(pending[:step])
                    played += pending[:step]
                    pending = pending[step:]
            pending = pending[:len(pending) // 2 * 2]
            if pending and turn == self.turn:
                out.write(pending)
                played += pending
        return bytes(played)
