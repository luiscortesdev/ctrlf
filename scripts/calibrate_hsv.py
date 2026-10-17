#!/usr/bin/env python3
"""
scripts/calibrate_hsv.py - Interactive HSV Calibration GUI for Laser Dot Detection

Provides trackbars to tune HSV lower/upper bounds for ambient lighting conditions.
Displays live video alongside the binary laser mask and detected crosshairs.
Press 's' to dump calibrated values directly to config/settings.yaml.
Press 'q' or 'Esc' to exit.
"""

import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.vision.camera import Camera
from src.vision.laser_tracker import LaserTracker

CONFIG_FILE = ROOT_DIR / "config" / "settings.yaml"
WINDOW_NAME = "Laser HSV Calibration (Press 's' to Save, 'q' to Quit)"


def nothing(x):
    pass


def main():
    # Load existing config
    cfg = {}
    if CONFIG_FILE.is_file():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    laser_cfg = cfg.get("laser", {})
    lower1 = laser_cfg.get("hsv_lower_1", [0, 100, 200])
    upper1 = laser_cfg.get("hsv_upper_1", [10, 255, 255])
    lower2 = laser_cfg.get("hsv_lower_2", [160, 100, 200])
    upper2 = laser_cfg.get("hsv_upper_2", [180, 255, 255])

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)

    # Sliders for Band 1 (Lower Red, e.g. 0 - 10)
    cv2.createTrackbar("H1_Min", WINDOW_NAME, lower1[0], 179, nothing)
    cv2.createTrackbar("H1_Max", WINDOW_NAME, upper1[0], 179, nothing)
    # Sliders for Band 2 (Upper Red, e.g. 160 - 180)
    cv2.createTrackbar("H2_Min", WINDOW_NAME, lower2[0], 179, nothing)
    cv2.createTrackbar("H2_Max", WINDOW_NAME, upper2[0], 179, nothing)
    # Shared Saturation and Value bounds
    cv2.createTrackbar("S_Min", WINDOW_NAME, lower1[1], 255, nothing)
    cv2.createTrackbar("S_Max", WINDOW_NAME, upper1[1], 255, nothing)
    cv2.createTrackbar("V_Min", WINDOW_NAME, lower1[2], 255, nothing)
    cv2.createTrackbar("V_Max", WINDOW_NAME, upper1[2], 255, nothing)

    print("=" * 60)
    print("Ctrl+F: Laser HSV Calibration GUI")
    print("Controls:")
    print("  - Adjust sliders to isolate the red laser dot from ambient light")
    print("  - Press 's' to SAVE tuned parameters to config/settings.yaml")
    print("  - Press 'q' or 'ESC' to exit")
    print("=" * 60)

    cam = Camera(auto_start=True)
    status_msg = "Ready - Adjust sliders"

    try:
        while True:
            ret, frame = cam.read()
            if not ret or frame is None:
                continue

            # Read slider positions
            h1_min = cv2.getTrackbarPos("H1_Min", WINDOW_NAME)
            h1_max = cv2.getTrackbarPos("H1_Max", WINDOW_NAME)
            h2_min = cv2.getTrackbarPos("H2_Min", WINDOW_NAME)
            h2_max = cv2.getTrackbarPos("H2_Max", WINDOW_NAME)
            s_min = cv2.getTrackbarPos("S_Min", WINDOW_NAME)
            s_max = cv2.getTrackbarPos("S_Max", WINDOW_NAME)
            v_min = cv2.getTrackbarPos("V_Min", WINDOW_NAME)
            v_max = cv2.getTrackbarPos("V_Max", WINDOW_NAME)

            b1_low = np.array([h1_min, s_min, v_min], dtype=np.uint8)
            b1_high = np.array([h1_max, s_max, v_max], dtype=np.uint8)
            b2_low = np.array([h2_min, s_min, v_min], dtype=np.uint8)
            b2_high = np.array([h2_max, s_max, v_max], dtype=np.uint8)

            # Construct tracker with live slider values
            tracker = LaserTracker(
                hsv_lower_1=tuple(b1_low.tolist()),
                hsv_upper_1=tuple(b1_high.tolist()),
                hsv_lower_2=tuple(b2_low.tolist()),
                hsv_upper_2=tuple(b2_high.tolist()),
                min_brightness=v_min,
            )

            laser_pos = tracker.detect(frame)

            # Generate live mask visualization
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            m1 = cv2.inRange(hsv, b1_low, b1_high)
            m2 = cv2.inRange(hsv, b2_low, b2_high)
            mask = cv2.bitwise_or(m1, m2)
            mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

            display_frame = frame.copy()
            if laser_pos.detected and laser_pos.pixel_coord:
                cx, cy = laser_pos.pixel_coord
                # Draw crosshairs and circle
                cv2.circle(display_frame, (cx, cy), 8, (0, 255, 0), 2)
                cv2.line(display_frame, (cx - 15, cy), (cx + 15, cy), (0, 255, 0), 2)
                cv2.line(display_frame, (cx, cy - 15), (cx, cy + 15), (0, 255, 0), 2)
                cv2.putText(
                    display_frame,
                    f"Laser ({cx}, {cy})",
                    (cx + 12, cy - 12),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                )

            # Overlay status
            cv2.putText(
                display_frame,
                status_msg,
                (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 255),
                2,
            )

            # Stack original and mask side-by-side
            preview_h = 360
            s_scale = preview_h / float(display_frame.shape[0])
            small_orig = cv2.resize(display_frame, None, fx=s_scale, fy=s_scale)
            small_mask = cv2.resize(mask_bgr, None, fx=s_scale, fy=s_scale)
            combined = np.hstack((small_orig, small_mask))

            cv2.imshow(WINDOW_NAME, combined)

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            elif key == ord("s"):
                # Save to config/settings.yaml
                if "laser" not in cfg:
                    cfg["laser"] = {}
                cfg["laser"]["hsv_lower_1"] = [h1_min, s_min, v_min]
                cfg["laser"]["hsv_upper_1"] = [h1_max, s_max, v_max]
                cfg["laser"]["hsv_lower_2"] = [h2_min, s_min, v_min]
                cfg["laser"]["hsv_upper_2"] = [h2_max, s_max, v_max]
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f, default_flow_style=False)
                status_msg = "SAVED to config/settings.yaml!"
                print(f"[SUCCESS] {status_msg}")

    finally:
        cam.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
