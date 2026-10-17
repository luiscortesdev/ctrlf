"""
What computer this is running on, and settings that keep up on it.

The full settings in app.py (OWLv2 at 768, 2x2 tiles, zooming in on missing things) need a
GPU: an Apple Silicon Mac or an NVIDIA card does a pass in a fraction of a second. On a
laptop without one (most Windows laptops), the same pass takes 20+ seconds and everything
falls behind. So without a GPU it starts lighter (one look per pass, smaller input) and
Governor steps the input size down further until a pass takes about TARGET_SECS.

Override anything in .env (or the environment), e.g. to try the full settings on a CPU:
    CTRLF_DETECTOR=owl        owl (finds small things, can be taught) or yolo (fast, misses keys)
    CTRLF_DETECT_SIZE=768     detector input size
    CTRLF_TILES=2x2           zoom into parts of the view, taking turns; off = whole view only
    CTRLF_FOCUS=6             zoom into where missing things were last seen; 0 = off
    CTRLF_CAMERA=0            room camera: an index, or a network stream address
    CTRLF_TEACH_CAMERA=1      the camera to hold things up to; off = room camera only
    CTRLF_THREADS=4           CPU threads for the detector
"""

import os
import platform
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repo

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_APPLE_SILICON = IS_MAC and platform.machine() == "arm64"

# Without a GPU: start here, and step the size down if passes are still slow.
CPU_DETECT_SIZE = 512
CPU_SIZES = (512, 448, 384, 320)
TARGET_SECS = 1.5  # a pass slower than this (on average) on a CPU: go one size smaller


def _dotenv():
    values = {}
    try:
        with open(os.path.join(ROOT, ".env")) as f:
            for line in f:
                name, _, value = line.strip().partition("=")
                if name.startswith("CTRLF_") and value:
                    values[name] = value.strip().strip('"')
    except OSError:
        pass
    return values


_ENV = _dotenv()


def setting(name, default=None):
    """CTRLF_<name> from the environment, then .env, else default."""
    return os.environ.get(f"CTRLF_{name}") or _ENV.get(f"CTRLF_{name}") or default


def gpu():
    """"mps" (Apple GPU), "cuda" (NVIDIA) or None."""
    try:
        import torch
    except ImportError:
        return None
    try:
        if torch.backends.mps.is_available():
            return "mps"
    except AttributeError:
        pass
    try:
        if torch.cuda.is_available():
            return "cuda"
    except AttributeError:
        pass
    return None


def _camera(value, default):
    if value is None:
        return default
    if value.lower() in ("off", "none"):
        return None
    return int(value) if value.isdigit() else value


def tune(detector, size, tiles, focus, camera, teach_camera):
    """app.py's settings, adjusted for this computer and CTRLF_* overrides.
    Returns (detector, size, tiles, focus, camera, teach_camera, summary)."""
    device = gpu()
    if device is None:  # CPU only: one look per pass, smaller input
        size, tiles, focus = CPU_DETECT_SIZE, None, 0
        limit_threads()
    if not IS_MAC and camera == 1:
        # 1 is the iPhone through Continuity Camera, a Mac thing. Elsewhere 1 is often
        # a laptop's infrared face-unlock camera (black and white), so use the webcam.
        camera, teach_camera = 0, None
    detector = setting("DETECTOR", detector)
    size = int(setting("DETECT_SIZE", size))
    t = setting("TILES")
    if t:
        tiles = None if t.lower() in ("off", "none", "0") else tuple(int(n) for n in t.lower().split("x"))
    focus = int(setting("FOCUS", focus))
    camera = _camera(setting("CAMERA"), camera)
    teach_camera = _camera(setting("TEACH_CAMERA"), teach_camera)
    where = {"mps": "Apple GPU", "cuda": "NVIDIA GPU"}.get(device, "CPU only, no GPU found")
    summary = (f"{platform.system()} {platform.machine()}, {where}: {detector} at {size}, "
               f"tiles {'x'.join(map(str, tiles)) if tiles else 'off'}, look-again {'on' if focus else 'off'}")
    return detector, size, tiles, focus, camera, teach_camera, summary


def limit_threads():
    """Leave some cores for the video and the website: torch takes them all by default,
    and then the live view stutters while a pass runs."""
    n = int(setting("THREADS", 0)) or max(2, (os.cpu_count() or 4) // 2)
    try:
        import torch

        torch.set_num_threads(n)
    except (ImportError, AttributeError):
        pass


class Governor:
    """On a CPU, steps the detector's input size down (CPU_SIZES) while passes take longer
    than TARGET_SECS. Does nothing with a GPU or when CTRLF_DETECT_SIZE is set."""

    def __init__(self, model):
        self.model = model
        self.on = gpu() is None and setting("DETECT_SIZE") is None and hasattr(model, "size")
        self.passes_at_change = 0

    def update(self, detector, passes):
        if not self.on or passes - self.passes_at_change < 4 or not detector.secs_per_frame:
            return  # let the average settle after each change (the first pass is slow anyway)
        smaller = [s for s in CPU_SIZES if s < self.model.size]
        if detector.secs_per_frame > TARGET_SECS and smaller:
            self.model.size = smaller[0]
            self.passes_at_change = passes
            print(f"\n(Detector is slow on this computer ({detector.secs_per_frame:.1f} s a pass): "
                  f"looking at {self.model.size} px now)")
        elif detector.secs_per_frame <= TARGET_SECS or not smaller:
            self.on = False  # settled
