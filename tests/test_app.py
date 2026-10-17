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


def test_find_dot():
    rng = np.random.default_rng(0)
    off = rng.integers(40, 80, (480, 640, 3)).astype(np.uint8)
    on = off.copy()
    on[200:206, 300:306, 2] = 255                      # a small red dot (BGR)
    x, y = laser_tracker.find_dot(off, on)
    assert abs(x - 302.5) < 2 and abs(y - 202.5) < 2
    assert laser_tracker.find_dot(off, off.copy()) is None
