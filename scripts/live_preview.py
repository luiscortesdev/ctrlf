#!/usr/bin/env python3
"""
scripts/live_preview.py - Live Vision Pipeline & Detection GUI Preview

Runs the complete vision stack concurrently:
  1. Threaded Camera capture (30 FPS without buffer delay)
  2. Asynchronous open-vocabulary YOLO-World Object Detection (BackgroundDetector)
  3. Real-time LaserTracker (HSV thresholding + peak brightness verification)
  4. Real-time annotated OpenCV GUI overlay

Press 'q' or 'ESC' to exit.
"""

import sys
import time
from pathlib import Path

import cv2
import numpy as np

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.vision.camera import Camera
from src.vision.detector import BackgroundDetector, ObjectDetector
from src.vision.laser_tracker import LaserTracker

WINDOW_NAME = "Ctrl+F - Live Vision Preview (Esc / 'q' to Quit)"


def main():
    print("=" * 60)
    print("Initializing Ctrl+F Vision Pipeline...")
    print("=" * 60)

    # 1. Start threaded camera
    print("[1/3] Starting low-latency camera stream...")
    cam = Camera(auto_start=True)

    # 2. Start YOLO-World detector in background worker thread
    print("[2/3] Loading YOLO-World open-vocabulary detector...")
    detector = ObjectDetector()
    bg_detector = BackgroundDetector(detector, conf_threshold=0.20)

    # 3. Initialize Laser Tracker
    print("[3/3] Initializing laser tracker...")
    tracker = LaserTracker()

    print("\nLive vision GUI running! Press 'q' or 'ESC' on the video window to quit.")
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)

    fps_count = 0
    fps_start = time.time()
    display_fps = 0.0

    try:
        while True:
            ret, frame = cam.read()
            if not ret or frame is None:
                time.sleep(0.005)
                continue

            h, w = frame.shape[:2]

            # Submit latest frame to background detector (non-blocking)
            bg_detector.submit(frame)

            # Retrieve most recent finished detections
            seq, det_frame, detections, det_ts = bg_detector.result()

            # Detect laser in the active frame
            laser_pos = tracker.detect(frame)

            # Render overlay
            display = frame.copy()

            # Draw detected object bounding boxes
            for obj in detections:
                box = obj.bbox
                x1 = int(box.x1 * w)
                y1 = int(box.y1 * h)
                x2 = int(box.x2 * w)
                y2 = int(box.y2 * h)

                cv2.rectangle(display, (x1, y1), (x2, y2), (0, 255, 0), 2)
                label_text = f"{obj.label} ({obj.confidence:.2f})"
                (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(display, (x1, y1 - th - 6), (x1 + tw + 6, y1), (0, 255, 0), -1)
                cv2.putText(
                    display,
                    label_text,
                    (x1 + 3, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 0, 0),
                    1,
                    cv2.LINE_AA,
                )

            # Draw laser crosshair
            if laser_pos.detected and laser_pos.pixel_coord:
                cx, cy = laser_pos.pixel_coord
                cv2.circle(display, (cx, cy), 8, (0, 0, 255), 2)
                cv2.line(display, (cx - 15, cy), (cx + 15, cy), (0, 0, 255), 2)
                cv2.line(display, (cx, cy - 15), (cx, cy + 15), (0, 0, 255), 2)
                cv2.putText(
                    display,
                    "LASER",
                    (cx + 12, cy - 12),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 0, 255),
                    1,
                )

            # Calculate FPS
            fps_count += 1
            if time.time() - fps_start >= 1.0:
                display_fps = fps_count / (time.time() - fps_start)
                fps_count = 0
                fps_start = time.time()

            det_fps = (1.0 / bg_detector.secs_per_frame) if bg_detector.secs_per_frame > 0 else 0.0

            # HUD Banner
            hud = f"Camera: {display_fps:.1f} FPS | Detector: {det_fps:.1f} FPS | Objects: {len(detections)}"
            cv2.putText(
                display,
                hud,
                (15, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

            cv2.imshow(WINDOW_NAME, display)

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break

    finally:
        cam.stop()
        bg_detector.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
