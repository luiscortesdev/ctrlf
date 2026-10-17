"""Tests for the website's API (src/web.py), against the real server on a free port.

The app, its memory and the camera are stand-ins, so this needs no camera, models or
ESP32. tick() is called the way the app's main loop calls it.
"""

import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

from src.web import WebBridge

NOW = time.time()


# ------------------------------------------------------------------ stand-ins
class FakeMemory:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class FakeApp:
    def __init__(self, rows):
        self.mem = FakeMemory(rows)
        self.visible = {"keys"}
        self.highlight = None
        self.teach = None
        self.aimer = type("Aimer", (), {"state": "idle"})()
        self.pointer = type("Pointer", (), {"connected": True})()

    def locate(self, label, now):
        row = next((r for r in self.mem.rows if r["label"] == label), None)
        return row, label in self.visible


def row(label, box, snapshot=None, last_seen=NOW):
    x1, y1, x2, y2 = box
    return {"label": label, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "frame_w": 640, "frame_h": 360,
            "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2, "last_seen": last_seen,
            "moved_at": last_seen, "snapshot": snapshot}


HELPERS = {
    "describe": lambda obj, mem: "on the desk",
    "surfaces": {"desk"},
    "objects": {"keys": [], "wallet": [], "earbuds case": ["airpods"], "desk": []},
    "taught": {},
}
VOICE = type("Voice", (), {"state": "idle"})()
DETECTOR = type("Detector", (), {"secs_per_frame": 0.4})()


def frame():
    return np.random.default_rng(0).integers(0, 255, (360, 640, 3), dtype=np.uint8)


@pytest.fixture
def server(tmp_path):
    os.makedirs(tmp_path / "web")
    (tmp_path / "web" / "index.html").write_text("<h1>hi</h1>")
    snap_dir = tmp_path / "snapshots"
    os.makedirs(snap_dir)
    snap = snap_dir / "keys_1760000000000.jpg"
    snap.write_bytes(b"\xff\xd8 a jpeg \xff\xd9")
    app = FakeApp([
        row("keys", (100, 200, 140, 230), str(snap)),
        row("earbuds case", (300, 100, 330, 120), None, NOW - 600),
        row("desk", (0, 150, 640, 360)),  # furniture: not listed
    ])
    bridge = WebBridge(str(tmp_path), str(snap_dir), str(tmp_path / "taught"))
    assert bridge.start(port=0)
    bridge.test_app = app
    bridge.test_snapshot = snap.read_bytes()
    yield bridge
    bridge.stop()


def tick(bridge, now=None):
    """One pass of the app's main loop, as far as the website is concerned."""
    bridge.state_at = 0.0  # build the state now, not after STATE_EVERY
    bridge.tick(bridge.test_app, frame(), None, VOICE, DETECTOR, [], now or time.time(), HELPERS)


def get(bridge, path):
    """(status, content type, body)"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{bridge.port}{path}", timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def post(bridge, path, data):
    """(status, JSON reply). data: a dict, or raw bytes to send as they are."""
    body = data if isinstance(data, bytes) else json.dumps(data).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{bridge.port}{path}", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ------------------------------------------------------------------ read
def test_get_status(server):
    status, _, body = get(server, "/api/status")
    assert status == 503  # the main loop hasn't built anything yet
    assert json.loads(body)["ok"] is False

    tick(server)
    status, ctype, body = get(server, "/api/status")
    assert status == 200 and ctype.startswith("application/json")
    s = json.loads(body)
    assert s["state"] == "IDLE"
    assert s["laser_connected"] is True
    assert s["aim"] == "idle" and s["voice"] == "idle" and s["detector_fps"] == 2.5

    server.test_app.highlight = ("keys", time.time() + 10)  # just asked, laser moving onto them
    server.test_app.aimer.state = "on"
    tick(server)
    s = json.loads(get(server, "/api/status")[2])
    assert s["state"] == "TRACKING" and s["target"] == "keys"
    assert s["target_bbox"] == [0.1562, 0.5556, 0.2188, 0.6389]


def test_list_items(server):
    tick(server)
    status, ctype, body = get(server, "/api/items")
    assert status == 200 and ctype.startswith("application/json")
    items = {it["name"]: it for it in json.loads(body)["items"]}
    assert set(items) == {"keys", "earbuds case"}  # not the desk

    keys = items["keys"]
    assert keys["visible"] is True
    assert keys["where"] == "on the desk"
    assert keys["bbox"] == [0.1562, 0.5556, 0.2188, 0.6389]
    assert keys["snapshot_url"] == "/api/items/keys/snapshot"
    time.strptime(keys["last_seen"], "%Y-%m-%dT%H:%M:%S")

    earbuds = items["earbuds case"]
    assert earbuds["visible"] is False
    assert earbuds["snapshot_url"] is None


def test_item_snapshot(server):
    tick(server)
    status, ctype, body = get(server, "/api/items/keys/snapshot")
    assert status == 200 and ctype == "image/jpeg"
    assert body == server.test_snapshot
    assert get(server, "/api/items/earbuds%20case/snapshot")[0] == 404  # no snapshot yet
    assert get(server, "/api/items/duck/snapshot")[0] == 404
    assert get(server, "/api/items/..%2F..%2Fweb%2Findex.html/snapshot")[0] == 404


def test_video_feed_headers(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        conn.request("GET", "/api/video_feed")
        r = conn.getresponse()
        assert r.status == 200
        assert r.getheader("Content-Type") == "multipart/x-mixed-replace; boundary=frame"
        for _ in range(50):  # the stream only encodes frames while someone's watching
            if server.room.viewers:
                break
            time.sleep(0.02)
        tick(server)
        part = r.read(64)
        assert part.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n")
    finally:
        conn.close()


def test_existing_routes_still_work(server):
    tick(server)
    state = json.loads(get(server, "/api/state")[2])
    assert {it["name"] for it in state["items"]} == {"keys", "earbuds case"}
    assert get(server, "/")[2] == b"<h1>hi</h1>"
    assert post(server, "/api/command", {"text": "list"}) == (200, {"ok": True})
    assert list(server.take_actions()) == [("command", "list")]


# ------------------------------------------------------------------ actions
def test_query_endpoint(server):
    status, reply = post(server, "/api/query", {"query": "  where are my keys? "})
    assert status == 200
    assert reply == {"ok": True, "action": "query", "query": "where are my keys?"}
    assert list(server.take_actions()) == [("command", "where are my keys?")]

    for bad in ({}, {"query": ""}, {"query": "   "}, {"query": 42}, {"text": "where are my keys"},
                {"query": "x" * 201}, b"not json", b"[1, 2]"):
        status, reply = post(server, "/api/query", bad)
        assert status == 400 and reply["ok"] is False, bad
    assert list(server.take_actions()) == []


def test_teach_endpoint(server):
    status, reply = post(server, "/api/teach", {"label": "  Retainer Case "})
    assert status == 200
    assert reply == {"ok": True, "action": "teach", "label": "retainer case"}
    assert list(server.take_actions()) == [("teach", "retainer case")]

    for bad in ({}, {"label": ""}, {"label": None}, {"label": 5}, {"label": ["keys"]},
                {"label": "x" * 41}, {"label": "<script>"}, {"name": "keys"}, b"{oops"):
        status, reply = post(server, "/api/teach", bad)
        assert status == 400 and reply["ok"] is False, bad
    assert list(server.take_actions()) == []


def test_point_endpoint(server):
    assert post(server, "/api/point", {"target": "Keys"}) == (
        200, {"ok": True, "action": "point", "target": "keys"})
    assert post(server, "/api/point", {"x": 0.5, "y": 0.3}) == (
        200, {"ok": True, "action": "point", "x": 0.5, "y": 0.3})
    assert post(server, "/api/point", {"x": 0, "y": 1}) == (
        200, {"ok": True, "action": "point", "x": 0.0, "y": 1.0})
    assert list(server.take_actions()) == [("point", "keys"), ("aim", (0.5, 0.3)), ("aim", (0.0, 1.0))]

    for bad in ({}, {"target": ""}, {"target": 3}, {"x": 0.5}, {"x": 1.5, "y": 0.2},
                {"x": -0.1, "y": 0.2}, {"x": "0.5", "y": 0.2}, {"x": True, "y": 0.2},
                {"x": float("nan"), "y": 0.2}, {"target": "keys", "x": 0.5, "y": 0.5}):
        body = json.dumps(bad).encode()  # (NaN goes out as the bare word NaN)
        status, reply = post(server, "/api/point", body)
        assert status == 400 and reply["ok"] is False, bad
    assert list(server.take_actions()) == []


def test_unknown_routes(server):
    assert get(server, "/api/nope")[0] == 404
    assert post(server, "/api/nope", {})[0] == 404


# ------------------------------------------------------------------ threads
def test_concurrency_safety(server):
    """Lots of browsers at once must not slow the main loop down or lock anything up."""
    stop = threading.Event()
    errors, tick_secs, drained = [], [], []

    def main_loop():  # ~30 FPS, like the app
        while not stop.is_set():
            t = time.perf_counter()
            for action in server.take_actions():
                drained.append(action)
            tick(server)
            tick_secs.append(time.perf_counter() - t)
            time.sleep(1 / 30)

    def browser(n):
        try:
            for k in range(15):
                for path in ("/api/status", "/api/items", "/api/state", "/api/items/keys/snapshot"):
                    assert get(server, path)[0] == 200, path
                assert post(server, "/api/query", {"query": f"where are my keys {n} {k}"})[0] == 200
        except Exception as e:  # noqa: BLE001 (reported below)
            errors.append(e)

    frames = []

    def viewer():
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            conn.request("GET", "/api/video_feed")
            r = conn.getresponse()
            while not stop.is_set():
                line = r.fp.readline()
                if line.startswith(b"Content-Length:"):
                    frames.append(len(r.fp.read(int(line.split()[1]) + 4)))  # blank line, jpeg, CRLF
        except Exception as e:  # noqa: BLE001
            if not stop.is_set():
                errors.append(e)
        finally:
            conn.close()

    tick(server)
    loop = threading.Thread(target=main_loop, daemon=True)
    viewers = [threading.Thread(target=viewer, daemon=True) for _ in range(2)]
    browsers = [threading.Thread(target=browser, args=(n,), daemon=True) for n in range(8)]
    for t in [loop, *viewers, *browsers]:
        t.start()
    for t in browsers:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in browsers), "a request hung"
    time.sleep(0.3)  # let the main loop pick up the last questions, and the viewers a frame
    stop.set()
    loop.join(timeout=5)

    assert not errors, errors
    assert sorted(a[1] for a in drained) == sorted(
        f"where are my keys {n} {k}" for n in range(8) for k in range(15))
    assert frames, "the video viewers got nothing"
    # The main loop never waits on a request: a tick is just building JSON and a JPEG.
    assert max(tick_secs) < 0.25, max(tick_secs)
