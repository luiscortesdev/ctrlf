"""Detect and point, end to end: a detection becomes a tracked object, "where are my keys?"
starts the Aimer, and the laser walks onto the keys, stays a few seconds and goes off.

Everything is the app's own code (App, Memory, Tracker, Aimer, Calibration, find_dot,
Pointer) except the hardware: FakeBoard reads Pointer's commands like
firmware/esp32_controller, and Room draws the camera's picture, with the red dot wherever
the head points while the laser is on. Calibration is fitted the way calibrate.py does it,
from a sweep, but the head has been nudged since, so pointing by calibration alone misses
and the closed loop has to correct. Run `pytest`."""

import math
import re

import cv2
import numpy as np
import pytest

from src import app
from src.hardware.serial_client import Pointer
from src.vision import laser_tracker
from src.vision.laser_tracker import PAN_MAX, PAN_MIN, TILT_MAX, TILT_MIN, Aimer, Calibration, RoughMap

W, H = 1280, 720
FPS = 30
KEYS = (850, 470, 910, 510)  # the keys' box in the camera picture, pixels (x1, y1, x2, y2)


class FakeBoard:
    """The serial port as Pointer uses it, read like firmware/esp32_controller: "P<deg>
    T<deg> L0/L1" and "C" on a line, clamped to the limits. The head jumps to the angles
    (the Aimer waits out the real glide anyway). Logs every line and laser change, and
    counts angles outside the limits that reach it."""

    in_waiting = 0

    def __init__(self, clock):
        self.clock = clock
        self.pan, self.tilt, self.laser = 90.0, 125.0, False
        self.lines, self.laser_log, self.out_of_range = [], [], 0

    def write(self, data):
        for line in data.decode().splitlines():
            self.lines.append((self.clock(), line))
            if line.startswith("R"):  # the LED ring
                continue
            laser = self.laser
            for c, v in re.findall(r"([PTL])(-?[\d.]+)", line):
                v = float(v)
                if c == "P":
                    self.out_of_range += not PAN_MIN <= v <= PAN_MAX
                    self.pan = min(max(v, PAN_MIN), PAN_MAX)
                elif c == "T":
                    self.out_of_range += not TILT_MIN <= v <= TILT_MAX
                    self.tilt = min(max(v, TILT_MIN), TILT_MAX)
                else:
                    laser = v != 0
            if line.strip() == "C":
                self.pan, self.tilt, laser = 90.0, (TILT_MIN + TILT_MAX) / 2, False
            if laser != self.laser:
                self.laser_log.append((self.clock(), laser))
            self.laser = laser
        return len(data)

    def read(self, n=1):
        return b""

    def flush(self):
        pass


class Room:
    """The camera: a fixed scene with the keys in it, a little sensor noise, and the laser
    dot where the head points. tilt_center: the tilt that lands mid-picture."""

    def __init__(self, board, tilt_center=125, seed=0):
        self.board, self.tilt_center = board, tilt_center
        rng = np.random.default_rng(seed)
        self.scene = rng.integers(70, 130, (H, W, 3)).astype(np.uint8)
        cv2.rectangle(self.scene, KEYS[:2], KEYS[2:], (40, 45, 50), -1)
        self.noise = [rng.integers(-3, 4, (H, W, 3)).astype(np.int16) for _ in range(4)]
        self.n = 0

    def pixel(self, pan, tilt):
        """Where the dot lands with the head at (pan, tilt): more pan = left, more tilt =
        down (as on the real head), 60 and 45 degrees across the picture, a bit of lens bend."""
        u = 0.5 + (90 - pan) / 60 + 0.03 * ((90 - pan) / 30) ** 2
        v = 0.5 + (tilt - self.tilt_center) / 45
        return u * W, v * H

    def frame(self):
        self.n += 1
        img = np.clip(self.scene + self.noise[self.n % 4], 0, 255).astype(np.uint8)
        if self.board.laser:
            x, y = (int(round(c)) for c in self.pixel(self.board.pan, self.board.tilt))
            if 2 <= x < W - 2 and 2 <= y < H - 2:
                img[y - 2:y + 3, x - 2:x + 3] = (60, 60, 255)  # BGR: red
        return img


def calibrate(room, nudge=(1.0, -0.8)):
    """What scripts/calibrate.py would have fitted: a sweep of angles and where the dot
    landed. The head got nudged by `nudge` degrees since, so it's a little off now."""
    points = []
    for pan in range(70, 111, 4):
        for tilt in range(TILT_MIN, 151, 4):
            x, y = room.pixel(pan + nudge[0], tilt + nudge[1])
            if 0 <= x < W and 0 <= y < H:
                points.append((pan, tilt, x, y))
    return Calibration.fit(points, (W, H))


class Clock:
    def __init__(self):
        self.t = 1_792_000_000.0  # a real date: snapshots print the time

    def __call__(self):
        return self.t


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """(App, board, room, clock, what it said) with a fresh memory in tmp_path."""

    def make(calibrated=True, tilt_center=125):
        clock = Clock()
        board = FakeBoard(clock)
        pointer = Pointer("none")  # opens nothing...
        pointer.ser = board        # ...and talks to the fake board
        room = Room(board, tilt_center)
        mapping = calibrate(room) if calibrated else RoughMap(app.PAN_CENTER, app.PAN_SPAN,
                                                             app.TILT_CENTER, app.TILT_SPAN)
        said = []
        monkeypatch.setattr(app, "say", said.append)  # no speaking out loud in tests
        a = app.App(app.Memory(str(tmp_path / "memory.db"), str(tmp_path / "snapshots")),
                    pointer, Aimer(pointer, mapping, lag=0.1))
        return a, board, room, clock, said

    return make


def see_keys(a, room, clock, box=KEYS, passes=4):
    """Detector passes that find the keys (what BackgroundDetector hands App.update)."""
    for _ in range(passes):
        a.update([("keys", 0.9, box)], room.frame(), clock.t)
        clock.t += 0.4


def run_frames(a, room, clock, secs=30):
    """The main loop's part: every camera frame goes to aimer.update, until the aimer is
    done (or `secs` pass: it must never hang)."""
    end = clock.t + secs
    while a.aimer.state != "idle" and clock.t < end:
        a.aimer.update(room.frame(), clock.t)
        clock.t += 1 / FPS
    assert a.aimer.state == "idle", "the aimer never finished"


def laser_intervals(board):
    """[(on, off)] times the laser was on."""
    out, on = [], None
    for t, state in board.laser_log:
        if state and on is None:
            on = t
        elif not state and on is not None:
            out.append((on, t))
            on = None
    assert on is None, "the laser was left on"
    return out


def moves(board):
    """[(time, pan, tilt, laser or None)] for every P/T command sent."""
    out = []
    for t, line in board.lines:
        m = re.match(r"P(-?[\d.]+) T(-?[\d.]+)(?: L(\d))?$", line)
        if m:
            out.append((t, float(m[1]), float(m[2]), None if m[3] is None else m[3] == "1"))
    return out


def test_where_are_my_keys(rig):
    a, board, room, clock, said = rig()
    see_keys(a, room, clock)
    assert "keys" in a.tracks and a.mem.get("keys")  # tracked and remembered

    started = []
    real_start = a.aimer.start
    a.aimer.start = lambda *args: (started.append(args), real_start(*args))
    a.command("where are my keys?", clock.t)
    assert said == ["Your keys are " + app.describe(a.mem.get("keys"), a.mem) + "."]

    # The target the Aimer gets is the box's center, in the camera's own pixels.
    (target, tol, size, _), = started
    assert target == ((KEYS[0] + KEYS[2]) // 2, (KEYS[1] + KEYS[3]) // 2) and size == (W, H)
    assert tol == max(10, 0.3 * min(KEYS[2] - KEYS[0], KEYS[3] - KEYS[1]))

    # First move: where the calibration says the box's center is, laser off on the way.
    t0, pan, tilt, laser = moves(board)[0]
    want = a.aimer.map.to_angles(target[0] / W, target[1] / H)
    assert (pan, tilt) == pytest.approx(want, abs=0.06) and laser is False

    run_frames(a, room, clock)
    assert a.aimer.message.startswith("on target after")
    tries = int(re.search(r"after (\d+) tries", a.aimer.message)[1])
    assert tries >= 2  # the head was nudged since calibrating: it had to correct

    # The dot really is on the keys, near their center.
    x, y = room.pixel(board.pan, board.tilt)
    assert KEYS[0] <= x <= KEYS[2] and KEYS[1] <= y <= KEYS[3]
    assert math.dist((x, y), target) <= tol + 3

    # Laser: off while moving, short flashes to find the dot, then on the keys 3-5 s, then off.
    assert all(laser is False for *_, laser in moves(board))  # every move: "... L0"
    *flashes, (on, off) = laser_intervals(board)
    assert 3.0 <= off - on <= 5.0
    assert all(b - a_ < 1.0 for a_, b in flashes)
    assert not board.laser and board.lines[-1][1] == "L0"

    # Never past a servo limit: no tilt above the table.
    assert board.out_of_range == 0
    assert all(PAN_MIN <= p <= PAN_MAX and TILT_MIN <= t <= TILT_MAX for _, p, t, _ in moves(board))


def test_not_calibrated_yet(rig):
    """No calibration.json (the first run on a new setup): it points by the rough mapping
    from app.py's settings, laser on for the hold, without chasing the dot."""
    a, board, room, clock, said = rig(calibrated=False)
    see_keys(a, room, clock)
    a.command("where are my keys?", clock.t)
    run_frames(a, room, clock)
    assert a.aimer.message.startswith("not calibrated yet")
    (_, pan, tilt, laser), = moves(board)  # one move, no corrections
    cx, cy = (KEYS[0] + KEYS[2]) // 2, (KEYS[1] + KEYS[3]) // 2
    assert (pan, tilt) == pytest.approx(a.aimer.map.to_angles(cx / W, cy / H), abs=0.06)
    assert laser is True
    (on, off), = laser_intervals(board)
    assert 3.0 <= off - on <= 5.0 and not board.laser


def test_hand_calibration(rig, tmp_path):
    """Calibrated by hand (scripts/calibrate_manual.py, for when the camera can't see the
    dot): one move straight to where the calibration says, laser on for the hold, no
    flashes to look for the dot. The file remembers it was made by hand."""
    a, board, room, clock, said = rig()
    a.aimer.map.manual = True
    path = str(tmp_path / "calibration.json")
    a.aimer.map.save(path)
    assert Calibration.load(path).manual and not calibrate(room).manual
    see_keys(a, room, clock)
    a.command("where are my keys?", clock.t)
    run_frames(a, room, clock)
    assert a.aimer.message == "pointing by the hand calibration"
    (_, pan, tilt, laser), = moves(board)  # one move, laser on, no corrections
    cx, cy = (KEYS[0] + KEYS[2]) // 2, (KEYS[1] + KEYS[3]) // 2
    assert (pan, tilt) == pytest.approx(a.aimer.map.to_angles(cx / W, cy / H), abs=0.06)
    assert laser is True
    (on, off), = laser_intervals(board)
    assert 3.0 <= off - on <= 5.0 and not board.laser


def test_never_aims_above_the_table(rig):
    """Keys seen high up in a picture whose top is above the head's highest tilt: it goes
    as high as TILT_MIN allows, never asks for more, and still switches the laser off."""
    a, board, room, clock, said = rig(tilt_center=100)  # tilt 90 lands at y=200
    box = (600, 40, 660, 80)
    see_keys(a, room, clock, box)
    a.command("where are my keys?", clock.t)
    run_frames(a, room, clock)
    assert a.aimer.message.startswith("as close as the servos allow")
    assert board.tilt == TILT_MIN
    assert board.out_of_range == 0
    assert min(t for _, _, t, _ in moves(board)) == TILT_MIN
    assert not board.laser and laser_intervals(board)


def test_last_seen_by_a_camera_with_another_picture_size(rig):
    """Remembered from a 1920x1080 camera, asked about while a 1280x720 one is on (the app
    can switch cameras): the target is scaled to the picture it's looking at."""
    a, board, room, clock, said = rig()
    a.update([], room.frame(), clock.t)  # a detector pass: the app knows the picture size
    big = tuple(int(v * 1.5) for v in KEYS)
    a.mem.observe("keys", big, np.zeros((1080, 1920, 3), np.uint8), clock.t - 3600)
    clock.t += 0.4
    a.command("where are my keys?", clock.t)
    assert said[-1].startswith("I last saw your keys")
    run_frames(a, room, clock)
    assert a.aimer.message.startswith("on target after"), a.aimer.message
    x, y = room.pixel(board.pan, board.tilt)
    assert KEYS[0] <= x <= KEYS[2] and KEYS[1] <= y <= KEYS[3]
    assert not board.laser


def test_hold_time():
    assert 3.0 <= laser_tracker.Aimer.HOLD_SECS <= 5.0
