"""Tests for the parts that don't need a camera, the models or the ESP32: run `pytest`."""

import numpy as np

from src import app
from src.vision import laser_tracker, tracker


def test_parse_name():
    assert app.parse_name("this is my retainer case") == "retainer case"
    assert app.parse_name("these are my keys") == "keys"
    assert app.parse_name("it's called bob") == "bob"
    assert app.parse_name("learn this") is None


def test_teach_request():
    assert app.teach_request("learn this") == (True, None)
    assert app.teach_request("i learned this") == (True, None)  # how speech-to-text heard it
    assert app.teach_request("learn my keys") == (True, "keys")
    assert app.teach_request("where are my keys")[0] is False


def test_names():
    assert app.label_for_name("airpods") == "earbuds case"
    assert app.find_label("where did i leave my air pods") == "earbuds case"
    app.TAUGHT["bhar"] = {"words": ["bhar"], "pictures": [[0.0]]}
    try:
        assert app.taught_name("bar") == "bhar"      # misheard
        assert app.taught_name("b h a r") == "bhar"  # spelled out
        assert app.taught_name("duck") is None
    finally:
        del app.TAUGHT["bhar"]


def test_tracker_needs_two_sightings_and_keeps_things_steady():
    tr = tracker.Tracker(lambda label: 0.3)
    tr.update([("keys", 0.4, (100, 100, 140, 130))], 0.0)
    assert not tr.shown()                              # one sighting could be a false alarm
    tr.update([("keys", 0.4, (102, 101, 141, 131))], 0.4)
    assert [t.label for t in tr.shown()] == ["keys"]
    tr.update([("keys", 0.26, (101, 100, 140, 130))], 0.8)  # dips under 0.3 but above 0.24
    assert tr.seen_now()["keys"][1][0] in range(99, 104)
    for k in range(tracker.MISS_PASSES):               # unseen for a while: still shown
        tr.update([], 1.2 + 0.4 * k)
    assert tr.shown()
    tr.update([], 9.0)                                 # gone
    assert not tr.shown()



def test_tracker_follows_something_being_carried():
    """Smoothing lags a moving box. If new sightings were only matched to the smoothed box,
    new tracks would keep starting on it and seen_now() (and the dashboard) would lose it."""
    tr = tracker.Tracker(lambda label: 0.3)
    for k in range(16):
        tr.update([("keys", 0.4, (300 + 15 * k, 400, 340 + 15 * k, 430))], 0.4 * k)
        if k >= 1:
            assert "keys" in tr.seen_now(), f"lost on pass {k}"
    assert len(tr.tracks) == 1


def test_tracker_sticks_with_one_of_two_look_alikes_in_different_tiles():
    """Two ID badges, each only found when its own tile comes around: the main one must
    not flip between them every time the other is the one seen."""
    tr = tracker.Tracker(lambda label: 0.3)
    mains = set()
    for k in range(24):
        if k % 4 == 0:
            tr.update([("id card", 0.42, (100, 100, 140, 130))], 0.4 * k)
        elif k % 4 == 2:
            tr.update([("id card", 0.41, (900, 500, 940, 530))], 0.4 * k)
        else:
            tr.update([], 0.4 * k)
        if "id card" in tr.main:
            mains.add(tr.main["id card"].int_box()[0])
    assert mains == {100}

def test_find_dot():
    rng = np.random.default_rng(0)
    off = rng.integers(40, 80, (480, 640, 3)).astype(np.uint8)
    on = off.copy()
    on[200:206, 300:306, 2] = 255                      # a small red dot (BGR)
    x, y = laser_tracker.find_dot(off, on)
    assert abs(x - 302.5) < 2 and abs(y - 202.5) < 2
    assert laser_tracker.find_dot(off, off.copy()) is None


class _Aimer:
    def __init__(self):
        self.started = []

    def start(self, target, tol, size, now):
        self.started.append((target, tol, size))


class _Mem:
    def get(self, label):
        if label == "earbuds case":
            return {"label": label, "x1": 100, "y1": 100, "x2": 140, "y2": 120, "cx": 120, "cy": 110,
                    "frame_w": 640, "frame_h": 360, "last_seen": 0.0, "snapshot": None}


def test_point_from_the_website():
    pointer = type("Pointer", (), {"connected": True, "ring": lambda self, *a: None})()
    a = app.App(_Mem(), pointer, _Aimer())
    a.point("airpods", 100.0)                          # its other name
    assert a.highlight[0] == "earbuds case"
    assert a.aimer.started == [((120, 110), 10, (640, 360))]

    a.aim_at((320.0, 180.0), (640, 360), 101.0)        # a spot in the view
    assert a.highlight is None
    assert a.aimer.started[-1] == ((320.0, 180.0), 10, (640, 360))

    pointer.connected = False
    a.aim_at((10.0, 10.0), (640, 360), 102.0)
    assert len(a.aimer.started) == 2                   # no laser: nothing to aim
