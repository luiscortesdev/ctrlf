"""scripts/calibrate_manual.py: steering the laser by keys and clicking where the dot is
gives a calibration that points where you click. The person is simulated: they click where
the dot really lands (Room.pixel), a few pixels off."""

import importlib.util
import os

import numpy as np
import pytest

from src.hardware.serial_client import Pointer
from src.vision.laser_tracker import Calibration
from tests.test_detect_and_point import H, W, Clock, FakeBoard, Room

spec = importlib.util.spec_from_file_location(
    "calibrate_manual", os.path.join(os.path.dirname(__file__), "..", "scripts", "calibrate_manual.py"))
calibrate_manual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibrate_manual)


@pytest.fixture
def session(tmp_path):
    board = FakeBoard(Clock())
    pointer = Pointer("none")
    pointer.ser = board
    s = calibrate_manual.Session(pointer, (W, H), str(tmp_path / "calibration.json"), 115)
    s.move(*calibrate_manual.START)
    return s, board, Room(board)


def test_clicks_make_a_calibration_that_points_where_you_click(session):
    s, board, room = session
    rng = np.random.default_rng(1)
    s.click(600, 300)
    assert s.points == []  # laser off: nothing to click on yet
    s.set_laser(True)
    for pan in (74, 86, 98, 110):
        for tilt in (117, 127, 137):
            s.move(pan, tilt)
            x, y = room.pixel(board.pan, board.tilt)
            s.click(x + rng.normal(0, 2), y + rng.normal(0, 2))
    assert len(s.points) == 12
    s.fit()
    saved = Calibration.load(s.path)
    assert saved.manual and len(saved.points) >= 10 and saved.error_px < 10

    s.testing = True
    for target in ((500, 250), (800, 400), (650, 550)):
        s.click(*target)
        assert board.laser
        x, y = room.pixel(board.pan, board.tilt)
        assert np.hypot(x - target[0], y - target[1]) < 15


def test_never_tilts_shallower_than_the_floor(session):
    s, board, room = session
    for _ in range(30):
        s.nudge(0, -calibrate_manual.STEP)  # W, again and again
    assert board.tilt == 115 == s.pointer.tilt


def test_fit_needs_eight_clicks(session, tmp_path):
    s, board, room = session
    s.set_laser(True)
    for i in range(5):
        s.move(80 + 5 * i, 125)
        s.click(*room.pixel(board.pan, board.tilt))
    s.fit()
    assert s.calib is None and not os.path.exists(s.path)
    s.undo()
    assert len(s.points) == 4
