"""
The website's link to the running app (see web/ and web/API.md).

Serves the web/ folder and a small API on http://localhost:8000 from a background
thread, using only the standard library (no extra installs):

    GET  /                 the intro, then the dashboard
    GET  /api/state        what the dashboard shows (JSON)
    GET  /api/events       the same, pushed each time it changes (server-sent events)
    POST /api/command      {"text": "where are my keys?"}: same as typing it in Terminal
    POST /api/listen       push-to-talk, same as SPACE in the video window
    POST /api/click        {"x": 0-1, "y": 0-1}: while teaching, "it's this one"
    GET  /video            live room view with boxes (MJPEG); also /api/video_feed
    GET  /video/teach      the laptop camera while teaching (MJPEG)
    GET  /snapshots/<f>    last-seen snapshots;  GET /taught/<f>  taught things' photos

The same, one piece at a time (for scripts and other clients):

    GET  /api/status                 state, target, message, aim, voice, laser_connected
    GET  /api/items                  what it remembers, with snapshot_url
    GET  /api/items/<name>/snapshot  that thing's last-seen snapshot (JPEG)
    POST /api/query                  {"query": "where are my keys?"}
    POST /api/teach                  {"label": "keys"}: start teaching it
    POST /api/point                  {"target": "keys"} or {"x": 0-1, "y": 0-1}: aim the laser

The app's objects (memory's SQLite connection, the tracker) belong to the main loop, so
the main loop calls tick() and everything the server sends is built there, never in the
server's threads.
"""

import json
import math
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlparse

import cv2

PORT = int(os.environ.get("CTRLF_WEB_PORT", "8000"))
STATE_EVERY = 0.2   # seconds between state updates
VIDEO_EVERY = 1 / 15  # live view frames per second sent to the browser
JPEG_QUALITY = 75
LABEL_RE = re.compile(r"[a-z0-9][a-z0-9 '-]{0,39}")  # a name to teach: "retainer case"

TYPES = {  # set explicitly: Windows' registry can map .js to text/plain
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".json": "application/json",
    ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # How many new connections can wait to be accepted. The default is 5, and macOS resets
    # the ones past that instead of making them wait: with 6 browsers' requests at once
    # some failed, with 8 almost half did.
    request_queue_size = 64


class Stream:
    """Latest JPEG of one video feed, handed to every connected browser."""

    def __init__(self):
        self.jpeg = None
        self.viewers = 0
        self.cond = threading.Condition()
        self.sent_at = 0.0

    def put(self, img, now):
        if self.viewers == 0 or now - self.sent_at < VIDEO_EVERY:
            return
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if ok:
            with self.cond:
                self.jpeg = buf.tobytes()
                self.sent_at = now
                self.cond.notify_all()


class WebBridge:
    def __init__(self, root, snap_dir, taught_dir):
        self.web_dir = os.path.join(root, "web")
        self.snap_dir = snap_dir
        self.taught_dir = taught_dir
        self.room = Stream()
        self.teach_feed = Stream()
        self.actions = deque()      # from the browser, run by the main loop
        self.state = None           # the latest _state(), replaced (never changed) by tick()
        self.state_json = b"{}"
        self.state_version = 0      # +1 each time the state changes (for /api/events)
        self.state_cond = threading.Condition()
        self.state_at = 0.0
        self.state_key = None       # what rebuilds the state at once when it changes
        self.heard = None           # (time, text) of the last question or command
        self.server = None
        self.port = None

    # ---- server
    def start(self, port=PORT):
        """port 0 picks a free one (see self.port)."""
        bridge = self

        class Handler(_Handler):
            pass
        Handler.bridge = bridge
        try:
            self.server = _Server(("127.0.0.1", port), Handler)
        except OSError as e:
            print(f"(website off: port {port} is busy ({e}). Stop the other server, "
                  f"or set CTRLF_WEB_PORT to another port.)")
            return False
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        print(f"Website: http://localhost:{self.port}  (dashboard: /dashboard.html)")
        return True

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # ---- called by the main loop
    def take_actions(self):
        while self.actions:
            yield self.actions.popleft()

    def note_command(self, text, now):
        self.heard = (now, text)

    def tick(self, app, room_view, teach_view, voice, detector, said, now, helpers):
        self.room.put(room_view, now)
        if teach_view is not None:
            self.teach_feed.put(teach_view, now)
        # Rebuilt right away when a detector pass lands or it says or hears something, so
        # the browser isn't a STATE_EVERY behind; otherwise every STATE_EVERY (things age).
        key = (detector.result()[0] if detector else None, said[-1] if said else None, self.heard,
               app.aimer.state if app.aimer else None, getattr(voice, "state", None),
               app.teach and app.teach.get("phase"), app.highlight)
        if key == self.state_key and now - self.state_at < STATE_EVERY:
            return
        self.state_key, self.state_at = key, now
        state = self._state(app, voice, detector, said, now, helpers, teach_view is not None)
        body = json.dumps(state).encode()
        if body == self.state_json:
            return
        # The handlers read these from their own threads, so the state is swapped for a new
        # dict, never changed in place.
        with self.state_cond:
            self.state, self.state_json = state, body
            self.state_version += 1
            self.state_cond.notify_all()

    # ---- the dashboard's view of the app
    def _state(self, app, voice, detector, said, now, h, teach_live):
        # What it remembers, and what's boxed in the video right now but not remembered yet
        # (that waits until it has sat still for a few passes, and a thing being carried
        # never does), so things show up here as soon as they do in the video.
        rows = app.mem.all()
        remembered = {r["label"] for r in rows}
        rows += [{"label": label, "snapshot": None} for label in app.tracks if label not in remembered]
        items = []
        for r in rows:
            if r["label"] in h["surfaces"]:
                continue
            obj, visible = app.locate(r["label"], now)
            if r["label"] not in remembered and not visible:
                continue
            obj = obj or r
            items.append({
                "name": r["label"],
                "visible": bool(visible),
                "last_seen": _iso(obj["last_seen"]),
                "where": h["describe"](obj, app.mem),
                "snapshot": f"/snapshots/{os.path.basename(r['snapshot'])}" if r.get("snapshot") else None,
                "bbox": _norm(obj),
            })

        highlight = app.highlight if app.highlight and now < app.highlight[1] else None
        aim_state = app.aimer.state if app.aimer else "idle"
        last_said = said[-1] if said else None
        status = {"state": "IDLE", "target": None, "result": None, "message": None}
        target = None
        if app.teach:
            status.update(state="TEACHING", target=app.teach.get("name"),
                          message=last_said[1] if last_said else None)
        elif highlight:
            label = highlight[0]
            obj, visible = app.locate(label, now)
            target = {"name": label, "bbox": _norm(obj)} if obj and visible else None
            message = last_said[1] if last_said else None
            if aim_state in ("off", "on"):
                status.update(state="TRACKING", target=label, message=message)
            else:
                status.update(state="REPORTING", target=label, message=message,
                              result="found" if visible else "hidden")
        elif self.heard and now - self.heard[0] < 6 and last_said and last_said[0] >= self.heard[0]:
            status.update(state="REPORTING", result="info", message=last_said[1])
        elif self.heard and now - self.heard[0] < 2:
            status.update(state="QUERYING", message=self.heard[1])

        teach = None
        if app.teach:
            teach = {"phase": app.teach.get("phase"), "name": app.teach.get("name"),
                     "laptop_feed": teach_live}
        return {
            "now": _iso(now),
            "items": items,
            "can_find": sorted(k for k in h["objects"] if k not in h["surfaces"]),
            "taught": sorted(h["taught"]),
            "status": status,
            "target": target,
            "aim": aim_state,
            "teach": teach,
            "voice": getattr(voice, "state", "off") if voice else "off",
            "heard": {"time": _iso(self.heard[0]), "text": self.heard[1]} if self.heard else None,
            "said": [{"time": _iso(t), "text": s} for t, s in list(said)[-12:]],
            "detector_fps": round(1 / detector.secs_per_frame, 1) if getattr(detector, "secs_per_frame", 0) else None,
            "laser_connected": bool(getattr(app.pointer, "connected", False)),
        }


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def _norm(obj):
    if not obj:
        return None
    w, h = obj["frame_w"] or 1, obj["frame_h"] or 1
    return [round(obj["x1"] / w, 4), round(obj["y1"] / h, 4), round(obj["x2"] / w, 4), round(obj["y2"] / h, 4)]


class _Handler(BaseHTTPRequestHandler):
    bridge = None  # set in WebBridge.start
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # quiet: the Terminal is for the app

    def _send(self, code, body, ctype="application/json", cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # No caching: every reload gets the latest files while we're still editing them.
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        return self._send(code, json.dumps(obj).encode())

    def _bad(self, error):
        return self._json(400, {"ok": False, "error": error})

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        b = self.bridge
        if path == "/api/state":
            return self._send(200, b.state_json)
        if path == "/api/events":
            return self._events(b)
        if path in ("/video", "/api/video_feed"):
            return self._stream(b.room)
        if path.startswith("/api/"):
            return self._api_get(path, b.state)
        if path == "/video/teach":
            return self._stream(b.teach_feed)
        if path.startswith("/snapshots/"):
            return self._file(b.snap_dir, path[len("/snapshots/"):], cache=True)
        if path.startswith("/taught/"):
            return self._file(b.taught_dir, path[len("/taught/"):])
        if path == "/":
            path = "/index.html"
        return self._file(b.web_dir, path.lstrip("/"))

    def _api_get(self, path, state):
        """Pieces of the latest state, built by the main loop (state is never changed)."""
        m = re.fullmatch(r"/api/items/(.+)/snapshot", path)
        if path not in ("/api/status", "/api/items") and not m:
            return self._json(404, {"ok": False})
        if state is None:
            return self._json(503, {"ok": False, "error": "starting"})
        if path == "/api/status":  # status.target is the name; target_bbox where it is
            keys = ("now", "aim", "teach", "voice", "detector_fps", "laser_connected")
            return self._json(200, {**state["status"], **{k: state[k] for k in keys},
                                    "target_bbox": state["target"] and state["target"]["bbox"]})
        if path == "/api/items":
            return self._json(200, {"items": [
                {**it, "snapshot_url": f"/api/items/{quote(it['name'])}/snapshot" if it["snapshot"] else None}
                for it in state["items"]]})
        item = next((it for it in state["items"] if it["name"] == m.group(1)), None)
        if not (item and item["snapshot"]):
            return self._json(404, {"ok": False, "error": "no snapshot"})
        # Same URL for the newer snapshot once it moves, so the browser mustn't cache it.
        return self._file(self.bridge.snap_dir, os.path.basename(item["snapshot"]))

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._bad("bad json")
        if not isinstance(data, dict):
            return self._bad("expected a JSON object")
        if path == "/api/query":  # a question, same as typing it in Terminal
            text = data.get("query")
            text = text.strip() if isinstance(text, str) else ""
            if not text or len(text) > 200:
                return self._bad('"query" must be text (1-200 characters)')
            self.bridge.actions.append(("command", text))
            return self._json(200, {"ok": True, "action": "query", "query": text})
        if path == "/api/teach":  # start teaching it, like "learn my keys"
            label = data.get("label")
            label = " ".join(label.lower().split()) if isinstance(label, str) else ""
            if not LABEL_RE.fullmatch(label):
                return self._bad('"label" must be a name: up to 40 letters, numbers and spaces')
            self.bridge.actions.append(("teach", label))
            return self._json(200, {"ok": True, "action": "teach", "label": label})
        if path == "/api/point":
            return self._point(data)
        if path == "/api/command":
            text = str(data.get("text", "")).strip()[:200]
            if not text:
                return self._send(400, b'{"ok": false, "error": "empty"}')
            self.bridge.actions.append(("command", text))
            return self._send(200, b'{"ok": true}')
        if path == "/api/click":  # while teaching: "it's this one" (x, y as 0-1 of the view)
            try:
                point = (min(1.0, max(0.0, float(data["x"]))), min(1.0, max(0.0, float(data["y"]))))
            except (KeyError, TypeError, ValueError):
                return self._send(400, b'{"ok": false}')
            self.bridge.actions.append(("click", point))
            return self._send(200, b'{"ok": true}')
        if path == "/api/listen":
            self.bridge.actions.append(("listen", None))
            return self._send(200, b'{"ok": true}')
        return self._send(404, b'{"ok": false}')

    def _point(self, data):
        """Aim the laser at something it knows ({"target"}) or a spot in the view ({"x", "y"})."""
        target = data.get("target")
        has_xy = "x" in data or "y" in data
        if target is not None and not has_xy:
            target = " ".join(target.lower().split()) if isinstance(target, str) else ""
            if not target or len(target) > 60:
                return self._bad('"target" must be a name')
            self.bridge.actions.append(("point", target))
            return self._json(200, {"ok": True, "action": "point", "target": target})
        if target is None and has_xy:
            x, y = data.get("x"), data.get("y")
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                       and 0 <= v <= 1 for v in (x, y)):
                return self._bad('"x" and "y" must both be numbers from 0 to 1')
            x, y = float(x), float(y)
            self.bridge.actions.append(("aim", (x, y)))
            return self._json(200, {"ok": True, "action": "point", "x": x, "y": y})
        return self._bad('send either {"target": name} or {"x": 0-1, "y": 0-1}')

    def _file(self, folder, rel, cache=False):
        full = os.path.realpath(os.path.join(folder, rel))
        if not full.startswith(os.path.realpath(folder) + os.sep) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        with open(full, "rb") as f:
            body = f.read()
        ext = os.path.splitext(full)[1].lower()
        return self._send(200, body, TYPES.get(ext, "application/octet-stream"), cache)

    def _events(self, b):
        """Server-sent events: the /api/state JSON each time it changes, the moment it does
        (no polling delay). A comment every few seconds when nothing changes, to notice a
        browser that's gone."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        version = 0  # nothing until the first state
        try:
            self.wfile.write(b"retry: 1000\n\n")  # reconnect a second after the app restarts
            while True:
                with b.state_cond:
                    b.state_cond.wait_for(lambda: b.state_version != version, timeout=5)
                    new, body = b.state_version, b.state_json
                if new == version:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    version = new
                    self.wfile.write(b"data: " + body + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _stream(self, stream):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        with stream.cond:
            stream.viewers += 1
        try:
            last = None
            while True:
                with stream.cond:
                    stream.cond.wait_for(lambda: stream.jpeg is not last, timeout=5)
                    jpeg = stream.jpeg
                if jpeg is None or jpeg is last:
                    continue
                last = jpeg
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with stream.cond:
                stream.viewers -= 1
