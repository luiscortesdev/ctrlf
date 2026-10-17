"""
Ctrl+F for Your Room: check everything before a demo, in about 15 s.

    source venv/bin/activate
    python scripts/preflight.py

It checks the network, the Tapo camera, the ESP32 (and its ring), Gemini, the offline
models, the mic and speaker, and the laser calibration, and prints what to fix. It doesn't
move the servos or turn on the laser. Close the app first: only one program can hold the
ESP32.
"""

import datetime
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

from src.app import CAMERA, GEMINI, POINTER_HOST, POINTER_LINK

problems = []


def report(ok, what, fix=None):
    print(f"  {'ok ' if ok else 'BAD'}  {what}")
    if not ok and fix:
        problems.append(fix)


def mac_address():
    """The Mac's address on the network it would use to reach the camera."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("172.20.10.1", 9))  # no packet is sent; it just picks the route
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main():
    print("Network")
    ip = mac_address()
    report(ip is not None and ip.startswith("172.20.10."), f"Mac at {ip}",
           "Join the iPhone hotspot (keep its hotspot screen open; Maximize Compatibility on).")

    print("Camera")
    if isinstance(CAMERA, str):
        import cv2

        from src.vision.camera import capture, find_stream

        found = find_stream(CAMERA)
        report(found is not None, f"Tapo at {found}" if found else f"no answer from {CAMERA}",
               "Is the Tapo plugged in and on the hotspot?")
        if found:
            cap = capture(found)
            ok, frame = cap.read() if cap.isOpened() else (False, None)
            cap.release()
            report(ok, f"video {frame.shape[1]}x{frame.shape[0]}" if ok else "no video",
                   "Check CAMERA_USER / CAMERA_PASSWORD in .env (the Tapo app's Camera Account).")
            if found != CAMERA:
                problems.append(f"The camera moved to {found}: put it in CAMERA in src/app.py.")
    else:
        report(True, f"camera index {CAMERA} (run the app from Terminal.app for permission)")

    print("ESP32")
    from src.hardware.serial_client import Pointer

    pointer = Pointer(POINTER_LINK, POINTER_HOST)
    t0 = time.time()
    while not pointer.connected and time.time() - t0 < 6:
        time.sleep(0.1)
    report(pointer.connected, f"connected, at pan {pointer.pan:g} tilt {pointer.tilt:g}"
           if pointer.connected else "not found",
           "Power the ESP32 and put it on the hotspot (or plug it into the Mac). Is another "
           "program (the app, Arduino's Serial Monitor) holding it?")
    if pointer.connected:
        report(pointer.has_ring, "LED ring firmware", "Upload the firmware with the ring.")
        pointer.ring("W")

    print("Laser calibration")
    from src.vision.laser_tracker import CALIB_PATH

    if os.path.exists(CALIB_PATH):
        made = datetime.datetime.fromtimestamp(os.path.getmtime(CALIB_PATH))
        report(True, f"calibration.json from {made:%a %H:%M} (redo it if the device moved since)")
    else:
        report(False, "no calibration.json: aiming will be rough",
               "Run scripts/calibrate.py once the device is in its demo spot.")

    print("Voice")
    if GEMINI:
        from src import gemini

        trouble = gemini.check()
        report(trouble is None, "Gemini key works" if trouble is None else f"Gemini: {trouble}",
               "Gemini isn't reachable: the app will use Whisper and the Mac's voice.")
    else:
        report(True, "GEMINI = False: offline (Whisper + the Mac's voice)")
    from huggingface_hub import try_to_load_from_cache

    for name, file in (("google/owlv2-base-patch16-ensemble", "config.json"),
                       ("mlx-community/whisper-small.en-mlx", "config.json")):
        cached = isinstance(try_to_load_from_cache(name, file), str)
        report(cached, f"{name.split('/')[1]} downloaded",
               "Run scripts/cache_models.py while there's good Wi-Fi.")
    try:
        import sounddevice as sd

        mic, speaker = (sd.query_devices(kind=k)["name"] for k in ("input", "output"))
        report(True, f"mic: {mic}")
        worn = any(w in speaker.lower() for w in ("airpods", "headphone", "buds", "beats"))
        report(not worn, f"speaker: {speaker}",
               "Sound goes to headphones: switch the output to the Mac or a speaker so the "
               "judges hear the answers (System Settings > Sound).")
    except Exception as e:
        report(False, f"audio: {e!r:.100}", "Plug in / pick a mic in System Settings > Sound.")
    volume = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                            capture_output=True, text=True).stdout.strip()
    report(volume.isdigit() and int(volume) >= 60, f"volume {volume or '?'}%",
           "Turn the volume up: the hall is loud.")

    print()
    if problems:
        print("Fix before the demo:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("All good.")
    sys.stdout.flush()
    os._exit(0)  # don't wait on the Wi-Fi link's background thread


if __name__ == "__main__":
    main()
