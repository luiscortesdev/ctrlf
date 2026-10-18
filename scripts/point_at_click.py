"""
Ctrl+F for Your Room: click anywhere in the camera's picture and the laser points there
for 3 seconds. The check that the calibration (data/calibration.json) works on the real
hardware: click a notebook, the keys, a table corner, and look where the dot lands.

    venv\\Scripts\\python scripts\\point_at_click.py                 the Wi-Fi camera
    venv\\Scripts\\python scripts\\point_at_click.py --camera webcam  a plugged-in camera

In the window: click = point there; L = laser off now; Esc / Q = quit (laser off).
Green dots: the calibration points. Red cross: where you clicked. Orange circle: where
the calibration says the dot lands (differs from the cross when the target is out of the
head's reach). Nobody looks toward the head while it runs.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

import cv2

from src.app import CAMERA_LAG_SECS, DISPLAY_W, POINTER_HOST, POINTER_LINK, camera_option
from src.hardware.serial_client import Pointer
from src.vision.camera import per_camera
from src.vision.laser_tracker import CALIB_PATH, Calibration
from src.vision.rtsp_camera import open_latest

ON_SECS = 3.0
WINDOW = "Ctrl+F point at click (Esc to quit)"


def main():
    source = camera_option(sys.argv[1:], "Click in the picture, the laser points there.")
    path = per_camera(CALIB_PATH, source)
    calib = Calibration.load(path)
    if calib is None:
        raise SystemExit(f"No {path} yet: run scripts\\calibrate.py first.")
    print(f"Calibration: {calib.kind} from {len(calib.points)} points, typically "
          f"{calib.error_px:.0f} px off at a new spot" + (" (refitted from an older file)"
                                                        if getattr(calib, "refitted", False) else ""))
    cam = open_latest(source, CAMERA_LAG_SECS)
    pointer = Pointer(POINTER_LINK, POINTER_HOST)
    t0 = time.time()
    while not pointer.connected and time.time() - t0 < 10:
        time.sleep(0.2)
    if not pointer.connected:
        raise SystemExit("No ESP32 pointer found. Plug it in (COM port) or put it on Wi-Fi, and "
                         "close anything else using it.")

    clicks, scale = [], [1.0]
    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, lambda e, x, y, *_: clicks.append((x / scale[0], y / scale[0]))
                         if e == cv2.EVENT_LBUTTONDOWN else None)
    target = lands = None
    off_at, status = 0.0, "Click anywhere in the picture."
    try:
        while True:
            frame = cam.get_frame()[0]
            if frame is None:
                if cv2.waitKey(10) & 0xFF == 27:
                    break
                continue
            h, w = frame.shape[:2]
            while clicks:
                x, y = clicks.pop(0)
                pan, tilt = calib.to_angles(x / w, y / h)
                pointer.move(pan, tilt, laser=True)  # clamped to the servo limits
                off_at = time.time() + ON_SECS
                u, v = calib.to_image(pointer.pan, pointer.tilt)
                target, lands = (x, y), (u * w, v * h)
                clamped = abs(pointer.pan - pan) > 0.05 or abs(pointer.tilt - tilt) > 0.05
                status = (f"({x:.0f}, {y:.0f}) -> pan {pointer.pan:g} tilt {pointer.tilt:g}"
                          + ("  OUT OF REACH: as close as the limits allow" if clamped else ""))
                print(status, flush=True)
            if off_at and time.time() >= off_at:
                pointer.send("L0")
                off_at = 0.0
            s = scale[0] = DISPLAY_W / w
            view = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            for _, _, px, py in calib.points:
                cv2.circle(view, (int(px * s), int(py * s)), 3, (0, 255, 0), -1)
            if target:
                cv2.drawMarker(view, (int(target[0] * s), int(target[1] * s)), (0, 0, 255),
                               cv2.MARKER_CROSS, 26, 2)
                cv2.circle(view, (int(lands[0] * s), int(lands[1] * s)), 14, (0, 165, 255), 2)
            text = status + ("   LASER ON" if off_at else "")
            cv2.putText(view, text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
            cv2.putText(view, text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow(WINDOW, view)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                break
            if key in (ord("l"), ord("L")):
                pointer.send("L0")
                off_at = 0.0
            pointer.poll()
    except KeyboardInterrupt:
        pass
    finally:
        pointer.send("L0")
        time.sleep(0.2)
        cam.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
