"""
Pointing the laser at what the camera sees: a detection's box -> its centre -> the pan/tilt
angles the calibration (data/calibration.json) says put the dot there -> the servos, laser
on for a few seconds, then off.

ObjectPointer blocks while the laser shows the spot (point_at_pixel sleeps): it's for
scripts like demo_find_and_point.py. The app's live loop points with laser_tracker.Aimer
instead: the same calibration and limits, without freezing the video (and it corrects by
watching the dot when the camera can see it).
"""

import difflib
import math
import threading
import time
from typing import Optional, Sequence, Tuple

from src.vision.laser_tracker import (CALIB_PATH, PAN_MAX, PAN_MIN, SETTLE_SECS, TILT_MAX,
                                      TILT_MIN, Calibration)

HOLD_SECS = 4.0
KEEP_ON_SECS = 8.0  # re-send L1 this often: the firmware turns the laser off after 15 s idle


def safe_angles(pan, tilt):
    """Clamped to the head's safe range, in tenths of a degree (what the firmware takes).
    Higher tilt = down: TILT_MIN is the lowest the beam may rise (the bottom of the far
    wall), so it never points above the table, toward people."""
    if not (math.isfinite(pan) and math.isfinite(tilt)):
        raise ValueError(f"no sensible angles for that spot ({pan}, {tilt})")
    return (round(min(max(pan, PAN_MIN), PAN_MAX), 1), round(min(max(tilt, TILT_MIN), TILT_MAX), 1))


def _parse(det):
    """(label, confidence, (x1, y1, x2, y2)) from a detection in any of the shapes around:
    the detector's tuples, (label, box), a dict, or an object with .label and .bbox/.box."""
    if isinstance(det, dict):
        box = det.get("box") or det.get("bbox")
        label, conf = det.get("label"), det.get("confidence", det.get("score", 1.0))
    elif isinstance(det, (tuple, list)):
        label, conf, box = (det[0], 1.0, det[1]) if len(det) == 2 else det[:3]
    else:
        label, conf = det.label, getattr(det, "confidence", 1.0)
        box = getattr(det, "bbox", None) or det.box
    if hasattr(box, "x1"):
        box = (box.x1, box.y1, box.x2, box.y2)
    return str(label), float(conf), tuple(float(v) for v in box)


class ObjectPointer:
    """pointer: serial_client.Pointer. calib: a Calibration (loaded from calib_path if not
    given). frame_size: (width, height) of the camera picture that pixel coordinates refer
    to (default: the calibration's)."""

    def __init__(self, pointer, calib=None, frame_size=None, calib_path=CALIB_PATH):
        self.pointer = pointer
        self.calib = calib or Calibration.load(calib_path)
        if self.calib is None:
            raise FileNotFoundError(f"No {calib_path}: run scripts/calibrate.py first.")
        self.frame_size = tuple(frame_size or self.calib.size)
        self.lock = threading.Lock()  # one pointing at a time
        self.target = None  # (u, v) being pointed at now, as fractions of the picture

    def pixel_to_servo(self, u, v, frame_size=None, normalized=None) -> Tuple[float, float]:
        """(u, v): a spot in the picture, as fractions [0, 1] or as pixels of a frame_size
        picture (normalized=None: fractions if both are <= 1). Returns safe (pan, tilt)."""
        w, h = frame_size or self.frame_size
        if normalized is None:
            normalized = u <= 1.0 and v <= 1.0
        if not normalized:
            u, v = u / w, v / h
        return safe_angles(*self.calib.to_angles(u, v))

    def point_at_pixel(self, u, v, duration=HOLD_SECS, frame_size=None, normalized=None) -> bool:
        """Glide there with the laser off, laser on for `duration` seconds, laser off (always,
        even if something fails). False if the ESP32 isn't connected or it's busy pointing."""
        if not self.pointer.connected:
            print("(no ESP32 connected: can't point)")
            return False
        pan, tilt = self.pixel_to_servo(u, v, frame_size, normalized)
        if not self.lock.acquire(blocking=False):
            print("(still pointing at the last one)")
            return False
        try:
            w, h = frame_size or self.frame_size
            self.target = (u, v) if (normalized or (normalized is None and u <= 1 and v <= 1)) \
                else (u / w, v / h)
            secs = self.pointer.move(pan, tilt, laser=False)  # off on the way: no sweep across the room
            time.sleep(secs + SETTLE_SECS)
            end = time.time() + duration
            while time.time() < end:
                self.pointer.send("L1")
                time.sleep(min(KEEP_ON_SECS, end - time.time()))
            return True
        finally:
            self.pointer.send("L0")
            self.target = None
            self.lock.release()

    def find(self, target_label, detections: Sequence) -> Optional[Tuple[str, float, tuple]]:
        """The best detection matching target_label: the same name (any case), a name that
        contains it or the other way round ("keys" / "car keys"), what the app calls it
        ("airpods" -> "earbuds case"), or a close spelling. Highest confidence wins."""
        want = target_label.strip().lower()
        if not want:
            return None
        dets = [_parse(d) for d in detections]
        names = {label.lower() for label, _, _ in dets}
        try:
            from src.app import find_label  # the app's own words for things
            word = find_label(want)
            if word:
                names.add(word)
                want_also = word.lower()
            else:
                want_also = want
        except Exception:
            want_also = want
        close = set(difflib.get_close_matches(want, list(names), n=3, cutoff=0.6))

        def matches(label):
            label = label.lower()
            return (label in (want, want_also) or want in label or label in want
                    or label in close)

        found = [d for d in dets if matches(d[0])]
        return max(found, key=lambda d: d[1]) if found else None

    def point_at_object(self, target_label, detections: Sequence, duration=HOLD_SECS,
                        frame_size=None) -> bool:
        """Point at target_label if it's among the detections (boxes in pixels of frame_size,
        or fractions). False, saying so, if it isn't visible right now."""
        det = self.find(target_label, detections)
        if det is None:
            print(f"(I don't see {target_label} right now)")
            return False
        label, conf, (x1, y1, x2, y2) = det
        u, v = (x1 + x2) / 2, (y1 + y2) / 2
        normalized = max(x1, y1, x2, y2) <= 1.0
        pan, tilt = self.pixel_to_servo(u, v, frame_size, normalized)
        print(f"[point] {label} ({conf:.2f}) at ({u:.3g}, {v:.3g}) -> pan {pan:g} tilt {tilt:g}")
        return self.point_at_pixel(u, v, duration, frame_size, normalized)
