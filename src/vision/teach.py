"""
Teaching with the laptop camera, for Ctrl+F for Your Room.

You hold the thing up to the laptop camera (close-up pictures), then show it to the room
camera: hold it up there, or put it down anywhere it can see. Close-ups alone can't find
it across the room: on a test, the
close-ups of a set of keys matched other spots in the room view better than the keys
themselves (0.73 vs 0.69). So instead, Placement watches the room view for
  - something held up in someone's hand (people do this without being asked), picking
    the one most like the close-ups: the app learns it right away (say "no" to undo), or
  - something new that appears and stays put, ranked using the close-ups; the app asks
    "Is this it?" first: on a test recording, a patch of window where someone's head had
    been looked just as new as the real thing.
Either way, candidates are ranked by how much they look like the close-ups plus how much
they look like the name it was given (keys held up: the wrist matched the close-ups best,
but the box with the keys won once "looks like keys" was added), and the app asks "Is
that it?" before learning anything. Then the room camera's own pictures of it are
learned; those are what find it later.
"""

import cv2
import numpy as np

from src.vision.detector import DECOYS, NOT_SHOWN, iou

DECOY_WORDS = set(DECOYS)  # a box whose best word is one of these isn't the thing

SMALL_W = 480          # change detection works on a small gray copy of the room view
CHANGED = 28           # gray levels of difference that count as "changed"
STILL = 18             # ...and less than this over the last STILL_SECS = not moving now
STILL_SECS = 1.0
MIN_CHANGED_FRAC = 0.25  # this much of a box must be new for it to be the new thing
STAY_PASSES = 3        # a candidate must be there this many detector passes in a row
HELD_PASSES = 2        # something held up must be seen this many passes in a row
# ...and look at least this much like the close-ups. A ball held up: 0.65-0.72; things in
# a friend's hands walking past: 0.42-0.49 (it asked "is this your ball?" about those).
HELD_MIN_LIKENESS = 0.6


def _small(frame):
    s = SMALL_W / frame.shape[1]
    g = cv2.cvtColor(cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA),
                     cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(g, (5, 5), 0).astype(np.int16), s


class Placement:
    """Finds where a just-taught thing was put down in the room view."""

    def __init__(self, close_pictures, texts):
        """close_pictures: (n, d) unit vectors (OWLv2 embeddings of the close-ups).
        texts: the detector's prompt texts, to recognize people (they're not it).
        Set .word to its name's query (OwlDetector.word) to also rank by the name."""
        self.close = None if close_pictures is None else np.asarray(close_pictures, np.float32)
        self.word = None
        self.texts = texts
        self.background = []   # small gray room views from before it was put down
        self.blocked = None    # where people were in those views: unknown, not "new"
        self.history = []      # (time, small gray) of the last few seconds
        self.candidates = []   # [{"box", "passes", "sim"}] seen in recent passes
        self.changed = None    # the last "new and still" mask, for drawing
        self.held = None       # {"box", "passes"}: the thing in someone's hand, if any
        self.likeness = []     # how much each of the last returned candidates looks like it

    def add_background(self, frame, look=None):
        """A room view from before it was put down (while it's held up to the laptop).
        look: the detector's Look of it, to know where people were standing."""
        g, s = _small(frame)
        self.background = (self.background + [g])[-12:]
        if self.blocked is None:
            self.blocked = np.zeros(g.shape, np.uint8)
        if look is not None:
            for x1, y1, x2, y2 in self._people(look, frame.shape):
                self.blocked[max(0, int(y1 * s)):int(y2 * s) + 1, max(0, int(x1 * s)):int(x2 * s) + 1] = 1

    def _people(self, look, shape):
        b = look.boxes
        h, w = shape[:2]
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        words = [self.texts[i] for i in look.scores.argmax(1)]
        return [tuple(b[i]) for i in range(len(b)) if words[i] in NOT_SHOWN
                and look.scores[i].max() > 0.12 and area[i] > 0.002 * w * h]

    def _likeness(self, look):
        """Per box: (how much it looks like the close-ups, that plus how much it looks like
        its name)."""
        close = (look.embeds @ self.close.T).max(1) if self.close is not None else np.zeros(len(look.boxes))
        if self.word is not None and look.shift is not None:
            return close, close + look.word_scores(self.word)
        return close, close

    def update(self, frame, look, now):
        """frame: the room view a detector pass looked at; look: that pass's detector.Look
        (embeddings as numpy). Returns None (keep watching), ("held", [boxes]) or ("placed",
        [boxes]): the likely candidates, most likely first."""
        held = self._held(look, frame.shape)
        if held:
            return "held", held
        placed = self._placed(frame, look, now)
        return ("placed", placed) if placed else None

    def _held(self, look, shape):
        """Things in someone's hand, most like the close-ups and the name first, once the
        best has been seen HELD_PASSES passes in a row."""
        b = look.boxes
        if self.close is None or not len(b):
            return None
        h, w = shape[:2]
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        best_prompt = look.scores.argmax(1)
        words = [self.texts[i] for i in best_prompt]
        close, sims = self._likeness(look)
        hands = [i for i in range(len(b)) if words[i] == "hand" and look.scores[i].max() > 0.15]
        cx, cy = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2
        found = {}
        for j in hands:
            hx, hy = cx[j], cy[j]
            reach = 1.2 * max(b[j, 2] - b[j, 0], b[j, 3] - b[j, 1])
            for i in np.where((np.hypot(cx - hx, cy - hy) < reach) & (area < 2.5 * area[j])
                              & (area < 0.15 * w * h) & (look.objectness > 0.05))[0]:
                if i == j or words[i] in NOT_SHOWN or self.texts[best_prompt[i]] in DECOY_WORDS:
                    continue
                if close[i] < HELD_MIN_LIKENESS:
                    continue
                found[int(i)] = float(sims[i])
        if not found:
            self.held = None
            return None
        ranked = []  # best first, one box per thing
        for i in sorted(found, key=lambda i: -found[i]):
            if all(iou(b[i], b[k]) < 0.5 for k in ranked):
                ranked.append(i)
        best = tuple(float(v) for v in b[ranked[0]])
        prev = self.held
        size = max(best[2] - best[0], best[3] - best[1])
        same = prev and (iou(prev["box"], best) > 0.2 or np.hypot(
            (prev["box"][0] + prev["box"][2] - best[0] - best[2]) / 2,
            (prev["box"][1] + prev["box"][3] - best[1] - best[3]) / 2) < 1.5 * size)
        self.held = {"box": best, "passes": prev["passes"] + 1 if same else 1}
        if self.held["passes"] < HELD_PASSES:
            return None
        self.likeness = [found[i] for i in ranked[:3]]
        return [tuple(float(v) for v in b[i]) for i in ranked[:3]]

    def _placed(self, frame, look, now):
        g, s = _small(frame)
        self.history = [(t, h) for t, h in self.history if now - t < 3 * STILL_SECS] + [(now, g)]
        if not self.background:
            self.background = [g]
            return None
        bg = np.median(np.stack(self.background), 0).astype(np.int16)
        g_adj = g - int(g.mean() - bg.mean())  # the camera's exposure drifts
        new = np.abs(g_adj - bg) > CHANGED
        old = [h for t, h in self.history if now - t >= STILL_SECS]
        moving = np.abs(g - old[-1]) > STILL if old else np.ones_like(new)
        mask = (new & ~moving).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        people = self._people(look, frame.shape)
        free = np.ones(mask.shape, bool)  # away from people
        for x1, y1, x2, y2 in people:
            free[max(0, int(y1 * s)):int(y2 * s) + 1, max(0, int(x1 * s)):int(x2 * s) + 1] = False
        if free.mean() > 0.2 and (new & free).mean() / free.mean() > 0.4:
            # Most of the picture changed away from people (lights, camera bumped): start over.
            self.background = [g]
            self.candidates = []
            return None
        self.changed = mask

        b = look.boxes
        h, w = frame.shape[:2]
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        words = [self.texts[i] for i in look.scores.argmax(1)]
        _, sims = self._likeness(look)
        found = []
        for i in range(len(b)):
            if not (0.0002 * w * h < area[i] < 0.15 * w * h) or look.objectness[i] < 0.05:
                continue
            if words[i] in NOT_SHOWN or any(iou(b[i], p) > 0.1 or _inside(b[i], p) for p in people):
                continue
            x1, y1, x2, y2 = (int(v * s) for v in b[i])
            part = mask[max(0, y1):y2 + 1, max(0, x1):x2 + 1]
            if part.size == 0 or part.mean() < MIN_CHANGED_FRAC:
                continue
            was_blocked = self.blocked is not None and \
                self.blocked[max(0, y1):y2 + 1, max(0, x1):x2 + 1].mean() > 0.5
            found.append({"box": tuple(float(v) for v in b[i]), "sim": float(sims[i]),
                          "fit": float(part.mean() * look.objectness[i]), "blocked": was_blocked})
        # How likely each is the thing: new, object-like, and (a little) like the close-ups.
        for c in found:
            c["rank"] = c["fit"] * (0.5 + max(0.0, c["sim"]))
        # One box per thing: where boxes overlap, the best-fitting one.
        found.sort(key=lambda c: -c["fit"])
        kept = []
        for c in found:
            if all(iou(c["box"], k["box"]) < 0.3 and not _inside(c["box"], k["box"])
                   and not _inside(k["box"], c["box"]) for k in kept):
                kept.append(c)
        # Follow candidates from pass to pass; they must stay put to count.
        nxt = []
        for c in kept:
            prev = next((p for p in self.candidates if iou(p["box"], c["box"]) > 0.4), None)
            c["passes"] = prev["passes"] + 1 if prev else 1
            c["rank"] = max(c["rank"], prev["rank"]) if prev else c["rank"]
            nxt.append(c)
        self.candidates = nxt
        # Spots someone was standing in before rank last: they may just be uncovered.
        ready = sorted([c for c in nxt if c["passes"] >= STAY_PASSES],
                       key=lambda c: (c["blocked"], -c["rank"]))
        self.likeness = [c["sim"] for c in ready[:3]]
        return [c["box"] for c in ready[:3]] or None


def _inside(a, b):
    """Is box a mostly inside box b?"""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy > 0.8 * (a[2] - a[0]) * (a[3] - a[1])
