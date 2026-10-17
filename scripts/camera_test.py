# Ctrl+F for Your Room: camera + object detection test
#
# Shows the live camera with a box around everything it recognizes, and prints what it
# sees about once a second. It uses the same objects, detector and thresholds as ctrlf.py,
# so use it to tune CONFIDENCE and LOOKS_LIKE there.
#
# One-time setup: see the top of ctrlf.py.
#
# Run it from Terminal.app (macOS only gives the camera to apps that ask for permission;
# approve it the first time):
#   source venv/bin/activate
#   python scripts/camera_test.py        (press Esc in the video window to quit)
#   python scripts/camera_test.py --camera webcam    (a plugged-in camera; see --help)

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

import time

import cv2

from src.app import (DETECT_SIZE, DETECTOR, DISPLAY_W, PROMPTS, TAUGHT, TILE_SIZE, TILES,
                     camera_option, draw_desk_area, draw_tile, draw_tracks, keep_level,
                     load_area, load_taught, open_room_camera, threshold)
from src.vision.detector import BackgroundDetector, make_detector
from src.vision.tracker import Tracker

cap = open_room_camera(camera_option(description="Live view with boxes, for tuning thresholds."))
print(f"Loading the {DETECTOR} detector (the first run downloads it)...")
model = make_detector(DETECTOR, PROMPTS, DETECT_SIZE)
load_taught(model)  # things taught in ctrlf.py; same settings as ctrlf.py
detector = BackgroundDetector(model, keep_level, load_area(), TILES, TILE_SIZE)
tracker = Tracker(threshold, single=lambda label: label in TAUGHT, keep=keep_level)

last_print, last_seq = 0, 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    detector.submit(frame)
    seq, _, candidates, det_time = detector.result()
    if seq != last_seq:
        last_seq = seq
        tracker.update(candidates, det_time)

    # Name, pixel center, confidence. These centers are what the laser will aim at.
    if seq and time.time() - last_print > 1:
        last_print = time.time()
        seen = [f"{t.label} @ ({t.center[0]:.0f},{t.center[1]:.0f}) {t.score:.2f}"
                for t in tracker.main.values()]
        print(" | ".join(seen) or "nothing", flush=True)

    s = DISPLAY_W / frame.shape[1]
    view = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    draw_tracks(view, tracker, s)
    draw_desk_area(view, detector.area, None)
    draw_tile(view, detector.tile, s)
    cv2.imshow("Ctrl+F test (Esc to quit)", view)
    if cv2.waitKey(1) == 27:
        break

cap.release()
cv2.destroyAllWindows()
