"""
src/vision/detector.py - Open-Vocabulary Object Detection via YOLO-World

Implements open-vocabulary detection returning strongly typed DetectedObject
and normalized BoundingBox contracts from src.core.types.
Supports synchronous and background threaded detection to prevent UI/video latency.
Adheres strictly to AGENTS.md Section 2, Section 6.2, and Section 9.
"""

from datetime import datetime
from pathlib import Path
import threading
import time
from typing import Any, List, Optional, Tuple

import cv2
import numpy as np
import yaml

from src.core.types import BoundingBox, DetectedObject


def _to_numpy(val: Any) -> np.ndarray:
    """Helper to convert torch tensors, mock objects, or lists to numpy arrays."""
    if hasattr(val, "cpu"):
        val = val.cpu()
    if hasattr(val, "numpy"):
        return val.numpy()
    if isinstance(val, np.ndarray):
        return val
    return np.array(val)


class ObjectDetector:
    """Open-vocabulary object detector utilizing YOLO-World."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        vocabulary: Optional[List[str]] = None,
        device: Optional[str] = None,
        conf_threshold: float = 0.25,
        model: Optional[Any] = None,
    ):
        self.conf_threshold = conf_threshold
        self.device = device

        # Load vocabulary from settings.yaml if not explicitly provided
        self.vocabulary = vocabulary or self._load_vocabulary()

        # Resolve model path
        if model_path is None:
            root_dir = Path(__file__).resolve().parents[2]
            local_weights = root_dir / "yolov8s-worldv2.pt"
            self.model_path = str(local_weights) if local_weights.is_file() else "yolov8s-worldv2.pt"
        else:
            self.model_path = model_path

        # Support dependency injection for tests
        if model is not None:
            self.model = model
        else:
            from ultralytics import YOLO
            self.model = YOLO(self.model_path)

        self.set_classes(self.vocabulary)

    @staticmethod
    def _load_vocabulary() -> List[str]:
        config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
        if config_path.is_file():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                    vocab = cfg.get("vocabulary")
                    if isinstance(vocab, list) and vocab:
                        return [str(v) for v in vocab]
            except Exception:
                pass
        return [
            "keys", "wallet", "phone", "airpods", "glasses",
            "charger", "water bottle", "laptop", "mouse",
        ]

    def set_classes(self, classes: List[str]) -> None:
        """Update runtime vocabulary classes for open-vocabulary detection."""
        self.vocabulary = list(classes)
        if hasattr(self.model, "set_classes"):
            try:
                self.model.set_classes(self.vocabulary)
            except Exception:
                pass

    def detect(
        self,
        frame: Optional[np.ndarray],
        conf_threshold: Optional[float] = None,
    ) -> List[DetectedObject]:
        """
        Execute detection on a single frame.
        Returns a list of DetectedObject instances with normalized BoundingBoxes.
        """
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return []

        h, w = frame.shape[:2]
        if h == 0 or w == 0:
            return []

        threshold = conf_threshold if conf_threshold is not None else self.conf_threshold

        try:
            kwargs = {"conf": threshold, "verbose": False}
            if self.device:
                kwargs["device"] = self.device

            results = self.model.predict(frame, **kwargs)
        except Exception:
            return []

        if not results:
            return []

        r = results[0]
        detected_objects: List[DetectedObject] = []

        if not hasattr(r, "boxes") or r.boxes is None:
            return []

        boxes = r.boxes
        cls_indices = _to_numpy(boxes.cls) if hasattr(boxes, "cls") and boxes.cls is not None else np.array([])
        confidences = _to_numpy(boxes.conf) if hasattr(boxes, "conf") and boxes.conf is not None else np.array([])
        coords = _to_numpy(boxes.xyxy) if hasattr(boxes, "xyxy") and boxes.xyxy is not None else np.array([])

        if len(coords) == 0:
            return []

        names_map = getattr(self.model, "names", {})
        now = datetime.utcnow()

        for i in range(len(coords)):
            score = float(confidences[i]) if i < len(confidences) else 0.0
            if score < threshold:
                continue

            cls_idx = int(cls_indices[i]) if i < len(cls_indices) else 0
            if isinstance(names_map, dict) and cls_idx in names_map:
                label = str(names_map[cls_idx])
            elif isinstance(names_map, list) and 0 <= cls_idx < len(names_map):
                label = str(names_map[cls_idx])
            elif 0 <= cls_idx < len(self.vocabulary):
                label = self.vocabulary[cls_idx]
            else:
                label = f"class_{cls_idx}"

            x1, y1, x2, y2 = coords[i]

            # Normalize bounding box coordinates to [0.0, 1.0]
            norm_x1 = max(0.0, min(1.0, float(x1) / w))
            norm_y1 = max(0.0, min(1.0, float(y1) / h))
            norm_x2 = max(0.0, min(1.0, float(x2) / w))
            norm_y2 = max(0.0, min(1.0, float(y2) / h))

            bbox = BoundingBox(
                x1=round(norm_x1, 4),
                y1=round(norm_y1, 4),
                x2=round(norm_x2, 4),
                y2=round(norm_y2, 4),
            )

            detected_objects.append(
                DetectedObject(
                    label=label,
                    confidence=round(score, 4),
                    bbox=bbox,
                    timestamp=now,
                )
            )

        return detected_objects


class BackgroundDetector:
    """
    Decoupled background thread runner for ObjectDetector.
    Ensures inference runs asynchronously without stalling camera or UI threads.
    """

    def __init__(self, detector: ObjectDetector, conf_threshold: Optional[float] = None):
        self.detector = detector
        self.conf_threshold = conf_threshold
        self._cv = threading.Condition()
        self._pending_frame: Optional[np.ndarray] = None
        self._result: Tuple[int, Optional[np.ndarray], List[DetectedObject], float] = (0, None, [], 0.0)
        self._running = True
        self.secs_per_frame: float = 0.0
        self.error: Optional[Exception] = None

        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()

    def submit(self, frame: np.ndarray) -> None:
        """Submit a new frame for detection. Automatically drops any unprocessed older frame."""
        with self._cv:
            self._pending_frame = frame
            self._cv.notify()

    def result(self) -> Tuple[int, Optional[np.ndarray], List[DetectedObject], float]:
        """
        Returns (seq, frame, list_of_detected_objects, timestamp).
        seq increments per completed frame.
        """
        with self._cv:
            return self._result

    def _worker_loop(self) -> None:
        seq = 0
        while self._running:
            with self._cv:
                while self._running and self._pending_frame is None:
                    self._cv.wait()
                if not self._running:
                    break
                frame = self._pending_frame
                self._pending_frame = None

            t0 = time.time()
            try:
                detections = self.detector.detect(frame, conf_threshold=self.conf_threshold)
                self.error = None
            except Exception as e:
                detections = []
                self.error = e

            elapsed = time.time() - t0
            self.secs_per_frame = elapsed

            seq += 1
            with self._cv:
                self._result = (seq, frame, detections, time.time())

    def stop(self) -> None:
        with self._cv:
            self._running = False
            self._cv.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
