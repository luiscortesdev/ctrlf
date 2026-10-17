"""
Closed-loop laser aiming by visual servoing, for Ctrl+F for Your Room.

A proportional controller: see where the dot is, nudge the head by a step proportional to
how far it is from the target's center, look again. It needs no calibration, only rough
gains (degrees per pixel of error) and which way each servo moves the dot, so it works on
a head calibrate.py hasn't mapped. On this rig a pixel is about 1/21 of a degree of pan and
1/16 of tilt (60 and 45 degrees across a 1280 x 720 image, see RoughMap); the gains are
a bit under that, so a step lands short of the target rather than past it.

It's on target once the dot has been inside the target box (shrunk by MARGIN on each
side), with the head holding still, for STABLE_FRAMES frames in a row. An axis within
DEADBAND_PX of the center stops, and moves again only once it's off by twice that, so
camera jitter doesn't make it hunt. If the camera runs further behind than it was told,
the pictures after a step don't show the step's result yet: it notices (see FRESH) and
waits longer. Angles are clamped to the firmware's limits before anything is sent,
whatever the target: the tilt never goes above TILT_MIN (the table top). A target past a
limit ends the loop instead of pushing against it.

Tested without hardware against a simulated head and camera: tests/sim.
"""

import math
import time
from dataclasses import dataclass

from src.vision.laser_tracker import (LASER_SETTLE_SECS, PAN_MAX, PAN_MIN, SETTLE_SECS,
                                      TILT_MAX, TILT_MIN)

KP_X, KP_Y = 0.04, 0.04   # degrees per pixel of error: pan from x, tilt from y
MAX_STEP_DEG = 3.0        # the most one step moves either servo
DEADBAND_PX = 8           # an axis this close to the target's center stays put
STABLE_FRAMES = 3         # frames in a row inside the box, holding still, to count as on target
MARGIN = 0.05             # "inside" means this fraction of the box's width/height in from its edges
STEP_RES = 0.1            # the firmware takes tenths of a degree
INVERT_PAN = True         # this head: more pan moves the dot left (-x)...
INVERT_TILT = False       # ...and more tilt moves it down (+y), as in RoughMap
LOST_FRAMES = 2           # dot missing this many frames in a row: back to where it was last seen
FRAME_SECS = 1 / 30       # holding still: wait for the next frame
HOLD_SECS = 4.0           # how long the laser stays on the target afterwards

# After a step, the next picture should show the dot moving about (d_pan / kp_x, d_tilt /
# kp_y) pixels. If it hasn't moved FRESH of that, the picture is from before the step; if
# it has gone more than OVERSHOOT times that and is still moving, the head was on its way
# (moving down, the firmware goes 2 degrees past and comes back). Either way the camera
# runs further behind than `lag`: acting on the picture would step the wrong amount, over
# and over, and a dot passing through the box would count as on target. So look again
# STALE_SECS later, and wait that much longer after every move from then on. (Well past
# but holding still is believed: the step before went further than its picture showed.)
FRESH = 0.25
OVERSHOOT = 1.4
CHECK_PX = 10             # steps smaller than this (pixels) can't be seen to fall short: jitter
STILL_PX = 4              # two pictures this close: the dot isn't moving
STALE_SECS = 0.1
MAX_EXTRA_LAG = 1.0
MAX_STALE = round(MAX_EXTRA_LAG / STALE_SECS)  # pictures it waits out for one step, then goes on anyway


@dataclass(frozen=True)
class BoundingBox:
    """A target box in fractions of the frame (0-1), x to the right and y down as in
    OpenCV. The laser dot comes in pixels, so it's converted with the frame size."""

    x1: float
    y1: float
    x2: float
    y2: float

    @classmethod
    def from_pixels(cls, x1, y1, x2, y2, size):
        w, h = size
        return cls(x1 / w, y1 / h, x2 / w, y2 / h)

    @property
    def center(self):
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    def to_pixels(self, size):
        w, h = size
        return self.x1 * w, self.y1 * h, self.x2 * w, self.y2 * h


class VisualServoController:
    """Walks the laser dot onto a target box.

    pointer: serial_client.Pointer, or anything with its pan, tilt, connected, move() and
    send(). lag: how far the camera's picture runs behind (camera.lag), added to every wait
    after a move; if pictures turn out staler than that, extra_lag grows to make up for it
    (and stays, for the next target). clock and sleep: time.monotonic and time.sleep, or a
    simulated clock."""

    def __init__(self, pointer, kp_x=KP_X, kp_y=KP_Y, max_step_deg=MAX_STEP_DEG,
                 deadband_px=DEADBAND_PX, stability_frames=STABLE_FRAMES, margin=MARGIN,
                 invert_pan=INVERT_PAN, invert_tilt=INVERT_TILT, lag=0.0, hold_secs=HOLD_SECS,
                 clock=time.monotonic, sleep=time.sleep):
        self.pointer = pointer
        self.kp_x, self.kp_y, self.max_step = kp_x, kp_y, max_step_deg
        self.deadband, self.stability_frames, self.margin = deadband_px, stability_frames, margin
        self.invert_pan, self.invert_tilt = invert_pan, invert_tilt
        self.lag, self.hold_secs = lag, hold_secs
        self.extra_lag = 0.0  # learned: how much staler than lag the pictures are
        self.clock, self.sleep = clock, sleep
        self.reset()

    def reset(self):
        """Forget the last target."""
        self.stable = 0       # frames in a row inside the box, holding still
        self.settled = [False, False]  # pan, tilt: came within the deadband (see _axis)
        self.misses = 0       # frames in a row without the dot
        self.pinned = 0       # frames in a row the target was past a servo limit
        self.seen_at = None   # (pan, tilt) the last time the dot was seen
        self.moved_from = None  # where the dot was before the last step, until a picture shows it moved
        self.expect = None    # (dx, dy) pixels that step should move it
        self.stale = 0        # pictures in a row that didn't show the last step done yet
        self.last_pic = None  # the last of them that showed the dot well past
        self.wait = 0.0       # seconds before the next frame is worth looking at
        self.iterations = 0   # frames looked at
        self.moves = 0        # steps sent
        self.error = None     # (ex, ey) pixels from the dot to the target's center, last seen
        self.outcome = ""     # how track_target ended

    # ------------------------------------------------------------------ one frame
    def compute_step(self, laser_px, target_bbox, frame_dims):
        """(d_pan, d_tilt) in degrees that move the dot at laser_px (pixels) toward the
        center of target_bbox (fractions of the frame), in a frame_dims = (width, height)
        image. Each is at most max_step, in tenths of a degree, 0 within the deadband."""
        x1, y1, x2, y2 = target_bbox.to_pixels(frame_dims)
        ex, ey = (x1 + x2) / 2 - laser_px[0], (y1 + y2) / 2 - laser_px[1]
        self.error = (ex, ey)
        half_w, half_h = (x2 - x1) * (0.5 - self.margin), (y2 - y1) * (0.5 - self.margin)
        return (self._axis(0, ex, self.kp_x, half_w, self.invert_pan),
                self._axis(1, ey, self.kp_y, half_h, self.invert_tilt))

    def _axis(self, i, err, kp, half_box, invert):
        # An axis that has come within the deadband stays put until it's off by twice
        # that, so camera jitter around the edge of the deadband doesn't make it twitch.
        # Never wider than half the box, or a dot just outside a small box would count as
        # close enough and never move in.
        band = min(self.deadband * (2 if self.settled[i] else 1), half_box)
        self.settled[i] = abs(err) < band
        if self.settled[i]:
            return 0.0
        step = round(min(max(kp * err, -self.max_step), self.max_step) / STEP_RES) * STEP_RES
        if step == 0:  # outside the deadband but under a tenth: the smallest step there is
            step = math.copysign(STEP_RES, err)
        return round(-step if invert else step, 1)

    def inside(self, laser_px, target_bbox, frame_dims):
        """Is the dot inside the box, at least `margin` of its size in from the edges?"""
        x1, y1, x2, y2 = target_bbox.to_pixels(frame_dims)
        mx, my = (x2 - x1) * self.margin, (y2 - y1) * self.margin
        x, y = laser_px
        return x1 + mx <= x <= x2 - mx and y1 + my <= y <= y2 - my

    def step_towards_target(self, laser_px, target_bbox, frame_dims):
        """One frame of the loop. laser_px: where the dot is now (pixels), or None if it
        wasn't seen. Sends a step unless the dot is within the deadband. Returns True once
        the dot has been inside the box, holding still, for stability_frames frames in a
        row. Afterwards, self.wait says how long before the next frame is worth looking at."""
        self.iterations += 1
        here = (self.pointer.pan, self.pointer.tilt)
        self.wait = FRAME_SECS
        if laser_px is None:
            self.stable = 0
            self.misses += 1
            if self.misses >= LOST_FRAMES and self.seen_at and self.seen_at != here:
                # Lost it right after a step (off the image, or behind something): go back.
                self._move(*self.seen_at)
                self.misses = 0
            return False
        if self.moved_from is not None:
            if not self._moved(laser_px) and self.stale < MAX_STALE:
                self.stale += 1  # the step isn't done in this picture yet (see FRESH)
                self.extra_lag = min(self.extra_lag + STALE_SECS, MAX_EXTRA_LAG)
                self.wait = STALE_SECS
                return False
            self.moved_from = None
        self.misses, self.seen_at = 0, here
        inside = self.inside(laser_px, target_bbox, frame_dims)
        dp, dt = self.compute_step(laser_px, target_bbox, frame_dims)
        pan = round(min(max(here[0] + dp, PAN_MIN), PAN_MAX), 1)
        tilt = round(min(max(here[1] + dt, TILT_MIN), TILT_MAX), 1)
        if (pan, tilt) != here:
            # Still moving: the frames that count are the ones where it holds still.
            self.stable = self.pinned = 0
            self._move(pan, tilt, laser_px)
        elif inside:
            self.stable, self.pinned = self.stable + 1, 0
        else:  # off target, and the limits stop every step toward it
            self.stable, self.pinned = 0, self.pinned + 1
        return self.stable >= self.stability_frames

    def _move(self, pan, tilt, dot=None):
        """Step to (pan, tilt); dot: where the dot is now, to check the next picture by."""
        dx = (pan - self.pointer.pan) / self.kp_x * (-1 if self.invert_pan else 1)
        dy = (tilt - self.pointer.tilt) / self.kp_y * (-1 if self.invert_tilt else 1)
        secs = self.pointer.move(pan, tilt)
        self.moves += 1
        self.wait = secs + SETTLE_SECS + self.lag + self.extra_lag
        self.moved_from, self.expect, self.stale, self.last_pic = dot, (dx, dy), 0, None

    def _moved(self, dot):
        """Does this picture show the dot where the last step put it (see FRESH)?"""
        (x0, y0), (dx, dy) = self.moved_from, self.expect
        mx, my = dot[0] - x0, dot[1] - y0
        far = math.hypot(dx, dy)
        if math.hypot(mx, my) > OVERSHOOT * max(far, 2 * CHECK_PX):  # well past
            last, self.last_pic = self.last_pic, dot
            return last is not None and math.dist(last, dot) <= STILL_PX
        return far < CHECK_PX or (mx * dx + my * dy) / far ** 2 >= FRESH

    # ------------------------------------------------------------------ the whole loop
    def track_target(self, target_bbox, get_laser_fn, frame_dims, timeout=6.0,
                     max_iterations=40):
        """Switch the laser on, walk the dot onto target_bbox and hold it there for
        hold_secs. get_laser_fn() returns where the dot is now (pixels) or None.
        frame_dims: (width, height) of the frames it looks at. Blocks until the dot is on
        target (True), or the target is past the servo limits, or timeout seconds or
        max_iterations frames have gone by (False; self.outcome says which). The laser is
        off when it returns, however it returns."""
        self.reset()
        if not self.pointer.connected:
            self.outcome = "no pointer"
            return False
        start = self.clock()
        try:
            self.pointer.send("L1")
            self.sleep(LASER_SETTLE_SECS + self.lag)
            while self.iterations < max_iterations:
                if self.clock() - start > timeout:
                    self.outcome = "timed out"
                    return False
                if self.step_towards_target(get_laser_fn(), target_bbox, frame_dims):
                    self.outcome = "on target"
                    self.sleep(self.hold_secs)
                    return True
                if self.pinned >= self.stability_frames:
                    self.outcome = "out of reach"
                    return False
                self.sleep(self.wait)
            self.outcome = "too many frames"
            return False
        finally:
            self.pointer.send("L0")
