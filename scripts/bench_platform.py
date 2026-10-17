# Ctrl+F for Your Room: is this laptop set up right?
#
# Prints the OS, Python and torch build, which device the models run on (and how to get a
# GPU build of torch if it's on the CPU but has a GPU), times the picture embeddings the
# detector makes, checks the microphone and Whisper, and says something out loud.
#
#   python scripts/bench_platform.py              (Windows: venv\Scripts\python ...)
#   python scripts/bench_platform.py --no-mic --no-tts --iters 5

import argparse
import os
import platform
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

import numpy as np

TORCH_INDEX = "https://download.pytorch.org/whl/"
CUDA_BUILD = "cu130"  # needs an NVIDIA driver from 2025 or later; older drivers: cu126


def row(name, value):
    print(f"  {name:<18} {value}")


def section(title):
    print(f"\n{title}")


def gpu_names():
    """The graphics adapters the OS knows about (whatever torch thinks)."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_VideoController).Name"],
                                 capture_output=True, text=True, timeout=15).stdout
        elif sys.platform == "darwin":
            out = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True,
                                 text=True, timeout=15).stdout
            out = "\n".join(line.split(":", 1)[1] for line in out.splitlines()
                            if "Chipset Model" in line)
        else:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=15).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def install_hint(build):
    python = r"venv\Scripts\python.exe" if sys.platform == "win32" else "venv/bin/python"
    return [f"uv pip install --python {python} --reinstall torch torchvision "
            f"--index-url {TORCH_INDEX}{build}"]


def gpu_advice(device):
    """What to do if the models run on the CPU but there's a GPU they could use."""
    if device.type != "cpu":
        return []
    names = gpu_names()
    row("graphics", ", ".join(names) or "(none found)")
    if any("nvidia" in n.lower() for n in names):
        return (["An NVIDIA GPU is here, but this torch is CPU-only. Install the CUDA build:"]
                + install_hint(CUDA_BUILD) + ["(If torch then says no CUDA, update the NVIDIA "
                                              "driver, or use cu126.)"])
    if sys.platform != "darwin" and any(k in n.lower() for n in names
                                        for k in ("arc", "intel(r) graphics", "iris xe")):
        return (["An Intel GPU is here (Core Ultra / Arc). torch's XPU build can run the "
                 "models on it (it needs a recent Intel graphics driver):"]
                + install_hint("xpu") + ["Iris Xe (11th-13th gen Core) isn't supported by it; "
                                         "only Core Ultra and Arc are."])
    if sys.platform == "darwin":
        return ["No Apple GPU (MPS): is this Python built for Apple Silicon (arm64)?"]
    return []


def sync(device):
    """Wait for the GPU to finish, so the timing is real."""
    import torch

    if device.type == "cuda":
        torch.cuda.synchronize()
    elif device.type == "mps":
        torch.mps.synchronize()
    elif device.type == "xpu":
        torch.xpu.synchronize()


def bench_vision(iters, size):
    """Times OwlDetector.look() on a made-up 224x224 frame: the picture embeddings every
    detector pass makes (padded to `size`, as the app does)."""
    from src.vision.detector import OwlDetector

    section(f"Picture embeddings (OWLv2, input {size}, {iters} runs)")
    t = time.perf_counter()
    owl = OwlDetector({"keys": ["keys"]}, size=size)
    row("load", f"{time.perf_counter() - t:.1f} s on {owl.device} ({str(owl.dtype).split('.')[-1]})")
    frame = np.random.default_rng(0).integers(0, 255, (224, 224, 3), dtype=np.uint8)
    for _ in range(2):  # the first runs are slow (memory, kernels)
        owl.look(frame)
    sync(owl.device)
    times = []
    for _ in range(iters):
        t = time.perf_counter()
        look = owl.look(frame)
        sync(owl.device)
        times.append((time.perf_counter() - t) * 1000)
    row("embeddings", f"{tuple(look.embeds.shape)}")
    row("average", f"{np.mean(times):.0f} ms  (fastest {min(times):.0f}, slowest {max(times):.0f})")


def bench_whisper():
    """Loads Whisper the way voice.py does and transcribes 3 s of quiet noise."""
    from src import voice

    section("Speech to text")
    audio = (np.random.default_rng(0).standard_normal(3 * voice.RATE) * 0.003).astype(np.float32)
    try:
        t = time.perf_counter()
        voice.transcribe(audio[:voice.RATE])
        row("load", f"{time.perf_counter() - t:.1f} s")
        t = time.perf_counter()
        voice.transcribe(audio)
        row("3 s of audio", f"{(time.perf_counter() - t) * 1000:.0f} ms")
        if sys.platform != "darwin":
            name, device, compute = voice.whisper_setup()
            row("model", f"faster-whisper {name} on {device} ({compute})")
        else:
            row("model", f"mlx-whisper {voice.MODEL}")
    except Exception as e:
        row("FAILED", f"{e!r:.200}")


def test_mic():
    from src import voice

    section("Microphone (1 s)")
    try:
        import sounddevice as sd

        row("input", sd.query_devices(kind="input")["name"])
        audio = sd.rec(voice.RATE, samplerate=voice.RATE, channels=1, dtype="float32")
        sd.wait()
    except Exception as e:
        row("FAILED", f"{e!r:.200}")
        print(f"  {voice.MIC_HELP}.")
        return
    audio = audio[:, 0]
    rms = float(np.sqrt(np.mean(audio ** 2)))
    row("RMS", f"{rms:.3g}  (peak {np.abs(audio).max():.3g}; talking counts above "
               f"{voice.SPEECH_LEVEL}, SPEECH_LEVEL in voice.py)")
    if not audio.any():
        print(f"  Only zeros: no microphone permission? {voice.MIC_HELP}.")


def test_tts():
    from src import tts

    section("Text to speech")
    t = time.perf_counter()
    voice = tts.pick_voice()
    row("voice", type(voice).__name__ + (f" {voice.command[0]}" if hasattr(voice, "command") else "")
        if voice else "(none)")
    if voice and tts.speak("Control F for your room is ready.", non_blocking=False):
        row("whole line took", f"{time.perf_counter() - t:.1f} s")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iters", type=int, default=20)
    p.add_argument("--size", type=int, default=None, help="detector input size (default: the app's)")
    p.add_argument("--no-vision", action="store_true")
    p.add_argument("--no-whisper", action="store_true")
    p.add_argument("--no-mic", action="store_true")
    p.add_argument("--no-tts", action="store_true")
    args = p.parse_args()

    import cv2
    import torch

    from src.device import get_torch_device

    section("This laptop")
    row("OS", f"{platform.system()} {platform.release()} ({platform.version()})")
    row("CPU", f"{platform.machine()}, {os.cpu_count()} cores  {platform.processor()}")
    row("Python", sys.version.split()[0] + f" ({sys.executable})")
    row("torch", f"{torch.__version__}  CUDA build: {torch.version.cuda or 'no'}"
                 + (f", XPU build: {getattr(torch.version, 'xpu', None) or 'no'}"
                    if sys.platform != "darwin" else ""))
    row("OpenCV", cv2.__version__)

    section("Device")
    device = get_torch_device()
    row("models run on", str(device) + (f" ({torch.get_num_threads()} threads)" if device.type == "cpu" else ""))
    if device.type == "cuda":
        row("GPU", torch.cuda.get_device_name(0))
    elif device.type == "xpu":
        row("GPU", torch.xpu.get_device_name(0))
    advice = gpu_advice(device)
    for line in advice:
        print(f"  {line}")

    if not args.no_vision:
        from src.app import DETECT_SIZE

        bench_vision(args.iters, args.size or DETECT_SIZE)
    if not args.no_whisper:
        bench_whisper()
    if not args.no_mic:
        test_mic()
    if not args.no_tts:
        test_tts()
    print()


if __name__ == "__main__":
    main()
