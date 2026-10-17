"""
Opening the camera (a Wi-Fi camera's stream, the iPhone through Continuity Camera, or the
Mac's own), and getting it back when it drops.

A Wi-Fi camera (like the Tapo TCW61) is an RTSP address in CAMERA (src/app.py). Its login
is the "Camera Account" from the Tapo app (Advanced Settings), kept in .env as
CAMERA_USER and CAMERA_PASSWORD, never in the code. If the hotspot gives the camera
another address, open_camera finds it on the same network.
"""

import concurrent.futures
import os
import socket
import sys
import threading
import urllib.parse

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo
ENV_PATH = os.path.join(ROOT, ".env")  # the Gemini key and the Wi-Fi camera's login


def env_value(name, path=ENV_PATH):
    """A setting from the environment, else from .env (where the logins live)."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        with open(path) as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key == name and value:
                    return value.strip().strip('"')
    except OSError:
        pass
    return None


def with_login(url):
    """The stream address with CAMERA_USER and CAMERA_PASSWORD from .env in it, if set."""
    user, password = env_value("CAMERA_USER"), env_value("CAMERA_PASSWORD")
    if not user or "@" in url:
        return url
    keep = "!$&'()*+,;="  # fine in a login as they are; others (like @ or :) get %-escaped
    login = urllib.parse.quote(user, safe=keep) + ":" + urllib.parse.quote(password or "", safe=keep)
    scheme, rest = url.split("://", 1)
    return f"{scheme}://{login}@{rest}"


def reachable(url, timeout=1.0):
    """Does anything answer on the stream's port? Quick, unlike opening a stream."""
    parts = urllib.parse.urlsplit(url)
    try:
        socket.create_connection((parts.hostname, parts.port or 554), timeout).close()
        return True
    except OSError:
        return False


def find_stream(url):
    """url, or the same stream at another address on its network (the hotspot can give the
    camera a new one when it rejoins), or None if nothing answers."""
    if reachable(url):
        return url
    parts = urllib.parse.urlsplit(url)
    prefix, port = parts.hostname.rsplit(".", 1)[0], parts.port or 554
    others = [parts._replace(netloc=f"{prefix}.{n}:{port}").geturl() for n in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(64) as pool:
        for other, ok in zip(others, pool.map(reachable, others)):
            if ok:
                return other
    return None


class StreamCamera:
    """A Wi-Fi camera's stream, with the calls the app makes on cv2.VideoCapture (read,
    isOpened, release). A thread keeps reading it, so read() always gets the newest frame;
    otherwise frames pile up whenever the app is busy and the picture falls behind."""

    TIMEOUT_MS = 5000  # nothing for this long = the stream stopped

    def __init__(self, url):
        # TCP: over Wi-Fi, lost UDP packets turn into smeared grey frames.
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer")
        self.cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                                    [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.TIMEOUT_MS,
                                     cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.TIMEOUT_MS])
        self.frame, self.count, self.taken = None, 0, 0
        self.new = threading.Condition()
        self.running = self.cap.isOpened()
        if self.running:
            threading.Thread(target=self._run, daemon=True).start()
        else:
            self.cap.release()

    def _run(self):
        while self.running:
            ok, frame = self.cap.read()
            with self.new:
                if ok:
                    self.frame, self.count = frame, self.count + 1
                else:
                    self.running = False
                self.new.notify_all()
        self.cap.release()  # here, not in release(): FFmpeg mustn't be closed mid-read

    def isOpened(self):
        return self.running

    def read(self):
        """The newest frame, once there's one that read() hasn't returned yet."""
        with self.new:
            self.new.wait_for(lambda: self.count > self.taken or not self.running,
                              self.TIMEOUT_MS / 1000)
            if self.count == self.taken:  # stopped, or nothing new for too long
                return False, None
            self.taken = self.count
            return True, self.frame

    def release(self):
        self.running = False


def local_camera(index):
    """A camera plugged into (or built into) the laptop. On Windows through DirectShow: the
    default (Media Foundation) takes seconds to open and keeps a queue of old frames."""
    if sys.platform == "win32":
        return cv2.VideoCapture(index, cv2.CAP_DSHOW)
    return cv2.VideoCapture(index)


def choose_camera(choice, wifi, webcam):
    """The camera --camera (or CTRLF_CAMERA) asks for: "wifi" -> wifi, the Wi-Fi camera's
    address; "webcam" -> webcam, a plugged-in camera's number; a number -> that camera; an
    rtsp:// address -> that stream."""
    choice = str(choice or "wifi").strip()
    if choice.lower() == "wifi":
        return wifi
    if choice.lower() == "webcam":
        return webcam
    if choice.isdigit():
        return int(choice)
    if "://" in choice:
        return choice
    raise ValueError(f'--camera {choice!r}: use "wifi", "webcam", a camera number (0, 1, ...) '
                     "or an rtsp:// address")


def per_camera(path, source):
    """A file that belongs to one camera's view (the laser calibration, the desk area). The
    Wi-Fi camera's is path itself; a plugged-in camera's gets "-webcam" in its name, so
    testing with a webcam doesn't use or overwrite the Wi-Fi camera's."""
    if isinstance(source, str):
        return path
    base, ext = os.path.splitext(path)
    return f"{base}-webcam{ext}"


def describe_camera(source):
    if isinstance(source, str):
        return f"the Wi-Fi camera at {urllib.parse.urlsplit(source).hostname}"
    return f"plugged-in camera {source}"


def list_cameras(most=6):
    """[(number, (width, height))] for the plugged-in cameras that send a picture."""
    found = []
    for index in range(most):
        cap = local_camera(index)
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        cap.release()
        if ok:
            found.append((index, frame.shape[1::-1]))
    return found


def capture(source):
    """A camera by index, or a stream by address (with the login from .env)."""
    return StreamCamera(with_login(source)) if isinstance(source, str) else local_camera(source)


def open_camera(index, lag=0.0):
    """Opens the camera; remembers which one in open_camera.source (for reconnecting), and
    how far its picture runs behind in open_camera.lag. lag: that for a Wi-Fi camera (a
    camera on the Mac itself has none)."""
    open_camera.source, open_camera.lag = index, 0.0
    if isinstance(index, str):  # a Wi-Fi camera
        found = find_stream(index)
        if found is None:
            raise SystemExit(f"Can't reach the camera at {urllib.parse.urlsplit(index).hostname}. "
                             "Is the Mac on the same Wi-Fi (the iPhone hotspot), and is the "
                             "camera plugged in? (Campus Wi-Fi often blocks this: use a hotspot.) "
                             "No Wi-Fi camera here? Add --camera webcam to use a plugged-in one.")
        where = urllib.parse.urlsplit(found).hostname
        if found != index:
            print(f"(The camera is at {where} now; put that in CAMERA to start faster.)")
        cap = capture(found)
        if not cap.isOpened():
            raise SystemExit(f"The camera at {where} answers but won't send video. Check "
                             "CAMERA_USER and CAMERA_PASSWORD in .env: they're the Camera "
                             "Account in the Tapo app (Advanced Settings), not the Tapo login.")
        open_camera.source, open_camera.lag = found, lag
        return cap
    cap = local_camera(index)
    if not cap.isOpened() and index != 0:
        # No iPhone? It has to be near the Mac, screen locked, still, and in landscape.
        print(f"(Camera {index} isn't available; using the Mac's own camera instead.)")
        cap = local_camera(0)
        open_camera.source = 0
    if not cap.isOpened():
        raise SystemExit("Can't open the camera. Run this from Terminal.app and allow camera "
                         "access (System Settings > Privacy & Security > Camera), then retry.")
    return cap


def reconnect_camera(cap, view, window):
    """The camera stopped (the iPhone locked, moved away or its stream paused, or the Wi-Fi
    camera dropped off): keep trying the same camera, showing the last picture meanwhile.
    Returns the new capture, or None if Esc was pressed. It never switches cameras: the
    laser calibration is for this one."""
    stream = isinstance(open_camera.source, str)
    hint = ("is the camera plugged in and on the hotspot?" if stream
            else "on the iPhone, tap Resume if it asks.")
    print(f"\n(Camera stopped sending frames. Reconnecting... {hint})")
    cap.release()
    while True:
        if view is not None:
            v = view.copy()
            cv2.rectangle(v, (0, 0), (v.shape[1], 60), (0, 0, 160), -1)
            cv2.putText(v, "Camera lost - reconnecting..." + ("" if stream else " (iPhone: tap Resume)"),
                        (14, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            cv2.imshow(window, v)
        if cv2.waitKey(700) & 0xFF == 27:
            return None
        if stream and not reachable(open_camera.source):
            continue  # don't freeze the window waiting on a stream that can't open
        cap = capture(open_camera.source)
        ok = cap.isOpened() and cap.read()[0]
        if ok:
            print("(Camera back.)")
            return cap
        cap.release()
