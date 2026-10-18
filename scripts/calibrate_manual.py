"""
Ctrl+F for Your Room: laser calibration by hand, for when the camera can't see the dot
(too small or too far away for it). You steer the laser with the keyboard and look at
the room; whenever the dot sits on something you can pick out in the camera's picture (a
chair leg, a table corner, a seam in the carpet), click that spot in the picture. Each click
pairs where the head points with where that is in the picture. 10-15 clicks spread over
the floor and the furniture are plenty. F fits and saves the calibration; T then lets you
click anywhere and see where it points. The app points by it without watching for the dot.

Run it when the camera and the head are in their final places, and again whenever either
one moves. Nobody in the room while the laser is on, and nobody looks toward the head.

    venv/bin/python scripts/calibrate_manual.py              (--camera webcam: see ctrlf.py)
    venv/bin/python scripts/calibrate_manual.py --more       keep the clicks from last time

Keys, in the video window:
    W / S    tilt: farther / nearer        A / D    pan one way / the other
    capitals (shift) = small steps         arrow keys work too
    L        laser on / off
    click    the dot is here (laser on)    U        undo the last click
    F        fit and save (8 clicks or more)
    T        test mode on / off: a click points the laser there
    Esc, Q   quit (the laser goes off)
--share view.jpg saves a copy of the window every 2 s, for someone helping from elsewhere.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

import cv2
import numpy as np

from src.app import CAMERA_LAG_SECS, DISPLAY_W, POINTER_HOST, POINTER_LINK, camera_option
from src.hardware.serial_client import Pointer
from src.vision.camera import open_camera, per_camera
from src.vision.laser_tracker import CALIB_PATH, TILT_MAX, TILT_MIN, Calibration

WINDOW = "Ctrl+F hand calibration (Esc to stop)"
STEP, SMALL_STEP = 2.0, 0.5  # degrees per key press
START = (90, 135)            # well down: the dot starts on the floor
# The head sits up high, so a shallow tilt crosses the room at head height. It never goes
# shallower than this here (--min-tilt changes it; the firmware's TILT_MIN is the backstop).
MIN_TILT = TILT_MIN
KEEP_ON_SECS = 8  # re-sends L1 this often: the firmware turns the laser off after 15 s idle
# What cv2.waitKeyEx gives for the arrow keys: Mac, Windows, Linux.
UP, DOWN = {63232, 2490368, 65362}, {63233, 2621440, 65364}
LEFT, RIGHT = {63234, 2424832, 65361}, {63235, 2555904, 65363}


FONT, SIZE, LINE, PAD = cv2.FONT_HERSHEY_SIMPLEX, 0.6, 26, 8


def wrap(text, width):
    """text split into lines that fit width pixels."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and cv2.getTextSize(trial, FONT, SIZE, 1)[0][0] > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def banner(lines, width):
    """A dark bar with lines of text on it, width pixels wide."""
    bar = np.full((LINE * len(lines) + 2 * PAD, width, 3), 32, np.uint8)
    for i, text in enumerate(lines):
        cv2.putText(bar, text, (12, PAD + LINE * i + 18), FONT, SIZE,
                    (255, 255, 255) if i else (0, 255, 255), 1, cv2.LINE_AA)
    return bar


class Session:
    def __init__(self, pointer, size, path, min_tilt, points=()):
        self.pointer, self.size, self.path, self.min_tilt = pointer, size, path, min_tilt
        self.points = list(points)  # [(pan, tilt, x, y)]
        self.laser, self.last_l1 = False, 0.0
        self.calib, self.testing, self.target = None, False, None
        self.message = "L turns the laser on. Steer it with W A S D, click where the dot is."

    def move(self, pan, tilt):
        tilt = min(max(tilt, self.min_tilt), TILT_MAX)
        self.pointer.move(pan, tilt, laser=self.laser)  # (clamps pan to its limits too)
        self.last_l1 = time.time()

    def nudge(self, dpan, dtilt):
        self.target = None
        self.move(self.pointer.pan + dpan, self.pointer.tilt + dtilt)

    def set_laser(self, on):
        self.laser = on
        self.pointer.send("L1" if on else "L0")
        self.last_l1 = time.time()

    def keep_on(self):
        if self.laser and time.time() - self.last_l1 > KEEP_ON_SECS:
            self.pointer.send("L1")
            self.last_l1 = time.time()

    def click(self, x, y):
        if self.testing:
            if not self.calib:
                self.message = "Fit first (F), then test."
                return
            pan, tilt = self.calib.to_angles(x / self.size[0], y / self.size[1])
            self.laser = True
            self.move(pan, tilt)
            self.target = (x, y)
            self.message = (f"Pointing at the cross: pan {self.pointer.pan:g}, tilt "
                            f"{self.pointer.tilt:g}. Is the dot on it?")
            return
        if not self.laser:
            self.message = "Turn the laser on (L) and put the dot somewhere first."
            return
        self.points.append((self.pointer.pan, self.pointer.tilt, x, y))
        self.message = (f"Click {len(self.points)}: pan {self.pointer.pan:g}, tilt "
                        f"{self.pointer.tilt:g} -> ({x:.0f}, {y:.0f}). Now somewhere else.")

    def undo(self):
        if self.points:
            self.points.pop()
            self.message = f"Removed the last click; {len(self.points)} left."

    def fit(self):
        if len(self.points) < 8:
            self.message = f"{len(self.points)} clicks; need at least 8, spread out."
            return
        calib = Calibration.fit(self.points, self.size)
        calib.manual = True
        calib.save(self.path)
        self.calib = calib
        dropped = len(self.points) - len(calib.points)
        self.message = (f"Saved {os.path.basename(self.path)}: clicks agree to about "
                        f"{calib.error_px:.0f} px ({calib.error_deg:.1f} deg)"
                        + (f", {dropped} odd one(s) left out" if dropped else "")
                        + ". T to test it.")
        print(self.message)

    def show(self, frame):
        s = DISPLAY_W / frame.shape[1]
        view = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        kept = set(self.calib.points) if self.calib else None
        for i, (_, _, x, y) in enumerate(self.points, 1):
            odd = kept is not None and self.points[i - 1] not in kept
            color = (0, 0, 255) if odd else (0, 255, 0)
            cv2.circle(view, (int(x * s), int(y * s)), 7, color, 2)
            cv2.putText(view, str(i), (int(x * s) + 9, int(y * s) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        if self.calib:  # where the fit thinks the dot is now
            u, v = self.calib.to_image(self.pointer.pan, self.pointer.tilt)
            cv2.circle(view, (int(u * view.shape[1]), int(v * view.shape[0])), 12,
                       (0, 165, 255), 1)
        if self.target:
            x, y = int(self.target[0] * s), int(self.target[1] * s)
            cv2.drawMarker(view, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 24, 2)
        status = (f"{'TEST: a click points the laser there' if self.testing else 'Click = the dot is here'}"
                  f"    pan {self.pointer.pan:g}   tilt {self.pointer.tilt:g}"
                  f"    laser {'ON' if self.laser else 'off'}    {len(self.points)} clicks")
        top = [status] + wrap(self.message, view.shape[1] - 24)
        bottom = ["W/S farther/nearer   A/D turn   (shift: small steps)   L laser   U undo",
                  "F fit + save   T test mode   Esc quit"]
        # The text goes above and below the picture, so none of the picture is hidden.
        view = cv2.vconcat([banner(top, view.shape[1]), view, banner(bottom, view.shape[1])])
        cv2.imshow(WINDOW, view)
        self.view = view
        return s, LINE * len(top) + 2 * PAD  # (scale, where the picture starts in the window)


def main():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--more", action="store_true")
    p.add_argument("--min-tilt", type=float, default=MIN_TILT)
    p.add_argument("--share", metavar="FILE", help="save a copy of the window there every 2 s")
    own, rest = p.parse_known_args()
    source = camera_option(rest, "Laser calibration by hand.")
    path = per_camera(CALIB_PATH, source)
    earlier = Calibration.load(path) if own.more else None
    cap = open_camera(source, CAMERA_LAG_SECS)
    pointer = Pointer(POINTER_LINK, POINTER_HOST)
    t0 = time.time()
    while not pointer.connected and time.time() - t0 < 10:  # (Wi-Fi takes a moment)
        time.sleep(0.2)
    if not pointer.connected:
        raise SystemExit("No ESP32 pointer found: power it and put it on the hotspot, or plug "
                         "its COM port in. Close anything else using it (ctrlf.py, a Serial "
                         "Monitor).")
    ok, frame = cap.read()
    if not ok:
        raise SystemExit("The camera sends no picture.")
    session = Session(pointer, frame.shape[1::-1], path, max(own.min_tilt, TILT_MIN),
                      [tuple(p) for p in earlier.points] if earlier else ())
    if earlier:
        session.message = f"Kept {len(earlier.points)} clicks from last time (U removes them)."
    session.move(*START)
    clicks = []
    place = [1.0, 0]  # the picture's scale in the window, and its top edge

    def on_mouse(event, x, y, *_):
        s, top = place
        x, y = x / s, (y - top) / s
        if event == cv2.EVENT_LBUTTONDOWN and 0 <= x < session.size[0] and 0 <= y < session.size[1]:
            clicks.append((x, y))

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_mouse)
    print(__doc__.split("Keys, in the video window:")[1])
    said, shared = None, 0.0
    try:
        while True:
            if session.message != said:  # (also for whoever follows along in the terminal)
                said = session.message
                print(said, flush=True)
            if own.share and time.time() - shared > 2 and hasattr(session, "view"):
                cv2.imwrite(own.share, session.view)
                shared = time.time()
            ok, frame = cap.read()
            if not ok:
                raise SystemExit("The camera stopped sending pictures.")
            place[:] = session.show(frame)
            key = cv2.waitKeyEx(1)
            ch = chr(key) if 0 <= key < 128 else ""
            step = SMALL_STEP if ch.isupper() else STEP
            if key == 27 or ch in ("q", "Q"):
                break
            if ch in ("w", "W") or key in UP:
                session.nudge(0, -step)
            elif ch in ("s", "S") or key in DOWN:
                session.nudge(0, step)
            elif ch in ("a", "A") or key in LEFT:
                session.nudge(step, 0)
            elif ch in ("d", "D") or key in RIGHT:
                session.nudge(-step, 0)
            elif ch in ("l", "L"):
                session.set_laser(not session.laser)
            elif ch in ("u", "U"):
                session.undo()
            elif ch in ("f", "F"):
                session.fit()
            elif ch in ("t", "T"):
                session.testing = not session.testing
                session.message = ("Test mode: click anywhere in the picture." if session.testing
                                   else "Back to adding clicks.")
            while clicks:
                session.click(*clicks.pop(0))
            session.keep_on()
    except KeyboardInterrupt:
        pass
    finally:
        pointer.send("L0")
        time.sleep(0.3)
        cap.release()
        cv2.destroyAllWindows()
    if session.points and not session.calib:
        print(f"Quit without saving the {len(session.points)} clicks: F saves them.")


if __name__ == "__main__":
    main()
