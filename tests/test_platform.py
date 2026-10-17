"""The parts that differ between the Mac and Windows: the device the models run on, the
laptop camera, the laptop's voice and the microphone. Nothing here needs a GPU, a camera,
a microphone or a speaker."""

import sys
import threading
import time

import numpy as np
import pytest
import torch

from src import device, tts, voice
from src.vision import camera


@pytest.fixture
def fresh_device(monkeypatch):
    monkeypatch.setattr(device, "_picked", None)
    for name in ("cuda", "mps"):
        monkeypatch.setattr(getattr(torch, name) if name == "cuda" else torch.backends.mps,
                            "is_available", lambda: False)
    if hasattr(torch, "xpu"):
        monkeypatch.setattr(torch.xpu, "is_available", lambda: False)
    threads = []
    monkeypatch.setattr(torch, "set_num_threads", threads.append)
    return threads


def test_device_prefers_cuda_then_xpu_then_mps(fresh_device, monkeypatch):
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    assert device.get_torch_device() == torch.device("mps")
    if hasattr(torch, "xpu"):
        monkeypatch.setattr(device, "_picked", None)
        monkeypatch.setattr(torch.xpu, "is_available", lambda: True)
        assert device.get_torch_device() == torch.device("xpu")
    monkeypatch.setattr(device, "_picked", None)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert device.get_torch_device() == torch.device("cuda:0")
    assert fresh_device == []  # thread count only matters on the CPU


def test_cpu_leaves_some_cores_free(fresh_device, monkeypatch):
    monkeypatch.delenv("CTRLF_TORCH_THREADS", raising=False)
    monkeypatch.setattr(device.os, "cpu_count", lambda: 16)
    assert device.get_torch_device() == torch.device("cpu")
    assert fresh_device == [12]
    for cores, threads in ((8, 6), (4, 3), (2, 2), (1, 1), (64, 60)):
        monkeypatch.setattr(device.os, "cpu_count", lambda: cores)
        assert device.cpu_threads() == threads
    monkeypatch.setenv("CTRLF_TORCH_THREADS", "5")
    assert device.cpu_threads() == 5


def test_half_precision_only_on_gpus():
    assert not device.half_precision("cpu")
    assert device.half_precision("mps") and device.half_precision(torch.device("cuda:0"))


def test_laptop_camera_uses_directshow_on_windows(monkeypatch):
    opened = []
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda *args: opened.append(args))
    monkeypatch.setattr(camera.sys, "platform", "win32")
    camera.local_camera(0)
    monkeypatch.setattr(camera.sys, "platform", "darwin")
    camera.local_camera(1)
    assert opened == [(0, camera.cv2.CAP_DSHOW), (1,)]


def test_pick_voice_per_platform(monkeypatch):
    mac = tts.pick_voice("darwin")
    assert isinstance(mac, tts.CommandVoice) and mac.command[0] == "say"

    monkeypatch.setattr(tts.shutil, "which", lambda name: name == "espeak")
    assert tts.pick_voice("linux").command == ["espeak"]
    monkeypatch.setattr(tts.shutil, "which", lambda name: None)
    assert tts.pick_voice("linux") is None

    def no_comtypes():
        raise ImportError("no comtypes")
    monkeypatch.setattr(tts.SapiVoice, "__init__", lambda self: no_comtypes())
    windows = tts.pick_voice("win32")
    assert windows.command[0] == "powershell" and windows.stdin


class FakeVoice:
    def __init__(self):
        self.said, self.cut = [], threading.Event()

    def say(self, text, stopped):
        self.said.append(text)
        while text == "long" and not stopped():
            time.sleep(0.01)
        if stopped():
            self.cut.set()

    def stop(self):
        pass


@pytest.fixture
def fake_voice(monkeypatch):
    fake = FakeVoice()
    monkeypatch.setattr(tts, "pick_voice", lambda: fake)
    monkeypatch.setattr(tts, "_started", False)
    monkeypatch.setattr(tts, "_jobs", tts.queue.Queue())
    return fake


def test_speak_waits_when_asked(fake_voice):
    assert tts.speak("one", non_blocking=False)
    assert tts.speak("two", non_blocking=False)
    assert fake_voice.said == ["one", "two"]
    assert not tts.speak("")


def test_stop_cuts_off_and_drops_whats_waiting(fake_voice):
    tts.speak("long")
    tts.speak("dropped")
    while not fake_voice.said:
        time.sleep(0.01)
    tts.stop()
    assert fake_voice.cut.wait(2)
    assert tts.speak("after", non_blocking=False)
    assert fake_voice.said == ["long", "after"]


def test_no_voice_is_not_an_error(monkeypatch):
    monkeypatch.setattr(tts, "pick_voice", lambda: None)
    monkeypatch.setattr(tts, "_started", False)
    assert tts.speak("hello", non_blocking=False) is False


class FakeSoundDevice:
    """Stands in for the sounddevice module."""

    def __init__(self, block=None, error=None):
        self.block, self.error = block, error  # block(): the next 0.1 s of sound

    def InputStream(self, **kwargs):
        if self.error:
            raise self.error
        outer = self

        class Stream:
            def start(self):
                pass

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n):
                time.sleep(0.01)
                return outer.block().reshape(-1, 1), False
        return Stream()


def listener():
    lst = voice.Listener.__new__(voice.Listener)  # no model loading
    lst._stop = threading.Event()
    return lst


def test_mic_that_wont_open_says_how_to_fix_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice",
                        FakeSoundDevice(error=OSError("Error opening InputStream")))
    monkeypatch.setattr(voice, "play_cue", lambda path: None)
    with pytest.raises(voice.MicError, match="Microphone|microphone"):
        listener()._record()


def test_mic_without_permission_records_zeros(monkeypatch):
    silent = lambda: np.zeros(voice.BLOCK, np.float32)  # noqa: E731
    monkeypatch.setitem(sys.modules, "sounddevice", FakeSoundDevice(silent))
    monkeypatch.setattr(voice, "play_cue", lambda path: None)
    monkeypatch.setattr(voice, "MAX_SECS", 0.3)
    with pytest.raises(voice.MicError, match="silence"):
        listener()._record()


def test_quiet_room_is_not_a_mic_problem(monkeypatch):
    rng = np.random.default_rng(0)
    quiet = lambda: (rng.standard_normal(voice.BLOCK) * 0.002).astype(np.float32)  # noqa: E731
    monkeypatch.setitem(sys.modules, "sounddevice", FakeSoundDevice(quiet))
    monkeypatch.setattr(voice, "play_cue", lambda path: None)
    monkeypatch.setattr(voice, "MAX_SECS", 0.3)
    audio, heard = listener()._record()
    assert len(audio) and not heard


def test_whisper_on_the_cpu_without_nvidia(monkeypatch):
    class NoCuda:
        @staticmethod
        def get_cuda_device_count():
            return 0
    monkeypatch.setitem(sys.modules, "ctranslate2", NoCuda)
    assert voice.whisper_setup() == (voice.WHISPER_CPU_MODEL, "cpu", "int8")
