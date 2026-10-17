"""
Opening the camera (the iPhone through Continuity Camera, the laptop's own, a USB webcam,
or a network camera's stream), and getting it back when it drops. Works on macOS, Windows
and Linux.
"""

import sys

import cv2


def capture(source):
    """cv2.VideoCapture for a camera index or stream address. On Windows, DirectShow: the
    default (Media Foundation) can take 10+ s to open a webcam and gives it at 640x480."""
    if isinstance(source, str) or sys.platform == "darwin":
        return cv2.VideoCapture(source)
    if sys.platform == "win32":
        cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(source)
    else:
        cap = cv2.VideoCapture(source)
    if cap.isOpened():  # webcams start small; ask for HD (it uses the closest it has)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap


def open_camera(index):
    """Opens the camera; remembers which one in open_camera.source (for reconnecting)."""
    open_camera.source = index
    cap = capture(index)
    if isinstance(index, str):  # a network camera
        if not cap.isOpened():
            raise SystemExit(f"Can't reach the camera at {index.split('@')[-1]}. Is it on the "
                             "same Wi-Fi as this computer? (Campus Wi-Fi often blocks this: try a hotspot.)")
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always the newest frame, not a backlog
        return cap
    if not cap.isOpened() and index != 0:
        # No iPhone? It has to be near the Mac, screen locked, still, and in landscape.
        print(f"(Camera {index} isn't available; using the built-in camera instead.)")
        cap = capture(0)
        open_camera.source = 0
    if not cap.isOpened():
        if sys.platform == "darwin":
            raise SystemExit("Can't open the camera. Run this from Terminal.app and allow camera "
                             "access (System Settings > Privacy & Security > Camera), then retry.")
        raise SystemExit("Can't open the camera. Is another app using it (Zoom, Teams, the "
                         "Camera app)? On Windows, check Settings > Privacy & security > Camera "
                         "> 'Let desktop apps access your camera'. Or set CTRLF_CAMERA in .env.")
    return cap


def reconnect_camera(cap, view, window):
    """The camera stopped (the iPhone locked, moved away or its stream paused): keep trying
    the same camera, showing the last picture meanwhile. Returns the new capture, or None
    if Esc was pressed. It never switches cameras: the laser calibration is for this one."""
    print("\n(Camera stopped sending frames. Reconnecting... on the iPhone, tap Resume if it asks.)")
    cap.release()
    while True:
        if view is not None:
            v = view.copy()
            cv2.rectangle(v, (0, 0), (v.shape[1], 60), (0, 0, 160), -1)
            cv2.putText(v, "Camera lost - reconnecting... (iPhone: tap Resume)", (14, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            cv2.imshow(window, v)
        if cv2.waitKey(700) & 0xFF == 27:
            return None
        cap = capture(open_camera.source)
        ok = cap.isOpened() and cap.read()[0]
        if ok:
            if isinstance(open_camera.source, str):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            print("(Camera back.)")
            return cap
        cap.release()
