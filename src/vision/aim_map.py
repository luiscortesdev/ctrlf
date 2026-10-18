"""
Fitting the laser calibration: which pan/tilt angles put the dot on a spot in the camera's
picture, learned from calibration points (pan, tilt, x, y).

Several kinds of map get fitted, and the one that best predicts points it wasn't fitted to
wins: each point is left out in turn and the rest predict it ("held out"). That is also
the error reported, so "typically 6 px off" means 6 px off at a new spot, not on the
points the fit has already seen (those always look better than the truth).

  projective      straightens the fisheye picture (a one-number lens bend), then a
                  perspective map onto the beam's directions. Exact for a flat surface with
                  an ideal lens and head, so it needs few points and behaves past them.
  poly2, poly3    polynomials in picture coordinates (what calibration.json used before)
  tps             a thin-plate spline: smooth, bends only where the points say so
  projective+tps  projective, plus a thin-plate spline for what's left (servo quirks, a
                  lens that isn't quite the model)

Picture coordinates here are centred on the middle of the picture, 1.0 = its side edge
(normalize), so the camera's resolution doesn't matter.
"""

from dataclasses import dataclass, field

import numpy as np

KINDS = ("projective", "poly2", "tps", "projective+tps", "poly3")  # simplest first
MIN_POINTS = {"projective": 6, "poly2": 8, "tps": 6, "projective+tps": 8, "poly3": 14}
LENS_BENDS = np.linspace(-0.6, 0.2, 17)   # tried by the projective map (division model)
SMOOTHING = (1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0)  # tried by the thin-plate spline
FOLDS_ABOVE = 40      # more points than this: hold out a tenth at a time, not one by one
SIMPLER_WITHIN = 1.05  # a simpler kind wins if it's within 5% of the best
ODD_TIMES, ODD_DEG = 4.0, 1.5  # a point is odd if held out it misses by 4x the median and 1.5 deg+


def normalize(xy, size):
    """Pixels (..., 2) -> centred picture coordinates (1.0 = the side edge)."""
    w, h = size
    return (np.asarray(xy, float) - [w / 2, h / 2]) / (w / 2)


def denormalize(q, size):
    w, h = size
    return np.asarray(q, float) * (w / 2) + [w / 2, h / 2]


def directions(ang):
    """(pan, tilt) degrees (..., 2) -> unit beam directions (..., 3) for an ideal head: tilt
    90 = level, more tilt = down, more pan = left."""
    ang = np.asarray(ang, float)
    az, dep = np.radians(ang[..., 0] - 90), np.radians(ang[..., 1] - 90)
    return np.stack([-np.sin(az) * np.cos(dep), np.cos(az) * np.cos(dep), -np.sin(dep)], -1)


def angles_of(d):
    d = np.asarray(d, float)
    az = np.degrees(np.arctan2(-d[..., 0], d[..., 1]))
    dep = np.degrees(np.arctan2(-d[..., 2], np.hypot(d[..., 0], d[..., 1])))
    return np.stack([90 + az, 90 + dep], -1)


def _rms(v):
    return float(np.sqrt(np.mean(np.square(v)))) if len(v) else 0.0


# ------------------------------------------------------------------ the kinds of map
class PolyMap:
    """Polynomial in picture coordinates, terms x^i y^j with i + j <= deg."""

    def __init__(self, deg, coef=None):
        self.deg, self.coef = deg, None if coef is None else np.asarray(coef, float)

    def _terms(self, q):
        q = np.atleast_2d(q)
        x, y = q[:, 0], q[:, 1]
        return np.stack([x ** i * y ** j for i in range(self.deg + 1)
                         for j in range(self.deg + 1 - i)], -1)

    def fit(self, q, ang):
        self.coef = np.linalg.lstsq(self._terms(q), ang, rcond=None)[0]
        return self

    def __call__(self, q):
        return self._terms(q) @ self.coef

    def to_dict(self):
        return {"kind": f"poly{self.deg}", "coef": self.coef.tolist()}


def _undistort(q, bend):
    """Division-model lens straightening: barrel (fisheye) bend is negative."""
    q = np.atleast_2d(q)
    return q / (1 + bend * np.sum(q * q, -1, keepdims=True))


def _homography(x, d):
    """3x3 H with d ~ H [x, 1] (direct linear transform), signed so H [x, 1] points along d."""
    xh = np.hstack([x, np.ones((len(x), 1))])
    z = np.zeros_like(xh)
    a = np.vstack([np.hstack([z, -d[:, 2:3] * xh, d[:, 1:2] * xh]),
                   np.hstack([d[:, 2:3] * xh, z, -d[:, 0:1] * xh])])
    hm = np.linalg.svd(a)[2][-1].reshape(3, 3)
    return hm if np.sum((xh @ hm.T) * d) >= 0 else -hm


class ProjectiveMap:
    def __init__(self, bend=None, hm=None):
        self.bend, self.hm = bend, None if hm is None else np.asarray(hm, float)

    def fit(self, q, ang):
        d = directions(ang)
        best = None
        for bend in LENS_BENDS:
            hm = _homography(_undistort(q, bend), d)
            self.bend, self.hm = bend, hm
            err = _rms(np.linalg.norm(self(q) - ang, axis=1))
            if best is None or err < best[0]:
                best = (err, bend, hm)
        _, self.bend, self.hm = best
        return self

    def __call__(self, q):
        x = _undistort(q, self.bend)
        return angles_of(np.hstack([x, np.ones((len(x), 1))]) @ self.hm.T)

    def to_dict(self):
        return {"kind": "projective", "bend": float(self.bend), "h": self.hm.tolist()}


def _tps_kernel(a, b):
    d2 = np.sum((a[:, None, :] - b[None, :, :]) ** 2, -1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(d2 > 0, 0.5 * d2 * np.log(d2), 0.0)  # r^2 log r


class SplineMap:
    """A thin-plate spline, on its own or correcting what a base map leaves. Its
    smoothing is picked by how well it predicts each point left out (exact for a spline,
    no refitting needed)."""

    def __init__(self, base=None):
        self.base = base
        self.centers = self.w = self.a = None
        self.smoothing = None

    def fit(self, q, ang):
        q = np.atleast_2d(q)
        rest = ang - (self.base.fit(q, ang)(q) if self.base else 0.0)
        n = len(q)
        k, p = _tps_kernel(q, q), np.hstack([np.ones((n, 1)), q])
        best = None
        for s in SMOOTHING:
            m = np.block([[k + n * s * np.eye(n), p], [p.T, np.zeros((3, 3))]])
            try:
                inv = np.linalg.inv(m)
            except np.linalg.LinAlgError:
                continue
            hat = np.hstack([k, p]) @ inv[:, :n]  # fitted = hat @ rest
            loo = (rest - hat @ rest) / np.clip(1 - np.diag(hat), 1e-6, None)[:, None]
            err = float(np.sum(loo ** 2))
            if best is None or err < best[0]:
                best = (err, s, inv)
        _, self.smoothing, inv = best
        sol = inv @ np.vstack([rest, np.zeros((3, 2))])
        self.centers, self.w, self.a = q, sol[:n], sol[n:]
        return self

    def __call__(self, q):
        q = np.atleast_2d(q)
        out = _tps_kernel(q, self.centers) @ self.w + np.hstack([np.ones((len(q), 1)), q]) @ self.a
        return out + (self.base(q) if self.base else 0.0)

    def to_dict(self):
        return {"kind": "projective+tps" if self.base else "tps", "smoothing": self.smoothing,
                "centers": self.centers.tolist(), "w": self.w.tolist(), "a": self.a.tolist(),
                "base": self.base.to_dict() if self.base else None}


def make(kind):
    return {"projective": ProjectiveMap, "poly2": lambda: PolyMap(2), "poly3": lambda: PolyMap(3),
            "tps": SplineMap, "projective+tps": lambda: SplineMap(ProjectiveMap())}[kind]()


def from_dict(d):
    kind = d["kind"]
    if kind.startswith("poly"):
        return PolyMap(int(kind[4:]), d["coef"])
    if kind == "projective":
        return ProjectiveMap(d["bend"], d["h"])
    m = SplineMap(from_dict(d["base"]) if d.get("base") else None)
    m.smoothing = d["smoothing"]
    m.centers, m.w, m.a = (np.asarray(d[k], float) for k in ("centers", "w", "a"))
    return m


# ------------------------------------------------------------------ choosing and checking
def held_out(kind, q, ang):
    """Each point's angles as predicted by a map of this kind fitted without it."""
    n = len(q)
    folds = n if n <= FOLDS_ABOVE else 10
    order = np.arange(n) if folds == n else np.random.default_rng(0).permutation(n)
    pred = np.empty_like(ang)
    for k in range(folds):
        test = order[k::folds]
        train = np.setdiff1d(np.arange(n), test)
        pred[test] = make(kind).fit(q[train], ang[train])(q[test])
    return pred


def jacobian(m, q, eps=1e-4):
    """d(pan, tilt)/d(picture coordinates) at each q: (N, 2, 2)."""
    q = np.atleast_2d(q)
    jx = (m(q + [eps, 0]) - m(q - [eps, 0])) / (2 * eps)
    jy = (m(q + [0, eps]) - m(q - [0, eps])) / (2 * eps)
    return np.stack([jx, jy], -1)


def pixel_misses(m, q, ang_err, size):
    """Angle errors (N, 2) at q -> how many pixels the dot lands off, by the map's slope."""
    j = jacobian(m, q)
    try:
        dq = np.linalg.solve(j, np.asarray(ang_err, float)[..., None])[..., 0]
    except np.linalg.LinAlgError:
        dq = np.linalg.lstsq(j.reshape(-1, 2), np.ravel(ang_err), rcond=None)[0].reshape(-1, 2)
    return np.linalg.norm(dq, axis=1) * size[0] / 2


def invert(m, ang, q0, steps=20):
    """The picture coordinates where map m gives angles ang (Newton's method from q0)."""
    q, ang = np.array(q0, float).reshape(1, 2), np.asarray(ang, float).reshape(1, 2)
    for _ in range(steps):
        err = ang - m(q)
        if np.max(np.abs(err)) < 1e-4:
            break
        try:
            step = np.linalg.solve(jacobian(m, q)[0], err[0])
        except np.linalg.LinAlgError:
            break
        q = q + np.clip(step, -0.25, 0.25)  # damped: never jump far on a bad slope
    return q[0]


@dataclass
class Fit:
    map: object
    kind: str
    kept: list                    # [(pan, tilt, x, y)] used
    dropped: list                 # [((pan, tilt, x, y), why)]
    miss_deg: np.ndarray          # per kept point, held out
    miss_px: np.ndarray
    error_deg: float              # typical (RMS) held-out miss
    error_px: float
    fit_px: float                 # on the points themselves (always looks better)
    kinds: dict = field(default_factory=dict)  # every kind tried: its held-out RMS degrees


def choose(q, ang, kinds=KINDS):
    """-> (kind, {kind: (rms degrees, per-point degrees)}) for the kinds there are enough
    points for: the simplest whose held-out error is within SIMPLER_WITHIN of the best."""
    tried = {}
    for kind in kinds:
        if len(q) >= MIN_POINTS[kind]:
            miss = np.linalg.norm(held_out(kind, q, ang) - ang, axis=1)
            tried[kind] = (_rms(miss), miss)
    if not tried:
        raise ValueError(f"need at least {min(MIN_POINTS.values())} points, got {len(q)}")
    best = min(r for r, _ in tried.values())
    return next(k for k in kinds if k in tried and tried[k][0] <= SIMPLER_WITHIN * best), tried


def fit_points(points, size, limits=None, kinds=KINDS):
    """points: [(pan, tilt, x, y)]; size: (width, height) of the picture; limits: (pan_min,
    pan_max, tilt_min, tilt_max), points outside which are dropped (the head can never be
    sent there, and they're usually on a wall). Odd points (held out, they miss by far
    more than the rest: a misclick, a bumped head) are dropped too, up to a fifth."""
    pts = [tuple(float(v) for v in p) for p in points]
    dropped = []
    if limits:
        p0, p1, t0, t1 = limits
        dropped = [(p, "outside the servo limits") for p in pts
                   if not (p0 <= p[0] <= p1 and t0 <= p[1] <= t1)]
        pts = [p for p in pts if p0 <= p[0] <= p1 and t0 <= p[1] <= t1]
    P = np.asarray(pts, float).reshape(-1, 4)
    q, ang = normalize(P[:, 2:], size), P[:, :2]
    kind, tried = choose(q, ang, kinds)
    miss = tried[kind][1]
    odd = miss > max(ODD_TIMES * np.median(miss), ODD_DEG)
    if len(P) >= 10 and 0 < odd.sum() <= len(P) // 5:
        dropped += [(pts[i], f"odd one out (held out it misses by {miss[i]:.1f} deg)")
                    for i in np.flatnonzero(odd)]
        keep = ~odd
        pts, q, ang = [p for p, k in zip(pts, keep) if k], q[keep], ang[keep]
        kind, tried = choose(q, ang, kinds)
        miss = tried[kind][1]
    m = make(kind).fit(q, ang)
    held = held_out(kind, q, ang)
    miss_px = pixel_misses(m, q, held - ang, size)
    fit_px = pixel_misses(m, q, m(q) - ang, size)
    return Fit(m, kind, pts, dropped, miss, miss_px, _rms(miss), _rms(miss_px), _rms(fit_px),
               {k: r for k, (r, _) in tried.items()})
