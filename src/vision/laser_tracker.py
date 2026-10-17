"""
Laser dot finding, calibration and closed-loop aiming for Ctrl+F for Your Room.

The camera is fixed and the laser sits on the pan/tilt head, so the app has to learn
which pan/tilt angles put the dot on a given pixel. calibrate.py sweeps the laser over a
grid of angles, finds the dot each time, and fits that mapping (calibration.json). When
you ask for something, Aimer points there using the mapping, looks for the dot, and
nudges it until it sits on the object.

The dot is found by comparing a frame with the laser off and one with it on, both taken
at the same pose: whatever got redder is the dot. That works in any lighting, as long as
nothing else moves in that instant.
"""

import json
import math
import os
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo
CALIB_PATH = os.path.join(ROOT, "data", "calibration.json")  # made by scripts/calibrate.py

# Keep in sync with esp32_pointer.ino.
PAN_MIN, PAN_MAX = 0, 180
TILT_MIN, TILT_MAX = 90, 160   # higher = down
SECS_PER_DEG = 0.010           # the firmware glides 1 degree per 10 ms

# Timing guesses; tune them once the laser is here (the camera runs a few frames behind).
SETTLE_SECS = 0.2              # after a move: let the head stop wobbling
LASER_SETTLE_SECS = 0.2        # after switching the laser, before trusting a frame
DOT_MIN = 25                   # the dot must brighten by at least this much (0-255)...
DOT_NOISE = 8                  # ...and by 8x the frame's noise level, whichever is more
MAX_CHANGED = 0.01             # more than 1% of the image changed = something moved: retry
MAX_DOT_AREA = 0.0008          # a dot is a small, compact spot: a brightened patch bigger
MAX_DOT_SIZE = 0.025           # than this fraction of the frame (about 40x40 px at 1080p),
                               # or longer than this fraction of its width (about 50 px), is
                               # a hand or the edge of an arm moving, not the laser
RED_RATIO = 1.5               # around a red dot, red brightens 1.5x+ more than green/blue;
                               # things moving brighten all three about equally (tune with
                               # the real laser: calibrate.py prints each dot's ratio)
SEARCH_RADIUS = 0.15           # once calibrated, look this far (fraction of frame width)
                               # around where the dot should be


# ------------------------------------------------------------------ finding the dot
def find_dot(off, on, near=None):  # (find_dot.red_ratio: the last spot's, for tuning)
    """Where did the laser dot appear? off/on: BGR frames from the same pose with the
    laser off and on. near: optional (x, y, radius) to search only around a spot.
    Returns (x, y) in pixels, or None if there's no clear dot."""
    ox = oy = 0  # where the searched area starts in the frame
    frame_area = off.shape[0] * off.shape[1]
    size = max(8, int(MAX_DOT_SIZE * off.shape[1]))
    if near:
        nx, ny, r = (int(v) for v in near)
        h, w = off.shape[:2]
        ox, oy = max(0, nx - r), max(0, ny - r)
        x1, y1 = min(w, nx + r + 1), min(h, ny + r + 1)
        if x1 - ox < 16 or y1 - oy < 16:  # the expected spot is off the image
            return None
        off, on = off[oy:y1, ox:x1], on[oy:y1, ox:x1]
    a = cv2.GaussianBlur(off, (5, 5), 0).astype(np.int16)
    b = cv2.GaussianBlur(on, (5, 5), 0).astype(np.int16)
    gain = b - a
    # A red rim around a center that's often blown out to white (on a white desk only
    # the brightening shows), so: red gain plus a quarter of the green and blue gain.
    up = np.clip(gain, 0, None).astype(np.float32)
    score = up[:, :, 2] + 0.25 * (up[:, :, 0] + up[:, :, 1])
    noise = 1.4826 * float(np.median(np.abs(gain[::4, ::4, 2])))  # typical frame-to-frame jitter
    need = max(DOT_MIN, DOT_NOISE * noise)
    _, peak, _, (x, y) = cv2.minMaxLoc(score)
    if peak < need or np.mean(score > need / 2) > MAX_CHANGED:
        return None
    # Is the bright spot around the peak small and compact?
    py0, px0 = max(0, y - size), max(0, x - size)
    # (A low cutoff, so a strip that crosses a bright area, where the gain is clipped,
    # still counts as one long piece.)
    cut = max(need / 2, peak / 4)
    patch = (score[py0:y + size + 1, px0:x + size + 1] > cut).astype(np.uint8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(patch, connectivity=8)
    blob = labels == labels[y - py0, x - px0]
    _, _, bw, bh, area = stats[labels[y - py0, x - px0]]
    if bw >= size or bh >= size or area > MAX_DOT_AREA * frame_area:
        return None
    # Is it red? Compare how much red rose with green and blue over the spot, leaving out
    # pixels where red was already near 255 and couldn't rise much (a bright desk).
    lit = b[py0:y + size + 1, px0:x + size + 1][blob]          # B, G, R with the laser on
    room = lit[:, 2] < 245
    if room.sum() >= 3:
        rise = up[py0:y + size + 1, px0:x + size + 1][blob][room].sum(0)
        find_dot.red_ratio = float(rise[2] / max(1.0, rise[0], rise[1]))
    else:  # the whole spot is blown out: fine if it looks red or white, not green or blue
        mean = lit.mean(0)
        find_dot.red_ratio = RED_RATIO if mean[2] >= max(mean[0], mean[1]) else 0.0
    if find_dot.red_ratio < RED_RATIO:
        return None
    # Center of the bright spot, finer than one pixel.
    r = 7
    y0, x0 = max(0, y - r), max(0, x - r)
    win = score[y0:y + r + 1, x0:x + r + 1]
    weights = np.clip(win - peak / 2, 0, None)
    ys, xs = np.mgrid[y0:y0 + win.shape[0], x0:x0 + win.shape[1]]
    total = weights.sum()
    return (ox + float((xs * weights).sum() / total), oy + float((ys * weights).sum() / total))


# ------------------------------------------------------------------ pixel <-> angles
def _terms(a, b, deg):
    """Polynomial terms a^i * b^j with i + j <= deg."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return np.stack([a ** i * b ** j for i in range(deg + 1) for j in range(deg + 1 - i)], -1)


def _degree(n):
    """How bendy a fit the number of points supports. On a simulated rig with lens
    distortion, degree 4 was off by 0.7 degrees typically, degree 2 by 2.4."""
    return 4 if n >= 30 else 3 if n >= 15 else 2


def _norm_angles(pan, tilt):
    return (np.asarray(pan, float) - 90) / 90, (np.asarray(tilt, float) - 125) / 35


class Calibration:
    """Maps a spot in the image to the pan/tilt angles that put the dot there, and back.
    Each direction is a polynomial fitted to the calibration points. Image positions are
    fractions of the frame (u = x / width, v = y / height), so the camera's resolution
    doesn't matter."""

    def __init__(self, deg, to_angles, to_image, points, error_deg, error_px, size):
        self.deg = deg
        self.A = np.asarray(to_angles, float)   # (terms, 2): image -> pan, tilt
        self.B = np.asarray(to_image, float)    # (terms, 2): angles -> u, v
        self.points, self.error_deg, self.error_px, self.size = points, error_deg, error_px, size

    @classmethod
    def fit(cls, points, size):
        """points: [(pan, tilt, x, y)] from the sweep; size: (width, height) of its frames."""
        P = np.asarray(points, float)
        w, h = size
        u, v = P[:, 2] / w, P[:, 3] / h
        deg = _degree(len(P))

        def fit_angles(keep):
            A = np.linalg.lstsq(_terms(u[keep], v[keep], deg), P[keep, :2], rcond=None)[0]
            return A, np.hypot(*(_terms(u, v, deg) @ A - P[:, :2]).T)  # degrees off, per point

        keep = np.ones(len(P), bool)
        for _ in range(3):  # drop points that don't agree with the rest (a false dot)
            A, miss = fit_angles(keep)
            new = miss <= max(1.0, 3 * np.median(miss[keep]))
            if new.sum() < 8 or (new == keep).all():
                break
            keep = new
        A, miss = fit_angles(keep)
        angle_terms = _terms(*_norm_angles(P[keep, 0], P[keep, 1]), deg)
        B = np.linalg.lstsq(angle_terms, np.stack([u[keep], v[keep]], -1), rcond=None)[0]
        err_deg = float(np.sqrt(np.mean(miss[keep] ** 2)))
        px = (angle_terms @ B) * [w, h] - P[keep, 2:]
        err_px = float(np.sqrt(np.mean(np.sum(px ** 2, 1))))
        kept = [tuple(p) for p, k in zip(points, keep) if k]
        return cls(deg, A, B, kept, err_deg, err_px, list(size))

    def to_angles(self, u, v):
        pan, tilt = _terms(u, v, self.deg) @ self.A
        return float(pan), float(tilt)

    def to_image(self, pan, tilt):
        u, v = _terms(*_norm_angles(pan, tilt), self.deg) @ self.B
        return float(u), float(v)

    def save(self, path=CALIB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"degree": self.deg, "to_angles": self.A.tolist(),
                       "to_image": self.B.tolist(),
                       "points": self.points, "error_deg": self.error_deg,
                       "error_px": self.error_px, "size": self.size,
                       "saved": time.strftime("%Y-%m-%d %H:%M")}, f, indent=1)

    @classmethod
    def load(cls, path=CALIB_PATH):
        if not os.path.exists(path):
            return None
        with open(path) as f:
            d = json.load(f)
        return cls(d["degree"], d["to_angles"], d["to_image"], d["points"], d["error_deg"],
                   d["error_px"], d["size"])


class RoughMap:
    """Stand-in until calibrate.py has run: a straight-line guess from ctrlf.py's settings.
    The closed loop can still fix small errors with it, if the directions are right."""

    def __init__(self, pan_center, pan_span, tilt_center, tilt_span):
        self.pc, self.ps, self.tc, self.ts = pan_center, pan_span, tilt_center, tilt_span

    def to_angles(self, u, v):
        return self.pc + (0.5 - u) * self.ps, self.tc + (v - 0.5) * self.ts


# ------------------------------------------------------------------ closed-loop aiming
class Aimer:
    """Points the laser at a pixel, then watches the camera and nudges the dot onto it.

    start() begins; update(frame, now) must be called with every camera frame and never
    blocks. Each try: move with the laser off, grab a frame, switch the laser on, grab
    another, find the dot, correct by the difference the mapping predicts."""

    MAX_TRIES = 5
    HOLD_SECS = 4.0  # how long the laser stays on the target afterwards (3-5 s to see it)

    def __init__(self, pointer, mapping, lag=0.0):
        # lag: how far the camera's picture runs behind; every wait gets that much longer
        self.pointer, self.map, self.lag = pointer, mapping, lag
        self.state, self.wait = "idle", 0.0
        self.target = self.dot = None
        self.message = ""

    def start(self, target, tol, size, now):
        """target: (x, y) pixel; tol: close enough, in pixels; size: (width, height)."""
        if not self.pointer.connected:
            return
        self.target, self.tol, self.size = target, tol, size
        self.tries, self.misses, self.dot, self.best = 0, 0, None, None
        if not hasattr(self.map, "to_image"):
            # Not calibrated: without knowing where the dot should be, anything moving in
            # the picture could pass for it, so point once and don't chase.
            self.pointer.move(*self._angles(target), laser=True)
            self._hold(now, "not calibrated yet, so pointing roughly (run calibrate.py)")
            return
        self._go(self._angles(target), now)

    def stop(self):
        if self.state != "idle":
            self.pointer.send("L0")
        self.state = "idle"

    def _angles(self, xy):
        return self.map.to_angles(xy[0] / self.size[0], xy[1] / self.size[1])

    def _go(self, angles, now):
        secs = self.pointer.move(*angles, laser=False)
        self.state, self.wait = "off", now + secs + SETTLE_SECS + self.lag

    def _hold(self, now, message):
        self.pointer.send("L1")
        self.state, self.wait, self.message = "hold", now + self.HOLD_SECS, message
        print(f"\n[laser] {message}")

    def update(self, frame, now):
        if self.state == "idle" or now < self.wait:
            return
        if self.state == "off":          # head has arrived, laser off: background frame
            self.off = frame
            self.pointer.send("L1")
            self.state, self.wait = "on", now + LASER_SETTLE_SECS + self.lag
        elif self.state == "on":         # laser on: find the dot and correct
            self._measure(frame, now)
        elif self.state == "hold":       # done showing it
            self.stop()

    def _measure(self, frame, now):
        near = None
        if hasattr(self.map, "to_image"):  # calibrated: look only where the dot should be
            u, v = self.map.to_image(self.pointer.pan, self.pointer.tilt)
            w, h = self.size
            near = (u * w, v * h, SEARCH_RADIUS * w)
        dot = find_dot(self.off, frame, near)
        if dot is None:
            self.misses += 1
            if self.misses <= 2:  # someone moved, or the camera lagged: look again
                self.pointer.send("L0")
                self.state, self.wait = "off", now + LASER_SETTLE_SECS + self.lag
            else:
                self._hold(now, "can't see the dot, so pointing by calibration alone")
            return
        self.tries += 1
        self.dot = dot
        err = math.dist(dot, self.target)
        here = (self.pointer.pan, self.pointer.tilt)
        if self.best is None or err < self.best[0]:
            self.best = (err, here)
        if err <= self.tol:
            return self._hold(now, f"on target after {self.tries} tries ({err:.0f} px off)")
        if self.tries >= self.MAX_TRIES:
            self.pointer.move(*self.best[1], laser=True)
            return self._hold(now, f"gave up after {self.tries} tries; best was "
                                   f"{self.best[0]:.0f} px off")
        (tp, tt), (dp, dt) = self._angles(self.target), self._angles(dot)
        pan, tilt = here[0] + (tp - dp), here[1] + (tt - dt)
        # As far as the limits let it go (a target above the table stays out of reach).
        pan, tilt = min(max(pan, PAN_MIN), PAN_MAX), min(max(tilt, TILT_MIN), TILT_MAX)
        if abs(pan - here[0]) < 0.05 and abs(tilt - here[1]) < 0.05:
            return self._hold(now, f"as close as the servos allow ({err:.0f} px off)")
        self._go((pan, tilt), now)
