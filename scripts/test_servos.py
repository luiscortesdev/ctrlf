#!/usr/bin/env python3
"""
scripts/test_servos.py - Live Hardware Sweep Diagnostic Tool

Validates ESP32 communication, pan/tilt servo actuation, laser switching,
and firmware safety clamp enforcement.

Usage:
    python scripts/test_servos.py [--port COM3] [--baud 115200]
"""

import argparse
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.exceptions import HardwareError
from src.hardware.serial_client import SerialClient


def run_diagnostics(port: str | None, baud: int) -> None:
    print("=" * 60)
    print("Ctrl+F: ESP32 Hardware Diagnostic & Servo Sweep")
    print("=" * 60)

    client = None
    try:
        # Step 1: Connect & Handshake
        print("\n[1/8] Connecting to ESP32 and waiting for READY handshake...")
        client = SerialClient(port=port, baud_rate=baud, handshake_timeout=5.0)
        print(f"      Connected successfully on port: {client.port}")

        # Step 2: Health check
        print("\n[2/8] Sending PING health check...")
        if client.ping():
            print("      Received PONG. Firmware is responsive.")

        # Step 3: Query limits
        print("\n[3/8] Querying firmware angle limits (LIMITS?)...")
        p_min, p_max, t_min, t_max = client.get_limits()
        print(f"      Firmware limits: Pan [{p_min}, {p_max}] deg | Tilt [{t_min}, {t_max}] deg")
        assert (p_min, p_max, t_min, t_max) == (10, 170, 0, 45), (
            f"Unexpected limits returned from firmware: {(p_min, p_max, t_min, t_max)}"
        )
        print("      Hard limit configuration verified.")

        # Step 4: Laser test
        print("\n[4/8] Testing laser diode (1-second pulse)...")
        client.set_laser(True)
        print("      Laser ON")
        time.sleep(1.0)
        client.set_laser(False)
        print("      Laser OFF")

        # Step 5: Pan sweep
        print("\n[5/8] Sweeping Pan servo (45 deg -> 135 deg -> 90 deg)...")
        for pan in range(45, 136, 5):
            client.set_position(pan, 20)
            time.sleep(0.03)
        for pan in range(135, 89, -5):
            client.set_position(pan, 20)
            time.sleep(0.03)
        print("      Pan sweep complete.")

        # Step 6: Tilt sweep
        print("\n[6/8] Sweeping Tilt servo (0 deg -> 40 deg -> 20 deg)...")
        for tilt in range(0, 41, 2):
            client.set_position(90, tilt)
            time.sleep(0.03)
        for tilt in range(40, 19, -2):
            client.set_position(90, tilt)
            time.sleep(0.03)
        print("      Tilt sweep complete.")

        # Step 7: Safety clamping check
        print("\n[7/8] Testing safety clamping: commanding Tilt = 70 deg (forbidden > 45)...")
        clamped_pan, clamped_tilt = client.set_position(90, 70)
        print(f"      Firmware clamped position to: Pan={clamped_pan}, Tilt={clamped_tilt}")
        assert clamped_tilt == 45, f"CRITICAL: Firmware failed to clamp tilt to 45 deg! Got {clamped_tilt}"
        print("      Physical safety verified: Tilt > 45 deg is strictly rejected by firmware.")

        print("\n" + "=" * 60)
        print("ALL HARDWARE TESTS PASSED SUCCESSFULLY!")
        print("=" * 60)

    except HardwareError as e:
        print(f"\n[ERROR] Hardware communication error: {e}", file=sys.stderr)
        sys.exit(1)
    except AssertionError as e:
        print(f"\n[FAIL] Assertion failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if client:
            print("\n[8/8] Teardown: Returning to home (90, 20) and shutting off laser...")
            try:
                client.set_laser(False)
                client.set_position(90, 20)
            except Exception:
                pass
            client.close()
            print("      Connection closed cleanly.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ESP32 Pan/Tilt & Laser Hardware Diagnostic")
    parser.add_argument("--port", type=str, default=None, help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    args = parser.parse_args()

    run_diagnostics(port=args.port, baud=args.baud)
