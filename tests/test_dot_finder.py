"""Finding the laser dot the way the real camera shows it, which is where the old sweep
found 0 of 35: a dot 1-2 pixels wide in a 1080p picture, squashed by compression (JPEG
here: like H.264 it keeps color at half resolution, which smears a tiny red dot), arriving
1.4 s late, with a laptop screen redrawing, someone walking through and the camera's clock
ticking. Plus the dot blown out to white, and the camera in black-and-white night mode.

The camera is simulated (Scene); the laser switches as Blink sends L0/L1, and each frame
shows the laser as it was `lag` seconds before the frame arrives."""

import cv2
import numpy as np
import pytest

from src.vision import laser_tracker
from src.vision.laser_tracker import Blink, find_dot, locate_dot

W, H = 1920, 1080
FPS = 15
DOT = (903.4, 611.7)


class Clock:
    t = 100.0


class Pointer:
    """Records when the laser was switched."""
    connected = True

    def __init__(self, clock):
        self.clock, self.log = clock, [(-1e9, False)]

    def send(self, cmd):
        if cmd in ("L0", "L1"):
            self.log.append((self.clock.t, cmd == "L1"))

    def laser_at(self, t):
        return [on for when, on in self.log if when <= t][-1]


class Scene:
    """A textured floor and table, the dot where the head points (if dot), and whatever
    else is going on."""

    def __init__(self, seed=0, dot=DOT, size=1.2, gain=90, white=False, gray=False,
                 screen=False, person=False, clock=False, quality=70):
        rng = np.random.default_rng(seed)
        self.rng = rng
        self.base = cv2.GaussianBlur(rng.integers(70, 160, (H, W, 3)).astype(np.uint8), (5, 5), 0)
        self.dot, self.size, self.gain, self.white = dot, size, gain, white
        self.gray, self.screen, self.person, self.clock, self.quality = gray, screen, person, clock, quality

    def frame(self, t, laser):
        img = self.base.astype(np.float32) + self.rng.normal(0, 2.5, (H, W, 3)).astype(np.float32)
        if self.screen:  # a laptop screen redrawing lines of text, every frame
            for _ in range(3):
                y = int(self.rng.integers(150, 330))
                img[y:y + 6, 300:300 + int(self.rng.integers(30, 220))] = 235
        if self.person:  # someone walking across, a dark blob with a bright shirt
            x = int(200 + 400 * t) % (W - 300)
            img[500:1000, x:x + 260] = (40, 50, 70)
            img[600:760, x + 40:x + 220] = (200, 200, 230)
        if self.clock:  # the camera's timestamp: white digits that change every second
            digits = cv2.putText(np.zeros((40, 260), np.uint8), f"{int(t) % 100000:05d}", (5, 32),
                                 cv2.FONT_HERSHEY_SIMPLEX, 1.2, 255, 3)
            img[20:60, 1600:1860][digits > 0] = 250
        if laser and self.dot:
            x0, y0 = self.dot
            ys, xs = np.mgrid[int(y0) - 6:int(y0) + 7, int(x0) - 6:int(x0) + 7]
            spot = np.exp(-((xs - x0) ** 2 + (ys - y0) ** 2) / (2 * (self.size / 2) ** 2))
            patch = img[ys, xs]
            patch[..., 2] += self.gain * spot
            # Blown out: the sensor saturates in the middle (white), the rim stays red.
            core = np.clip((spot - 0.5) * 2, 0, 1) if self.white else spot / 5
            patch[..., :2] += self.gain * core[..., None]
            img[ys, xs] = patch
        img = np.clip(img, 0, 255).astype(np.uint8)
        if self.gray:
            img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        if self.quality:
            img = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.quality])[1],
                               cv2.IMREAD_COLOR)
        return img


def look(scene, lag=1.4, assumed_lag=None, near=None, pattern=Blink.PATTERN, secs=20):
    """Run one Blink against the scene, the camera `lag` seconds behind. Returns it done."""
    clock = Clock()
    pointer = Pointer(clock)
    blink = Blink(pointer, lag if assumed_lag is None else assumed_lag, near, pattern)
    blink.start(clock.t, 0.3)
    end = clock.t + secs
    while not blink.update(scene.frame(clock.t, pointer.laser_at(clock.t - lag)), clock.t):
        clock.t += 1 / FPS
        assert clock.t < end, "the look never finished"
    assert not pointer.laser_at(clock.t)  # always leaves the laser off
    return blink


def on_the_dot(blink, within=1.5):
    assert blink.dot is not None, blink.why
    assert np.hypot(blink.dot.x - DOT[0], blink.dot.y - DOT[1]) < within, blink.why
    return blink.dot


NEAR = (DOT[0] + 60, DOT[1] - 40, 0.15 * W)  # where a calibration expects it: a bit off


@pytest.mark.parametrize("size", [1.2, 2.0])
def test_a_dot_one_or_two_pixels_wide_through_compression(size):
    on_the_dot(look(Scene(size=size, gain=70), near=NEAR))


def test_too_faint_to_see_says_so():
    """A dim dot smaller than a pixel is lost in the noise once compressed: it says it saw
    nothing (then the calibration is done by hand and the Aimer doesn't watch for the dot),
    rather than calling some noise the dot."""
    for seed in range(3):
        blink = look(Scene(seed=seed, size=0.6, gain=40), near=NEAR)
        assert blink.dot is None or np.hypot(blink.dot.x - DOT[0], blink.dot.y - DOT[1]) < 2


def test_whole_picture_search_without_a_calibration():
    on_the_dot(look(Scene(screen=True, clock=True)))


def test_people_and_screens_dont_hide_the_dot():
    on_the_dot(look(Scene(screen=True, person=True, clock=True), near=(DOT[0], DOT[1], 500)))


def test_no_dot_in_view_means_no_dot():
    """The laser pointing outside the picture while everything else moves: it must say
    there's no dot, not pick a screen, a person or the clock."""
    for near in (None, (DOT[0], DOT[1], 500)):
        blink = look(Scene(dot=None, screen=True, person=True, clock=True), near=near)
        assert blink.dot is None, blink.why


def test_blown_out_to_white():
    dot = on_the_dot(look(Scene(gain=200, white=True, size=2.5), near=NEAR))
    assert dot.saturated


def test_black_and_white_night_mode():
    on_the_dot(look(Scene(gray=True, gain=60), near=NEAR))


def test_a_camera_later_than_assumed_finds_nothing_rather_than_something_wrong():
    """Assume 0.3 s when it's really 1.4 s (what happened with CAMERA_LAG_SECS = 0.8 on
    the Tapo): the 'on' frames still show the laser off. No false dot, and the reason
    says nothing brightened."""
    blink = look(Scene(screen=True), lag=1.4, assumed_lag=0.3, near=NEAR)
    assert blink.dot is None and "nothing brightened" in blink.why
    on_the_dot(look(Scene(screen=True), lag=1.4, assumed_lag=1.5, near=NEAR))


def test_camera_stops_sending():
    clock = Clock()
    blink = Blink(Pointer(clock), 0.2)
    blink.start(clock.t)
    while not blink.update(None, clock.t):
        clock.t += 0.1
        assert clock.t < 110
    assert blink.dot is None and "no pictures" in blink.why


def test_one_pair_looks_past_a_bigger_change_elsewhere():
    """The old finder looked only at the biggest change in the picture: one line of a
    laptop screen redrawing was enough to hide a clear dot."""
    rng = np.random.default_rng(0)
    off = cv2.GaussianBlur(rng.integers(90, 140, (H, W, 3)).astype(np.uint8), (3, 3), 0)
    on = off.astype(np.int16)
    on[600:603, 900:903, 2] += 160          # a clear 3x3 red dot
    on[200:206, 300:330] += 120             # a line of text redrawn on a screen
    on = np.clip(on, 0, 255).astype(np.uint8)
    x, y = find_dot(off, on)
    assert abs(x - 901) < 1 and abs(y - 601) < 1
    assert find_dot(off, off.copy()) is None


def test_says_why():
    rng = np.random.default_rng(1)
    off = rng.integers(60, 100, (480, 640, 3)).astype(np.uint8)
    assert locate_dot([(off, off.copy())]) is None and "nothing brightened" in locate_dot.why
    on = off.copy()
    on[100:300, 100:300] = 250  # a big change, nothing dot-like
    assert locate_dot([(off, on)]) is None and "too big" in locate_dot.why


def test_timing_constants_are_sane():
    # A look takes about 3 x (lag + settle + capture): ~6 s with a 1.4 s camera.
    assert laser_tracker.CAPTURE_SECS >= 0.2 and laser_tracker.LASER_SETTLE_SECS >= 0.1
