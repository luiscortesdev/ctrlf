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
- **Ask out loud**: push-to-talk (SPACE), or type the question. Gemini hears the
  recording and works out what you mean ("did you see where I put my AirPods?", "and my
  wallet?", "nah, wrong one"), then the answer is spoken in Gemini's voice. Gemini only
  picks the action; where things are always comes from the camera's memory. Local
  Whisper and the Mac's voice take over when there's no Wi-Fi or Gemini is slow.
- **Phone alerts**: when your phone says you've left home (an iPhone Shortcut), it checks
  the room for your keys, wallet, ID and earbuds and pushes what you forgot to your phone.
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
| `src/hardware/serial_client.py` | Link to the ESP32, over USB or Wi-Fi |
| `src/voice.py` | Push-to-talk (Gemini, with mlx-whisper as the fallback) and speaking the answers |
| `src/gemini.py` | Gemini: understands the spoken question, speaks the answer |
| `src/phone.py` | Phone alerts through ntfy.sh when you leave home without something |
| `src/web.py` | The website's link to the app: serves `web/`, the live view and `/api/*` |
| `web/` | The website: 3D intro + dashboard (plain HTML/CSS/JS, works offline) |
| `scripts/camera_test.py` | Live view with boxes, for tuning thresholds |
| `scripts/calibrate.py` | Laser calibration sweep (writes `data/calibration.json`) |
| `scripts/measure_lag.py` | Times how far the Wi-Fi camera runs behind, with the LED ring (for `CAMERA_LAG_SECS`) |
| `scripts/preflight.py` | Checks the network, camera, ESP32, Gemini, models, audio and calibration before a demo |
| `firmware/esp32_controller/` | ESP32-S3 firmware: smooth servo glides, anti-backlash, laser auto-off, Wi-Fi, LED ring |
| `data/` | What it remembers and learns. Not in git: it's photos of your room |

## Setup (Mac, Apple Silicon)

```bash
~/.pyenv/versions/3.11.8/bin/python -m venv venv   # Python 3.11; 3.14 is too new for torch
source venv/bin/activate
pip install -r requirements.txt
python scripts/cache_models.py                      # OWLv2 + Whisper, ~1.1 GB, once
echo "GEMINI_API_KEY=your-key" > .env                # from aistudio.google.com; never commit it
```

Without a Gemini key it still works, offline: Whisper hears the questions and the Mac's
`say` answers (or set `GEMINI = False` in `src/app.py`).

## Run

From **Terminal.app** (macOS only gives the camera and mic to apps that asked; approve
both the first time):

```bash
source venv/bin/activate
python scripts/ctrlf.py
```

The website starts with it: open **http://localhost:8000** for the intro, then the
dashboard (live view, ask box, last seen, tracking, teaching). It's the `web/` folder,
served by `src/web.py` from the same Python process, so there's nothing else to run.
Use `CTRLF_WEB_PORT=8001` if port 8000 is busy. See `web/README.md` and `web/API.md`.

Camera: a Tapo TCW61 Wi-Fi camera on the iPhone hotspot is the default (`CAMERA` in
`src/app.py` is its RTSP address; if the hotspot gives it a new address, the app finds it).
Its login is the "Camera Account" from the Tapo app (Advanced Settings), not the Tapo
login: add `CAMERA_USER=...` and `CAMERA_PASSWORD=...` to `.env`. Turn off its timestamp
and logo overlays in the Tapo app (the ticking clock confuses the laser's dot finder).
`CAMERA = 1` = the iPhone through Continuity Camera (it falls back to the Mac's camera).

No Wi-Fi camera? Pick another one at startup (works for `ctrlf.py`, `calibrate.py` and
`camera_test.py`, or set `CTRLF_CAMERA` instead):

```bash
python scripts/ctrlf.py --list-cameras     # the plugged-in cameras' numbers
python scripts/ctrlf.py --camera webcam    # WEBCAM in src/app.py (0 = usually the Mac's own)
python scripts/ctrlf.py --camera 1         # a USB webcam is usually 1 on a Mac
```

With no `--camera`, if the Wi-Fi camera can't be reached it uses the plugged-in camera
(`WEBCAM`) instead, so the app and the website still start; `--camera wifi` means the Wi-Fi
camera or nothing (for the demo).

A plugged-in camera sees the room differently, so it gets its own laser calibration and desk
area (`data/calibration-webcam.json`, `data/desk_area-webcam.json`); the Wi-Fi camera's
files are left alone. Calibrate it with `python scripts/calibrate.py --camera webcam`.
In the video window: SPACE = ask out
loud, T = teach something, D = only look at a desk area, Esc = quit. Typed commands:
`where are my keys?`, `learn this`, `forget keys`, `call it ball` (rename what was just
taught), `list`, `wipe`, `servo 90 120`, `laser on` / `laser off`, `save`, `phone` (alert
setup), `phone test`, `left home`, `ring test` (which way the LED ring faces), `quit`.

Tests: `pytest`.

## ESP32 pointer

ESP32-S3 DevKitC (N16R8). Pan servo signal on GPIO4, tilt on GPIO5, laser on GPIO6. Power
the servos from a separate 5V supply with a 1000 uF capacitor across it, and connect its
ground to the ESP32's GND. LED ring (16 x WS2812): DI on GPIO7, VCC on the board's 3V3 pin
(not 5V: the board's "5Vin" pin is an input, and a 5 V ring reads 3.3 V data unreliably).

It works over USB (the COM port) or Wi-Fi. For Wi-Fi, copy
`firmware/esp32_controller/src/secrets.example.h` to `secrets.h` next to it and put your
network in it (2.4 GHz only; a phone hotspot with Maximize Compatibility works, campus
Wi-Fi with a login doesn't). The Mac must be on the same network. With `POINTER_LINK =
"auto"` (src/app.py) the app uses USB when the board is plugged in, else finds it on
Wi-Fi by itself; only connections that send the key from `secrets.h` are obeyed, and the
laser turns off if the app goes quiet.

Build and upload with PlatformIO (`pio run -t upload`, see `platformio.ini`) or the
Arduino IDE (ESP32 core 3.x, board "ESP32S3 Dev Module", Flash Size 16MB, PSRAM OPI).
Commands at 115200 baud or over Wi-Fi: `P<deg> T<deg> L0/L1` (tenths allowed), `C`, `?`;
replies `OK P92.5 T125.0 L0 R1`. LED ring: `RW` watching, `RL` listening, `RT` thinking,
`RF<deg>` found, `RS<deg>` last seen, `RN` not seen, `RX` test (see main.cpp). Higher tilt
points down; set TILT_MIN/TILT_MAX so the beam can never go above table height. The laser
turns itself off after 15 s.
