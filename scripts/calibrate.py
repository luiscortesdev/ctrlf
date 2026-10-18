"""
Ctrl+F for Your Room: laser calibration.

Run it once the camera and the pan/tilt head (with the laser) are in their final places,
and again whenever either one gets moved. It visits a grid of pan/tilt angles (16 by
default, over the table/floor), and at each one takes a picture with the laser off and one
with it on. The biggest brightening between them is the dot:

  - sure of it (bright, small, where the calibration so far expects it): taken by itself
  - not sure: the live view shows the difference in red, the laser blinks so you can spot
    it; CLICK the dot (Enter takes the yellow guess, S skips, R looks again)

So it never ends with nothing. It then fits the mapping (a homography with lens bend, or
a polynomial, whichever predicts left-out points best) and saves data/calibration.json.

    venv\\Scripts\\python scripts\\calibrate.py                     the Wi-Fi camera
    venv\\Scripts\\python scripts\\calibrate.py --camera webcam      a plugged-in camera
    --pans 60 120 20 --tilts 116 140 8    the grid (FROM TO STEP), default as shown
    --lag 2.0     seconds the camera runs behind (Tapo on a hotspot: 1.5-2; webcam: 0.3)
    --more        keep the points from last time (data/calibration-points.json)

Nobody in front of the head or looking toward it while it runs. Esc stops early and fits
what it has (6 points at least).
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

import cv2
import numpy as np

from src.app import CAMERA_LAG_SECS, DISPLAY_W, POINTER_HOST, POINTER_LINK, camera_option
from src.hardware.serial_client import Pointer
from src.vision.camera import per_camera
from src.vision.laser_tracker import CALIB_PATH, SETTLE_SECS, TILT_MAX, TILT_MIN, Calibration
from src.vision.rtsp_camera import open_latest

PANS, TILTS = (60, 120, 20), (116, 140, 8)  # FROM TO STEP: 4 x 4 = 16 points
AUTO_MIN = 35        # the dot must brighten by this much (grayscale, 0-255) to be taken alone...
AUTO_BRIGHT = 150    # ...and be this bright with the laser on...
AUTO_AREA = 300      # ...with at most this many pixels changed around it (more: a hand moved)...
AUTO_CHANGED = 3000  # ...and at most this many in the whole picture (more: someone moved)...
AUTO_AGREE = 120     # ...and within this many pixels of where the fit so far expects it
FRAMES = 3           # frames averaged per picture (less noise)
MIN_FIT = 6
WINDOW = "Ctrl+F calibration (Esc = stop and fit)"
POINTS_PATH = os.path.join(os.path.dirname(CALIB_PATH), "calibration-points.json")


class View:
    """The window: shows frames, maps clicks back to full-size pixels, never freezes."""

    def __init__(self, cam):
        self.cam, self.clicks, self.scale = cam, [], 1.0
        cv2.namedWindow(WINDOW)
        cv2.setMouseCallback(WINDOW, self._mouse)
        self.marks = []  # (x, y, color) drawn on every frame

    def _mouse(self, event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.clicks.append((x / self.scale, y / self.scale))

    def show(self, frame=None, lines=(), extra=None):
        """Draw frame (or the newest one) with the marks and text; returns the key pressed."""
        if frame is None:
            frame = self.cam.get_frame()[0]
            if frame is None:
                return cv2.waitKey(10)
        self.scale = DISPLAY_W / frame.shape[1]
        view = cv2.resize(frame, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        if extra is not None:  # the brightening, in red (grown first, so shrinking keeps a 1 px dot)
            red = cv2.resize(cv2.dilate(extra, np.ones((5, 5), np.uint8)), (view.shape[1], view.shape[0]),
                             interpolation=cv2.INTER_NEAREST)
            view[..., 2] = np.maximum(view[..., 2], np.clip(red * 4, 0, 255).astype(np.uint8))
        for x, y, color in self.marks:
            cv2.circle(view, (int(x * self.scale), int(y * self.scale)), 7, color, 2)
        for i, text in enumerate(lines):
            cv2.putText(view, text, (12, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
            cv2.putText(view, text, (12, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.imshow(WINDOW, view)
        key = cv2.waitKey(1) & 0xFF
        if key == 27:
            raise KeyboardInterrupt
        return key

    def wait(self, secs, lines=()):
        end = time.time() + secs
        while time.time() < end:
            self.show(lines=lines)


def picture(view, pointer, laser, lag, lines):
    """Switch the laser, then average FRAMES frames that arrived after `lag` (so they show
    the room after the switch). Grayscale float."""
    pointer.send("L1" if laser else "L0")
    t, pics = time.time() + lag, []
    for _ in range(FRAMES):
        frame = view.cam.frame_after(t, idle=lambda: view.show(lines=lines))
        if frame is None:
            raise SystemExit("The camera stopped sending pictures.")
        t = view.cam.get_frame()[2] + 1e-3  # the next one after this
        pics.append(frame)
    color = np.mean(pics, axis=0).astype(np.float32)
    return cv2.cvtColor(color, cv2.COLOR_BGR2GRAY), color


def look(view, pointer, lag, lines):
    """(diff, on color picture): laser off picture, laser on picture, laser off."""
    off, _ = picture(view, pointer, False, lag, lines)
    on, on_color = picture(view, pointer, True, lag, lines)
    pointer.send("L0")
    return np.clip(on - off, 0, 255), on, on_color


def judge(diff, on, expect):
    """(x, y, sure, why): where the dot probably is, and whether to take it without asking."""
    _, top, _, (x, y) = cv2.minMaxLoc(diff)
    # The middle of the spot, finer than a pixel.
    y0, x0 = max(0, y - 3), max(0, x - 3)
    win = diff[y0:y + 4, x0:x + 4]
    w = np.clip(win - top / 2, 0, None) + 1e-6
    gy, gx = np.mgrid[y0:y0 + win.shape[0], x0:x0 + win.shape[1]]
    cx, cy = float((gx * w).sum() / w.sum()), float((gy * w).sum() / w.sum())
    around = diff[max(0, y - 30):y + 31, max(0, x - 30):x + 31]
    near = int((around > top / 2).sum())
    changed = int((diff > AUTO_MIN).sum())
    if top <= AUTO_MIN:
        return cx, cy, False, f"weak: brightened by {top:.0f} (needs {AUTO_MIN})"
    if on[y, x] < AUTO_BRIGHT:
        return cx, cy, False, f"not bright enough with the laser on ({on[y, x]:.0f})"
    if near > AUTO_AREA:
        return cx, cy, False, f"a big patch changed ({near} px): something moved?"
    if changed > AUTO_CHANGED:
        return cx, cy, False, f"{changed} px changed across the picture: someone moved?"
    if expect is not None and np.hypot(cx - expect[0], cy - expect[1]) > AUTO_AGREE:
        return cx, cy, False, f"not where the calibration so far expects it"
    return cx, cy, True, f"+{top:.0f}, {near} px"


def ask(view, pointer, diff, guess, lines):
    """The person finds the dot: live view with the brightening in red, the laser blinking.
    Returns (x, y), "skip" or "again"."""
    pointer.send("L1")
    view.clicks.clear()
    view.marks.append((guess[0], guess[1], (0, 255, 255)))
    blink, on = time.time(), True
    try:
        while True:
            if time.time() - blink > 0.5:  # blink so the dot is easy to spot in the video
                on, blink = not on, time.time()
                pointer.send("L1" if on else "L0")
            key = view.show(lines=lines + ["CLICK the dot  |  Enter: yellow guess  |  S skip  |  R look again"],
                            extra=diff)
            if view.clicks:
                return view.clicks.pop()
            if key in (13, 10):
                return guess
            if key in (ord("s"), ord("S")):
                return "skip"
            if key in (ord("r"), ord("R")):
                return "again"
    finally:
        view.marks.pop()
        pointer.send("L0")


def expected(points, size, pan, tilt):
    """Where the fit so far says the dot lands, or None if too few points yet."""
    if len(points) < MIN_FIT:
        return None
    try:
        u, v = Calibration.fit(points, size).to_image(pan, tilt)
        return u * size[0], v * size[1]
    except Exception:
        return None


def grid(pans, tilts):
    """Snaking order, so moves stay short."""
    ps = list(range(pans[0], pans[1] + 1, pans[2]))
    ts = [t for t in range(tilts[0], tilts[1] + 1, tilts[2]) if TILT_MIN <= t <= TILT_MAX]
    return [(p, t) for i, t in enumerate(ts) for p in (ps if i % 2 == 0 else ps[::-1])]


def main():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--pans", type=int, nargs=3, default=PANS, metavar=("FROM", "TO", "STEP"))
    p.add_argument("--tilts", type=int, nargs=3, default=TILTS, metavar=("FROM", "TO", "STEP"))
    p.add_argument("--lag", type=float, default=None)
    p.add_argument("--more", action="store_true")
    own, rest = p.parse_known_args()
    source = camera_option(rest, "Laser calibration.")
    lag = own.lag if own.lag is not None else (max(CAMERA_LAG_SECS, 1.5) if isinstance(source, str) else 0.3)
    path = per_camera(CALIB_PATH, source)
    points_path = per_camera(POINTS_PATH, source)
    points = []
    if own.more and os.path.exists(points_path):
        with open(points_path) as f:
            points = [tuple(pt) for pt in json.load(f)["points"]]
        print(f"Kept {len(points)} points from last time.")

    cam = open_latest(source, lag)
    pointer = Pointer(POINTER_LINK, POINTER_HOST)
    t0 = time.time()
    while not pointer.connected and time.time() - t0 < 10:  # (Wi-Fi takes a moment)
        time.sleep(0.2)
    if not pointer.connected:
        raise SystemExit("No ESP32 pointer found. Plug it in (COM port) or put it on Wi-Fi, and "
                         "close anything else using it (Serial Monitor, ctrlf.py).")
    first = cam.frame_after(0, timeout=10)
    if first is None:
        raise SystemExit("The camera sends no picture.")
    size = first.shape[1::-1]  # (width, height)
    print(f"Camera {size[0]}x{size[1]}, waiting {lag:.1f} s for it after every switch (--lag).")
    input("The laser is about to sweep the table. Make sure nobody is looking toward the "
          "head, then press Enter... ")

    view = View(cam)
    view.marks = [(x, y, (0, 255, 0)) for _, _, x, y in points]
    order = grid(own.pans, own.tilts)
    auto = asked = skipped = 0
    try:
        for n, (pan, tilt) in enumerate(order, 1):
            secs = pointer.move(pan, tilt, laser=False)
            lines = [f"point {n}/{len(order)}: pan {pointer.pan:g} tilt {pointer.tilt:g}   "
                     f"{len(points)} found ({auto} auto, {asked} clicked, {skipped} skipped)"]
            view.wait(secs + SETTLE_SECS, lines)
            expect = expected(points, size, pointer.pan, pointer.tilt)
            while True:
                diff, on, _ = look(view, pointer, lag, lines)
                x, y, sure, why = judge(diff, on, expect)
                if sure:
                    got = (x, y)
                    auto += 1
                    break
                print(f"  {n:2d}/{len(order)}  not sure ({why}): asking")
                got = ask(view, pointer, diff, (x, y), lines + [f"not sure: {why}"])
                if got != "again":
                    asked += got != "skip"
                    break
            if got == "skip":
                skipped += 1
                print(f"  {n:2d}/{len(order)}  pan {pointer.pan:g} tilt {pointer.tilt:g}  skipped")
                continue
            points.append((pointer.pan, pointer.tilt, float(got[0]), float(got[1])))
            view.marks.append((got[0], got[1], (0, 255, 0)))
            print(f"  {n:2d}/{len(order)}  pan {pointer.pan:g} tilt {pointer.tilt:g}  dot at "
                  f"({got[0]:.0f}, {got[1]:.0f})" + (f"  auto: {why}" if sure else "  clicked"))
            os.makedirs(os.path.dirname(points_path), exist_ok=True)
            with open(points_path, "w") as f:  # nothing lost if it stops halfway
                json.dump({"size": list(size), "points": points}, f)
    except KeyboardInterrupt:
        print("Stopped early.")
    finally:
        pointer.send("L0")

    if len(points) < MIN_FIT:
        cam.release()
        raise SystemExit(f"Only {len(points)} points; need at least {MIN_FIT}. They're kept in "
                         f"{points_path}: run again with --more to add to them.")
    # The camera saw the dot by itself at most points: the app may watch it and correct.
    calib = Calibration.fit(points, size, manual=auto < len(points),
                            dot_visible=auto >= max(4, len(points) // 2))
    calib.save(path)
    print(f"\nSaved {path}: {calib.kind} fit from {len(calib.points)} points "
          f"({auto} found by itself, {asked} clicked). At a new spot it typically misses by "
          f"{calib.error_px:.0f} px ({calib.error_deg:.2f} deg).")
    for pt, why in calib.dropped:
        print(f"  left out pan {pt[0]:g} tilt {pt[1]:g}: {why}")
    print("Check it: venv\\Scripts\\python scripts\\point_at_click.py")
    try:
        view.wait(2, ["Saved. Check it with point_at_click.py"])
    except KeyboardInterrupt:
        pass
    cam.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
