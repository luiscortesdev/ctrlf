"""
Ctrl+F for Your Room: first test of the pan/tilt head and laser, no camera needed.

Plug the ESP32's COM port into the laptop (servos on their own 5 V supply, grounds joined),
close anything else using the port (Serial Monitor, the app), then from the repo:
    venv\\Scripts\\python scripts\\test_servos.py               the checks, Enter between them
    venv\\Scripts\\python scripts\\test_servos.py --jog         move it yourself: p95 t140 p+5 l1 ...
    venv\\Scripts\\python scripts\\test_servos.py --port COM5   if it finds the wrong port
(On a Mac: venv/bin/python scripts/test_servos.py.)

The checks: where the board says it points; a pan sweep; a tilt sweep; the eye-safety
limit (it asks the board to tilt up past TILT_MIN and checks the board refuses, then you
look at where the head points); the laser for 2 s, pointing down at the desk. The laser
stays off otherwise. Keep faces away from the head anyway. Ctrl+C stops, laser off.
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

from src.hardware.serial_client import Pointer, usb_ports
from src.vision.laser_tracker import PAN_MAX, PAN_MIN, SECS_PER_DEG, TILT_MAX, TILT_MIN

CENTER = (90, (TILT_MIN + TILT_MAX) / 2)  # where the firmware's "C" goes
DESK_TILT = 145                           # well down: the laser lands on the desk


def ask(pointer, cmd, secs=1.0):
    """Send a command line; return (pan, tilt, laser) from the board's "OK P.. T.. L..",
    or None if it didn't answer. Prints anything else it says."""
    pointer.send(cmd)
    text, end = "", time.time() + secs
    while time.time() < end:
        text += pointer.ser.read(pointer.ser.in_waiting or 1).decode(errors="ignore")
        m = re.search(r"OK P(-?[\d.]+) T(-?[\d.]+) L(\d)[^\n]*\n", text)
        if m:
            for line in text[:m.start()].splitlines():
                if line.strip():
                    print(f"  [esp32] {line.strip()}")
            return float(m[1]), float(m[2]), m[3] == "1"
        time.sleep(0.02)
    print(f"  (no answer to {cmd!r}; it said {text.strip()!r})")
    return None


def go(pointer, pan, tilt):
    """Move (clamped to the limits, like the app does) and wait for the glide to end."""
    before = (pointer.pan, pointer.tilt)
    pointer.move(pan, tilt)
    reply = ask(pointer, "?")
    time.sleep(max(abs(pointer.pan - before[0]), abs(pointer.tilt - before[1])) * SECS_PER_DEG + 0.25)
    return reply


def center(pointer):
    """The firmware's "C": both servos to the middle, laser off."""
    reply = ask(pointer, "C")
    pointer.pan, pointer.tilt = CENTER
    return reply


def pause(args, what):
    print(f"\n== {what}")
    if not args.yes:
        input("   Enter to go on (Ctrl+C stops) ")


def checks(pointer, args):
    pause(args, f"Center: pan {CENTER[0]}, tilt {CENTER[1]:g}, laser off")
    print("  board says:", center(pointer))
    time.sleep(1)

    pause(args, "Pan sweep 90 -> 60 -> 120 -> 90 (laser off). Watch it turn smoothly both ways")
    for pan in (80, 70, 60, 70, 80, 90, 100, 110, 120, 110, 100, 90):
        print(f"  pan {pan:3d}: {go(pointer, pan, CENTER[1])}")
        time.sleep(0.2)

    pause(args, "Tilt sweep 125 -> 155 (down) -> 100 (up) -> 125 (laser off). Higher tilt = down")
    for tilt in (135, 145, 155, 145, 135, 125, 115, 105, 100, 110, 125):
        print(f"  tilt {tilt:3d}: {go(pointer, CENTER[0], tilt)}")
        time.sleep(0.2)

    pause(args, f"Eye-safety limit: asking the board to tilt up to {TILT_MIN - 30} (laser off)")
    reply = ask(pointer, f"T{TILT_MIN - 30}")  # straight to the board, past Pointer's own clamp
    pointer.tilt = reply[1] if reply else TILT_MIN
    time.sleep((CENTER[1] - TILT_MIN) * SECS_PER_DEG + 0.5)
    ok = reply is not None and reply[1] == TILT_MIN
    print(f"  board says {reply}: " + (f"refused, stopped at TILT_MIN = {TILT_MIN}. Good." if ok else
          f"expected tilt {TILT_MIN}. The firmware's TILT_MIN doesn't match src/vision/laser_tracker.py: "
          "upload the firmware again, or make them the same."))
    print("  LOOK at the head now: this is as high as it can ever point. If a beam from here would")
    print("  go above the table top (toward eyes), raise TILT_MIN in firmware/esp32_controller/src/")
    print("  main.cpp AND src/vision/laser_tracker.py, upload, and run this again.")
    if not args.yes:
        input("   Enter when you've looked ")
    reply = ask(pointer, f"T{TILT_MAX + 30}")
    print(f"  asked for tilt {TILT_MAX + 30}, board says {reply}: "
          + ("stopped at TILT_MAX. Good." if reply and reply[1] == TILT_MAX else f"expected {TILT_MAX}."))
    pointer.tilt = TILT_MAX
    time.sleep(0.6)
    go(pointer, *CENTER)

    if args.no_laser:
        print("\n(--no-laser: skipping the laser)")
    else:
        pause(args, f"Laser on for 2 s, pointing down at the desk (tilt {DESK_TILT})")
        go(pointer, CENTER[0], DESK_TILT)
        print("  board says:", ask(pointer, "L1"), " <- L should be True: look for the red dot")
        time.sleep(2)
        print("  board says:", ask(pointer, "L0"), " <- laser off")

    pause(args, "Back to center")
    center(pointer)
    print("\nDone. If anything moved the wrong way or juddered, check the 5 V supply, the 1000 uF")
    print("capacitor and the shared ground first. Next: python scripts/test_servos.py --jog")


JOG_HELP = """Commands: p95 / t140 (go to), p+5 / t-5 (nudge), c (center, laser off),
l1 / l0 (laser), ? (where), raw <firmware command> (e.g. raw RX), q (quit).
Angles are clamped like the app does: pan {pmin}-{pmax}, tilt {tmin}-{tmax} (higher = down)."""


def jog(pointer):
    print(JOG_HELP.format(pmin=PAN_MIN, pmax=PAN_MAX, tmin=TILT_MIN, tmax=TILT_MAX))
    print("The laser turns itself off 15 s after the last command.")
    while True:
        try:
            line = input(f"[pan {pointer.pan:g} tilt {pointer.tilt:g}] > ").strip().lower()
        except EOFError:
            return
        m = re.fullmatch(r"([pt])\s*([+-]?)\s*(\d+(?:\.\d+)?)", line)
        if line in ("q", "quit", "exit"):
            return
        elif m:
            axis, sign, value = m[1], m[2], float(m[3])
            now = pointer.pan if axis == "p" else pointer.tilt
            target = now + value if sign == "+" else now - value if sign == "-" else value
            pan, tilt = (target, pointer.tilt) if axis == "p" else (pointer.pan, target)
            print("  board says:", go(pointer, pan, tilt))
        elif line == "c":
            print("  board says:", center(pointer))
        elif line in ("l1", "l0", "?"):
            print("  board says:", ask(pointer, line.upper()))
        elif line.startswith("raw "):
            print("  board says:", ask(pointer, line[4:].upper()))
        elif line:
            print(JOG_HELP.format(pmin=PAN_MIN, pmax=PAN_MAX, tmin=TILT_MIN, tmax=TILT_MAX))


def main():
    parser = argparse.ArgumentParser(description="Test the pan/tilt head and laser over USB.")
    parser.add_argument("--port", help="serial port, e.g. COM5 (default: find it)")
    parser.add_argument("--jog", action="store_true", help="move it yourself instead of the checks")
    parser.add_argument("--yes", action="store_true", help="don't wait for Enter between checks")
    parser.add_argument("--no-laser", action="store_true", help="never switch the laser on")
    args = parser.parse_args()

    ports = usb_ports(args.port)
    print("Serial ports that could be the ESP32:", ", ".join(p for p, _ in ports) or "none")
    pointer = Pointer("usb", port=args.port)
    if not pointer.connected:
        print("\nNo ESP32 answering over USB. Check, in order:")
        print("  - the cable is in the board's port labeled COM (or UART), and it's a data cable,")
        print("    not a charge-only one (Device Manager > Ports (COM & LPT) should show it);")
        print("  - no Ports entry, or one with a yellow '!': install the USB-serial driver (CP210x")
        print("    from Silicon Labs, or CH343/CH340 from WCH, whichever chip is on the board);")
        print("  - the firmware is uploaded (see README: ESP32 pointer);")
        print("  - nothing else has the port open (Serial Monitor, pio device monitor, the app).")
        sys.exit(1)
    print(f"Board says it points at pan {pointer.pan:g}, tilt {pointer.tilt:g}"
          f"{', with the LED ring' if pointer.has_ring else ''}.")
    try:
        jog(pointer) if args.jog else checks(pointer, args)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        pointer.send("L0")
        pointer.ser.close()


if __name__ == "__main__":
    main()
