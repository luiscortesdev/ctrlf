"""Tests for the Wi-Fi camera code (src/vision/camera.py) and the laser's wait for a lagging
camera. No camera needed: the stream and the network are stand-ins."""

import os
import socket
import time

import pytest

from src.vision import camera, laser_tracker


def test_env_value(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('GEMINI_API_KEY=abc\nCAMERA_USER=bob\nCAMERA_PASSWORD="two words"\n')
    monkeypatch.delenv("CAMERA_USER", raising=False)
    monkeypatch.delenv("CAMERA_PASSWORD", raising=False)
    assert camera.env_value("CAMERA_USER", env) == "bob"
    assert camera.env_value("CAMERA_PASSWORD", env) == "two words"
    assert camera.env_value("CAMERA_NOPE", env) is None
    monkeypatch.setenv("CAMERA_USER", "alice")  # the environment wins
    assert camera.env_value("CAMERA_USER", env) == "alice"
    assert camera.env_value("CAMERA_USER", tmp_path / "missing") == "alice"


def test_with_login(monkeypatch):
    login = {"CAMERA_USER": "cam", "CAMERA_PASSWORD": "p@ss:w/rd!"}
    monkeypatch.setattr(camera, "env_value", login.get)
    url = "rtsp://172.20.10.5:554/stream1"
    assert camera.with_login(url) == "rtsp://cam:p%40ss%3Aw%2Frd!@172.20.10.5:554/stream1"
    assert camera.with_login("rtsp://a:b@host/s") == "rtsp://a:b@host/s"  # already has one
    monkeypatch.setattr(camera, "env_value", {}.get)
    assert camera.with_login(url) == url                                  # no login set


def test_reachable():
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    assert camera.reachable(f"rtsp://127.0.0.1:{port}/stream1")
    listener.close()
    assert not camera.reachable(f"rtsp://127.0.0.1:{port}/stream1", timeout=0.2)


def test_find_stream(monkeypatch):
    answering = set()
    monkeypatch.setattr(camera, "reachable", lambda url: url in answering)
    url = "rtsp://172.20.10.5:554/stream1"
    assert camera.find_stream(url) is None
    answering.add("rtsp://172.20.10.7:554/stream1")  # the hotspot gave it a new address
    assert camera.find_stream(url) == "rtsp://172.20.10.7:554/stream1"
    answering.add(url)
    assert camera.find_stream(url) == url


class _FakeCapture:
    """Sends frames 1..n, a little apart, then stops like a dropped stream."""

    def __init__(self, n):
        self.left = list(range(1, n + 1))
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        time.sleep(0.01)
        return (True, self.left.pop(0)) if self.left else (False, None)

    def release(self):
        self.released = True


def test_stream_camera_newest_frame(monkeypatch):
    fake = _FakeCapture(20)
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda *args: fake)
    cam = camera.StreamCamera("rtsp://x/stream1")
    got = []
    while True:
        ok, frame = cam.read()
        if not ok:
            break
        got.append(frame)
        time.sleep(0.03)  # a busy app: frames arrive faster than it reads them
    assert got and got == sorted(set(got))  # never the same frame twice, never older
    assert len(got) < 20                     # skipped ahead to the newest, no backlog
    assert not cam.isOpened()
    assert fake.released


def test_open_camera_lag_only_for_streams(monkeypatch):
    monkeypatch.setattr(camera, "find_stream", lambda url: url)
    monkeypatch.setattr(camera, "capture", lambda source: _FakeCapture(1))
    camera.open_camera("rtsp://172.20.10.5:554/stream1", 0.8)
    assert camera.open_camera.lag == 0.8
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda index, *backend: _FakeCapture(1))
    camera.open_camera(1, 0.8)
    assert camera.open_camera.lag == 0.0


def test_aimer_waits_for_camera_lag():
    class Pointer:
        connected = True
        pan = tilt = 90

        def move(self, pan, tilt, laser=None):
            return 0.5  # seconds the move takes

        def send(self, cmd):
            pass

    class Mapping:  # calibrated: it can say where the dot should land
        def to_angles(self, u, v):
            return 90, 120

        def to_image(self, pan, tilt):
            return 0.5, 0.5

    aimer = laser_tracker.Aimer(Pointer(), Mapping(), lag=0.8)
    aimer.start((320, 180), 10, (640, 360), 0.0)
    arrived = 0.5 + laser_tracker.SETTLE_SECS + 0.8
    assert aimer.state == "off" and abs(aimer.wait - arrived) < 1e-9
    aimer.update(None, arrived - 0.01)  # the picture still shows the head moving
    assert aimer.state == "off"
    aimer.update("laser-off frame", arrived)
    assert aimer.state == "on"
    assert abs(aimer.wait - (arrived + laser_tracker.LASER_SETTLE_SECS + 0.8)) < 1e-9


def test_choose_camera():
    wifi = "rtsp://172.20.10.5:554/stream1"
    assert camera.choose_camera(None, wifi, 0) == wifi
    assert camera.choose_camera("WiFi", wifi, 0) == wifi
    assert camera.choose_camera("webcam", wifi, 1) == 1
    assert camera.choose_camera(" 2 ", wifi, 0) == 2
    assert camera.choose_camera("rtsp://10.0.0.9/live", wifi, 0) == "rtsp://10.0.0.9/live"
    with pytest.raises(ValueError):
        camera.choose_camera("usb", wifi, 0)


def test_webcam_has_its_own_calibration_and_desk_area():
    path = os.path.join("data", "calibration.json")
    assert camera.per_camera(path, "rtsp://172.20.10.5:554/stream1") == path
    assert camera.per_camera(path, 0) == os.path.join("data", "calibration-webcam.json")


def test_camera_option(monkeypatch, capsys):
    from src import app

    monkeypatch.delenv("CTRLF_CAMERA", raising=False)
    monkeypatch.setattr(app, "AREA_PATH", app.AREA_PATH)
    assert app.camera_option([]) == app.CAMERA
    assert app.AREA_PATH.endswith("desk_area.json")
    assert app.camera_option(["--camera", "webcam"]) == app.WEBCAM
    assert app.AREA_PATH.endswith("desk_area-webcam.json")
    monkeypatch.setenv("CTRLF_CAMERA", "3")
    assert app.camera_option([]) == 3
    assert app.camera_option(["--camera", "wifi"]) == app.CAMERA  # the flag wins
    with pytest.raises(SystemExit):
        app.camera_option(["--camera", "usb"])
    monkeypatch.setattr(app, "list_cameras", lambda: [(0, (1280, 720)), (1, (1920, 1080))])
    with pytest.raises(SystemExit) as stop:
        app.camera_option(["--list-cameras"])
    assert stop.value.code == 0 and "--camera 1    1920x1080" in capsys.readouterr().out


def test_room_camera_falls_back_only_when_not_asked_for(monkeypatch):
    from src import app

    opened = []

    def fake_open(source, lag=0.0):
        opened.append(source)
        if isinstance(source, str):
            raise SystemExit("Can't reach the camera.")
        fake_open.source = source  # like open_camera.source
        return _FakeCapture(1)
    monkeypatch.delenv("CTRLF_CAMERA", raising=False)
    monkeypatch.setattr(app, "open_camera", fake_open)
    monkeypatch.setattr(app, "AREA_PATH", app.AREA_PATH)

    app.open_room_camera(app.camera_option([]))  # the default: falls back to the webcam
    assert opened == [app.CAMERA, app.WEBCAM] and fake_open.source == app.WEBCAM
    assert app.AREA_PATH.endswith("desk_area-webcam.json")

    with pytest.raises(SystemExit):  # asked for the Wi-Fi camera: no fallback
        app.open_room_camera(app.camera_option(["--camera", "wifi"]))


def test_nothing_to_type_in_doesnt_quit(monkeypatch):
    from src import app

    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    q = app.queue.Queue()
    app.read_questions(q)
    assert q.empty()
