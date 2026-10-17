"""
src/vision/laser_tracker.py - Laser Dot Extraction via Dual-Band HSV & Intensity Verification

Isolates red laser dot using dual HSV red-wrap masks, morphological opening,
contour area filtering, and peak brightness verification (>= 240).
Adheres strictly to AGENTS.md Section 5.1 and Section 12.
"""

from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import yaml

from src.core.types import LaserPosition


class LaserTracker:
    """Detects and isolates a red laser dot centroid from camera frames."""

    def __init__(
        self,
        hsv_lower_1: Optional[Tuple[int, int, int]] = None,
        hsv_upper_1: Optional[Tuple[int, int, int]] = None,
        hsv_lower_2: Optional[Tuple[int, int, int]] = None,
        hsv_upper_2: Optional[Tuple[int, int, int]] = None,
        min_contour_area: Optional[float] = None,
        max_contour_area: Optional[float] = None,
        min_brightness: int = 240,
    ):
        cfg = self._load_config()
        self.lower1 = np.array(hsv_lower_1 or cfg.get("hsv_lower_1", [0, 100, 200]), dtype=np.uint8)
        self.upper1 = np.array(hsv_upper_1 or cfg.get("hsv_upper_1", [10, 255, 255]), dtype=np.uint8)
        self.lower2 = np.array(hsv_lower_2 or cfg.get("hsv_lower_2", [160, 100, 200]), dtype=np.uint8)
        self.upper2 = np.array(hsv_upper_2 or cfg.get("hsv_upper_2", [180, 255, 255]), dtype=np.uint8)
        self.min_area = min_contour_area if min_contour_area is not None else cfg.get("min_contour_area", 2)
        self.max_area = max_contour_area if max_contour_area is not None else cfg.get("max_contour_area", 120)
        self.min_brightness = min_brightness
        self._kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

    @staticmethod
    def _load_config() -> dict:
        config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
        if config_path.is_file():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    return data.get("laser", {})
            except Exception:
                pass
        return {}

    def detect(self, frame: Optional[np.ndarray]) -> LaserPosition:
        """Extract laser dot position from frame. Returns LaserPosition."""
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0 or len(frame.shape) < 3:
            return LaserPosition(detected=False)

        h, w = frame.shape[:2]
        if h == 0 or w == 0:
            return LaserPosition(detected=False)

        # 1. Convert frame to HSV and Grayscale
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # 2. Dual-band red thresholding
        mask1 = cv2.inRange(hsv, self.lower1, self.upper1)
        mask2 = cv2.inRange(hsv, self.lower2, self.upper2)
        mask = cv2.bitwise_or(mask1, mask2)

        # 3. Morphological open (3x3 kernel) to eliminate single-pixel noise
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)

        # 4. Extract contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best_candidate: Optional[Tuple[int, int, float, float, float]] = None
        max_candidate_brightness = -1

        for c in contours:
            area = cv2.contourArea(c)
            if not (self.min_area <= area <= self.max_area):
                continue

            M = cv2.moments(c)
            if M["m00"] == 0:
                continue

            cx = int(round(M["m10"] / M["m00"]))
            cy = int(round(M["m01"] / M["m00"]))

            if not (0 <= cx < w and 0 <= cy < h):
                continue

            # 5. Peak brightness verification in a 3x3 window around centroid
            y_min, y_max = max(0, cy - 1), min(h, cy + 2)
            x_min, x_max = max(0, cx - 1), min(w, cx + 2)
            peak_gray = int(np.max(gray[y_min:y_max, x_min:x_max]))
            peak_red = int(np.max(frame[y_min:y_max, x_min:x_max, 2]))
            peak_val = max(peak_gray, peak_red)

            if peak_val >= self.min_brightness:
                if peak_val > max_candidate_brightness:
                    max_candidate_brightness = peak_val
                    conf = min(1.0, float(peak_val / 255.0))
                    best_candidate = (cx, cy, cx / float(w), cy / float(h), conf)

        if best_candidate is not None:
            cx, cy, norm_x, norm_y, conf = best_candidate
            return LaserPosition(
                detected=True,
                pixel_coord=(cx, cy),
                normalized_coord=(norm_x, norm_y),
                confidence=conf,
            )

        return LaserPosition(detected=False)
