# Ctrl+F prototype (Mac)

A working first version of the whole loop, tested on a MacBook (Apple Silicon) with its
built-in camera on Oct 16. It watches the camera, remembers where objects are, and answers
"where are my keys?" out loud, and it can drive the ESP32 pan/tilt pointer.

| File | What it does |
|---|---|
| `ctrlf.py` | The app: detection, memory (SQLite + snapshots), questions typed or spoken, answers via macOS `say`, live view, ESP32 link |
| `detector.py` | Object detectors. OWLv2 is the default; YOLO-World is a faster fallback |
| `voice.py` | Push-to-talk: SPACE in the video window, local Whisper (mlx-whisper) |
| `camera_test.py` | Live view with detection boxes, for tuning thresholds |
| `esp32_pointer/` | ESP32-S3 firmware: serial commands `P<deg> T<deg> L0/L1`, `C`, `?` |

## Why OWLv2 and not YOLO-World

On a recording with keys, an AirPods case, a phone and an ID badge, YOLO-World (v8s and v8x)
and YOLOE-11L scored about 0.003 on the keys and the AirPods case, even at 1280 px and with
other prompt wordings. OWLv2 found the keys in 85% of frames at confidence 0.30, with no
false keys. It takes about 0.4 s per frame on an M-series GPU, so it runs in a background
thread while the video stays at 30 fps. Decoy prompts (necklace, hand, ...) stop
look-alikes, such as a cross pendant, from counting as keys.

## Setup

```bash
~/.pyenv/versions/3.11.8/bin/python -m venv venv   # Python 3.11; 3.14 is too new for torch
source venv/bin/activate
pip install -r requirements.txt
```

The first run downloads OWLv2 (~620 MB) and Whisper small.en (~480 MB). After that it
works offline.

## Run

Run it from **Terminal.app**. macOS only lets apps that asked for camera and mic
permission use them; approve both the first time.

```bash
python ctrlf.py
```

- Ask out loud: press SPACE in the video window and ask.
- Or type in the terminal: `where are my keys?`, `list`, `wipe`, `servo 90 60`,
  `laser on` / `laser off`, `save` (writes the live view to a picture), `quit`.

## ESP32 pointer

ESP32-S3 DevKitC (N16R8), plugged in through its COM port.

Wiring:
- Pan servo signal to GPIO4, tilt servo signal to GPIO5, laser to GPIO6.
- Servos are powered from a separate 5V supply, with a 1000 µF capacitor across it.
- Connect the ESP32's GND to the servo supply's ground.

Upload:

```bash
arduino-cli compile --upload -p /dev/cu.usbmodem* \
  --fqbn "esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi" esp32_pointer
```

Tilt is clamped to `TILT_MIN..TILT_MAX`, and the laser turns itself off after 15 s
without a command. `ctrlf.py` only talks to the board if it answers `?` like this
firmware does.
