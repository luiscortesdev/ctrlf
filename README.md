# Ctrl+F for Your Room

A camera that remembers where your things are, and a laser on a pan/tilt head that points
at them. Ask "where are my keys?" and it says where they are ("Your keys are on the
chair") while the laser lands on them. If they're out of sight, it says when and where it
last saw them and shows that snapshot. Everything runs locally on a Mac; the ESP32 only
drives the servos and the laser.

## What it does

- **Remembers objects**: OWLv2 (open-vocabulary detection) finds everyday things by name
  (keys, wallet, phone, AirPods case, ...) and furniture, so answers can say "on the chair".
- **Learns your own things**: say "learn this" (or press T), say what it is, hold it up
  to the laptop camera, then show it to the room camera and answer "is this it?". It
  finds them by how they look, so it's YOUR keys, not anything key-like, and it keeps
  learning how they look wherever they're put down.
- **Ask out loud**: push-to-talk (SPACE) with local Whisper, or type the question.
- **Points with the laser**: aims, watches its own dot through the camera, and corrects.
- **Steady live view**: boxes are tracked from pass to pass and glide with the video.

## Layout

| Path | What it is |
|---|---|
| `scripts/ctrlf.py` | Start the app |
| `src/app.py` | The app: memory (SQLite + snapshots), questions and answers, teaching, live view |
| `src/vision/detector.py` | OWLv2 detection (YOLO-World fallback), taught things' pictures, background thread |
| `src/vision/tracker.py` | Turns jumpy detections into steady objects |
| `src/vision/glide.py` | Moves boxes with the video (optical flow) between detector passes |
| `src/vision/teach.py` | Finds a just-taught thing in the room view |
| `src/vision/laser_tracker.py` | Finds the laser dot, pixel-to-angle calibration, closed-loop aiming |
| `src/vision/camera.py` | Opening the camera (iPhone, Mac, or a network stream) and reconnecting |
| `src/hardware/serial_client.py` | Serial link to the ESP32 |
| `src/voice.py` | Push-to-talk speech to text (mlx-whisper) |
| `scripts/camera_test.py` | Live view with boxes, for tuning thresholds |
| `scripts/calibrate.py` | Laser calibration sweep (writes `data/calibration.json`) |
| `firmware/esp32_controller/` | ESP32-S3 firmware: smooth servo glides, anti-backlash, laser auto-off |
| `data/` | What it remembers and learns. Not in git: it's photos of your room |

## Setup (Mac, Apple Silicon)

```bash
~/.pyenv/versions/3.11.8/bin/python -m venv venv   # Python 3.11; 3.14 is too new for torch
source venv/bin/activate
pip install -r requirements.txt
python scripts/cache_models.py                      # OWLv2 + Whisper, ~1.1 GB, once
```

## Run

From **Terminal.app** (macOS only gives the camera and mic to apps that asked; approve
both the first time):

```bash
source venv/bin/activate
python scripts/ctrlf.py
```

Camera: the iPhone through Continuity Camera is the default (`CAMERA = 1` in
`src/app.py`); it falls back to the Mac's camera. In the video window: SPACE = ask out
loud, T = teach something, D = only look at a desk area, Esc = quit. Typed commands:
`where are my keys?`, `learn this`, `forget keys`, `call it ball` (rename what was just
taught), `list`, `wipe`, `servo 90 120`, `laser on` / `laser off`, `save`, `quit`.

Tests: `pytest`.

## ESP32 pointer

ESP32-S3 DevKitC (N16R8), plugged in through its COM port. Pan servo signal on GPIO4,
tilt on GPIO5, laser on GPIO6. Power the servos from a separate 5V supply with a 1000 uF
capacitor across it, and connect its ground to the ESP32's GND.

Build and upload with PlatformIO (`pio run -t upload`, see `platformio.ini`) or the
Arduino IDE (ESP32 core 3.x, board "ESP32S3 Dev Module", Flash Size 16MB, PSRAM OPI).
Serial at 115200: `P<deg> T<deg> L0/L1` (tenths allowed), `C`, `?`; replies
`OK P92.5 T125.0 L0`. Higher tilt points down; set TILT_MIN/TILT_MAX so the beam can
never go above table height. The laser turns itself off after 15 s.
