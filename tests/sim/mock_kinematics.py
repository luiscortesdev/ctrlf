"""
A software stand-in for the pan/tilt laser head and the camera, to test
src/vision/visual_servoing.py without the ESP32 or a webcam.

MockPointer is the real serial_client.Pointer wired to a FakeFirmware instead of a serial
port, so commands go through the same clamping and "P.. T.. L.." formatting as on the
hardware. FakeFirmware reads them like firmware/esp32_controller: clamps to the limits,
glides 1 degree per 10 ms (moving down, it goes BACKLASH_DEG past and comes back up), and
switches the laser. VirtualKinematicRig is the camera: it shows where the head pointed
`latency` seconds ago, as a pixel, with jitter, a slightly rotated camera, lens bend, gear
slop and the odd missed detection. Everything runs on a simulated clock, so a 6 s timeout
or a 4 s hold takes no real time.
"""

import math
import random

from src.hardware.serial_client import Pointer
from src.vision.laser_tracker import PAN_MAX, PAN_MIN, SECS_PER_DEG, TILT_MAX, TILT_MIN
from src.vision.visual_servoing import BoundingBox, VisualServoController

BACKLASH_DEG = 2  # keep in sync with the firmware
SIZE = (1280, 720)
PX_PER_DEG = (1280 / 60, 720 / 45)  # app.py's PAN_SPAN, TILT_SPAN across a 1280 x 720 image
CENTER = (90, 125)                  # app.py's PAN_CENTER, TILT_CENTER: the image center
LAG = 0.1                           # the camera lag the controller is told about


class SimClock:
    """time.monotonic and time.sleep for the controller, without the waiting."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, secs):
        self.t += max(0.0, secs)


class _Servo:
    """One axis as the firmware drives it: it glides through each move's waypoints at
    1 degree per SECS_PER_DEG, and lands up to `slop` degrees off (the gears)."""

    def __init__(self, deg, lo, hi, slop, rng):
        self.lo, self.hi, self.slop, self.rng = lo, hi, slop, rng
        self.moves = [(-math.inf, [deg])]  # (start time, waypoints it really goes through)
        self.target = deg                  # where it was told to go

    def at(self, t):
        """Where it really points at time t (which may be in the past)."""
        t0, path = next(m for m in reversed(self.moves) if m[0] <= t)
        left = (t - t0) / SECS_PER_DEG  # degrees it can have covered since
        pos = path[0]
        for nxt in path[1:]:
            if left <= abs(nxt - pos):
                return pos + math.copysign(left, nxt - pos)
            left -= abs(nxt - pos)
            pos = nxt
        return pos

    def go(self, target, t):
        before, self.target = self.target, min(max(target, self.lo), self.hi)
        via = max(self.target - BACKLASH_DEG, self.lo) if self.target < before else self.target
        off = self.rng.gauss(0, self.slop)
        self.moves.append((t, [self.at(t), via + off, self.target + off]))


class FakeFirmware:
    """The serial port as Pointer uses it (write, read, in_waiting, flush), read like
    firmware/esp32_controller: "P<deg> T<deg> L0/L1" and "C" on a line, values to a tenth
    of a degree. Counts P/T values outside the limits that reach it (the board clamps them,
    but nothing should send them)."""

    def __init__(self, clock, slop_deg=0.0, rng=None):
        rng = rng or random.Random(0)
        self.clock = clock
        self.pan = _Servo(90, PAN_MIN, PAN_MAX, slop_deg, rng)
        self.tilt = _Servo((TILT_MIN + TILT_MAX) / 2, TILT_MIN, TILT_MAX, slop_deg, rng)
        self.laser, self.laser_since = False, 0.0
        self.lines, self.out_of_range = [], 0

    def write(self, data):
        for line in data.decode().splitlines():
            self.lines.append(line)
            self._command(line)
        return len(data)

    def _command(self, line):
        pan, tilt, laser = self.pan.target, self.tilt.target, self.laser
        acted, i = False, 0
        while i < len(line):
            c = line[i]
            i += 1
            if c in "PTL":
                start = i
                while i < len(line) and (line[i].isdigit() or line[i] in "-."):
                    i += 1
                v = round(float(line[start:i] or 0) * 10) / 10
                if c == "P":
                    pan = v
                    self.out_of_range += not PAN_MIN <= v <= PAN_MAX
                elif c == "T":
                    tilt = v
                    self.out_of_range += not TILT_MIN <= v <= TILT_MAX
                else:
                    laser = v != 0
                acted = True
            elif c == "C":
                pan, tilt, laser, acted = 90, (TILT_MIN + TILT_MAX) / 2, False, True
        if acted:
            now = self.clock()
            self.pan.go(pan, now)
            self.tilt.go(tilt, now)
            if laser and not self.laser:
                self.laser_since = now
            self.laser = laser

    def angles(self, t):
        """(pan, tilt) the head really pointed at, at time t."""
        return self.pan.at(t), self.tilt.at(t)

    in_waiting = 0  # it doesn't answer: the controller never reads

    def read(self, n=1):
        return b""

    def flush(self):
        pass


class MockPointer(Pointer):
    """serial_client.Pointer on a FakeFirmware instead of a port. Records every angle it
    was asked for, before Pointer clamps it: a request past a limit counts as a safety
    violation even though Pointer and the firmware would both refuse it."""

    def __init__(self, clock, slop_deg=0.0, seed=0):
        super().__init__(link="none")  # opens nothing
        self.ser = FakeFirmware(clock, slop_deg, random.Random(seed))
        self.requests = []  # (pan, tilt) as asked for
        self.host_violations = 0

    def move(self, pan, tilt, laser=None):
        self.requests.append((pan, tilt))
        self.host_violations += not (PAN_MIN <= pan <= PAN_MAX and TILT_MIN <= tilt <= TILT_MAX)
        return super().move(pan, tilt, laser)

    @property
    def safety_violations(self):
        """Angles past the limits asked of Pointer, or sent around it to the board."""
        return self.host_violations + self.ser.out_of_range

    @property
    def laser(self):
        return self.ser.laser


class VirtualKinematicRig:
    """The head, its firmware and the camera looking at the desk.

    The dot lands at (W/2, H/2) + (pan - CENTER pan, tilt - CENTER tilt) * px_per_deg,
    then the camera's roll and lens bend move it a little. invert_pan False: more pan
    moves the dot right (+x); invert_tilt False: more tilt moves it down (+y). The
    defaults are this head's (RoughMap in laser_tracker.py: more pan = left)."""

    def __init__(self, size=SIZE, px_per_deg=PX_PER_DEG, center=CENTER, invert_pan=True,
                 invert_tilt=False, roll_deg=0.0, bend=0.0, jitter_px=1.0, latency=LAG,
                 miss_rate=0.0, slop_deg=0.1, seed=0):
        self.size, self.px_per_deg, self.center = size, px_per_deg, center
        self.invert_pan, self.invert_tilt = invert_pan, invert_tilt
        self.roll, self.bend = math.radians(roll_deg), bend
        self.jitter, self.latency, self.miss_rate = jitter_px, latency, miss_rate
        self.rng = random.Random(seed)
        self.clock = SimClock()
        self.pointer = MockPointer(self.clock, slop_deg, seed)

    def controller(self, **kw):
        """A VisualServoController on this rig's pointer and clock."""
        kw.setdefault("lag", LAG)
        return VisualServoController(self.pointer, clock=self.clock, sleep=self.clock.sleep, **kw)

    def project(self, pan, tilt):
        """The pixel the dot lands on with the head at (pan, tilt)."""
        w, h = self.size
        dx = (pan - self.center[0]) * self.px_per_deg[0] * (-1 if self.invert_pan else 1)
        dy = (tilt - self.center[1]) * self.px_per_deg[1] * (-1 if self.invert_tilt else 1)
        c, s = math.cos(self.roll), math.sin(self.roll)
        dx, dy = c * dx - s * dy, s * dx + c * dy
        k = 1 + self.bend * (dx * dx + dy * dy) / (w / 2) ** 2  # barrel (-) or pincushion (+)
        return w / 2 + dx * k, h / 2 + dy * k

    def true_dot(self):
        """Where the dot really is right now: no camera lag, no jitter."""
        return self.project(*self.pointer.ser.angles(self.clock()))

    def dot_inside(self, box):
        x, y = self.true_dot()
        x1, y1, x2, y2 = box.to_pixels(self.size)
        return x1 <= x <= x2 and y1 <= y <= y2

    def get_laser(self):
        """Where the camera sees the dot now, in pixels, or None (laser off, off the
        image, or missed), like laser_tracker.find_dot."""
        seen = self.clock() - self.latency  # the picture runs behind
        fw = self.pointer.ser
        if not fw.laser or seen < fw.laser_since:
            return None
        x, y = self.project(*fw.angles(seen))
        x, y = x + self.rng.gauss(0, self.jitter), y + self.rng.gauss(0, self.jitter)
        if not (0 <= x < self.size[0] and 0 <= y < self.size[1]):
            return None
        if self.rng.random() < self.miss_rate:
            return None
        return x, y


def box_at(cx, cy, w, h):
    """A BoundingBox from its center and size, all fractions of the frame."""
    return BoundingBox(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def monte_carlo(runs=50, seed=0, latency=(0.03, 0.2), **controller_kw):
    """track_target on `runs` random targets across the desk, each on a rig that differs
    from what the controller assumes: scale +-20%, camera rolled up to 4 degrees, lens bend,
    camera lag in the `latency` range (the controller is told LAG), 1 px jitter, 0.1 degree
    gear slop, 5% missed detections. Returns one dict per run."""
    rng = random.Random(seed)
    results = []
    for i in range(runs):
        rig = VirtualKinematicRig(
            px_per_deg=(PX_PER_DEG[0] * rng.uniform(0.8, 1.2), PX_PER_DEG[1] * rng.uniform(0.8, 1.2)),
            roll_deg=rng.uniform(-4, 4), bend=rng.uniform(-0.06, 0.06),
            latency=rng.uniform(*latency), miss_rate=0.05, seed=seed * 1000 + i)
        box = box_at(rng.uniform(0.15, 0.85), rng.uniform(0.20, 0.80),
                     rng.uniform(0.04, 0.12), rng.uniform(0.06, 0.16))
        ctl = rig.controller(**controller_kw)
        start = rig.clock()
        ok = ctl.track_target(box, rig.get_laser, rig.size)
        results.append({
            "converged": ok,
            "on_target": rig.dot_inside(box),  # what really happened, not what it saw
            "iterations": ctl.iterations,
            "moves": ctl.moves,
            "secs": rig.clock() - start - (ctl.hold_secs if ok else 0),
            "violations": rig.pointer.safety_violations,
            "laser_off": not rig.pointer.laser,
            "outcome": ctl.outcome,
        })
    return results
