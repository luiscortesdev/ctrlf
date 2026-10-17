"""
src/vision/camera.py - Low-Latency Dedicated Thread Camera Capture

Continuously drops stale buffered frames using a grab() loop so retrieve()
always delivers the latest camera frame without latency.
Adheres to AGENTS.md Section 2 (Concurrency Model) and Section 12 (Lag Mitigation).
"""

import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import yaml


class Camera:
    """Threaded camera capture that eliminates OpenCV's internal 5-frame buffer lag."""

    def __init__(
        self,
        device_index: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[int] = None,
        auto_start: bool = True,
        cap: Optional[cv2.VideoCapture] = None,
    ):
        # Load defaults from config/settings.yaml if available
        cfg = self._load_config()
        self.device_index = device_index if device_index is not None else cfg.get("device_index", 0)
        self.width = width if width is not None else cfg.get("width", 1280)
        self.height = height if height is not None else cfg.get("height", 720)
        self.fps = fps if fps is not None else cfg.get("fps", 30)

        self._cap = cap
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._has_new_frame = False
        self._read_success = False

        if auto_start:
            self.start()

    @staticmethod
    def _load_config() -> dict:
        config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
        if config_path.is_file():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    return data.get("camera", {})
            except Exception:
                pass
        return {}

    def start(self) -> "Camera":
        with self._lock:
            if self._running:
                return self

            if self._cap is None:
                self._cap = cv2.VideoCapture(self.device_index)
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                self._cap.set(cv2.CAP_PROP_FPS, self.fps)

            self._running = True
            self._thread = threading.Thread(target=self._capture_worker, daemon=True)
            self._thread.start()
        return self

    def _capture_worker(self) -> None:
        """Dedicated thread executing grab() continuously to discard stale queued frames."""
        while self._running:
            if self._cap is None or not self._cap.isOpened():
                time.sleep(0.01)
                continue

            # grab() quickly advances the hardware buffer without decoding
            grabbed = self._cap.grab()
            if not grabbed:
                time.sleep(0.005)
                continue

            # Decode only when requested or keep the latest retrieved frame ready
            with self._lock:
                ret, frame = self._cap.retrieve()
                if ret and frame is not None:
                    self._latest_frame = frame
                    self._read_success = True
                    self._has_new_frame = True
                else:
                    self._read_success = False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Retrieve the most recent camera frame in a thread-safe manner."""
        with self._lock:
            if self._latest_frame is None:
                return False, None
            # Return copy to prevent race conditions during downstream processing
            return self._read_success, self._latest_frame.copy()

    def is_opened(self) -> bool:
        with self._lock:
            return self._cap is not None and self._cap.isOpened()

    def stop(self) -> None:
        with self._lock:
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None

        with self._lock:
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            self._latest_frame = None

    def release(self) -> None:
        self.stop()

    def __enter__(self) -> "Camera":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
