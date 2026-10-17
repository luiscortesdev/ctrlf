"""How fast something new in view reaches the dashboard.

Replays detector passes through the real App, Tracker, Memory and WebBridge (no camera,
models or ESP32). A pass is PASS_SECS apart, like the detector at ~2.5 passes a second.
"""

import http.client
import json
import os
import time

import numpy as np
import pytest

from src import app as A
from src.web import WebBridge

PASS_SECS = 0.4
FRAME = np.zeros((720, 1280, 3), np.uint8)
HELPERS = {"describe": A.describe, "surfaces": A.SURFACES, "objects": A.OBJECTS, "taught": A.TAUGHT}
KEYS = (600, 400, 640, 430)  # small: only found when a tile or focus spot zooms in on it


class Detector:
    """Stand-in for BackgroundDetector: seq goes up by one per finished pass."""
    secs_per_frame = PASS_SECS

    def __init__(self):
        self.seq = 0

    def result(self):
        return self.seq, FRAME, [], 0.0


def make(tmp_path):
    mem = A.Memory(str(tmp_path / "memory.db"), str(tmp_path / "snapshots"))
    app = A.App(mem, type("Pointer", (), {"connected": False})())
    web = WebBridge(str(tmp_path), str(tmp_path / "snapshots"), str(tmp_path))
    return app, web


def listed(web):
    return web.state is not None and any(it["name"] == "keys" for it in web.state["items"])


def replay(app, web, sightings, passes=16):
    """sightings(k, focus) -> box or None for pass k. Returns (pass the video first boxes
    it, pass the dashboard first lists it)."""
    detector, video, dashboard = Detector(), None, None
    focus = []
    for k in range(passes):
        now = k * PASS_SECS
        box = sightings(k, focus)
        app.update([("keys", 0.4, box)] if box else [], FRAME, now)
        focus = app.focus_spots((FRAME.shape[1], FRAME.shape[0]), now)
        detector.seq += 1
        web.tick(app, FRAME, None, None, detector, [], now + 0.03, HELPERS)  # next frame
        if video is None and any(t.label == "keys" for t in app.tracker.shown()):
            video = k
        if dashboard is None and listed(web):
            dashboard = k
    return video, dashboard


def covers(region, box):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


# ------------------------------------------------------------------ the dashboard
def test_dashboard_lists_it_as_soon_as_the_video_boxes_it(tmp_path):
    app, web = make(tmp_path)
    video, dashboard = replay(app, web, lambda k, focus: KEYS)
    assert video == 1                 # two sightings: the tracker's confirmation
    assert dashboard == video         # not 2 passes later, once it's in memory


def test_dashboard_lists_something_being_carried(tmp_path):
    """Held in a hand it never sits still, so it never gets remembered, but it's in view."""
    app, web = make(tmp_path)
    # 45 px a pass: more than the stillness wiggle room (38 px), and the tracker follows it.
    video, dashboard = replay(app, web, lambda k, focus: (200 + 45 * k, 300, 300 + 45 * k, 360))
    assert video == 1
    assert dashboard == video
    item = next(it for it in web.state["items"] if it["name"] == "keys")
    assert item["visible"] is True and item["snapshot"] is None and item["bbox"]


def test_a_remembered_thing_keeps_its_snapshot(tmp_path):
    app, web = make(tmp_path)
    replay(app, web, lambda k, focus: KEYS, passes=6)
    item = next(it for it in web.state["items"] if it["name"] == "keys")
    assert item["snapshot"] and item["visible"] is True


# ------------------------------------------------------------------ the detector
def test_a_new_sighting_is_looked_at_again_next_pass(tmp_path):
    """Found once in a zoomed tile, it's zoomed into again the very next pass instead of
    waiting for that tile to come around again (every 4th pass)."""
    app, _ = make(tmp_path)
    app.update([("keys", 0.4, KEYS)], FRAME, 0.0)
    assert not app.tracker.shown()    # one sighting: not shown yet
    spots = app.focus_spots((1280, 720), 0.0)
    assert spots and covers(spots[0], KEYS)


def test_a_new_sighting_comes_before_old_spots(tmp_path):
    app, _ = make(tmp_path)
    app.mem.observe("wallet", (100, 100, 140, 130), FRAME, 1000.0)  # remembered, now gone
    assert len(app.focus_spots((1280, 720), 1100.0)) == 1
    app.update([("keys", 0.4, KEYS)], FRAME, 1100.0)
    spots = app.focus_spots((1280, 720), 1100.0)
    assert all(covers(s, KEYS) for s in spots)


def test_something_only_a_zoom_finds_shows_up_in_two_passes(tmp_path):
    app, web = make(tmp_path)

    def sightings(k, focus):  # its tile comes around every 4th pass, or a focus spot
        return KEYS if k % 4 == 0 or any(covers(f, KEYS) for f in focus) else None

    video, dashboard = replay(app, web, sightings)
    assert video == 1 and dashboard == 1  # was pass 4 and pass 12


# ------------------------------------------------------------------ getting it to the browser
def test_state_is_rebuilt_right_after_a_detector_pass(tmp_path):
    app, web = make(tmp_path)
    detector = Detector()
    web.tick(app, FRAME, None, None, detector, [], 10.0, HELPERS)
    app.update([("keys", 0.4, KEYS)], FRAME, 10.0)
    app.update([("keys", 0.4, KEYS)], FRAME, 10.02)
    detector.seq += 1
    web.tick(app, FRAME, None, None, detector, [], 10.03, HELPERS)  # well within STATE_EVERY
    assert listed(web)


def test_state_is_rebuilt_when_it_says_something(tmp_path):
    app, web = make(tmp_path)
    detector, said = Detector(), []
    web.tick(app, FRAME, None, None, detector, said, 10.0, HELPERS)
    said.append((10.01, "Your keys are on the desk."))
    web.tick(app, FRAME, None, None, detector, said, 10.03, HELPERS)
    assert web.state["said"][-1]["text"] == "Your keys are on the desk."


@pytest.fixture
def server(tmp_path):
    app, web = make(tmp_path)
    assert web.start(port=0)
    yield app, web
    web.stop()


def read_event(r):
    """The next server-sent event's data (skipping keep-alive comments)."""
    while True:
        line = r.fp.readline()
        if line.startswith(b"data: "):
            assert r.fp.readline() == b"\n"
            return json.loads(line[6:])
        assert line in (b"\n", b": keep-alive\n", b"retry: 1000\n"), line


def test_events_push_new_state_without_polling(server):
    app, web = server
    detector = Detector()
    web.tick(app, FRAME, None, None, detector, [], time.time(), HELPERS)
    conn = http.client.HTTPConnection("127.0.0.1", web.port, timeout=5)
    try:
        conn.request("GET", "/api/events")
        r = conn.getresponse()
        assert r.status == 200
        assert r.getheader("Content-Type") == "text/event-stream"
        assert read_event(r)["items"] == []  # the current state straight away

        app.update([("keys", 0.4, KEYS)], FRAME, time.time())
        app.update([("keys", 0.4, KEYS)], FRAME, time.time())
        detector.seq += 1
        sent = time.perf_counter()
        web.tick(app, FRAME, None, None, detector, [], time.time(), HELPERS)
        state = read_event(r)
        assert time.perf_counter() - sent < 0.1
        assert [it["name"] for it in state["items"]] == ["keys"]
    finally:
        conn.close()


def test_events_are_only_sent_when_something_changed(server):
    app, web = server
    detector = Detector()
    web.tick(app, FRAME, None, None, detector, [], 50.0, HELPERS)
    first = web.state_version
    web.state_at = 0.0
    web.tick(app, FRAME, None, None, detector, [], 50.0, HELPERS)  # same second, same state
    assert web.state_version == first


# ------------------------------------------------------------------ "In view" vs "Out of sight"
def badges(tmp_path, pass_secs, here, passes):
    """Each pass's result reaches the main loop a pass after it started (the detector runs
    back to back). Returns the dashboard's visible flag for keys after each pass."""
    app, web = make(tmp_path)
    detector, out = Detector(), []
    for k in range(passes):
        started = 1000 + k * pass_secs
        app.update([("keys", 0.4, KEYS)] if here(k) else [], FRAME, started)
        detector.seq += 1
        web.tick(app, FRAME, None, None, detector, [], started + pass_secs, HELPERS)
        item = next((it for it in web.state["items"] if it["name"] == "keys"), None)
        out.append(item and item["visible"])
    return out


@pytest.mark.parametrize("pass_secs", [0.4, 3.4])  # the Mac; OWLv2 on a laptop CPU
def test_in_view_while_the_detector_keeps_seeing_it(tmp_path, pass_secs):
    seen = badges(tmp_path, pass_secs, lambda k: True, passes=8)
    assert seen[1:] == [True] * 7   # from the pass the video boxes it


def test_in_view_through_a_missed_pass_on_a_slow_detector(tmp_path):
    seen = badges(tmp_path, 3.4, lambda k: k != 4, passes=8)
    assert seen[1:] == [True] * 7


@pytest.mark.parametrize("pass_secs", [0.4, 3.4])
def test_out_of_sight_once_it_is_gone(tmp_path, pass_secs):
    seen = badges(tmp_path, pass_secs, lambda k: k < 4, passes=20)
    assert seen[3] is True
    assert seen[-1] is False        # still listed (remembered), but out of sight
