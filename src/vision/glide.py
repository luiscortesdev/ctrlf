"""
Moves boxes along with the video between detector passes, for Ctrl+F for Your Room.

The detector finishes 2-3 passes a second, and each result is for a frame from a moment
ago, so on their own the boxes jump and trail behind things that move. Between passes,
Glide follows a few corner points inside each box from frame to frame (Lucas-Kanade
optical flow on a small gray copy of the video, about a millisecond) and shifts the box
by how far they moved, so boxes follow things at the camera's frame rate.
"""

import collections

import cv2
import numpy as np

SMALL_W = 480
MAX_AREA = 0.1  # bigger boxes (furniture) stay put: people walking past would drag them
POINTS = 15     # corner points followed per box


class Glide:
    def __init__(self):
        self.history = collections.deque(maxlen=60)  # (time, small gray) of recent frames
        self.scale = 1.0
        self.points = {}   # id(track) -> (points now, the same points in the detector's frame)
        self.offsets = {}  # id(track) -> (dx, dy) in frame pixels since the detector's frame

    def frame(self, frame, now):
        """Call with every camera frame."""
        self.scale = s = SMALL_W / frame.shape[1]
        g = cv2.cvtColor(cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY)
        prev = self.history[-1][1] if self.history else None
        self.history.append((now, g))
        if prev is not None and self.points:
            self._follow(prev, g)

    def anchor(self, tracks, frame_size, det_time):
        """After a detector pass: tracks' boxes are where things were at det_time. Find
        points in them in that frame, and follow them to the newest frame."""
        if not self.history:
            return
        w, h = frame_size
        s = self.scale
        then = min(self.history, key=lambda th: abs(th[0] - det_time))[1]
        now_g = self.history[-1][1]
        alive = set()
        for t in tracks:
            x1, y1, x2, y2 = t.box
            if (x2 - x1) * (y2 - y1) > MAX_AREA * w * h:
                continue
            mask = np.zeros(then.shape, np.uint8)
            mask[max(0, int(y1 * s)):int(y2 * s) + 1, max(0, int(x1 * s)):int(x2 * s) + 1] = 255
            pts = cv2.goodFeaturesToTrack(then, POINTS, 0.01, 3, mask=mask)
            if pts is None or len(pts) < 3:
                continue
            pts = pts.astype(np.float32)
            if then is now_g:
                nxt, ok = pts, np.ones(len(pts), bool)
            else:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(then, now_g, pts, None, winSize=(15, 15), maxLevel=3)
                ok = st[:, 0] == 1
            if ok.sum() < 3:
                continue
            self.points[id(t)] = (nxt[ok], pts[ok])
            alive.add(id(t))
        for k in list(self.points):
            if k not in alive:
                self.points.pop(k)
        self._offsets()

    def _follow(self, prev, g):
        keys = list(self.points)
        allp = np.concatenate([self.points[k][0] for k in keys])
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, allp, None, winSize=(15, 15), maxLevel=2)
        i = 0
        for k in keys:
            cur, start = self.points[k]
            n = len(cur)
            ok = st[i:i + n, 0] == 1
            i += n
            if ok.sum() < 3:
                self.points.pop(k)  # lost them: the box stays where the detector put it
            else:
                self.points[k] = (nxt[i - n:i][ok], start[ok])
        self._offsets()

    def _offsets(self):
        self.offsets = {k: tuple(np.median((cur - start).reshape(-1, 2), 0) / self.scale)
                        for k, (cur, start) in self.points.items()}

    def box(self, track):
        """The track's box, moved along with the video since the detector saw it."""
        dx, dy = self.offsets.get(id(track), (0.0, 0.0))
        x1, y1, x2, y2 = track.box
        return x1 + dx, y1 + dy, x2 + dx, y2 + dy
