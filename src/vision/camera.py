"""
Opening the camera (the iPhone through Continuity Camera, the Mac's own, or a network
camera's stream), and getting it back when it drops.
"""

import cv2


def open_camera(index):
    """Opens the camera; remembers which one in open_camera.source (for reconnecting)."""
    open_camera.source = index
    cap = cv2.VideoCapture(index)
    if isinstance(index, str):  # a network camera
        if not cap.isOpened():
            raise SystemExit(f"Can't reach the camera at {index.split('@')[-1]}. Is it on the "
                             "same Wi-Fi as the Mac? (Campus Wi-Fi often blocks this: try a hotspot.)")
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always the newest frame, not a backlog
        return cap
    if not cap.isOpened() and index != 0:
        # No iPhone? It has to be near the Mac, screen locked, still, and in landscape.
        print(f"(Camera {index} isn't available; using the Mac's own camera instead.)")
        cap = cv2.VideoCapture(0)
        open_camera.source = 0
    if not cap.isOpened():
        raise SystemExit("Can't open the camera. Run this from Terminal.app and allow camera "
                         "access (System Settings > Privacy & Security > Camera), then retry.")
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
        cap = cv2.VideoCapture(open_camera.source)
        ok = cap.isOpened() and cap.read()[0]
        if ok:
            if isinstance(open_camera.source, str):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            print("(Camera back.)")
            return cap
        cap.release()
