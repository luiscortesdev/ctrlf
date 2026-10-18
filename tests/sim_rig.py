"""A simulated room for testing the laser calibration math, built like the real one: a
fisheye camera mounted high and pitched down, the pan/tilt head `baseline` metres away from
it, a floor and a table, and servos that don't quite turn the angle they're told (gain and
offset errors, a head base that isn't level).

Everything is in metres; the world has x to the right, y into the room, z up, the floor at
z = 0. Pan and tilt follow the real head: more pan = left, more tilt = down, tilt 90 =
level."""

import numpy as np

W, H = 1920, 1080


def _rot_y(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])


class SimRig:
    def __init__(self, baseline=(0.05, 0.0, 0.0), cam_height=2.2, cam_pitch=35.0, hfov=120.0,
                 servo_gain=(1.04, 0.95), servo_offset=(1.5, -2.0), head_roll=1.5,
                 table=((-0.6, 0.6), (1.6, 2.6), 0.75)):
        self.cam = np.array([0.0, 0.0, cam_height])
        self.head = self.cam + np.asarray(baseline, float)
        p = np.radians(cam_pitch)
        # The camera's axes in the world: right, down (the picture's y) and forward.
        self.axes = np.array([[1.0, 0, 0], [0, -np.sin(p), -np.cos(p)], [0, np.cos(p), -np.sin(p)]])
        self.f = (W / 2) / np.radians(hfov / 2)  # equidistant fisheye: r = f * angle off-axis
        self.gain, self.offset = servo_gain, servo_offset
        self.roll = _rot_y(head_roll)
        (self.tx0, self.tx1), (self.ty0, self.ty1), self.tz = table

    # ---------------------------------------------------------------- camera
    def pixel(self, X):
        """World point(s) (..., 3) -> pixel(s) (..., 2)."""
        v = (np.asarray(X, float) - self.cam) @ self.axes.T  # (right, down, forward)
        theta = np.arctan2(np.hypot(v[..., 0], v[..., 1]), v[..., 2])
        phi = np.arctan2(v[..., 1], v[..., 0])
        r = self.f * theta
        return np.stack([W / 2 + r * np.cos(phi), H / 2 + r * np.sin(phi)], -1)

    def ray(self, xy):
        """Pixel(s) -> unit direction(s) from the camera, in the world."""
        xy = np.asarray(xy, float)
        dx, dy = xy[..., 0] - W / 2, xy[..., 1] - H / 2
        theta, phi = np.hypot(dx, dy) / self.f, np.arctan2(dy, dx)
        v = np.stack([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)], -1)
        return v @ self.axes

    def on_table(self, X):
        X = np.asarray(X)
        return (self.tx0 <= X[..., 0]) & (X[..., 0] <= self.tx1) & \
               (self.ty0 <= X[..., 1]) & (X[..., 1] <= self.ty1)

    def _hit(self, origin, d):
        """Where rays from origin along d (..., 3) first hit the table top or the floor."""
        d = np.asarray(d, float)
        top = origin + d * ((self.tz - origin[2]) / d[..., 2])[..., None]
        floor = origin + d * ((0.0 - origin[2]) / d[..., 2])[..., None]
        return np.where(self.on_table(top)[..., None], top, floor)

    def surface(self, xy):
        """What the camera sees at pixel(s) xy: the table top if it's there, else the floor."""
        return self._hit(self.cam, self.ray(xy))

    # ---------------------------------------------------------------- laser head
    def beam(self, pan, tilt):
        """Unit direction(s) the beam really goes for commanded (pan, tilt)."""
        az = np.radians((np.asarray(pan, float) - 90) * self.gain[0] + self.offset[0])
        dep = np.radians((np.asarray(tilt, float) - 90) * self.gain[1] + self.offset[1])
        d = np.stack([-np.sin(az) * np.cos(dep), np.cos(az) * np.cos(dep), -np.sin(dep)], -1)
        return d @ self.roll.T

    def landing(self, pan, tilt):
        return self._hit(self.head, self.beam(pan, tilt))

    def dot(self, pan, tilt):
        """Where the dot shows up in the picture for commanded (pan, tilt)."""
        return self.pixel(self.landing(pan, tilt))

    def angles_for(self, X):
        """The commanded (pan, tilt) that put the dot on world point(s) X."""
        v = np.asarray(X, float) - self.head
        v = (v / np.linalg.norm(v, axis=-1, keepdims=True)) @ self.roll  # undo the roll
        az = np.degrees(np.arctan2(-v[..., 0], v[..., 1]))
        dep = np.degrees(np.arctan2(-v[..., 2], np.hypot(v[..., 0], v[..., 1])))
        return np.stack([90 + (az - self.offset[0]) / self.gain[0],
                         90 + (dep - self.offset[1]) / self.gain[1]], -1)

    # ---------------------------------------------------------------- calibration data
    def table_grid(self, nx, ny, margin=0.1):
        """World points on the table top, a grid nx by ny, `margin` metres in from its edges."""
        xs = np.linspace(self.tx0 + margin, self.tx1 - margin, nx)
        ys = np.linspace(self.ty0 + margin, self.ty1 - margin, ny)
        return np.array([(x, y, self.tz) for y in ys for x in xs])

    def guided_points(self, world, rng, click_px=2.0, servo_deg=0.25):
        """What the guided calibration records for these spots: the person clicks each spot
        in the picture (a couple of pixels off) and steers the dot onto it in the room,
        stopping within the key step (servo_deg off). Returns [(pan, tilt, x, y)]."""
        xy = self.pixel(world) + rng.normal(0, click_px, (len(world), 2))
        ang = self.angles_for(world) + rng.uniform(-servo_deg, servo_deg, (len(world), 2))
        return [(a[0], a[1], p[0], p[1]) for a, p in zip(ang, xy)]

    def miss(self, mapping, world):
        """Aim at each world point by mapping (pixel -> angles); how far the dot lands from
        it, in metres and in pixels. mapping: f(xy (N, 2)) -> angles (N, 2)."""
        xy = self.pixel(world)
        ang = mapping(xy)
        land = self.landing(ang[:, 0], ang[:, 1])
        return np.linalg.norm(land - world, axis=1), np.linalg.norm(self.pixel(land) - xy, axis=1)
