"""
The link to the ESP32 pan/tilt laser pointer (firmware/esp32_controller), over USB or Wi-Fi:
commands P<deg> T<deg> L0/L1, C, ?, and R... for the LED ring; replies "OK P92.5 T125.0 L0 R1".

Wi-Fi: the network and a key are in firmware/esp32_controller/src/secrets.h (not in git; copy
secrets.example.h). The board is built with that file, and this reads the key from it.
"""

import glob
import os
import re
import select
import socket
import threading
import time

POINTER_PORT, BEACON_PORT = 3333, 3334  # keep in sync with the firmware
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo
SECRETS_PATH = os.path.join(ROOT, "firmware", "esp32_controller", "src", "secrets.h")


def wifi_key(path=SECRETS_PATH):
    """(key, hostname) for reaching the ESP32 over Wi-Fi, from the firmware's secrets.h (the
    board is built with the same file, so they match), or None if no network is set there."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    string = r'"(?:[^"\\]|\\.)*"'
    text = re.sub(string + r"|//[^\n]*", lambda m: m[0] if m[0][0] == '"' else "", text)  # no comments
    key = re.search(r'#define\s+LINK_KEY\s+"([^"]+)"', text)
    host = re.search(r'#define\s+HOSTNAME\s+"([^"]+)"', text)
    networks = re.findall(r'\{\s*"((?:[^"\\]|\\.)*)"\s*,\s*' + string + r"\s*\}", text)  # {"name", "pw"}
    if not key or not any(networks):
        return None
    return key.group(1), host.group(1) if host else "ctrlf"


class NetLink:
    """The ESP32 over Wi-Fi, with the calls Pointer makes on a serial port (write, read,
    in_waiting, flush). A thread finds the board (it announces itself on BEACON_PORT every
    2 s; else <hostname>.local), connects, sends the key, collects what the board says, and
    sends "?" every HEARTBEAT_SECS so both ends notice when the link dies. Then it
    reconnects; commands sent meanwhile are lost, and the board switches the laser off."""

    HEARTBEAT_SECS = 2
    DEAD_SECS = 6  # heard nothing for this long: the link is dead

    def __init__(self, host, hostname, key, on_hello):
        self.host, self.hostname, self.key = host, hostname, key
        self.on_hello = on_hello  # called with the board's "OK P.. T.. L.." once connected
        self.up = threading.Event()
        self.busy = False     # another program is using the board
        self.refused = False  # the board has a different key
        self.lock = threading.RLock()
        self.sock, self.rx = None, b""
        self.last_tx = self.last_rx = 0.0
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def in_waiting(self):
        return len(self.rx)

    def read(self, n=1):
        with self.lock:
            data, self.rx = self.rx[:n], self.rx[n:]
        return data

    def write(self, data):
        with self.lock:
            if self.sock is None:
                return 0
            try:
                self.sock.sendall(data)
                self.last_tx = time.time()
            except OSError:
                self._drop()
        return len(data)

    def flush(self):
        pass  # write() has sent it already

    def _drop(self):
        with self.lock:
            if self.sock is not None:
                self.sock.close()
                self.sock = None
            self.up.clear()

    def _find(self):
        """The board's address: the host given, the one it announces, or its .local name."""
        if self.host:
            return self.host
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                udp.bind(("", BEACON_PORT))
                end = time.time() + 3
                while time.time() < end:
                    udp.settimeout(max(0.01, end - time.time()))
                    data, (ip, _) = udp.recvfrom(128)
                    if data.startswith(b"CTRLF "):
                        return ip
        except OSError:  # timed out (or the port is taken): try its name
            pass
        return f"{self.hostname}.local"

    def _connect(self):
        """(socket, address, reply), or (None, the board's "ERR ..." reply, reply) if it
        turned us down."""
        where = self._find()
        sock = socket.create_connection((where, POINTER_PORT), timeout=3)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # send commands at once
            sock.sendall(f"KEY {self.key}\n".encode())
            reply, end = "", time.time() + 3
            while "\n" not in reply and time.time() < end:
                chunk = sock.recv(256)
                if not chunk:
                    break
                reply += chunk.decode(errors="ignore")
        except OSError:
            sock.close()
            raise
        if reply.startswith("OK P"):
            sock.settimeout(0.5)  # a stuck write shouldn't freeze the video for long
            return sock, where, reply
        sock.close()
        if reply.startswith("ERR"):
            return None, reply.strip(), reply
        raise OSError(f"unexpected reply {reply!r}")

    def _run(self):
        while True:
            try:
                sock, where, reply = self._connect()
            except OSError:
                time.sleep(1)
                continue
            if sock is None and "busy" in where:
                if not self.busy:
                    print("\n(The ESP32 is busy with another program over Wi-Fi: is ctrlf.py or "
                          "calibrate.py already running? Waiting for it...)")
                    self.busy = True
                time.sleep(2)
                continue
            if sock is None:
                self.refused = True
                print("\n(The ESP32 turned down the Wi-Fi key: upload the firmware again, so it "
                      "has the key that's in secrets.h. Laser control off.)")
                return
            self.busy = False
            with self.lock:
                self.sock, self.rx = sock, b""
                self.last_rx = self.last_tx = time.time()
            print(f"\n(Pointer connected over Wi-Fi: {where})")
            self.on_hello(reply)  # before it counts as up, so the board's reply is known by then
            with self.lock:
                if self.sock is sock:
                    self.up.set()
            self._serve(sock)
            print("\n(Lost the ESP32 on Wi-Fi: reconnecting...)")

    def _serve(self, sock):
        """Collect what the board says and keep the link checked, until it drops."""
        while self.sock is sock and time.time() - self.last_rx < self.DEAD_SECS:
            if time.time() - self.last_tx > self.HEARTBEAT_SECS:
                self.write(b"?\n")
            try:
                data = sock.recv(4096) if select.select([sock], [], [], 0.2)[0] else None
            except (OSError, ValueError):  # closed meanwhile by write()
                break
            if data == b"":  # the board closed it
                break
            if data:
                with self.lock:
                    self.rx = (self.rx + data)[-65536:]
                    self.last_rx = time.time()
        self._drop()


# USB vendor IDs of ESP32 boards' USB ports: Espressif's own (ESP32-S3 native USB), and the
# USB-serial chips on DevKits (Silicon Labs CP210x, WCH CH340/CH9102, FTDI).
ESP_VIDS = {0x303A, 0x10C4, 0x1A86, 0x0403}


def find_ports():
    """Serial ports that look like an ESP32, best guess first: /dev/cu.usbmodem... on a
    Mac, COM3... on Windows, /dev/ttyACM0 or /dev/ttyUSB0 on Linux. CTRLF_SERIAL_PORT
    (environment or .env) picks one."""
    from src.machine import setting

    chosen = setting("SERIAL_PORT")
    if chosen:
        return [chosen]
    try:
        from serial.tools import list_ports

        found = [p for p in list_ports.comports() if p.vid in ESP_VIDS]
        if found:
            return [p.device for p in sorted(found, key=lambda p: p.vid != 0x303A)]
    except ImportError:
        pass
    return sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
                  + glob.glob("/dev/cu.wchusbserial*"))


class Pointer:
    """Talks to the ESP32. link: "usb", "wifi", or "auto" = USB when it's plugged into the
    Mac, else Wi-Fi. host: its Wi-Fi address, or None to find it. Optional: everything else
    works without it."""

    def __init__(self, link="auto", host=None, secrets=SECRETS_PATH):
        self.link, self.host, self.secrets = link, host, secrets
        self.ser = None  # the serial port, or a NetLink (same calls)
        self.pan, self.tilt = 90, 125  # last commanded position (the firmware reports it)
        self.warned = False
        self.has_ring = False  # the firmware says "R1" if it has the LED ring
        self.ring_base = None  # "W" or "0": sent again after reconnecting
        if link in ("usb", "auto"):
            self._open_usb()
        if self.ser is None and link in ("wifi", "auto"):
            self._open_wifi()

    def _hello(self, reply):
        """The board's "OK P.. T.. L.. R1": where it's pointing, and whether it has the ring."""
        m = re.search(r"OK P([\d.]+) T([\d.]+).*", reply)  # (over USB, after its boot messages)
        self.pan, self.tilt = float(m.group(1)), float(m.group(2))
        self.has_ring = " R1" in m.group(0)
        if self.ring_base:  # it forgets when it restarts or loses us
            self.ring(self.ring_base)

    def ring(self, show, angle=None):
        """The LED ring: "W" watching, "0" off, "L" listening, "T" thinking, "I" done with
        those, "N" not seen, and pointing toward angle (app.ring_angle): "F" found, "S" last
        seen, "A" forgot it. Does nothing without a ring (older firmware would take "RT"
        as a tilt)."""
        if show in ("W", "0"):
            self.ring_base = show
        if self.has_ring and self.ser is not None:  # (over Wi-Fi, lost while the link is down)
            self.ser.write(f"R{show}{'' if angle is None else round(angle)}\n".encode())

    def _open_usb(self):
        try:
            import serial
        except ImportError:
            print("(pyserial not installed: no USB link to the ESP32)")
            return
        ports = find_ports()
        if not ports:
            if self.link == "usb":
                print("(No ESP32 plugged in: laser control off. That's fine for now.)")
            return
        try:
            self.ser = serial.Serial(ports[0], 115200, timeout=0)
        except Exception as e:
            print(f"(Couldn't open {ports[0]}: {e})")
            return
        # Opening the port can reboot the ESP32 (it wiggles the servos for ~2 s first),
        # so keep asking for its position until it answers.
        reply, t0, asked = "", time.time(), 0
        while time.time() - t0 < 4 and "OK P" not in reply:
            if time.time() - asked > 0.5:
                self.ser.write(b"?\n")
                asked = time.time()
            time.sleep(0.05)
            reply += self.ser.read(self.ser.in_waiting or 1).decode(errors="ignore")
        if "OK P" in reply:
            self._hello(reply)
            print(f"(Pointer connected over USB: {ports[0]})")
        else:  # some other sketch: don't send it anything
            print(f"(Something is on {ports[0]} but it doesn't answer like esp32_controller. "
                  f"Is the firmware uploaded? " + ("Trying Wi-Fi.)" if self.link == "auto"
                                                   else "Laser control off.)"))
            self.ser.close()
            self.ser = None

    def _open_wifi(self):
        wifi = wifi_key(self.secrets)
        if not wifi:
            print("(No ESP32 on USB, and no Wi-Fi network in firmware/esp32_controller/src/"
                  "secrets.h: laser control off. That's fine for now.)")
            return
        self.ser = NetLink(self.host, wifi[1], wifi[0], self._hello)
        if not self.ser.up.wait(4) and not (self.ser.busy or self.ser.refused):
            print("(Looking for the ESP32 on Wi-Fi: it connects by itself once the board is on "
                  "and on the same network as the Mac.)")

    @property
    def connected(self):
        if isinstance(self.ser, NetLink):
            return self.ser.up.is_set()
        return self.ser is not None

    def send(self, cmd):
        if not self.connected:
            if not self.warned:  # once, not for every command while the link is down
                print("(no ESP32 pointer connected)")
                self.warned = True
            return
        self.warned = False
        self.ser.write((cmd + "\n").encode())

    def move(self, pan, tilt, laser=None):
        """Send the head to (pan, tilt), clamped like the firmware does. Returns roughly
        how many seconds the glide takes."""
        from src.vision.laser_tracker import PAN_MAX, PAN_MIN, SECS_PER_DEG, TILT_MAX, TILT_MIN

        pan = round(min(max(pan, PAN_MIN), PAN_MAX), 1)  # the firmware takes tenths
        tilt = round(min(max(tilt, TILT_MIN), TILT_MAX), 1)
        secs = max(abs(pan - self.pan), abs(tilt - self.tilt)) * SECS_PER_DEG
        self.pan, self.tilt = pan, tilt
        self.send(f"P{pan:g} T{tilt:g}" + ("" if laser is None else f" L{int(laser)}"))
        return secs

    def poll(self):
        """Print anything the ESP32 says (e.g. 'laser auto-off'), except the "OK" replies."""
        if self.ser and self.ser.in_waiting:
            for line in self.ser.read(self.ser.in_waiting).decode(errors="ignore").splitlines():
                if line.strip() and not line.startswith("OK "):
                    print(f"[esp32] {line.strip()}")
