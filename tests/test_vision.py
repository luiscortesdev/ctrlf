"""
tests/test_vision.py - Vision Layer Audit and Regression Test Suite

Verifies:
1. LaserTracker dual-band HSV extraction and peak brightness verification.
2. BoundingBox geometry, centering, and containment logic.
3. ObjectDetector interface compliance returning List[DetectedObject] and normalized BoundingBox.
4. BackgroundDetector asynchronous worker decoupling.
5. Camera non-blocking threaded capture.
"""

import time
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from src.core.types import BoundingBox, DetectedObject, LaserPosition
from src.vision.camera import Camera
from src.vision.detector import BackgroundDetector, ObjectDetector
from src.vision.laser_tracker import LaserTracker


def test_laser_tracker():
    """Places a simulated red dot on a blank frame;
    asserts LaserPosition.detected == True and returns correct coordinates."""
    tracker = LaserTracker(min_brightness=240)

    # 1. Blank dark frame -> should not detect
    blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    res_blank = tracker.detect(blank_frame)
    assert isinstance(res_blank, LaserPosition)
    assert res_blank.detected is False
    assert res_blank.pixel_coord is None

    # 2. Simulated laser dot at (320, 240)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.circle(frame, (320, 240), 4, (0, 0, 255), -1)

    res = tracker.detect(frame)
    assert isinstance(res, LaserPosition)
    assert res.detected is True
    assert res.pixel_coord is not None
    cx, cy = res.pixel_coord
    # Assert coordinates within 2 pixels of true simulated center
    assert abs(cx - 320) <= 2
    assert abs(cy - 240) <= 2
    assert res.normalized_coord is not None
    nx, ny = res.normalized_coord
    assert abs(nx - (320 / 640.0)) <= 0.01
    assert abs(ny - (240 / 480.0)) <= 0.01
    assert res.confidence > 0.0

    # 3. Dim red object without high brightness (e.g. red book/cup) -> must be rejected
    dim_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.circle(dim_frame, (100, 100), 4, (0, 0, 160), -1)  # Dim red: V=160 < 200, brightness < 240
    res_dim = tracker.detect(dim_frame)
    assert res_dim.detected is False


def test_bounding_box_containment():
    """Asserts BoundingBox.contains_point() properly evaluates target overlap."""
    bbox = BoundingBox(x1=0.2, y1=0.2, x2=0.6, y2=0.6)

    # Test center point
    assert bbox.center == (0.4, 0.4)
    assert bbox.contains_point((0.4, 0.4)) is True

    # Test points inside box
    assert bbox.contains_point((0.25, 0.25)) is True
    assert bbox.contains_point((0.55, 0.55)) is True

    # Test points outside box
    assert bbox.contains_point((0.15, 0.4)) is False  # Left
    assert bbox.contains_point((0.65, 0.4)) is False  # Right
    assert bbox.contains_point((0.4, 0.15)) is False  # Above
    assert bbox.contains_point((0.4, 0.65)) is False  # Below

    # Test margin tolerance
    assert bbox.contains_point((0.22, 0.22), margin=0.0) is True
    assert bbox.contains_point((0.22, 0.22), margin=0.05) is False


def test_detector_interface():
    """Verifies detector.detect(frame) returns a List[DetectedObject] adhering to types.py."""
    mock_model = MagicMock()
    mock_boxes = MagicMock()
    mock_boxes.cls = np.array([0, 1])
    mock_boxes.conf = np.array([0.89, 0.72])
    # Coordinates in pixels for a 640x480 frame
    mock_boxes.xyxy = np.array([
        [64.0, 48.0, 192.0, 144.0],   # norm: [0.1, 0.1, 0.3, 0.3]
        [320.0, 240.0, 480.0, 360.0], # norm: [0.5, 0.5, 0.75, 0.75]
    ])
    mock_result = MagicMock()
    mock_result.boxes = mock_boxes
    mock_model.predict.return_value = [mock_result]
    mock_model.names = {0: "keys", 1: "laptop"}

    detector = ObjectDetector(
        vocabulary=["keys", "laptop"],
        model=mock_model,
        conf_threshold=0.25,
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    detections = detector.detect(frame)

    assert isinstance(detections, list)
    assert len(detections) == 2

    d1, d2 = detections
    assert isinstance(d1, DetectedObject)
    assert d1.label == "keys"
    assert d1.confidence == 0.89
    assert isinstance(d1.bbox, BoundingBox)
    assert pytest.approx(d1.bbox.x1, 0.01) == 0.1
    assert pytest.approx(d1.bbox.y1, 0.01) == 0.1
    assert pytest.approx(d1.bbox.x2, 0.01) == 0.3
    assert pytest.approx(d1.bbox.y2, 0.01) == 0.3

    assert isinstance(d2, DetectedObject)
    assert d2.label == "laptop"
    assert d2.confidence == 0.72
    assert pytest.approx(d2.bbox.x1, 0.01) == 0.5
    assert pytest.approx(d2.bbox.y1, 0.01) == 0.5


def test_background_detector():
    """Verifies BackgroundDetector executes detection asynchronously without blocking."""
    mock_model = MagicMock()
    mock_boxes = MagicMock()
    mock_boxes.cls = np.array([0])
    mock_boxes.conf = np.array([0.95])
    mock_boxes.xyxy = np.array([[100, 100, 200, 200]])
    mock_result = MagicMock()
    mock_result.boxes = mock_boxes
    mock_model.predict.return_value = [mock_result]
    mock_model.names = {0: "wallet"}

    detector = ObjectDetector(vocabulary=["wallet"], model=mock_model)
    bg_detector = BackgroundDetector(detector)

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    bg_detector.submit(frame)

    # Wait briefly for worker thread to process
    time.sleep(0.05)
    seq, res_frame, detections, ts = bg_detector.result()

    assert seq >= 1
    assert res_frame is not None
    assert len(detections) == 1
    assert detections[0].label == "wallet"
    bg_detector.stop()


def test_camera_threaded():
    """Verifies Camera runs grab() in background and returns latest frame."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    test_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    test_frame[10, 10] = [255, 255, 255]
    mock_cap.grab.return_value = True
    mock_cap.retrieve.return_value = (True, test_frame)

    cam = Camera(auto_start=False, cap=mock_cap)
    cam.start()

    time.sleep(0.05)
    ret, frame = cam.read()

    assert ret is True
    assert frame is not None
    assert frame.shape == (720, 1280, 3)
    cam.stop()
    assert cam._running is False
