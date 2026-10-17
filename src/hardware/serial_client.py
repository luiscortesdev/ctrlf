"""
The serial link to the ESP32 pan/tilt laser pointer (firmware/esp32_controller): commands
P<deg> T<deg> L0/L1, C, ?; replies "OK P92.5 T125.0 L0".
"""

import glob
import re
import time


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
    """Talks to the ESP32 over USB. Optional: everything else works without it."""

    def __init__(self):
        self.ser = None
        self.pan, self.tilt = 90, 125  # last commanded position (the firmware reports it)
        try:
            import serial
        except ImportError:
            print("(pyserial not installed: laser control off)")
            return
        ports = find_ports()
        if not ports:
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
            m = re.search(r"OK P([\d.]+) T([\d.]+)", reply)
            self.pan, self.tilt = float(m.group(1)), float(m.group(2))
            print(f"(Pointer connected on {ports[0]})")
        else:  # some other sketch: don't send it anything
            print(f"(Something is on {ports[0]} but it doesn't answer like esp32_pointer. "
                  f"Is the sketch uploaded? Laser control off.)")
            self.ser.close()
            self.ser = None

    @property
    def connected(self):
        return self.ser is not None

    def send(self, cmd):
        if not self.ser:
            print("(no ESP32 pointer connected)")
            return
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


