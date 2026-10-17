import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import serial
import serial.tools.list_ports
import yaml

from src.core.exceptions import (
    SerialConnectionError,
    SerialProtocolError,
    SerialTimeoutError,
)


class SerialClient:
    """Robust, thread-safe serial transport client for ESP32 pan/tilt controller."""

    def __init__(
        self,
        port: Optional[str] = None,
        baud_rate: int = 115200,
        timeout: float = 1.0,
        handshake_timeout: float = 3.0,
        auto_connect: bool = True,
    ):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.handshake_timeout = handshake_timeout
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.RLock()

        if auto_connect:
            self.connect()

    def _resolve_port(self) -> str:
        """Resolve serial port from arguments, settings.yaml, or auto-detection."""
        if self.port:
            return self.port

        # Attempt to read from config/settings.yaml
        config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
        if config_path.is_file():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                    configured_port = cfg.get("serial", {}).get("port")
                    if configured_port:
                        # Check if configured port actually exists on system
                        available_ports = [p.device for p in serial.tools.list_ports.comports()]
                        if configured_port in available_ports or sys.platform != "win32":
                            return configured_port
            except Exception:
                pass

        # Auto-detect from available system serial ports
        ports = serial.tools.list_ports.comports()
        if not ports:
            raise SerialConnectionError("No active serial ports detected on the system.")

        # Prefer common USB-UART bridges (CP210x, CH340, FTDI, etc.)
        for p in ports:
            desc = (p.description or "").lower()
            if any(k in desc for k in ["cp210", "ch340", "ftdi", "uart", "usb serial"]):
                return p.device

        # Fallback to first available port
        return ports[0].device

    def connect(self) -> None:
        """Establish serial connection and perform handshake waiting for READY."""
        with self._lock:
            resolved_port = self._resolve_port()
            self.port = resolved_port

            try:
                self._serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baud_rate,
                    timeout=self.timeout,
                    write_timeout=self.timeout,
                )
            except (serial.SerialException, OSError) as e:
                raise SerialConnectionError(f"Failed to open serial port {self.port}: {e}") from e

            # Wait for ESP32 reset / handshake READY
            self._wait_for_ready(self.handshake_timeout)

    def _wait_for_ready(self, timeout_sec: float) -> None:
        """Wait up to timeout_sec for the ESP32 to send READY on boot."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                line_bytes = self._serial.readline()
                if not line_bytes:
                    time.sleep(0.01)
                    continue
                line = line_bytes.decode("ascii", errors="replace").strip()
                if line == "READY":
                    return
            except (serial.SerialException, OSError) as e:
                raise SerialConnectionError(f"Serial communication error during handshake: {e}") from e

        raise SerialTimeoutError(f"Handshake timed out waiting for 'READY' from ESP32 after {timeout_sec:.1f}s")

    def _send_command(self, cmd: str) -> str:
        """Send an ASCII command and return the single-line response."""
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                raise SerialConnectionError("Serial port is not open.")

            formatted = f"{cmd.strip()}\n"
            try:
                self._serial.write(formatted.encode("ascii"))
                self._serial.flush()

                line_bytes = self._serial.readline()
                if not line_bytes:
                    raise SerialTimeoutError(f"Timeout waiting for response to command: '{cmd.strip()}'")

                line = line_bytes.decode("ascii", errors="replace").strip()
                if line.startswith("ERR:"):
                    raise SerialProtocolError(f"Device returned error for '{cmd.strip()}': {line}")

                return line
            except (serial.SerialTimeoutException, SerialTimeoutError) as e:
                raise SerialTimeoutError(str(e)) from e
            except (serial.SerialException, OSError) as e:
                raise SerialConnectionError(f"Serial error executing '{cmd.strip()}': {e}") from e

    def ping(self) -> bool:
        """Health check verifying microcontroller responsiveness."""
        response = self._send_command("PING")
        if response == "PONG":
            return True
        raise SerialProtocolError(f"Expected 'PONG', got '{response}'")

    def set_laser(self, state: bool) -> bool:
        """Toggle laser power state (True=ON, False=OFF). Returns current state."""
        cmd = f"LASER:{1 if state else 0}"
        response = self._send_command(cmd)
        match = re.match(r"^OK:LASER=(\d+)$", response)
        if match:
            return bool(int(match.group(1)))
        raise SerialProtocolError(f"Unexpected response to {cmd}: '{response}'")

    def set_position(self, pan: int, tilt: int) -> Tuple[int, int]:
        """Set absolute servo angles in degrees. Returns clamped (pan, tilt)."""
        cmd = f"POS:{int(pan)},{int(tilt)}"
        response = self._send_command(cmd)
        match = re.match(r"^OK:POS=(\d+),(\d+)$", response)
        if match:
            return int(match.group(1)), int(match.group(2))
        raise SerialProtocolError(f"Unexpected response to {cmd}: '{response}'")

    def step(self, d_pan: int, d_tilt: int) -> Tuple[int, int]:
        """Step servos by relative deltas. Returns updated (pan, tilt)."""
        cmd = f"STEP:{int(d_pan)},{int(d_tilt)}"
        response = self._send_command(cmd)
        match = re.match(r"^OK:STEP=(\d+),(\d+)$", response)
        if match:
            return int(match.group(1)), int(match.group(2))
        raise SerialProtocolError(f"Unexpected response to {cmd}: '{response}'")

    def get_limits(self) -> Tuple[int, int, int, int]:
        """Query configured angle limits. Returns (pan_min, pan_max, tilt_min, tilt_max)."""
        response = self._send_command("LIMITS?")
        match = re.match(r"^OK:LIMITS=(\d+),(\d+),(\d+),(\d+)$", response)
        if match:
            return (
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
            )
        raise SerialProtocolError(f"Unexpected response to LIMITS?: '{response}'")

    def close(self) -> None:
        """Close serial connection safely."""
        with self._lock:
            if self._serial is not None and self._serial.is_open:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None

    def __enter__(self) -> "SerialClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
