"""
The laptop's own voice, offline: what Speaker (voice.py) says things with when Gemini's
voice isn't there, on the Mac, Windows or Linux.

  macOS    `say`. (pyttsx3's macOS driver hangs unless it's on the main thread, and that's
           the OpenCV window's.)
  Windows  SAPI5, the Windows voices, through comtypes. pyttsx3 wraps the same thing, but in
           tests (pyttsx3 2.99, Oct 17) reusing its engine skipped every other line, and it
           couldn't be cut off. Without comtypes: PowerShell's System.Speech (slower to start).
  Linux    espeak-ng or espeak.

speak(text) says it in the background, after whatever is being said already;
speak(text, non_blocking=False) returns once it's been said. stop() cuts it off and drops
what's waiting.
"""

import queue
import shutil
import subprocess
import sys
import threading

MAC_VOICE = None      # e.g. "Ava (Premium)" (see SAY_VOICE in voice.py); None = the system one
WINDOWS_VOICE = None  # part of a voice's name, e.g. "Zira"; None = the default one

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console popping up on Windows
SAPI_ASYNC, SAPI_PURGE = 1, 2  # SpVoice.Speak flags


class CommandVoice:
    """A program that says its argument (or what it's sent, with stdin=True) and exits."""

    def __init__(self, command, stdin=False):
        self.command, self.stdin, self.proc = command, stdin, None

    def say(self, text, stopped):
        if stopped():
            return
        if self.stdin:
            self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, creationflags=NO_WINDOW)
            self.proc.communicate(text.encode("utf-8"))
        else:
            self.proc = subprocess.Popen(self.command + [text], creationflags=NO_WINDOW)
            self.proc.wait()

    def stop(self):
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()


class SapiVoice:
    """The Windows voices. A COM object belongs to the thread that made it, so it's made on
    the first say(), on the thread that says everything."""

    def __init__(self):
        import comtypes.client  # noqa: F401  (missing: ImportError, so PowerShell instead)

        self.voice = None

    def say(self, text, stopped):
        if self.voice is None:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()
            self.voice = comtypes.client.CreateObject("SAPI.SpVoice")
            if WINDOWS_VOICE:
                voices = self.voice.GetVoices()
                for i in range(voices.Count):
                    if WINDOWS_VOICE.lower() in voices.Item(i).GetDescription().lower():
                        self.voice.Voice = voices.Item(i)
                        break
        self.voice.Speak(text, SAPI_ASYNC)
        while not self.voice.WaitUntilDone(50):  # ms
            if stopped():
                self.voice.Speak("", SAPI_ASYNC | SAPI_PURGE)
                return

    def stop(self):
        pass  # say() sees stopped() within 50 ms


POWERSHELL_SAY = ("[Console]::InputEncoding = [Text.Encoding]::UTF8; "
                  "Add-Type -AssemblyName System.Speech; "
                  "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                  ".Speak([Console]::In.ReadToEnd())")


def pick_voice(platform=sys.platform):
    """This laptop's voice, or None if it has none."""
    if platform == "darwin":
        return CommandVoice(["say"] + (["-v", MAC_VOICE] if MAC_VOICE else []))
    if platform == "win32":
        try:
            return SapiVoice()
        except ImportError:
            return CommandVoice(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                 POWERSHELL_SAY], stdin=True)
    for name in ("espeak-ng", "espeak"):
        if shutil.which(name):
            return CommandVoice([name])
    return None


_jobs = queue.Queue()
_voice = None
_turn = 0  # stop() starts a new turn: lines from before it aren't said
_started = False
_start_lock = threading.Lock()


def _start():
    global _voice, _started
    with _start_lock:
        if not _started:
            _started = True
            _voice = pick_voice()
            if _voice is None:
                print("\n(No voice on this laptop: install espeak-ng to hear answers.)")
            else:
                threading.Thread(target=_run, daemon=True).start()


def _run():
    while True:
        turn, text, done = _jobs.get()
        try:
            if turn == _turn:
                _voice.say(text, lambda: turn != _turn)
        except Exception as e:
            print(f"\n(voice: {e!r:.160})")
        done.set()


def speak(text, non_blocking=True):
    """Says text, after whatever's being said. False if this laptop has no voice."""
    _start()
    if _voice is None or not text:
        return False
    done = threading.Event()
    _jobs.put((_turn, text, done))
    if not non_blocking:
        done.wait()
    return True


def stop():
    """Cuts off what's being said and drops what's waiting."""
    global _turn
    _turn += 1
    if _voice:
        _voice.stop()
