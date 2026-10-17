"""
Ctrl+F for Your Room: laser calibration.

Run it once the camera and the pan/tilt head (with the laser) are in their final places,
and again whenever either one gets moved. It sweeps the laser over a grid of pan/tilt
angles, finds the dot in the camera each time, and fits the mapping from camera pixels to
angles. The result goes in calibration.json, which ctrlf.py loads at startup.

Before you run it:
  - Nothing will bump the camera or the head.
  - Nobody stands in front of the head or looks toward it while it sweeps.
  - Even, not-too-bright light helps; don't move in front of the camera while it runs.

Run it from Terminal.app (Esc in the video window stops it):
    source venv/bin/activate
    python scripts/calibrate.py
Takes about a minute.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

import time

import cv2

from src.app import CAMERA, CAMERA_LAG_SECS, DISPLAY_W, POINTER_HOST, POINTER_LINK
from src.hardware.serial_client import Pointer
from src.vision.camera import open_camera
from src.vision.laser_tracker import (CALIB_PATH, LASER_SETTLE_SECS, SETTLE_SECS, TILT_MAX, TILT_MIN,
                   Calibration, find_dot)

# The grid of angles to try. Points where the dot lands outside the camera's view are
# skipped, so it's fine to cover more than the camera sees.
PANS = range(30, 151, 15)
TILTS = [t for t in range(95, 156, 10) if TILT_MIN <= t <= TILT_MAX]
WINDOW = "Ctrl+F calibration (Esc to stop)"


class LiveCamera:
    """Keeps reading and showing frames while the head moves, so frames stay fresh."""

    def __init__(self, cap):
        self.cap = cap
        self.found = []  # dots seen so far, drawn on the view

    def wait(self, secs):
        end, frame = time.time() + secs, None
        while frame is None or time.time() < end:
            ok, frame = self.cap.read()
            if not ok:
                raise SystemExit("Camera stopped sending frames.")
            self.show(frame)
        return frame

    def show(self, frame):
        s = DISPLAY_W / frame.shape[1]
        view = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        for x, y in self.found:
            cv2.circle(view, (int(x * s), int(y * s)), 6, (0, 255, 0), 2)
        cv2.putText(view, f"{len(self.found)} dots found", (12, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.imshow(WINDOW, view)
        if cv2.waitKey(1) & 0xFF == 27:
            raise KeyboardInterrupt


def sweep(camera, pointer, pans, tilts, log=print):
    """Visit every grid point (snaking, so moves stay short). Returns [(pan, tilt, x, y)]."""
    points = []
    order = [(p, t) for i, t in enumerate(tilts) for p in (pans if i % 2 == 0 else pans[::-1])]
    for n, (pan, tilt) in enumerate(order, 1):
        secs = pointer.move(pan, tilt, laser=False)
        off = camera.wait(secs + SETTLE_SECS + open_camera.lag)
        pointer.send("L1")
        on = camera.wait(LASER_SETTLE_SECS + open_camera.lag)
        pointer.send("L0")
        dot = find_dot(off, on)
        if dot:
            points.append((pointer.pan, pointer.tilt, dot[0], dot[1]))
            camera.found.append(dot)
        log(f"  {n:2d}/{len(order)}  pan {pointer.pan:3d} tilt {pointer.tilt:3d}  "
            + (f"dot at ({dot[0]:.0f}, {dot[1]:.0f}), red ratio {find_dot.red_ratio:.1f}"
               if dot else "no dot"))
    return points


def main():
    cap = open_camera(CAMERA, CAMERA_LAG_SECS)
    pointer = Pointer(POINTER_LINK, POINTER_HOST)
    if not pointer.connected:
        raise SystemExit("No ESP32 pointer found. Plug the ESP32 in (COM port) or put it on "
                         "Wi-Fi (firmware/esp32_controller/src/secrets.h), and close anything "
                         "else using it, like the Arduino Serial Monitor or ctrlf.py.")
    input("The laser is about to sweep the desk. Make sure nobody is looking toward the "
          "head, then press Enter... ")
    camera = LiveCamera(cap)
    size = camera.wait(0.5).shape[1::-1]  # (width, height)
    try:
        points = sweep(camera, pointer, list(PANS), TILTS)
    except KeyboardInterrupt:
        raise SystemExit("Stopped.")
    finally:
        pointer.send("L0")

    if len(points) < 8:
        raise SystemExit(
            f"Only found the dot {len(points)} times; need at least 8. Check that the laser "
            "points into the camera's view, dim the lights a bit, and run it again.")
    calib = Calibration.fit(points, size)
    calib.save(CALIB_PATH)
    print(f"\nSaved {CALIB_PATH}: {len(calib.points)} of {len(points)} points kept, "
          f"typical error {calib.error_px:.0f} px ({calib.error_deg:.2f} degrees).")
    print("ctrlf.py corrects what's left by watching the dot.")
    camera.wait(3)
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
