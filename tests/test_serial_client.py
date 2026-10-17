"""The ESP32 link over Wi-Fi and the LED ring commands, against a fake board on this Mac
that speaks the firmware's protocol: run `pytest`."""

import socket
import threading
import time

import pytest

from src import app
from src.hardware import serial_client
from src.hardware.serial_client import NetLink, Pointer, wifi_key

KEY = "testkey"


class FakeBoard:
    """Like firmware/esp32_controller over Wi-Fi: the first line must be the key, one
    program at a time ("ERR busy"), "OK ..." after every line."""

    def __init__(self, ring=True):
        self.ring, self.lines, self.app, self.heard = ring, [], None, 0.0
        self.connections = 0
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def ok(self):
        return ("OK P90.0 T125.0 L0" + (" R1" if self.ring else "") + "\n").encode()

    def _accept(self):
        while True:
            c, _ = self.srv.accept()
            f = c.makefile()
            if f.readline().strip() != f"KEY {KEY}":
                c.sendall(b"ERR wrong key\n")
                c.close()
            elif self.app and time.time() - self.heard < 3:
                c.sendall(b"ERR busy\n")
                c.close()
            else:
                self.app, self.heard = c, time.time()
                self.connections += 1
                c.sendall(self.ok())
                threading.Thread(target=self._serve, args=(c, f), daemon=True).start()

    def _serve(self, c, f):
        try:
            for line in f:
                self.heard = time.time()
                self.lines.append(line.strip())
                c.sendall(self.ok())
        except (OSError, ValueError):
            pass

    def restart(self):  # the board rebooted: the connection is gone
        self.app.shutdown(socket.SHUT_RDWR)
        self.app.close()
        self.app = None


def wait(cond, secs=5):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


@pytest.fixture
def secrets(tmp_path):
    path = tmp_path / "secrets.h"
    path.write_text('const char *WIFI_NETWORKS[][2] = {\n  {"Net", "pw"},  // {"x", "y"}\n};\n'
                    f'#define HOSTNAME "ctrlf"\n#define LINK_KEY "{KEY}"\n')
    return str(path)


def board_pointer(monkeypatch, secrets, ring=True):
    board = FakeBoard(ring)
    monkeypatch.setattr(serial_client, "POINTER_PORT", board.port)
    return board, Pointer("wifi", "127.0.0.1", secrets)


def test_wifi_key(tmp_path, secrets):
    assert wifi_key(secrets) == (KEY, "ctrlf")
    one_line = tmp_path / "one.h"
    one_line.write_text('const char *WIFI_NETWORKS[][2] = {{"Net", "pw"}};\n#define LINK_KEY "k"\n')
    assert wifi_key(str(one_line)) == ("k", "ctrlf")
    empty = tmp_path / "empty.h"  # the template: no network named (the comment doesn't count)
    empty.write_text('const char *WIFI_NETWORKS[][2] = {\n  {"", ""},  // {"name", "password"}\n};\n'
                     '#define LINK_KEY "k"\n')
    assert wifi_key(str(empty)) is None
    assert wifi_key(str(tmp_path / "missing.h")) is None


def test_commands_and_reconnect(monkeypatch, secrets):
    board, p = board_pointer(monkeypatch, secrets)
    assert p.connected
    p.move(60, 130, laser=True)
    assert wait(lambda: "P60 T130 L1" in board.lines)
    board.restart()
    assert wait(lambda: board.connections == 2 and p.connected, 10)  # reconnected by itself
    p.move(90, 125)
    assert wait(lambda: "P90 T125" in board.lines)


def test_heartbeat_notices_a_silent_board(monkeypatch, secrets):
    monkeypatch.setattr(NetLink, "HEARTBEAT_SECS", 0.2)
    monkeypatch.setattr(NetLink, "DEAD_SECS", 1.0)
    board, p = board_pointer(monkeypatch, secrets)
    assert wait(lambda: board.lines.count("?") >= 2, 3)
    board.ok = lambda: b""  # stops answering, keeps the connection
    assert wait(lambda: not p.connected, 3)


def test_busy_and_wrong_key(monkeypatch, secrets, tmp_path):
    board, p = board_pointer(monkeypatch, secrets)
    second = Pointer("wifi", "127.0.0.1", secrets)  # like calibrate.py while ctrlf.py runs
    assert wait(lambda: second.ser.busy, 5) and not second.connected and p.connected
    bad = tmp_path / "bad.h"
    bad.write_text('const char *WIFI_NETWORKS[][2] = {{"Net", "pw"}};\n#define LINK_KEY "nope"\n')
    wrong = Pointer("wifi", "127.0.0.1", str(bad))
    assert wait(lambda: wrong.ser.refused, 5) and not wrong.connected


def test_ring(monkeypatch, secrets):
    board, p = board_pointer(monkeypatch, secrets)
    p.ring("W")
    assert p.has_ring and wait(lambda: "RW" in board.lines)
    p.ring("F", 270.4)
    assert wait(lambda: "RF270" in board.lines)
    board.lines.clear()
    board.restart()
    assert wait(lambda: "RW" in board.lines, 10)  # watching again after reconnecting


def test_no_ring_commands_for_older_firmware(monkeypatch, secrets):
    board, p = board_pointer(monkeypatch, secrets, ring=False)
    p.ring("W")
    p.ring("T")  # older firmware would read this as a tilt
    p.ring("F", 10)
    p.send("?")
    assert wait(lambda: "?" in board.lines)
    assert not any(line.startswith("R") for line in board.lines)


def test_ring_angle(monkeypatch):
    monkeypatch.setattr(app, "RING_LED0_DEG", 90)
    monkeypatch.setattr(app, "RING_CLOCKWISE", True)
    spot = lambda x, y: {"cx": x, "cy": y, "frame_w": 1000, "frame_h": 600}
    # Seen from the front, the camera's right is the ring's left: LED 12 of 16.
    assert round(app.ring_angle(spot(900, 300))) == 270
    assert round(app.ring_angle(spot(100, 300))) == 90   # camera's left: LED 4
    assert round(app.ring_angle(spot(500, 550))) == 180  # below: LED 8
    assert round(app.ring_angle(spot(500, 50))) == 0     # above: LED 0
    monkeypatch.setattr(app, "RING_CLOCKWISE", False)
    assert round(app.ring_angle(spot(900, 300))) == 90
