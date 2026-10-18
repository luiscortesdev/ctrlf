"""Finding the ESP32 on USB (Windows COM ports, Linux, a Mac) and opening it without
resetting it, against fake serial ports: run `pytest`."""

import glob
import time
from types import SimpleNamespace

import pytest
import serial
from serial.tools import list_ports

from src.hardware import serial_client
from src.hardware.serial_client import ESPRESSIF_VID, Pointer, usb_ports

OK = b"OK P90.0 T125.0 L0 R1\n"


def info(device, vid, description=""):
    return SimpleNamespace(device=device, vid=vid, description=description)


@pytest.fixture
def ports(monkeypatch):
    """Set which ports the laptop has: ports([...]) with info(...) entries."""
    monkeypatch.delenv("CTRLF_PORT", raising=False)
    monkeypatch.setattr(glob, "glob", lambda pattern: [])  # no Mac /dev/cu.* ports
    return lambda found: monkeypatch.setattr(list_ports, "comports", lambda: found)


@pytest.fixture
def fast_clock(monkeypatch):
    """time.sleep advances time.time instead of waiting (the 4 s handshake timeout)."""
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setattr(time, "sleep", lambda s: now.__setitem__(0, now[0] + s))


class FakeSerial:
    """serial.Serial as Pointer uses it. boards: {port: what it answers "?" with, or an
    exception to raise on open}. Records the DTR/RTS levels each port was opened with."""

    boards, opened = {}, []

    def __init__(self):
        self.port, self.baudrate, self.timeout = None, None, None
        self.dtr = self.rts = True  # pyserial's defaults
        self.rx, self.is_open = b"", False

    def open(self):
        answer = self.boards.get(self.port)
        if isinstance(answer, Exception):
            raise answer
        self.opened.append((self.port, self.dtr, self.rts))
        self.is_open = True

    def write(self, data):
        if b"?" in data and self.boards.get(self.port):
            self.rx += self.boards[self.port]
        return len(data)

    @property
    def in_waiting(self):
        return len(self.rx)

    def read(self, n=1):
        data, self.rx = self.rx[:n], self.rx[n:]
        return data

    def close(self):
        self.is_open = False


@pytest.fixture
def fake_serial(monkeypatch):
    FakeSerial.boards, FakeSerial.opened = {}, []
    monkeypatch.setattr(serial, "Serial", FakeSerial)
    return FakeSerial


def test_windows_ports_in_order(ports):
    ports([info("COM1", None, "Communications Port"),        # built-in, not USB
           info("COM12", None, "Standard Serial over Bluetooth link"),
           info("COM10", 0x10C4, "Silicon Labs CP210x"),
           info("COM4", ESPRESSIF_VID, "USB JTAG/serial debug unit"),
           info("COM3", 0x1A86, "USB-SERIAL CH340"),
           info("COM5", 0x046D, "a mouse or something")])
    # USB-serial chips by number (COM3 before COM10), the S3's own USB port last.
    assert usb_ports() == [("COM3", 0x1A86), ("COM10", 0x10C4), ("COM4", ESPRESSIF_VID)]


def test_port_chosen_by_hand(ports, monkeypatch):
    ports([info("COM3", 0x1A86)])
    assert usb_ports("COM7") == [("COM7", None)]
    monkeypatch.setenv("CTRLF_PORT", "COM9")
    assert usb_ports() == [("COM9", None)]


def test_mac_ports_still_found(monkeypatch):
    monkeypatch.delenv("CTRLF_PORT", raising=False)
    monkeypatch.setattr(glob, "glob", lambda p: ["/dev/cu.usbserial-110"] if p == "/dev/cu.usbserial*" else [])
    monkeypatch.setattr(list_ports, "comports", lambda: [
        info("/dev/cu.usbserial-110", 0x10C4), info("/dev/tty.usbserial-110", 0x10C4)])
    assert usb_ports() == [("/dev/cu.usbserial-110", None)]


def test_opens_without_resetting_the_board(ports, fake_serial, fast_clock):
    ports([info("COM3", 0x1A86)])
    fake_serial.boards = {"COM3": b"Ctrl+F pointer ready.\n" + OK}
    pointer = Pointer("usb")
    assert pointer.connected and pointer.ser.port == "COM3"
    assert (pointer.pan, pointer.tilt, pointer.has_ring) == (90, 125, True)
    assert fake_serial.opened == [("COM3", False, False)]  # DTR and RTS low from the start


def test_skips_a_busy_port(ports, fake_serial, fast_clock, capsys):
    ports([info("COM3", 0x1A86), info("COM6", 0x10C4)])
    fake_serial.boards = {"COM3": serial.SerialException(
                              "could not open port 'COM3': PermissionError(13, 'Access is denied.', None, 5)"),
                          "COM6": OK}
    pointer = Pointer("usb")
    assert pointer.connected and pointer.ser.port == "COM6"
    assert "Serial Monitor" in capsys.readouterr().out  # says what's probably holding COM3


def test_wrong_usb_socket(ports, fake_serial, fast_clock, capsys):
    """Plugged into the S3's own USB port: the firmware doesn't answer there."""
    ports([info("COM4", ESPRESSIF_VID)])
    fake_serial.boards = {"COM4": b""}
    pointer = Pointer("usb")
    assert not pointer.connected
    out = capsys.readouterr().out
    assert "labeled COM" in out and "Laser control off" in out


def test_no_board_at_all(ports, fake_serial, capsys):
    ports([])
    assert not Pointer("usb").connected
    assert "No ESP32 plugged in" in capsys.readouterr().out


def test_bench_script_reads_replies(fast_clock):
    """scripts/test_servos.py's ask(): the board's reply, past its other chatter."""
    import importlib.util
    import os

    path = os.path.join(serial_client.ROOT, "scripts", "test_servos.py")
    spec = importlib.util.spec_from_file_location("test_servos_script", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    ser = FakeSerial()
    ser.rx = b"laser auto-off\nOK P60.0 T90.0 L1 R1\n"
    pointer = SimpleNamespace(ser=ser, send=lambda cmd: None)
    assert script.ask(pointer, "T60") == (60.0, 90.0, True)
    ser.rx = b""
    assert script.ask(pointer, "?") is None  # no answer: None, after the timeout
