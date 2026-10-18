"""The website (web/) against the app's server (src/web.py): every request the frontend
makes, sent exactly the way the frontend sends it, and every reply checked for what the
frontend reads out of it.

Where each request comes from:

    GET  /api/state                api.js backendReachable(), poll()
    GET  /api/events               api.js startLive() (EventSource)
    POST /api/command {"text"}     api.js command()/ask()/forget(); teach.js, world/add.js
    POST /api/listen  (no body)    api.js listen()
    POST /api/click   {"x", "y"}   api.js click(); teach.js, world/add.js
    GET  /video                    api.js videoUrl()       (<img src>)
    GET  /video/teach              api.js teachVideoUrl()  (<img src>)
    GET  /snapshots/<file>         items[].snapshot        (<img src>)
    GET  /taught/<name>.jpg        api.js taughtPhoto()    (<img src>)
    GET  /, *.html, js, css, ...   the pages and what they load

Runs the real server on a free port with stand-ins for the app (see test_web_api.py), so it
needs no camera, models or ESP32.
"""

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import pytest

from src.web import WebBridge
from tests.test_web_api import DETECTOR, NOW, VOICE, FakeApp, frame, row

ROOT = Path(__file__).resolve().parents[1]
ISO_LOCAL = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")  # no zone: JS reads it as local time

HELPERS = {
    "describe": lambda obj, mem: "left of the laptop",
    "surfaces": {"desk"},
    "objects": {"keys": [], "wallet": [], "earbuds case": ["airpods"], "desk": []},
    "taught": {"retainer case": {}, "kid's retainer": {}},
}


@pytest.fixture
def server(tmp_path):
    """The server with the repo's real web/ folder."""
    snap_dir, taught_dir = tmp_path / "snapshots", tmp_path / "taught"
    os.makedirs(snap_dir)
    os.makedirs(taught_dir)
    snap = snap_dir / "keys_1760000000000.jpg"
    snap.write_bytes(b"\xff\xd8 keys \xff\xd9")
    for name in HELPERS["taught"]:  # how the app names them (App.finish_teach)
        (taught_dir / (name.replace(" ", "_") + ".jpg")).write_bytes(b"\xff\xd8 " + name.encode() + b" \xff\xd9")
    bridge = WebBridge(str(ROOT), str(snap_dir), str(taught_dir))
    assert bridge.start(port=0)
    bridge.test_app = FakeApp([
        row("keys", (100, 200, 140, 230), str(snap)),
        row("earbuds case", (300, 100, 330, 120), None, NOW - 600),
        row("desk", (0, 150, 640, 360)),  # furniture: never listed
    ])
    yield bridge
    bridge.stop()


def tick(bridge, said=(), teach_view=None):
    """One pass of the app's main loop. said: [(time, text)] it has said."""
    bridge.state_at = 0.0  # build the state now, not after STATE_EVERY
    bridge.tick(bridge.test_app, frame(), teach_view, VOICE, DETECTOR, list(said), time.time(), HELPERS)


def get(bridge, path):
    """(status, headers, body)"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{bridge.port}{path}", timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def state(bridge):
    status, headers, body = get(bridge, "/api/state")
    assert status == 200 and "json" in headers["Content-Type"]
    return json.loads(body)


def post(bridge, path, body=None, headers=None):
    """(status, JSON reply). body: a dict sent as JSON (like JSON.stringify), raw bytes, or
    None for no body at all (like fetch(url, {method: "POST"}))."""
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        headers = {"Content-Type": "application/json", **(headers or {})}
    conn = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=5)
    try:
        conn.request("POST", path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, json.loads(r.read())
    finally:
        conn.close()


def js_encode_uri_component(s):
    return quote(s, safe="-_.!~*'()")


def check_state(s):
    """What api.js applyState() and the pages read out of /api/state, with those types."""
    assert isinstance(s["items"], list) and isinstance(s["taught"], list) and isinstance(s["can_find"], list)
    for it in s["items"]:
        assert isinstance(it["name"], str) and isinstance(it["visible"], bool)
        assert ISO_LOCAL.fullmatch(it["last_seen"]), it["last_seen"]
        assert isinstance(it["where"], str)
        assert it["snapshot"] is None or it["snapshot"].startswith("/snapshots/")
        x1, y1, x2, y2 = it["bbox"]  # dashboard.js reads bbox[0..3] as fractions of the view
        assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1
    st = s["status"]
    assert set(st) >= {"state", "target", "result", "message"}
    assert st["state"] in ("IDLE", "QUERYING", "TRACKING", "REPORTING", "TEACHING")
    assert st["result"] in (None, "found", "hidden", "info")
    assert s["target"] is None or (isinstance(s["target"]["name"], str) and len(s["target"]["bbox"]) == 4)
    assert s["teach"] is None or set(s["teach"]) == {"phase", "name", "laptop_feed"}
    assert s["heard"] is None or (ISO_LOCAL.fullmatch(s["heard"]["time"]) and isinstance(s["heard"]["text"], str))
    for m in s["said"]:
        assert ISO_LOCAL.fullmatch(m["time"]) and isinstance(m["text"], str)
    assert isinstance(s["voice"], str)
    assert isinstance(s["laser_connected"], bool)


# ------------------------------------------------------------------ GET /api/state
def test_state_before_first_pass_still_counts_as_live(server):
    # backendReachable(): res.ok and a JSON content type. applyState() skips the {}.
    status, headers, body = get(server, "/api/state")
    assert status == 200 and "json" in headers["Content-Type"]
    assert json.loads(body) == {}
    assert headers["Cache-Control"] == "no-store"


def test_state_idle(server):
    tick(server)
    s = state(server)
    check_state(s)
    items = {it["name"]: it for it in s["items"]}
    assert set(items) == {"keys", "earbuds case"}
    assert items["keys"]["visible"] is True
    assert items["keys"]["where"] == "left of the laptop"
    assert items["keys"]["bbox"] == [0.1562, 0.5556, 0.2188, 0.6389]
    assert items["keys"]["last_seen"] == time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW))
    assert items["earbuds case"]["visible"] is False and items["earbuds case"]["snapshot"] is None
    assert s["taught"] == ["kid's retainer", "retainer case"]
    assert s["can_find"] == ["earbuds case", "keys", "wallet"]  # not the desk
    assert s["status"] == {"state": "IDLE", "target": None, "result": None, "message": None}
    assert s["target"] is None and s["teach"] is None and s["heard"] is None and s["said"] == []
    assert s["laser_connected"] is True and s["voice"] == "idle"


def test_state_query_lifecycle(server):
    """The states dashboard.js and world/main.js switch on, in the order a question goes."""
    app = server.test_app
    t0 = time.time()
    server.note_command("where are my keys?", t0)
    tick(server)
    s = state(server)
    check_state(s)
    # Both pages show the question from status.message (mock mode sends it as heard).
    assert s["status"] == {"state": "QUERYING", "target": None, "result": None, "message": "where are my keys?"}
    assert s["heard"]["text"] == "where are my keys?"

    app.highlight = ("keys", time.time() + 10)
    app.aimer.state = "on"
    tick(server)
    s = state(server)
    check_state(s)
    assert s["status"]["state"] == "TRACKING" and s["status"]["target"] == "keys"
    assert s["target"] == {"name": "keys", "bbox": [0.1562, 0.5556, 0.2188, 0.6389]}

    app.aimer.state = "hold"
    said = [(time.time(), "Your keys are left of the laptop.")]
    tick(server, said)
    s = state(server)
    check_state(s)
    assert s["status"] == {"state": "REPORTING", "target": "keys", "result": "found",
                           "message": "Your keys are left of the laptop."}
    assert s["said"][-1]["text"] == "Your keys are left of the laptop."
    assert s["aim"] == "hold"

    app.highlight = None
    server.heard = None
    tick(server, said)
    assert state(server)["status"]["state"] == "IDLE"


def test_state_hidden_item(server):
    app = server.test_app
    app.highlight = ("earbuds case", time.time() + 10)
    tick(server, [(time.time(), "Your earbuds case is out of sight.")])
    s = state(server)
    check_state(s)
    assert s["status"]["state"] == "REPORTING" and s["status"]["result"] == "hidden"
    assert s["status"]["target"] == "earbuds case"
    assert s["target"] is None  # nothing to draw a reticle on


def test_state_other_answer(server):
    t0 = time.time()
    server.note_command("where is my unicorn", t0)
    tick(server, [(t0 + 0.1, "I haven't seen a unicorn.")])
    s = state(server)
    check_state(s)
    assert s["status"] == {"state": "REPORTING", "target": None, "result": "info",
                           "message": "I haven't seen a unicorn."}


@pytest.mark.parametrize("laptop", [False, True])
def test_state_teaching(server, laptop):
    """teach.js / world/add.js read teach.{phase, name, laptop_feed} and the last thing said."""
    server.test_app.teach = {"phase": "looking", "name": "retainer case", "laptop": laptop}
    tick(server, [(time.time(), "Show me the retainer case.")], teach_view=frame() if laptop else None)
    s = state(server)
    check_state(s)
    assert s["teach"] == {"phase": "looking", "name": "retainer case", "laptop_feed": laptop}
    assert s["status"]["state"] == "TEACHING" and s["status"]["target"] == "retainer case"
    assert s["said"][-1]["text"] == "Show me the retainer case."


# ------------------------------------------------------------------ GET /api/events
def test_events_push_the_state(server):
    tick(server)
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        conn.request("GET", "/api/events", headers={"Accept": "text/event-stream"})
        r = conn.getresponse()
        assert r.status == 200
        assert r.getheader("Content-Type") == "text/event-stream"
        assert r.getheader("Cache-Control") == "no-store"

        def next_event():
            while True:
                line = r.fp.readline()
                if line.startswith(b"data: "):
                    return json.loads(line[len(b"data: "):])

        assert r.fp.readline() == b"retry: 1000\n"
        first = next_event()  # the current state, straight away
        check_state(first)
        assert first == state(server)

        server.test_app.highlight = ("keys", time.time() + 10)
        server.test_app.aimer.state = "on"
        tick(server)
        pushed = next_event()  # and again the moment it changes
        assert pushed["status"]["state"] == "TRACKING"
        assert pushed == state(server)
    finally:
        conn.close()


# ------------------------------------------------------------------ POST /api/command
@pytest.mark.parametrize("text", [
    "where are my keys?",          # ask()
    "where is my retainer case",   # Find buttons
    "this is my retainer case",    # teaching from a name
    "learn this", "yes", "no", "cancel", "retainer case",
    "forget kid's retainer",       # forget()
])
def test_command(server, text):
    assert post(server, "/api/command", {"text": text}) == (200, {"ok": True})
    assert list(server.take_actions()) == [("command", text)]


def test_command_is_trimmed_and_capped(server):
    assert post(server, "/api/command", {"text": "  " + "k" * 300 + " "}) == (200, {"ok": True})
    assert list(server.take_actions()) == [("command", "k" * 200)]


@pytest.mark.parametrize("bad", [
    {}, {"text": ""}, {"text": "   "}, {"text": None}, {"text": 5}, {"text": ["keys"]},
    {"query": "where are my keys"}, b"not json", b"[1, 2]", b'"keys"', b"\xff\xfe",
])
def test_bad_command(server, bad):
    status, reply = post(server, "/api/command", bad, {"Content-Type": "application/json"})
    assert status == 400
    assert reply["ok"] is False and isinstance(reply["error"], str)
    assert list(server.take_actions()) == []


# ------------------------------------------------------------------ POST /api/listen
def test_listen_with_no_body(server):
    assert post(server, "/api/listen") == (200, {"ok": True})  # http.client: no Content-Length
    assert post(server, "/api/listen", b"") == (200, {"ok": True})  # browsers: Content-Length: 0
    assert list(server.take_actions()) == [("listen", None), ("listen", None)]


# ------------------------------------------------------------------ POST /api/click
def test_click(server):
    assert post(server, "/api/click", {"x": 0.25, "y": 0.75}) == (200, {"ok": True})
    assert post(server, "/api/click", {"x": 0, "y": 1}) == (200, {"ok": True})
    assert post(server, "/api/click", {"x": 1.4, "y": -0.2}) == (200, {"ok": True})  # kept in the view
    assert list(server.take_actions()) == [("click", (0.25, 0.75)), ("click", (0.0, 1.0)), ("click", (1.0, 0.0))]


@pytest.mark.parametrize("bad", [
    {}, {"x": 0.5}, {"x": None, "y": 0.5},  # JSON.stringify(NaN) is null
    {"x": "left", "y": 0.5}, {"x": [0.5], "y": 0.5}, b"{", b"[0.5, 0.5]",
])
def test_bad_click(server, bad):
    status, reply = post(server, "/api/click", bad, {"Content-Type": "application/json"})
    assert status == 400
    assert reply["ok"] is False and isinstance(reply["error"], str)
    assert list(server.take_actions()) == []


def test_bad_content_length(server):
    for length in ("-5", "lots"):
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            conn.putrequest("POST", "/api/command")
            conn.putheader("Content-Length", length)
            conn.endheaders()
            r = conn.getresponse()  # straight away, not when the connection times out
            assert r.status == 400 and json.loads(r.read())["ok"] is False
        finally:
            conn.close()


# ------------------------------------------------------------------ video (<img src>)
@pytest.mark.parametrize("path,teach", [("/video", False), ("/api/video_feed", False), ("/video/teach", True)])
def test_video(server, path, teach):
    feed = server.teach_feed if teach else server.room
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        assert r.status == 200
        assert r.getheader("Content-Type") == "multipart/x-mixed-replace; boundary=frame"
        assert r.getheader("Cache-Control") == "no-store"
        for _ in range(100):  # frames are only encoded while someone's watching
            if feed.viewers:
                break
            time.sleep(0.02)
        assert feed.viewers == 1
        for _ in range(2):  # a frame, then the next one: it keeps streaming
            time.sleep(0.1)  # past VIDEO_EVERY
            tick(server, teach_view=frame() if teach else None)
            assert r.fp.readline() == b"--frame\r\n"
            assert r.fp.readline() == b"Content-Type: image/jpeg\r\n"
            length = int(r.fp.readline().split(b":")[1])
            assert r.fp.readline() == b"\r\n"
            jpeg = r.fp.read(length)
            assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
            assert r.fp.readline() == b"\r\n"
    finally:
        conn.close()


# ------------------------------------------------------------------ photos (<img src>)
def test_snapshot_from_state(server):
    tick(server)
    keys = next(it for it in state(server)["items"] if it["name"] == "keys")
    assert keys["snapshot"] == "/snapshots/keys_1760000000000.jpg"
    status, headers, body = get(server, keys["snapshot"])
    assert status == 200 and headers["Content-Type"] == "image/jpeg"
    assert body == b"\xff\xd8 keys \xff\xd9"
    assert "max-age" in headers["Cache-Control"]  # each snapshot has its own name


@pytest.mark.parametrize("name", list(HELPERS["taught"]))
def test_taught_photo(server, name):
    # api.js taughtPhoto(): `/taught/${encodeURIComponent(name.replace(/ /g, "_"))}.jpg`
    url = f"/taught/{js_encode_uri_component(name.replace(' ', '_'))}.jpg"
    status, headers, body = get(server, url)
    assert status == 200 and headers["Content-Type"] == "image/jpeg"
    assert body == b"\xff\xd8 " + name.encode() + b" \xff\xd9"
    assert headers["Cache-Control"] == "no-store"  # teaching it again replaces it


def test_missing_photos(server):
    # The pages hide the <img> when it fails (onerror), so these must be clean 404s.
    assert get(server, "/taught/never_taught.jpg")[0] == 404
    assert get(server, "/snapshots/keys_1.jpg")[0] == 404


# ------------------------------------------------------------------ the pages and their files
MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
        ".woff2": "font/woff2", ".mp3": "audio/mpeg"}


def _refs(path, text):
    """Local files a page, script or stylesheet loads, as URL paths."""
    base = path.rsplit("/", 1)[0] + "/"
    if path.endswith(".html"):
        found = re.findall(r'(?:src|href)="([^"]+)"', text)
        found += [v for m in re.findall(r'<script type="importmap">(.*?)</script>', text, re.S)
                  for v in json.loads(m)["imports"].values()]
    elif path.endswith(".js"):
        found = re.findall(r'(?:^|\s)(?:import|from)\s*["\'](\.{1,2}/[^"\']+)["\']', text, re.M)
    else:
        found = re.findall(r'url\("?([^")]+)"?\)', text)
    out = set()
    for ref in found:
        if re.match(r"(?:[a-z]+:|#|//)", ref):  # other sites, data: URLs, anchors
            continue
        out.add(urlparse(urljoin(base, ref)).path)
    return out


def test_pages_and_everything_they_load(server):
    seen, todo = set(), ["/", "/index.html", "/dashboard.html", "/classic.html"]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        status, headers, body = get(server, path)
        assert status == 200, path
        ext = os.path.splitext(path)[1] or ".html"
        if ext in MIME:  # module scripts don't run unless they're served as JavaScript
            assert headers["Content-Type"].startswith(MIME[ext]), (path, headers["Content-Type"])
        if ext in (".html", ".js", ".css"):
            todo += _refs(path if path != "/" else "/index.html", body.decode("utf-8"))
    assert {"/js/api.js", "/js/world/main.js", "/js/dashboard.js", "/vendor/three.module.js",
            "/vendor/three.core.js", "/css/app.css", "/img/favicon.svg"} <= seen
    # sound.js builds these with new URL(`../audio/${name}.mp3`, import.meta.url)
    for mp3 in sorted((ROOT / "web" / "audio").glob("*.mp3")):
        status, headers, _ = get(server, f"/audio/{mp3.name}")
        assert status == 200 and headers["Content-Type"] == "audio/mpeg", mp3.name


# ------------------------------------------------------------------ what the frontend never sends
def test_unknown_and_unsafe_paths(server):
    assert get(server, "/api/nope")[0] == 404
    assert get(server, "/api/command")[0] == 404  # GET on a POST route
    assert get(server, "/nope.html")[0] == 404
    assert post(server, "/api/nope", {})[0] == 404
    for path in ("/snapshots/..%2F..%2Fsrc%2Fweb.py", "/taught/..%2F..%2Fsrc%2Fweb.py",
                 "/..%2Fsrc%2Fweb.py", "/../src/web.py"):
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            assert r.status == 404, path
            assert b"WebBridge" not in r.read()
        finally:
            conn.close()
