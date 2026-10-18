"""
The newest camera frame, never an old one. A thread keeps reading the camera and keeps
only the latest frame, so a Wi-Fi stream's backlog can't pile up while the script is busy;
get_frame() returns it at once. Works for a plugged-in camera too.

This removes the backlog on the laptop, not the camera's own delay: the Tapo over a phone
hotspot still shows the room a second or two late. frame_after(t) waits for a frame that
arrived after t, so "switch the laser, wait `lag`, take the next frame" sees the change.
"""

import threading
import time

from src.vision.camera import open_camera


class LatestFrame:
    def __init__(self, cap):
        self.cap = cap
        self.latest_frame, self.frame_id, self.frame_time = None, 0, 0.0
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)  # stream hiccup: keep trying
                continue
            with self.lock:
                self.latest_frame, self.frame_id, self.frame_time = frame, self.frame_id + 1, time.time()

    def get_frame(self):
        """(frame, id, arrival time) of the newest frame; frame is None until the first."""
        with self.lock:
            return self.latest_frame, self.frame_id, self.frame_time

    def frame_after(self, t, timeout=6.0, idle=None):
        """The first frame that arrived at or after time t (waits for it), or None.
        idle(): called while waiting (to keep a window responsive)."""
        end = time.time() + max(0.0, t - time.time()) + timeout
        while time.time() < end:
            frame, _, when = self.get_frame()
            if frame is not None and when >= t:
                return frame
            if idle:
                idle()
            else:
                time.sleep(0.005)
        return None

    def release(self):
        self.running = False
        self.thread.join(timeout=2)
        self.cap.release()


def open_latest(source, lag=0.0):
    """Open a camera (an rtsp:// address or a camera number) as a LatestFrame."""
    return LatestFrame(open_camera(source, lag))
