"""Visual servoing (src/vision/visual_servoing.py) against the simulated head, firmware
and camera in mock_kinematics.py: no ESP32 or webcam needed. `pytest tests/sim -v -s`
also prints the Monte Carlo report."""

import math
import statistics

import pytest

from src.vision.laser_tracker import PAN_MAX, PAN_MIN, TILT_MAX, TILT_MIN
from src.vision.visual_servoing import MAX_STEP_DEG, BoundingBox, VisualServoController
from tests.sim.mock_kinematics import CENTER, PX_PER_DEG, VirtualKinematicRig, box_at, monte_carlo

SIZE = (1280, 720)


def within_limits(requests):
    return all(PAN_MIN <= p <= PAN_MAX and TILT_MIN <= t <= TILT_MAX for p, t in requests)


def run_frames(rig, ctl, box, frames):
    """Call step_towards_target frame by frame, as track_target does, but keep going after
    it's on target. Returns (frame it first said on target or None, the step each frame)."""
    rig.pointer.send("L1")
    rig.clock.sleep(0.5)
    first, steps = None, []
    for i in range(frames):
        before = (rig.pointer.pan, rig.pointer.tilt)
        if ctl.step_towards_target(rig.get_laser(), box, rig.size) and first is None:
            first = i
        steps.append((round(rig.pointer.pan - before[0], 1), round(rig.pointer.tilt - before[1], 1)))
        rig.clock.sleep(ctl.wait)
    return first, steps


def reversals(values):
    signs = [math.copysign(1, v) for v in values if v]
    return sum(a != b for a, b in zip(signs, signs[1:]))


# ------------------------------------------------------------------ the harness itself
def test_harness_follows_the_firmware():
    """The mock is only worth testing against if it behaves like the board: limits
    clamped, tenths of a degree, a glide that takes time, and violations counted."""
    rig = VirtualKinematicRig(slop_deg=0.0, jitter_px=0.0, latency=0.0)
    assert (rig.pointer.pan, rig.pointer.tilt) == CENTER
    secs = rig.pointer.move(100.04, 130)
    assert rig.pointer.ser.lines[-1] == "P100 T130"
    assert rig.pointer.ser.angles(rig.clock())[0] == 90  # hasn't started gliding yet
    rig.clock.sleep(secs)
    assert rig.pointer.ser.angles(rig.clock()) == pytest.approx((100, 130))
    assert rig.get_laser() is None  # laser off
    rig.pointer.send("L1")
    rig.clock.sleep(0.01)
    assert rig.get_laser() == pytest.approx(rig.project(100, 130))
    assert rig.get_laser()[0] < SIZE[0] / 2  # this head: more pan = left
    assert rig.pointer.safety_violations == 0
    rig.pointer.move(90, TILT_MIN - 30)  # above the table: Pointer clamps it, but it was asked
    assert rig.pointer.safety_violations == 1
    rig.clock.sleep(1)
    assert rig.pointer.ser.angles(rig.clock())[1] == TILT_MIN
    assert rig.get_laser() is None  # that's above the picture
    rig.pointer.send(f"T{TILT_MIN - 30}")  # straight to the board, around Pointer's clamp
    assert rig.pointer.safety_violations == 2


def test_harness_camera_lag():
    """The camera shows where the head was `latency` seconds ago."""
    rig = VirtualKinematicRig(slop_deg=0.0, jitter_px=0.0, latency=0.3)
    rig.pointer.send("L1")
    rig.clock.sleep(1)
    rig.pointer.move(80, 125)  # 10 degrees: a 0.1 s glide
    rig.clock.sleep(0.2)
    assert rig.get_laser() == pytest.approx(rig.project(90, 125))  # still the old picture
    rig.clock.sleep(0.2)
    assert rig.get_laser() == pytest.approx(rig.project(80, 125))


# ------------------------------------------------------------------ the controller
def test_coordinate_normalization():
    """Boxes are fractions of the frame and the dot is in pixels: the box must be scaled
    to pixels, not compared as is (0.65 against 832)."""
    box = BoundingBox(0.6, 0.5, 0.7, 0.7)  # center (0.65, 0.6) = pixel (832, 432)
    assert box.center == pytest.approx((0.65, 0.6))
    assert box.to_pixels(SIZE) == pytest.approx((768, 360, 896, 504))
    back = BoundingBox.from_pixels(768, 360, 896, 504, SIZE)
    assert (back.x1, back.y1, back.x2, back.y2) == pytest.approx((0.6, 0.5, 0.7, 0.7))

    ctl = VisualServoController(None, invert_pan=False, invert_tilt=False)
    assert ctl.compute_step((832, 432), box, SIZE) == (0, 0)
    assert ctl.error == pytest.approx((0, 0))
    # Where the center would be if it weren't scaled: far up and left, so full steps.
    assert ctl.compute_step((0.65, 0.6), box, SIZE) == (MAX_STEP_DEG, MAX_STEP_DEG)
    # Same box in a smaller frame: a different pixel, still the center.
    assert ctl.compute_step((416, 216), box, (640, 360)) == (0, 0)
    # A proportional step in tenths: 50 px right, 25 px down of the dot.
    assert ctl.compute_step((782, 407), box, SIZE) == (2.0, 1.0)


def test_basic_convergence():
    rig = VirtualKinematicRig(seed=1)
    assert (rig.pointer.pan, rig.pointer.tilt) == CENTER
    ctl = rig.controller()
    box = box_at(0.65, 0.60, 0.08, 0.10)
    assert ctl.track_target(box, rig.get_laser, rig.size)
    assert ctl.outcome == "on target"
    assert ctl.iterations <= 15
    assert rig.dot_inside(box)  # really there, not only in the camera's eyes
    assert not rig.pointer.laser  # off again after the hold
    assert rig.pointer.ser.lines[0] == "L1" and rig.pointer.ser.lines[-1] == "L0"
    assert rig.pointer.safety_violations == 0


@pytest.mark.parametrize("box", [
    box_at(0.5, 0.02, 0.08, 0.04),          # at the top of the picture, above the table
    BoundingBox(0.45, -0.4, 0.55, -0.3),    # above the picture: the ceiling
], ids=["top-edge", "ceiling"])
def test_safety_clamp_enforcement(box):
    """A target the dot can only reach by tilting up past TILT_MIN: it goes as far as the
    limit allows, never asks for more, and gives up without timing out."""
    # A camera that sees well above the highest tilt, so the dot stays in view at the limit.
    rig = VirtualKinematicRig(px_per_deg=(PX_PER_DEG[0], 8), seed=2)
    ctl = rig.controller()
    assert not ctl.track_target(box, rig.get_laser, rig.size, timeout=6.0, max_iterations=40)
    assert ctl.outcome == "out of reach"
    assert ctl.iterations <= 40
    assert min(t for _, t in rig.pointer.requests) == TILT_MIN  # went right up to the limit
    assert within_limits(rig.pointer.requests)
    assert rig.pointer.safety_violations == 0
    assert not rig.pointer.laser


def test_gives_up_without_the_dot():
    """No dot, ever (laser blocked): it stops at the timeout or the frame limit, and
    switches the laser off."""
    rig = VirtualKinematicRig()
    ctl = rig.controller()
    assert not ctl.track_target(box_at(0.3, 0.6, 0.1, 0.1), lambda: None, rig.size,
                                timeout=6.0, max_iterations=40)
    assert ctl.outcome in ("timed out", "too many frames")
    assert ctl.iterations <= 40 and rig.clock() <= 6.0 + 1.0
    assert ctl.moves == 0  # nothing to steer by, so no blind moves
    assert not rig.pointer.laser


def test_laser_off_after_an_error():
    rig = VirtualKinematicRig()
    ctl = rig.controller()

    def broken_camera():
        raise RuntimeError("camera unplugged")

    with pytest.raises(RuntimeError):
        ctl.track_target(box_at(0.3, 0.6, 0.1, 0.1), broken_camera, rig.size)
    assert rig.pointer.ser.lines[-1] == "L0" and not rig.pointer.laser


def test_finds_the_dot_again_after_losing_it():
    """A step that takes the dot off the picture: it goes back to where it last saw it."""
    rig = VirtualKinematicRig(seed=4)
    ctl = rig.controller()
    box = box_at(0.5, 0.5, 0.1, 0.1)
    rig.pointer.send("L1")
    rig.clock.sleep(0.5)
    ctl.step_towards_target(rig.get_laser(), box, rig.size)  # seen here; stays (on center)
    seen_at = (rig.pointer.pan, rig.pointer.tilt)
    rig.pointer.move(seen_at[0] + 40, seen_at[1])  # something knocked it off the picture
    rig.clock.sleep(1)
    for _ in range(2):
        assert rig.get_laser() is None
        ctl.step_towards_target(rig.get_laser(), box, rig.size)
    assert (rig.pointer.pan, rig.pointer.tilt) == seen_at
    rig.clock.sleep(ctl.wait)
    assert rig.get_laser() is not None


def test_oscillation_damping():
    """A small target just off center, a camera with 3x the usual jitter: it settles and
    stays put, no hopping back and forth across the box's edge."""
    rig = VirtualKinematicRig(jitter_px=3.0, seed=3)
    ctl = rig.controller()
    box = box_at(0.53, 0.52, 0.05, 0.07)
    first, steps = run_frames(rig, ctl, box, 30)
    assert first is not None and first <= 6
    assert reversals([s[0] for s in steps]) == 0
    assert reversals([s[1] for s in steps]) == 0
    assert all(s == (0, 0) for s in steps[first:])  # once on target, it holds still
    assert ctl.stable == 30 - first + 2  # every frame since was inside


def test_counts_only_frames_holding_still():
    """Inside a big box but off its center, it steps closer: that frame doesn't count
    toward on target, since the next picture may catch the head on its way."""
    rig = VirtualKinematicRig(jitter_px=0.0, slop_deg=0.0, seed=9)
    ctl = rig.controller()
    box = box_at(0.53, 0.50, 0.3, 0.3)  # the dot starts inside, 38 px left of the center
    rig.pointer.send("L1")
    rig.clock.sleep(0.5)
    assert ctl.inside(rig.get_laser(), box, rig.size)
    assert not ctl.step_towards_target(rig.get_laser(), box, rig.size)
    assert ctl.moves == 1 and ctl.stable == 0
    first, steps = run_frames(rig, ctl, box, 10)
    assert first == 2 and steps[:3] == [(0, 0)] * 3  # three frames holding still after the step


def test_deadband_hysteresis():
    """Within 8 px an axis stops; then it takes 16 px to move it again."""
    ctl = VisualServoController(None, invert_pan=False, invert_tilt=False)
    box = BoundingBox.from_pixels(600, 300, 700, 400, SIZE)  # center (650, 350)
    assert ctl.compute_step((640, 350), box, SIZE) == (0.4, 0)  # 10 px off in x only
    assert ctl.compute_step((643, 356), box, SIZE) == (0, 0)    # 7 and 6 px: close enough
    assert ctl.compute_step((636, 336), box, SIZE) == (0, 0)    # 14 px each way: jitter
    assert ctl.compute_step((630, 350), box, SIZE) == (0.8, 0)  # 20 px: it moved, follow
    assert ctl.compute_step((640, 350), box, SIZE) == (0.4, 0)  # not settled: 10 px is too far


def test_tiny_box_smaller_than_the_deadband():
    """An 8 x 8 px box: the deadband shrinks to fit, or a dot 5 px off would never move in."""
    rig = VirtualKinematicRig(jitter_px=0.5, slop_deg=0.05, seed=5)
    ctl = rig.controller()
    box = BoundingBox.from_pixels(796, 446, 804, 454, rig.size)
    assert ctl.compute_step((805, 450), box, rig.size) != (0, 0)
    assert ctl.track_target(box, rig.get_laser, rig.size)


@pytest.mark.parametrize("invert_pan", [False, True])
@pytest.mark.parametrize("invert_tilt", [False, True])
def test_axis_inversions(invert_pan, invert_tilt):
    """Servos mounted either way round: with the flags set to match, it converges."""
    rig = VirtualKinematicRig(invert_pan=invert_pan, invert_tilt=invert_tilt, seed=6)
    ctl = rig.controller(invert_pan=invert_pan, invert_tilt=invert_tilt)
    box = box_at(0.30, 0.70, 0.08, 0.10)
    assert ctl.track_target(box, rig.get_laser, rig.size)
    assert rig.dot_inside(box)
    assert rig.pointer.safety_violations == 0


def test_wrong_inversion_fails_safely():
    """A flag set wrong drives the dot away from the target: it doesn't get there, but it
    stays within the limits and ends with the laser off."""
    rig = VirtualKinematicRig(invert_pan=True, seed=7)
    ctl = rig.controller(invert_pan=False)
    assert not ctl.track_target(box_at(0.30, 0.50, 0.08, 0.10), rig.get_laser, rig.size)
    assert within_limits(rig.pointer.requests)
    assert rig.pointer.safety_violations == 0
    assert not rig.pointer.laser


def test_camera_slower_than_told():
    """The camera runs 0.4 s behind, the controller was told 0.1 s: the picture after each
    step still shows the head before it (a one-step lag). Acting on those would step twice
    and overshoot, and a dot passing through the box would pass for on target. It notices
    the pictures are stale, waits longer, and gets there for real."""
    rig = VirtualKinematicRig(latency=0.4, seed=8)
    ctl = rig.controller()
    box = box_at(0.25, 0.70, 0.06, 0.08)
    assert ctl.track_target(box, rig.get_laser, rig.size)
    assert rig.dot_inside(box)
    assert 0.1 <= ctl.extra_lag <= 0.5  # learned roughly the missing 0.3 s
    assert rig.pointer.safety_violations == 0


def test_monte_carlo_stale_camera():
    """50 targets with the camera 0.2-0.35 s further behind than the controller was told."""
    runs = monte_carlo(runs=50, seed=1, latency=(0.3, 0.45))
    done = [r for r in runs if r["converged"]]
    print(f"\nStale camera: {len(done)}/50 converged, mean {statistics.mean(r['iterations'] for r in done):.1f} "
          f"frames, failures {[r['outcome'] for r in runs if not r['converged']]}")
    assert len(done) >= 47
    assert all(r["on_target"] for r in done)  # it never says on target when it isn't
    assert sum(r["violations"] for r in runs) == 0


def test_monte_carlo_grid():
    """50 random targets across the reachable desk, each on a rig that's off from what the
    controller assumes (see monte_carlo)."""
    runs = monte_carlo(runs=50, seed=0)
    done = [r for r in runs if r["converged"]]
    rate = len(done) / len(runs)
    mean_iters = statistics.mean(r["iterations"] for r in done)
    report = [
        "",
        "Monte Carlo: 50 targets, x 0.15-0.85, y 0.20-0.80",
        f"  converged          {len(done)}/{len(runs)} ({rate:.0%})",
        f"  really on target   {sum(r['on_target'] for r in done)}/{len(done)}",
        f"  frames (mean/max)  {mean_iters:.1f} / {max(r['iterations'] for r in done)}",
        f"  steps sent (mean)  {statistics.mean(r['moves'] for r in done):.1f}",
        f"  time (mean/max)    {statistics.mean(r['secs'] for r in done):.2f} s / "
        f"{max(r['secs'] for r in done):.2f} s",
        f"  limit violations   {sum(r['violations'] for r in runs)}",
        f"  laser left on      {sum(not r['laser_off'] for r in runs)}",
        f"  failures           {[r['outcome'] for r in runs if not r['converged']]}",
    ]
    print("\n".join(report))
    assert rate >= 0.98
    assert all(r["on_target"] for r in done)
    assert mean_iters <= 12
    assert sum(r["violations"] for r in runs) == 0
    assert all(r["laser_off"] for r in runs)
