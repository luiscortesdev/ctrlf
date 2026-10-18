"""
Ctrl+F for Your Room: find and point, on the desk. The detector runs on the live camera,
the window shows what it finds, and the laser points at what you ask for.

    venv\\Scripts\\python scripts\\demo_find_and_point.py                 the Wi-Fi camera
    venv\\Scripts\\python scripts\\demo_find_and_point.py --camera webcam  a plugged-in one

In the terminal: type a name (keys, wallet, phone, ...) and Enter: the laser points at it
for 4 s. "list" shows what it sees now; "q" quits.
In the window: SPACE points at the most confident thing in view; Q or Esc quits (servos
back to the middle, laser off).

Needs data/calibration.json (scripts/calibrate.py). Nobody looks toward the head.
"""

import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

import cv2

from src.app import (CAMERA_LAG_SECS, DETECT_SIZE, DETECTOR, DISPLAY_W, POINTER_HOST, POINTER_LINK,
                     PROMPTS, TILE_SIZE, TILES, camera_option, keep_level, load_area, load_taught,
                     threshold)
from src.hardware.pointer import ObjectPointer
from src.hardware.serial_client import Pointer
from src.vision.camera import per_camera
from src.vision.detector import BackgroundDetector, make_detector
from src.vision.laser_tracker import CALIB_PATH, Calibration
from src.vision.rtsp_camera import open_latest

WINDOW = "Ctrl+F find and point (Q to quit)"


def read_console(lines):
    while True:
        try:
            lines.put(input().strip())
        except EOFError:
            lines.put("q")
            return


def main():
    source = camera_option(sys.argv[1:], "Find things and point the laser at them.")
    path = per_camera(CALIB_PATH, source)
    calib = Calibration.load(path)
    if calib is None:
        raise SystemExit(f"No {path} yet: run scripts\\calibrate.py first.")
    print(f"Calibration: {calib.kind}, {len(calib.points)} points, typically {calib.error_px:.0f} px off.")
    cam = open_latest(source, CAMERA_LAG_SECS)
    pointer = Pointer(POINTER_LINK, POINTER_HOST)
    t0 = time.time()
    while not pointer.connected and time.time() - t0 < 10:
        time.sleep(0.2)
    if not pointer.connected:
        raise SystemExit("No ESP32 pointer found. Plug it in (COM port) or put it on Wi-Fi.")
    aim = ObjectPointer(pointer, calib)

    print(f"Loading the {DETECTOR} detector...")
    model = make_detector(DETECTOR, PROMPTS, DETECT_SIZE)
    load_taught(model)
    detector = BackgroundDetector(model, keep_level, load_area(), TILES, TILE_SIZE)

    lines = queue.Queue()
    threading.Thread(target=read_console, args=(lines,), daemon=True).start()
    print('Type a name and Enter to point at it ("list" = what it sees, "q" = quit). '
          "SPACE in the window = the most confident thing.")

    def point(name, dets, size):
        threading.Thread(target=aim.point_at_object, args=(name, dets),
                         kwargs={"frame_size": size}, daemon=True).start()

    last_id, dets, size = 0, [], None
    try:
        while True:
            frame, frame_id, _ = cam.get_frame()
            if frame is None:
                if cv2.waitKey(10) & 0xFF in (27, ord("q")):
                    break
                continue
            if frame_id != last_id:
                detector.submit(frame)  # (it drops frames it hasn't got to yet)
                last_id = frame_id
            seq, det_frame, cands, _ = detector.result()
            if seq:
                size = det_frame.shape[1::-1]
                dets = sorted(((l, c, b) for l, c, b in cands if c >= threshold(l)),
                              key=lambda d: -d[1])

            while not lines.empty():
                text = lines.get()
                if text.lower() in ("q", "quit", "exit"):
                    raise KeyboardInterrupt
                if text.lower() == "list":
                    print(", ".join(f"{l} {c:.2f}" for l, c, _ in dets) or "(nothing in view)")
                elif text:
                    point(text, list(dets), size)

            s = DISPLAY_W / frame.shape[1]
            view = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            ds = DISPLAY_W / size[0] if size else s
            for label, conf, (x1, y1, x2, y2) in dets:
                cv2.rectangle(view, (int(x1 * ds), int(y1 * ds)), (int(x2 * ds), int(y2 * ds)), (0, 255, 0), 2)
                cv2.putText(view, f"{label} {conf:.2f}", (int(x1 * ds), max(14, int(y1 * ds) - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            if aim.target:
                tx, ty = int(aim.target[0] * view.shape[1]), int(aim.target[1] * view.shape[0])
                cv2.drawMarker(view, (tx, ty), (0, 0, 255), cv2.MARKER_CROSS, 30, 2)
            status = ("LASER ON" if aim.target else "type a name in the terminal, or SPACE") + \
                     f"   detector {detector.secs_per_frame:.1f} s/pass"
            cv2.putText(view, status, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
            cv2.putText(view, status, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow(WINDOW, view)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                break
            if key == ord(" "):
                if dets:
                    point(dets[0][0], list(dets), size)
                else:
                    print("(nothing in view to point at)")
            pointer.poll()
    except KeyboardInterrupt:
        pass
    finally:
        pointer.send("C")   # servos back to the middle, laser off
        pointer.send("L0")
        time.sleep(0.3)
        cam.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
