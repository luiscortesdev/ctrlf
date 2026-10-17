"""
Turns each detector pass's candidates into steady objects for Ctrl+F for Your Room.

Raw detector output is jumpy: a box can show up in one pass and not the next (things
only seen in a zoomed-in tile come back every 4th pass), wobble a few pixels, flicker
when its score hovers around the threshold, or jump between two look-alikes (two ID
badges in view). The tracker:
  - follows each object from pass to pass and smooths its box and score,
  - only shows it after CONFIRM_HITS sightings (one-off false alarms never show up),
  - keeps it for a few passes after it's last seen (long enough for the tiles to come
    around again),
  - lets an object it's already following stay at a lower score than a new one needs
    (KEEP_FRAC of its threshold), so it doesn't flicker,
  - picks one "main" box per label for memory and answers, and sticks with it unless
    another one is clearly better.
"""

import math

CONFIRM_HITS = 2   # sightings at its full threshold before an object is shown
KEEP_FRAC = 0.8    # an object already followed may drop to this fraction of its threshold
MISS_PASSES = 5    # passes it may go unseen before it's dropped (a 2x2 tile cycle + 1)
SMOOTH = 0.35      # how much each new box moves the smoothed one (1 = no smoothing)
SWITCH_MARGIN = 0.06  # another box must score this much higher to become the main one


class Track:
    def __init__(self, label, score, box, now):
        self.label, self.score = label, score
        self.box = [float(v) for v in box]
        self.last = tuple(box)  # the latest sighting itself, which the smoothed box lags
        self.hits, self.misses = 1, 0
        self.strong = 1  # sightings at its full threshold
        self.first, self.seen = now, now
        self.fresh = True  # seen in the latest pass

    @property
    def size(self):
        return max(self.box[2] - self.box[0], self.box[3] - self.box[1], 1)

    @property
    def center(self):
        return (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2

    @property
    def confirmed(self):
        return self.strong >= CONFIRM_HITS

    def int_box(self):
        return tuple(int(round(v)) for v in self.box)

    def hit(self, score, box, now, strong):
        new_c = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        if math.dist(new_c, self.center) > 0.4 * self.size:  # it moved: jump there
            self.box = [float(v) for v in box]
        else:
            self.box = [o + SMOOTH * (n - o) for o, n in zip(self.box, box)]
        self.score = 0.5 * self.score + 0.5 * score
        self.hits += 1
        self.strong += strong
        self.misses = 0
        self.last = tuple(box)
        self.seen, self.fresh = now, True


def _overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _same_thing(track, box):
    """Is this box probably the object the track follows? Checked against its latest
    sighting too: while it's being carried, the smoothed box falls behind, and without
    this every other sighting would start a new track."""
    c = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    last = ((track.last[0] + track.last[2]) / 2, (track.last[1] + track.last[3]) / 2)
    return (_overlap(track.box, box) > 0.3 or math.dist(c, track.center) < 0.5 * track.size
            or math.dist(c, last) < 0.5 * track.size)


class Tracker:
    def __init__(self, threshold, single=lambda label: False, keep=None):
        """threshold(label): score a new object needs. single(label): True if there is only
        one of it (a taught object: YOUR keys), so a confident sighting elsewhere means
        it moved there, rather than a second one. keep(label): score an object already
        followed needs to stay followed (default KEEP_FRAC of its threshold)."""
        self.threshold, self.single = threshold, single
        self.keep = keep or (lambda label: KEEP_FRAC * threshold(label))
        self.tracks = []
        self.main = {}  # label -> Track used for memory and answers

    def update(self, candidates, now):
        """candidates: [(label, score, box)] from one detector pass."""
        for t in self.tracks:
            t.fresh = False
        used = set()
        for label, score, box in sorted(candidates, key=lambda c: -c[1]):
            mine = [t for t in self.tracks if t.label == label and id(t) not in used]
            match = next((t for t in sorted(mine, key=lambda t: -t.score) if _same_thing(t, box)), None)
            if match is None and self.single(label) and mine and score >= self.threshold(label) \
                    and (score >= mine[0].score - 0.2 or mine[0].misses >= 2):
                match = mine[0]  # the only one there is: it moved (unless a much better sighting
                # of it is still where it was, like the real keys next to their reflection)
            if match is not None:
                if score >= self.keep(label):
                    match.hit(score, box, now, score >= self.threshold(label))
                    used.add(id(match))
            elif score >= self.threshold(label):
                if self.single(label) and any(t.label == label for t in self.tracks):
                    continue  # already following it, and this one wasn't confident enough
                t = Track(label, score, box, now)
                self.tracks.append(t)
                used.add(id(t))
        for t in self.tracks:
            if not t.fresh:
                t.misses += 1
        self.tracks = [t for t in self.tracks if t.misses <= MISS_PASSES]
        self._merge_duplicates()
        self._pick_main()

    def _merge_duplicates(self):
        keep = []
        for t in sorted(self.tracks, key=lambda t: (-t.hits, -t.score)):
            if not any(k.label == t.label and _overlap(k.box, t.box) > 0.5 for k in keep):
                keep.append(t)
        self.tracks = keep

    def _pick_main(self):
        for label in {t.label for t in self.tracks} | set(self.main):
            shown = [t for t in self.tracks if t.label == label and t.confirmed]
            if not shown:
                self.main.pop(label, None)
                continue
            best = max(shown, key=lambda t: t.score)
            cur = self.main.get(label)
            if cur in shown and cur.score >= best.score - SWITCH_MARGIN:
                continue
            self.main[label] = best

    def shown(self):
        """Tracks to draw: confirmed ones (including ones missed for a pass or two)."""
        return [t for t in self.tracks if t.confirmed]

    def seen_now(self):
        """{label: (score, box)} for the main object of each label seen in the latest pass."""
        return {label: (t.score, t.int_box()) for label, t in self.main.items() if t.fresh}

    def add(self, label, box, now, score=0.6):
        """Start following something right away (just taught, so no doubt about it)."""
        self.tracks = [t for t in self.tracks if not (t.label == label and self.single(label))]
        t = Track(label, score, box, now)
        t.hits = t.strong = CONFIRM_HITS
        self.tracks.append(t)
        self._pick_main()

    def clear(self):
        self.tracks, self.main = [], {}
