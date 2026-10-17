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
#   python camera_test.py        (press Esc in the video window to quit)

import time

import cv2

from ctrlf import CAMERA, CONFIDENCE, DETECTOR, DISPLAY_W, PROMPTS, open_camera
from detector import BackgroundDetector, draw_detections, make_detector

print(f"Loading the {DETECTOR} detector (the first run downloads it)...")
detector = BackgroundDetector(make_detector(DETECTOR, PROMPTS), CONFIDENCE)
cap = open_camera(CAMERA)

last_print = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    detector.submit(frame)
    seq, _, detections, _ = detector.result()

    # Name, pixel center, confidence. These centers are what the laser will aim at.
    if seq and time.time() - last_print > 1:
        last_print = time.time()
        seen = [f"{label} @ ({(x1 + x2) // 2},{(y1 + y2) // 2}) {conf:.2f}"
                for label, (conf, (x1, y1, x2, y2)) in detections.items()]
        print(" | ".join(seen) or "nothing", flush=True)

    s = DISPLAY_W / frame.shape[1]
    view = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    draw_detections(view, detections, scale=s)
    cv2.imshow("Ctrl+F test (Esc to quit)", view)
    if cv2.waitKey(1) == 27:
        break

cap.release()
cv2.destroyAllWindows()
