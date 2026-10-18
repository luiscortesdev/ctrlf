"""The calibration math (src/vision/aim_map.py) on a simulated room built like the real
one (tests/sim_rig.py): a 1080p fisheye camera up high, the laser head next to it, a table,
servos with gain and offset errors. Aiming is judged where it matters, by where the dot
really lands, at spots the calibration never saw."""

import json

import numpy as np
import pytest

from src.vision import aim_map
from src.vision.laser_tracker import TILT_MIN, Calibration
from tests.sim_rig import H, W, SimRig

LIMITS = (0, 180, TILT_MIN, 160)


def aim(fit):
    return lambda xy: fit.map(aim_map.normalize(xy, (W, H)))


def guided(rig, nx, ny, seed=0, margin=0.15):
    return rig.guided_points(rig.table_grid(nx, ny, margin), np.random.default_rng(seed))


@pytest.mark.parametrize("baseline", [0.05, 0.15, 0.5])
def test_twelve_guided_points_on_the_table_aim_within_a_couple_of_cm(baseline):
    """12 clicks on the table, wherever the head sits relative to the camera: spots all
    over the table (also outside the clicked ones) get the dot within a few cm."""
    rig = SimRig(baseline=(baseline, 0, -0.05))
    fit = aim_map.fit_points(guided(rig, 4, 3), (W, H), LIMITS)
    targets = rig.table_grid(12, 9, margin=0.08)
    cm, px = rig.miss(aim(fit), targets)
    assert np.median(cm) < 0.02 and np.percentile(cm, 90) < 0.04
    assert np.sqrt(np.mean(px ** 2)) < 6 and px.max() < 15


def test_the_reported_error_is_honest():
    """It says how far it misses at NEW spots: never much better than the truth (the old
    fit reported its error on its own points, 2-3x too rosy)."""
    for seed in range(4):
        rig = SimRig(baseline=(0.1, 0, -0.05))
        fit = aim_map.fit_points(guided(rig, 4, 3, seed), (W, H), LIMITS)
        _, px = rig.miss(aim(fit), rig.table_grid(12, 9, margin=0.12))
        true_px = np.sqrt(np.mean(px ** 2))
        assert fit.error_px >= 0.8 * true_px, (fit.error_px, true_px)
        assert fit.error_px <= 4 * true_px + 3
        assert fit.fit_px <= fit.error_px  # on its own points it always looks better


def test_few_points_dont_swing_wild():
    """8 points: a polynomial with that few can swing tens of cm off between and past
    them; the projective map, which knows the shape of the problem, stays close."""
    rig = SimRig()
    pts = guided(rig, 4, 2)
    fit = aim_map.fit_points(pts, (W, H), LIMITS)
    targets = rig.table_grid(12, 9, margin=0.05)
    cm, px = rig.miss(aim(fit), targets)
    assert fit.kind in ("projective", "projective+tps") and np.median(cm) < 0.02 and px.max() < 15
    P = np.asarray(pts)
    poly = aim_map.PolyMap(2).fit(aim_map.normalize(P[:, 2:], (W, H)), P[:, :2])
    _, poly_px = rig.miss(lambda xy: poly(aim_map.normalize(xy, (W, H))), targets)
    assert poly_px.max() > 2 * px.max()


def test_more_points_more_accurate():
    rig = SimRig()
    targets = rig.table_grid(12, 9, margin=0.08)
    few = rig.miss(aim(aim_map.fit_points(guided(rig, 3, 2), (W, H), LIMITS)), targets)[1]
    many = rig.miss(aim(aim_map.fit_points(guided(rig, 5, 4, margin=0.1), (W, H), LIMITS)), targets)[1]
    assert np.sqrt(np.mean(many ** 2)) < np.sqrt(np.mean(few ** 2))


def test_calibrate_on_the_surface_you_aim_at():
    """Why the guided calibration happens on the demo table: a pixel -> angle map only
    holds for one surface unless the head sits right at the camera. Calibrated on the floor
    and aimed at the table top, a head 50 cm from the camera misses by ~18 cm (keys are 6); 5 cm
    away it's a couple of cm. Calibrated on the table, both are fine."""
    def floor_sweep(rig):
        rng, pts = np.random.default_rng(0), []
        for pan in range(60, 121, 10):
            for tilt in range(114, 135, 5):
                land = rig.landing(pan, tilt)
                xy = rig.pixel(land)
                if land[2] < 0.01 and 0 <= xy[0] < W and 0 <= xy[1] < H:
                    pts.append((pan, tilt, *(xy + rng.normal(0, 1.5, 2))))
        return pts

    table = SimRig().table_grid(12, 9, margin=0.08)
    near, far = SimRig(baseline=(0.05, 0, -0.05)), SimRig(baseline=(0.5, 0, -0.05))
    assert np.median(near.miss(aim(aim_map.fit_points(floor_sweep(near), (W, H), LIMITS)), table)[0]) < 0.04
    assert np.median(far.miss(aim(aim_map.fit_points(floor_sweep(far), (W, H), LIMITS)), table)[0]) > 0.12
    assert np.median(far.miss(aim(aim_map.fit_points(guided(far, 4, 3), (W, H), LIMITS)), table)[0]) < 0.03


def test_a_misclick_is_left_out():
    rig = SimRig()
    pts = guided(rig, 4, 4, margin=0.1)
    pan, tilt, x, y = pts[5]
    pts[5] = (pan, tilt, x + 150, y - 90)  # clicked the wrong spot
    fit = aim_map.fit_points(pts, (W, H), LIMITS)
    assert [p for p, _ in fit.dropped] == [pts[5]] and "odd" in fit.dropped[0][1]
    _, px = rig.miss(aim(fit), rig.table_grid(12, 9, margin=0.08))
    assert np.sqrt(np.mean(px ** 2)) < 6


def test_points_the_head_cant_reach_are_left_out():
    """The old hand calibration had clicks on the walls at tilt 90, above today's limit:
    they can never be aimed at and only bend the fit."""
    rig = SimRig()
    pts = guided(rig, 4, 3) + [(80, 90, 700, 40), (100, 92, 1300, 60)]
    fit = aim_map.fit_points(pts, (W, H), LIMITS)
    assert len(fit.kept) == 12 and all("limits" in why for _, why in fit.dropped)


def test_too_few_points():
    rig = SimRig()
    with pytest.raises(ValueError):
        aim_map.fit_points(guided(rig, 5, 1)[:5], (W, H), LIMITS)


@pytest.mark.parametrize("kind", aim_map.KINDS)
def test_every_kind_saves_loads_and_inverts(kind, tmp_path):
    rig = SimRig()
    pts = guided(rig, 5, 4, margin=0.1)
    fit = aim_map.fit_points(pts, (W, H), LIMITS, kinds=(kind,))
    calib = Calibration(fit.map, fit.kind, fit.kept, fit.error_deg, fit.error_px, [W, H],
                        manual=True, dot_visible=False)
    path = str(tmp_path / "calibration.json")
    calib.save(path)
    back = Calibration.load(path)
    assert back.kind == kind and back.manual and back.dot_visible is False
    for u, v in ((0.5, 0.5), (0.45, 0.6), (0.55, 0.4)):
        assert back.to_angles(u, v) == pytest.approx(calib.to_angles(u, v), abs=1e-9)
        pan, tilt = back.to_angles(u, v)
        assert back.to_image(pan, tilt) == pytest.approx((u, v), abs=1e-4)


def test_an_old_calibration_file_is_refitted(tmp_path):
    """calibration.json from before (one polynomial): loads, fitted again from its points,
    leaving out the ones above today's tilt limit."""
    rig = SimRig()
    pts = [list(p) for p in guided(rig, 4, 3)] + [[80, 90, 700, 40]]
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps({"degree": 2, "to_angles": [], "to_image": [], "points": pts,
                                "error_deg": 0.1, "error_px": 9, "size": [W, H], "manual": True}))
    calib = Calibration.load(str(path))
    assert calib.refitted and calib.manual and not calib.dot_visible
    assert len(calib.points) == 12 and len(calib.dropped) == 1
    pan, tilt = calib.to_angles(0.5, 0.5)
    assert TILT_MIN <= tilt <= 160
