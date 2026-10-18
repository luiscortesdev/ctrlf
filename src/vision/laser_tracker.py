"""
Laser dot finding, calibration and closed-loop aiming for Ctrl+F for Your Room.

The camera is fixed and the laser sits on the pan/tilt head, so the app has to learn
which pan/tilt angles put the dot on a given pixel. A calibration (calibrate_guided.py,
calibrate_manual.py or calibrate.py) pairs angles with spots in the picture, and aim_map.py
fits the mapping. When you ask for something, Aimer points there using the mapping; if the
camera could see the dot while calibrating, it also looks for the dot and nudges it on.

The dot is found by blinking the laser at one pose (off, on, off) and comparing the
pictures: a spot that brightened against the off pictures before AND after is the dot.
Each phase averages the frames that arrive once the change must be in the picture (the
Wi-Fi camera runs a second or more behind), so it works in any lighting, with people
moving around, and for a dot only a pixel or two wide.
"""

import json
import math
import os
import time
from dataclasses import dataclass

import cv2
import numpy as np

from src.vision import aim_map

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo
CALIB_PATH = os.path.join(ROOT, "data", "calibration.json")  # made by the calibrate scripts

# The firmware allows pan 0-180 and tilt 90-160; these are the narrower software limits
# every move goes through (Pointer.move, Aimer, ObjectPointer).
PAN_MIN, PAN_MAX = 10, 170
# Higher tilt = down. TILT_MIN keeps the beam on the floor: mounted high in the corner of the
# room (Oct 17), the bottom of the far wall is at tilt ~110. The firmware's own limit is 90.
TILT_MIN, TILT_MAX = 112, 160
SECS_PER_DEG = 0.010           # the firmware glides 1 degree per 10 ms

SETTLE_SECS = 0.2              # after a move: let the head stop wobbling
LASER_SETTLE_SECS = 0.2        # after switching the laser, before trusting a frame
CAPTURE_SECS = 0.3             # then average this long's frames (noise and compression wash out)
NO_FRAMES_SECS = 2.0           # no frame for this long past a phase: give up on this look
DOT_MIN = 12                   # the dot must brighten at least this much (0-255, red-weighted)
DOT_MIN_ONE = 25               # ...with only one off/on pair (no second look to agree)
DOT_NOISE = 8                  # ...and at least 8x the picture's noise
MAX_DOT_AREA = 0.0008          # a dot is a small, compact spot: a brightened patch bigger
MAX_DOT_SIZE = 0.025           # than this fraction of the frame (about 40x40 px at 1080p),
                               # or longer than this fraction of its width (about 50 px), is
                               # a hand or the edge of an arm moving, not the laser
BUSY = 0.25                    # more than this share changed around a spot: part of a mover
CANDIDATES = 40                # small brightened spots checked, strongest first
RED_RATIO = 1.3                # one pair: red must rise 1.3x more than green and blue
RED_RATIO_BLINKS = 1.1         # pairs that agree: a bit more (compression smears a tiny dot's
                               # color). A blown-out white middle is judged by the rim around it.
GRAY = 2.0                     # channels this close on average: black-and-white night mode,
                               # where color can't be judged (so turn the clock overlay off)
SEARCH_RADIUS = 0.15           # once calibrated, look this far (fraction of frame width)
                               # around where the dot should be


# ------------------------------------------------------------------ finding the dot
@dataclass
class Dot:
    x: float
    y: float
    score: float       # how much it brightened (0-255, red-weighted), in its weakest pair
    snr: float         # score / the picture's noise
    red_ratio: float   # how much more red rose than green/blue (blown out: judged by color)
    area: int          # pixels in the spot
    saturated: bool    # its middle was blown out (255) with the laser on
    pairs: int         # off/on pairs that agreed


def locate_dot(pairs, near=None, full_size=None, origin=(0, 0)):
    """Where is the laser dot? pairs: [(off, on)], BGR pictures (or averages of several)
    with the laser off and on at the same pose. A spot counts only if it brightened in
    every pair, is small and compact, isn't part of something bigger changing around it,
    and didn't turn green or blue. near: (x, y, radius) to look only around there.
    full_size: (width, height) of the whole frame when the pictures are crops of it, and
    origin: where the crop starts in it (results are in whole-frame pixels).
    Returns a Dot or None; locate_dot.why says what it saw, either way (for tuning)."""
    h, w = pairs[0][0].shape[:2]
    fw, fh = full_size or (w, h)
    ox, oy = origin
    if near:
        nx, ny, r = (int(round(v)) for v in near)
        cx, cy = max(0, nx - ox - r), max(0, ny - oy - r)
        x1, y1 = min(w, nx - ox + r + 1), min(h, ny - oy + r + 1)
        if x1 - cx < 16 or y1 - cy < 16:
            locate_dot.why = "where the dot should be is off the picture"
            return None
        pairs = [(a[cy:y1, cx:x1], b[cy:y1, cx:x1]) for a, b in pairs]
        ox, oy = ox + cx, oy + cy
    gains = np.stack([b.astype(np.float32) - a.astype(np.float32) for a, b in pairs])
    up = np.clip(gains, 0, None)
    # A red rim around a middle that's often blown out to white (on a white desk only the
    # brightening shows), so: red gain plus a quarter of the green and blue gain.
    scores = up[..., 2] + 0.25 * (up[..., 0] + up[..., 1])
    k = 0 if len(pairs) <= 3 else 1  # the weakest pair (the 2nd weakest of 4+: one may be off)
    score = np.partition(scores, k, axis=0)[k] if len(pairs) > 1 else scores[0]
    noise = 1.4826 * float(np.median(np.abs(gains[:, ::2, ::2, 2])))  # typical jitter
    need = max(DOT_MIN if len(pairs) > 1 else DOT_MIN_ONE, DOT_NOISE * noise)
    if score.max() < need:
        locate_dot.why = (f"nothing brightened enough: the most was {score.max():.0f}, "
                          f"it needs {need:.0f} (noise {noise:.1f})")
        return None
    # Everything that brightened, in connected pieces (a low cutoff, so a strip crossing a
    # bright area, where the gain is clipped, still counts as one piece). A piece too big
    # or too long is a person or a screen, not the dot; the rest get one candidate each,
    # at their strongest pixel.
    size = max(8, int(MAX_DOT_SIZE * fw))
    changed = (score > need / 2).astype(np.uint8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(changed, connectivity=8)
    small = (stats[:, 2] < size) & (stats[:, 3] < size) & (stats[:, 4] <= MAX_DOT_AREA * fw * fh)
    small[0] = False  # (the background)
    big = changed.astype(bool) & ~small[labels]
    flat = np.flatnonzero((score >= need) & small[labels])
    flat = flat[np.argsort(-score.flat[flat], kind="stable")]
    _, first = np.unique(labels.flat[flat], return_index=True)
    best = flat[np.sort(first)][:CANDIDATES]
    on = np.mean([b for _, b in pairs], axis=0, dtype=np.float32)
    rise = up.mean(0)
    base = on - gains.mean(0)  # the laser-off picture
    gray = float(np.mean(np.abs(on[..., 2] - on[..., 1]) + np.abs(on[..., 1] - on[..., 0]))) < GRAY
    red_need = RED_RATIO if len(pairs) == 1 else RED_RATIO_BLINKS
    rejected = []
    for i in best:
        y, x = divmod(int(i), score.shape[1])
        peak = float(score[y, x])
        where = f"({ox + x}, {oy + y}) +{peak:.0f}"
        area = int(stats[labels[y, x], 4])
        py0, px0 = max(0, y - size), max(0, x - size)
        win = (slice(py0, y + size + 1), slice(px0, x + size + 1))
        blob = labels[win] == labels[y, x]
        if big[max(0, y - 6):y + 7, max(0, x - 6):x + 7].any():
            rejected.append(f"{where} next to something big changing")
            continue
        if ((score[win] > need / 2) & ~blob).mean() > BUSY:
            rejected.append(f"{where} part of something moving")
            continue
        # Is it red? How much red rose compared with green and blue over the spot, leaving
        # out pixels whose red was near 255 already (a bright desk: it couldn't rise). A
        # blown-out white middle is judged by the rim around it, where the laser's red
        # shows (a white light's rim is white).
        def ratio(r):
            return float(r[2] / max(1.0, r[0], r[1]))

        room = base[win][blob][:, 2] < 235
        red = ratio(rise[win][blob][room].sum(0)) if room.any() else 0.0
        if red < red_need:
            ring = cv2.dilate(blob.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & ~blob
            rim = rise[win][ring]
            if rim.sum() > need * len(rim) / 4:  # the rim brightened too: it has a say
                red = max(red, ratio(rim.sum(0)))
        if red < red_need and not gray:
            rejected.append(f"{where} not red ({red:.1f})")
            continue
        lit = on[win][blob]
        # The middle of the spot, finer than a pixel.
        rr = 3
        y0, x0 = max(0, y - rr), max(0, x - rr)
        cw = score[y0:y + rr + 1, x0:x + rr + 1]
        weights = np.clip(cw - peak / 2, 0, None) + 1e-6 * (cw >= peak)
        gy, gx = np.mgrid[y0:y0 + cw.shape[0], x0:x0 + cw.shape[1]]
        total = weights.sum()
        dot = Dot(ox + float((gx * weights).sum() / total), oy + float((gy * weights).sum() / total),
                  peak, peak / max(noise, 0.5), red, int(area), bool((lit >= 250).all(1).any()),
                  len(pairs))
        locate_dot.why = (f"dot at ({dot.x:.0f}, {dot.y:.0f}): +{peak:.0f} ({dot.snr:.0f}x noise), "
                          f"{area} px, red {red:.1f}" + (", blown out" if dot.saturated else "")
                          + (", black-and-white picture" if gray else "")
                          + (f"; passed over {len(rejected)}" if rejected else ""))
        return dot
    movers = "something big changed (people or screens); " if big.any() else ""
    locate_dot.why = (movers + "no spot looked like the dot: " + "; ".join(rejected[:6])
                      + (f"; and {len(rejected) - 6} more" if len(rejected) > 6 else ""))
    return None


def find_dot(off, on, near=None):
    """locate_dot for one off/on pair, as (x, y) or None (find_dot.red_ratio: the dot's)."""
    dot = locate_dot([(off, on)], near)
    find_dot.red_ratio = dot.red_ratio if dot else 0.0
    return (dot.x, dot.y) if dot else None


class Blink:
    """One look for the dot at the head's current pose. The laser goes off, on, off
    (PATTERN); in each phase, the frames that arrive once the switch must show in the
    picture (lag + LASER_SETTLE_SECS later) are averaged for `capture` seconds. Each on
    phase is compared with the off phase before AND after it, so something that moved
    during just one of them doesn't count.

    start(now, wait) begins (wait: the head is still gliding there); update(frame, now)
    with every camera frame never blocks and returns True once done; then .dot is a Dot
    (or None) and .why says what it saw. The laser is off at the end."""

    PATTERN = ("off", "on", "off")

    def __init__(self, pointer, lag, near=None, pattern=PATTERN, capture=CAPTURE_SECS):
        self.pointer, self.lag, self.near = pointer, lag, near
        self.pattern, self.capture = pattern, capture
        self.dot, self.why, self.done = None, "", False

    def start(self, now, wait=0.0):
        self.phase, self.sums, self.counts = 0, [], []
        self.crop = None
        self.dot, self.why, self.done = None, "", False
        self._begin(now, max(wait, LASER_SETTLE_SECS))

    def _begin(self, now, settle=LASER_SETTLE_SECS):
        self.pointer.send("L1" if self.pattern[self.phase] == "on" else "L0")
        self.from_t = now + settle + self.lag  # frames before this still show the old state
        self.until = self.from_t + self.capture
        self.sums.append(None)
        self.counts.append(0)

    def update(self, frame, now):
        if self.done:
            return True
        if frame is not None and now >= self.from_t:
            if self.crop is None:
                self._place(frame.shape)
            x0, y0, x1, y1 = self.crop
            part = frame[y0:y1, x0:x1].astype(np.float32)
            self.sums[-1] = part if self.sums[-1] is None else self.sums[-1] + part
            self.counts[-1] += 1
        if now >= self.until and self.counts[-1]:
            self.phase += 1
            if self.phase == len(self.pattern):
                self._finish()
            else:
                self._begin(now)
        elif now >= self.until + NO_FRAMES_SECS:
            self.pointer.send("L0")
            self.dot, self.why, self.done = None, "no pictures came from the camera", True
        return self.done

    def _place(self, shape):
        """Only the part of the picture around where the dot should be is kept."""
        h, w = shape[:2]
        self.size = (w, h)
        if self.near:
            nx, ny, r = self.near
            self.crop = (int(max(0, nx - r)), int(max(0, ny - r)),
                         int(min(w, nx + r + 1)), int(min(h, ny + r + 1)))
            if self.crop[2] - self.crop[0] < 16 or self.crop[3] - self.crop[1] < 16:
                self.crop = (0, 0, w, h)  # (expected off the picture: look everywhere)
        else:
            self.crop = (0, 0, w, h)

    def _finish(self):
        self.pointer.send("L0")
        self.done = True
        means = [s / c for s, c in zip(self.sums, self.counts)]
        pairs = [(means[j], means[i]) for i, state in enumerate(self.pattern) if state == "on"
                 for j in (i - 1, i + 1) if 0 <= j < len(means) and self.pattern[j] == "off"]
        self.dot = locate_dot(pairs, full_size=self.size, origin=self.crop[:2]) if pairs else None
        self.why = locate_dot.why if pairs else "the pattern has no off/on pair"


# ------------------------------------------------------------------ pixel <-> angles
class Calibration:
    """Maps a spot in the image to the pan/tilt angles that put the dot there, and back
    (aim_map.py fits it). Image positions are fractions of the frame (u = x / width,
    v = y / height), so the camera's resolution doesn't matter.

    error_px / error_deg: how far it typically misses at a spot it wasn't fitted to.
    manual: made by hand (calibrate_manual.py / calibrate_guided.py). dot_visible: the
    camera saw the dot while calibrating, so the Aimer watches for it and corrects;
    otherwise it points by the calibration alone. (Unset: true unless made by hand.)"""

    def __init__(self, mapping, kind, points, error_deg, error_px, size, manual=False,
                 dropped=(), fit_px=None, kinds=None, misses=None, dot_visible=None):
        self.map, self.kind = mapping, kind
        self.points, self.error_deg, self.error_px, self.size = points, error_deg, error_px, size
        self.manual, self._dot_visible = manual, dot_visible
        self.dropped, self.fit_px, self.kinds = list(dropped), fit_px, kinds or {}
        self.misses = list(misses) if misses is not None else []  # per point, px, held out
        P = np.asarray(points, float).reshape(-1, 4)
        self._angles, self._q = P[:, :2], aim_map.normalize(P[:, 2:], size)

    @property
    def dot_visible(self):
        return (not self.manual) if self._dot_visible is None else self._dot_visible

    @dot_visible.setter
    def dot_visible(self, value):
        self._dot_visible = value

    @classmethod
    def fit(cls, points, size, manual=False, dot_visible=None):
        """points: [(pan, tilt, x, y)]; size: (width, height) of their frames. Points the
        head can't be sent to any more (outside the limits) and odd ones are left out."""
        f = aim_map.fit_points(points, size, (PAN_MIN, PAN_MAX, TILT_MIN, TILT_MAX))
        return cls(f.map, f.kind, f.kept, f.error_deg, f.error_px, list(size), manual,
                   f.dropped, f.fit_px, f.kinds, [float(m) for m in f.miss_px], dot_visible)

    def to_angles(self, u, v):
        w, h = self.size
        pan, tilt = self.map(aim_map.normalize([u * w, v * h], self.size))[0]
        return float(pan), float(tilt)

    def to_image(self, pan, tilt):
        near = np.argmin(np.sum((self._angles - [pan, tilt]) ** 2, 1))  # start the search there
        q = aim_map.invert(self.map, [pan, tilt], self._q[near])
        x, y = aim_map.denormalize(q, self.size)
        return float(x / self.size[0]), float(y / self.size[1])

    def save(self, path=CALIB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"model": self.map.to_dict(), "kind": self.kind,
                       "points": [list(p) for p in self.points], "misses_px": self.misses,
                       "dropped": [[list(p), why] for p, why in self.dropped],
                       "error_deg": self.error_deg, "error_px": self.error_px,
                       "fit_px": self.fit_px, "kinds": self.kinds, "size": list(self.size),
                       "manual": self.manual, "dot_visible": self._dot_visible,
                       "saved": time.strftime("%Y-%m-%d %H:%M")}, f, indent=1)

    @classmethod
    def load(cls, path=CALIB_PATH):
        """The calibration in path, or None. An older file (one polynomial, no "model") is
        fitted again from its points, the new way."""
        if not os.path.exists(path):
            return None
        with open(path) as f:
            d = json.load(f)
        if "model" not in d:
            calib = cls.fit(d["points"], d["size"], d.get("manual", False))
            calib.refitted = True
            return calib
        return cls(aim_map.from_dict(d["model"]), d["kind"], [tuple(p) for p in d["points"]],
                   d["error_deg"], d["error_px"], d["size"], d.get("manual", False),
                   [(tuple(p), why) for p, why in d.get("dropped", [])], d.get("fit_px"),
                   d.get("kinds"), d.get("misses_px"), d.get("dot_visible"))


class RoughMap:
    """Stand-in until a calibration exists: a straight-line guess from ctrlf.py's settings.
    The closed loop can still fix small errors with it, if the directions are right."""

    def __init__(self, pan_center, pan_span, tilt_center, tilt_span):
        self.pc, self.ps, self.tc, self.ts = pan_center, pan_span, tilt_center, tilt_span

    def to_angles(self, u, v):
        return self.pc + (0.5 - u) * self.ps, self.tc + (v - 0.5) * self.ts


# ------------------------------------------------------------------ closed-loop aiming
class Aimer:
    """Points the laser at a pixel, then (if the camera can see the dot) blinks it, finds
    it and nudges it onto the target.

    start() begins; update(frame, now) must be called with every camera frame and never
    blocks. Each try: move with the laser off, Blink (off, on, off) to find the dot,
    correct by the difference the mapping predicts."""

    MAX_TRIES = 5
    HOLD_SECS = 4.0  # how long the laser stays on the target afterwards (3-5 s to see it)

    def __init__(self, pointer, mapping, lag=0.0):
        # lag: how far the camera's picture runs behind; every wait gets that much longer
        self.pointer, self.map, self.lag = pointer, mapping, lag
        self.state, self.wait = "idle", 0.0
        self.target = self.dot = self.blink = None
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
            self._hold(now, "not calibrated yet, so pointing roughly (run calibrate_guided.py)")
            return
        if not getattr(self.map, "dot_visible", True):
            # The camera couldn't see the dot while calibrating: no use watching for it.
            self.pointer.move(*self._angles(target), laser=True)
            self._hold(now, "pointing by the hand calibration")
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
        self._look(now, secs + SETTLE_SECS)

    def _look(self, now, wait=0.0):
        """Blink where the dot should be now (calibrated: only around there)."""
        u, v = self.map.to_image(self.pointer.pan, self.pointer.tilt)
        w, h = self.size
        self.blink = Blink(self.pointer, self.lag, (u * w, v * h, SEARCH_RADIUS * w))
        self.blink.start(now, wait)
        self.state = "looking"

    def _hold(self, now, message):
        self.pointer.send("L1")
        self.state, self.wait, self.message = "hold", now + self.HOLD_SECS, message
        print(f"\n[laser] {message}")

    def update(self, frame, now):
        if self.state == "looking":
            if self.blink.update(frame, now):
                self._measured(self.blink.dot, now)
        elif self.state == "hold" and now >= self.wait:  # done showing it
            self.stop()

    def _measured(self, dot, now):
        if dot is None:
            self.misses += 1
            if self.misses <= 1:  # someone walked through, or the camera lagged: once more
                self._look(now)
            else:
                self._hold(now, f"can't see the dot ({self.blink.why}), so pointing by "
                                "calibration alone")
            return
        self.tries += 1
        self.dot = (dot.x, dot.y)
        err = math.dist(self.dot, self.target)
        here = (self.pointer.pan, self.pointer.tilt)
        if self.best is None or err < self.best[0]:
            self.best = (err, here)
        if err <= self.tol:
            return self._hold(now, f"on target after {self.tries} tries ({err:.0f} px off)")
        if self.tries >= self.MAX_TRIES:
            self.pointer.move(*self.best[1], laser=True)
            return self._hold(now, f"gave up after {self.tries} tries; best was "
                                   f"{self.best[0]:.0f} px off")
        (tp, tt), (dp, dt) = self._angles(self.target), self._angles(self.dot)
        pan, tilt = here[0] + (tp - dp), here[1] + (tt - dt)
        # As far as the limits let it go (a target above the table stays out of reach).
        pan, tilt = min(max(pan, PAN_MIN), PAN_MAX), min(max(tilt, TILT_MIN), TILT_MAX)
        if abs(pan - here[0]) < 0.05 and abs(tilt - here[1]) < 0.05:
            return self._hold(now, f"as close as the servos allow ({err:.0f} px off)")
        self._go((pan, tilt), now)
