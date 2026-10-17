"""
Ctrl+F for Your Room: how far the camera's picture runs behind (CAMERA_LAG_SECS).

The Wi-Fi camera's video arrives late, so the laser aiming has to wait that long after a
move before it trusts a frame. This measures it with the LED ring, no laser needed: it
switches the ring on and off and times how long each change takes to show up in the video.

Before you run it:
  - The ESP32 is on (USB or Wi-Fi) with the ring working, and the ring is in the camera's
    view, close enough to be more than a few pixels. Hold it or prop it up facing the camera.
  - Nothing else moves in the view while it runs (about 30 s). Turn off the Tapo's
    timestamp overlay, or its ticking clock can look like the ring.

Run it (no window; the Tapo stream needs no camera permission):
    source venv/bin/activate
    python scripts/measure_lag.py
Then put the number it suggests in CAMERA_LAG_SECS in src/app.py.
"""

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

import cv2
import numpy as np

from src.app import CAMERA, POINTER_HOST, POINTER_LINK
from src.hardware.serial_client import Pointer
from src.vision.camera import open_camera

TRIES = 8          # on/off switches to time
WAIT_SECS = 2.5    # each switch: long enough for any lag we'd put up with
CHANGE = 40        # a pixel "changed" if it got this much brighter (0-255, the brightest channel)


def frames_for(cap, secs):
    """(arrival time, grey-ish frame) for secs. read() hands over the newest frame as soon
    as it comes in, so the time it returns is when that frame reached the Mac."""
    out, end = [], time.time() + secs
    while time.time() < end:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("The camera stopped sending video.")
        out.append((time.time(), frame.max(axis=2).astype(np.int16)))  # brightest channel
    return out


def ring(pointer, on):
    pointer.ring("X" if on else "0")  # X: three LEDs at full, the rest dim; 0: all off
    return time.time()


def main():
    cap = open_camera(CAMERA)
    pointer = Pointer(POINTER_LINK, POINTER_HOST)
    t0 = time.time()
    while not (pointer.connected and pointer.has_ring) and time.time() - t0 < 8:
        time.sleep(0.1)
    if not pointer.connected:
        raise SystemExit("No ESP32: plug it in or put it on the same Wi-Fi as the Mac.")
    if not pointer.has_ring:
        raise SystemExit("The ESP32 doesn't report a ring (old firmware?).")
    pointer.send("RB255")  # full brightness: easier to see from across the room

    # Where is the ring in the picture? Pixels that brighten when it turns on AND darken
    # when it turns off (a clock overlay or someone moving doesn't do both in step).
    ring(pointer, False)
    off1 = frames_for(cap, WAIT_SECS)[-1][1]
    ring(pointer, True)
    on = frames_for(cap, WAIT_SECS)[-1][1]
    ring(pointer, False)
    off2 = frames_for(cap, WAIT_SECS)[-1][1]
    mask = (on - off1 > CHANGE) & (on - off2 > CHANGE)
    if mask.sum() < 4:
        raise SystemExit(f"Can't see the ring change ({mask.sum()} pixels). Is it in view, "
                         "facing the camera, and not in direct sun?")
    ys, xs = np.nonzero(mask)
    print(f"Ring found: {mask.sum()} pixels around ({int(xs.mean())}, {int(ys.mean())}).")
    low, high = off1[mask].mean(), on[mask].mean()
    half = (low + high) / 2

    lags, on_now = [], False
    for i in range(TRIES):
        on_now = not on_now
        sent = ring(pointer, on_now)
        frames = frames_for(cap, WAIT_SECS)
        seen = [t for t, f in frames if (f[mask].mean() > half) == on_now]
        if not seen:
            print(f"  try {i + 1}: not seen within {WAIT_SECS} s (skipped)")
            continue
        lags.append(seen[0] - sent)
        print(f"  try {i + 1} ({'on' if on_now else 'off'}): {lags[-1]:.2f} s")
    ring(pointer, False)
    pointer.send("RB120")  # back to the firmware's default cap
    pointer.ring("W")
    cap.release()

    if len(lags) < 3:
        raise SystemExit("Too few changes seen to say. Move the ring closer and try again.")
    gaps = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
    frame_secs = statistics.median(gaps)
    print(f"\nLag: median {statistics.median(lags):.2f} s, worst {max(lags):.2f} s "
          f"({len(lags)} changes; a frame every {frame_secs * 1000:.0f} ms).")
    # The aimer waits this long after every move, so cover the worst case plus a frame.
    print(f"Suggested CAMERA_LAG_SECS = {max(lags) + frame_secs:.2f}")


if __name__ == "__main__":
    main()
