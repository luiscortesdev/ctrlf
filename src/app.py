"""
Ctrl+F for Your Room: laptop app, version 1

What it does
  - Watches the camera and recognizes objects (OWLv2, running on your Mac; see detector.py).
  - When an object sits still for a moment, it remembers what it is, where it is,
    when it was seen, and saves a snapshot (memory.db + snapshots/ folder).
  - Type a question like "where are my keys?" and it answers out loud
    ("Your keys are in the back left, just left of the laptop.") and circles
    them on screen. If they're out of sight, it says when and where it last saw
    them and shows that snapshot.
  - If the ESP32 pointer is connected (USB, or Wi-Fi: see POINTER_LINK), it swings the
    laser toward the object and nudges it on by watching the dot (laser.py; rough aim
    until calibrate.py has run). Its LED ring shows what it's doing and points too.
  - Teach it your own things: say "learn this" (or press T) and hold it up or click on
    it in the video; it collects pictures of it for a few seconds and asks what it is.
    Taught things are found by how they look, so YOUR keys, not anything key-like.

Setup (once, in Terminal, in the repo folder). Use Python 3.11, not Homebrew's 3.14:
    ~/.pyenv/versions/3.11.8/bin/python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

Run it from Terminal.app. macOS only gives the camera to apps that ask for permission,
and Terminal does (approve it the first time):
    source venv/bin/activate
    python scripts/ctrlf.py
What it remembers and learns is kept in data/ (never committed: it's photos of your room).
The first run downloads OWLv2 (~620 MB); after that it works offline.

Ask out loud: press SPACE in the video window, then ask (see voice.py).
Or type in the Terminal window (not the video window):
    where are my keys?     ask about anything in OBJECTS below
    list                   show everything it remembers
    wipe                   forget everything (or click "Wipe memory" in the video window)
    servo 90 120           point the head: pan, tilt (higher tilt = down)
    laser on / laser off   hardware testing
    save                   save the live view as a picture (view_<time>.jpg)
    learn this             teach it something (or "this is my retainer case", or T in the
                           video window): hold it up to the laptop camera, name it, put it
                           down where the room camera sees it, and answer "is this it?"
                           (yes / no, Y / N). Or click on it in the video.
    forget retainer case   forget a taught thing: what it looks like and where it was
    phone                  set up alerts on your phone for things you leave behind (phone.py)
    phone test             send a test alert
    left home              act as if your phone said you left home
    ring test              LED ring: LED 0 red, LED 1 green, LED 4 blue (to set RING_LED0_DEG)
In the video window: D, then click two corners = only look at the desk (D again = all).
    quit                   (or press Esc in the video window)

"""

import collections
import glob
import json
import math
import os
import queue
import re
import sqlite3
import sys
import threading
import time

import cv2
import numpy as np

from src.hardware.serial_client import Pointer
from src.vision.camera import capture, open_camera, reconnect_camera
from src.vision.detector import CUTOFF_MARGIN, PICTURE_MATCH

# ------------------------------------------------------------------ settings
# The Tapo Wi-Fi camera (TCW61) on the iPhone hotspot. Its login is the "Camera Account"
# from the Tapo app (Advanced Settings), kept in .env as CAMERA_USER and CAMERA_PASSWORD.
# If the hotspot gives it another address, the app finds it there. 1 = the iPhone
# (Continuity Camera), 0 = the Mac's built-in.
CAMERA = "rtsp://172.20.10.5:554/stream1"
# How far a Wi-Fi camera's picture runs behind; the laser waits this much longer before
# trusting a frame. A guess: measure it once the laser is here.
CAMERA_LAG_SECS = 0.8
MIRROR_LEFT_RIGHT = False  # True if the camera faces you, so "left" means YOUR left
DETECTOR = "owl"           # "owl" finds keys and earbuds; "yolo" is faster but misses them
# Detector input size: 960 is OWLv2's native size; 768 is 1.7x faster for about the same
# results on our recording; 640 is 2.5x faster and plenty once a desk area is set (D key).
DETECT_SIZE = 768
# Watching a whole room, keys across it are tiny. Each detector pass also zooms into one
# part of the view (a 2x2 grid here, overlapping), taking turns. On a room recording that
# raised keys on a chair from 0.34 to 0.45 confidence and found the phone in 62 frames
# instead of 26. None = whole view only (faster).
TILES = (2, 2)
TILE_SIZE = 640
DISPLAY_W = 1280           # width of the live view window
VOICE = True               # SPACE in the video window = push-to-talk (needs mlx-whisper)
# Gemini hears the questions and says the answers (src/gemini.py; needs GEMINI_API_KEY in .env
# and internet). It falls back to Whisper and the computer's own voice by itself. False = those only.
GEMINI = True
# Phone alerts (src/phone.py): when your phone says you've left home, it looks for these for
# LEAVE_LOOK_SECS, and whatever is still sitting in the room gets pushed to your phone.
# Type "phone" in the app for the setup. False = off.
PHONE = True
ESSENTIALS = ["keys", "wallet", "id card", "earbuds case"]
LEAVE_LOOK_SECS = 6
# How it reaches the ESP32 (servos + laser): "usb", "wifi", or "auto" = USB when it's
# plugged into the Mac, else Wi-Fi. Wi-Fi needs a network in
# firmware/esp32_controller/src/secrets.h and the Mac on that same network. The first
# time, macOS asks to let Terminal find devices on the local network: allow it.
POINTER_LINK = "auto"
# None = find it by itself (it announces itself every 2 s), or its address, as the
# Serial Monitor shows when it joins the network, e.g. "172.20.10.4".
POINTER_HOST = None
# The LED ring on the device (GPIO 7): a dim breath while the camera is on, blue while
# listening, a spinner while thinking, then green toward the thing found (amber: where it
# was last seen; red flashes: not seen). Mount it facing the room, by the camera, so the
# lit LED points at the object. Then type "ring test" and look at it from the front:
RING_LED0_DEG = 90     # where the red LED (LED 0) is: 0 = right, 90 = top, 180 = left, 270 = bottom
RING_CLOCKWISE = True  # the green one (LED 1) is clockwise from the red one

# What it looks for -> words people might use when asking.
# Add anything: the detector recognizes objects from their names.
# Big everyday things (laptop, mug, book) are landmarks for "left of the laptop".
OBJECTS = {
    "keys": ["keys", "key", "keychain", "car keys"],
    "wallet": ["wallet"],
    "earbuds case": ["earbuds", "earbud", "ear buds", "airpods", "air pods", "earbuds case",
                     "headphones"],
    "phone": ["phone", "iphone", "cell phone", "smartphone"],
    "glasses": ["glasses", "sunglasses", "spectacles"],
    "charger": ["charger", "charging cable", "power brick", "cable"],
    "water bottle": ["water bottle", "bottle", "hydro flask"],
    # No "remote": a phone lying flat, seen edge-on, scored as a remote (0.56 vs 0.30), so
    # it stole the phone. Add it back if a remote is one of your props.
    "id card": ["id", "id card", "student id", "gator 1", "card"],
    "laptop": ["laptop", "computer", "macbook"],
    "mug": ["mug", "cup", "coffee"],
    "book": ["book", "notebook"],
    # Furniture: things sit on these, so answers can say "on the chair".
    "table": ["table", "kitchen table", "coffee table"],
    "desk": ["desk"],
    "couch": ["couch", "sofa"],
    "chair": ["chair"],
    "bed": ["bed"],
    "shelf": ["shelf", "bookshelf", "ledge", "window sill"],
    "tv": ["tv", "television"],
}
SURFACES = {"table", "desk", "couch", "chair", "bed", "shelf"}  # "on the ..."

# Extra ways to describe an object to the detector (its label is always used too).
LOOKS_LIKE = {
    "keys": ["keychain", "a bunch of keys"],
    "earbuds case": ["airpods case"],
    "id card": ["badge"],
    "phone": ["smartphone", "cell phone"],
    "shelf": ["window sill"],
}
PROMPTS = {label: LOOKS_LIKE.get(label, []) for label in OBJECTS}

# Lower = finds more; higher = fewer false alarms. Picked from a test recording on the Mac
# camera: at 0.30 keys were found in 85% of frames with no false alarms; at 0.25 "remote"
# and "glasses" started showing up on chair legs and a whiteboard drawing.
# Re-tune these once the demo camera is in place: false alarms depend on the background.
CONFIDENCE = {"default": 0.30, "keys": 0.25, "glasses": 0.35, "laptop": 0.35}
STABLE_HITS = 3       # detections in a row in the same spot before it's remembered (~1 s)
STILL_FRAC = 0.03     # how far it can wiggle (fraction of image width) and still count as still
MOVED_FRAC = 0.05     # how far it must move (fraction of image width) for a new snapshot
SIZE_FRAC = 0.25      # ...or this fraction of its own size, if bigger (a chair's box wobbles)
VISIBLE_SECS = 3.0    # seen this recently = "it's here right now"
PRESENT_SECS = 15     # answers: seen this recently = "your keys are ...", not "I last saw them"
TEACH_SECS = 4.0      # how long it collects pictures of something you're showing it
# Teach with the Mac's own camera: hold things up to the laptop, then put them down where
# the room camera sees them (it finds them, asks "is this it?", and learns that view too).
# None = teach with the room camera only (hold it up to it, or click on it).
TEACH_CAMERA = 0
# Taught things must match their pictures to show up (detector.PICTURE_MATCH scores 0.5);
# the word alone almost never gets there (a servo scored 0.32 as "keys").
TAUGHT_CONFIDENCE = 0.5
# Taught things learn how they look wherever they're put down: once one has sat still for
# a few passes and been recognized there for sure, its picture from there is added (a tape
# taught in someone's hand matched only 0.77 on a far shelf, under the 0.80 cutoff). Never
# while it's in someone's hand (a ball carried around picked up fingers and a chin), and
# the picture must still look like the taught ones (LEARN_DRIFT), so lessons can't drift.
LEARN_STILL_PASSES = 3
LEARNED_PICTURES = 24  # kept per thing (the ones from teaching are always kept too)
LEARN_DRIFT = 0.08     # a learned picture must match the taught ones within this of the cutoff
LEARN_MARGIN = 0.05    # ...and them by this much more than any other taught thing's pictures
HIGHLIGHT_SECS = 6    # how long the green circle stays after a question

# Look again where missing things were last seen: while something remembered is out of
# sight, each detector pass also zooms into its last spot (taking turns if several), at
# the same zoom as a tile. Tiles take turns, so a spot gets zoomed into every 4th pass;
# this checks it every pass. Zooming in further didn't help: keys on a chair across the
# room scored 0.45 at tile zoom and no better in tighter crops.
FOCUS_SPOTS = 6       # how many missing things' spots it takes turns over

# The settings above are for a computer with a GPU (Apple Silicon or NVIDIA). Without one
# they'd take 20+ seconds a pass, so src/machine.py lightens them, and CTRLF_* in .env
# overrides any of them (see machine.py).
from src import machine  # noqa: E402

DETECTOR, DETECT_SIZE, TILES, FOCUS_SPOTS, CAMERA, TEACH_CAMERA, MACHINE = machine.tune(
    DETECTOR, DETECT_SIZE, TILES, FOCUS_SPOTS, CAMERA, TEACH_CAMERA)

# Rough laser aim, used only until calibrate.py has made calibration.json.
# If the head moves the wrong way, flip the sign of that SPAN.
PAN_CENTER, PAN_SPAN = 90, 60    # pan degrees at image center; degrees across the image width
TILT_CENTER, TILT_SPAN = 125, 45  # tilt at image center (higher = down; firmware allows 90-160)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repo
DATA = os.path.join(ROOT, "data")  # everything it remembers (not in git: it's personal)
AREA_PATH = os.path.join(DATA, "desk_area.json")  # set with the D key in the video window
DB_PATH = os.path.join(DATA, "memory.db")
TAUGHT_PATH = os.path.join(DATA, "taught.json")  # what taught things look like
TAUGHT_DIR = os.path.join(DATA, "taught")          # a picture of each, for you
SNAP_DIR = os.path.join(DATA, "snapshots")
WINDOW = "Ctrl+F for Your Room (Esc to quit)"
TEACH_WINDOW = "Teach: laptop camera"


# ------------------------------------------------------------------ taught things
TAUGHT = {}  # label -> {"words": [...], "pictures": [[floats], ...], "photo": path}


def threshold(label):
    """Score a new sighting needs before it counts."""
    if label in TAUGHT:
        return TAUGHT_CONFIDENCE
    return CONFIDENCE.get(label, CONFIDENCE["default"])


def keep_level(label):
    """Score something already being followed needs to stay followed. A taught thing put
    down far away can match its pictures less well (a tape on a far shelf: 0.74-0.77 with a
    0.80 cutoff) until it has learned how it looks there, so it may stay down to 0.35 on the
    picture scale (0.5 = its cutoff; with a 0.80 cutoff, a similarity of 0.74). Others:
    KEEP_FRAC of their threshold. Starting to follow still needs the cutoff."""
    from src.vision.tracker import KEEP_FRAC

    if label in TAUGHT:
        return 0.35
    return KEEP_FRAC * threshold(label)


def cutoff(label):
    """A taught thing's own picture-match cutoff (see detector.PICTURE_MATCH)."""
    return TAUGHT.get(label, {}).get("cutoff", PICTURE_MATCH)


def name_words(label):
    """Ways to ask for a newly taught thing: 'retainer case' / 'retainer cases'."""
    return [label, label[:-1] if label.endswith("s") else label + "s"]


def load_taught(owl):
    """Read taught.json and teach the detector (owl: an OwlDetector) everything in it."""
    import torch

    try:
        with open(TAUGHT_PATH) as f:
            TAUGHT.update(json.load(f))
    except (OSError, ValueError):
        return
    for label, t in TAUGHT.items():
        OBJECTS.setdefault(label, t["words"])
        if hasattr(owl, "set_examples") and t["pictures"]:
            owl.set_examples(label, t["pictures"] + t.get("seen", []))
            owl.cutoffs[label] = t.get("cutoff", PICTURE_MATCH)
    if TAUGHT:
        print(f"(Knows what these look like: {', '.join(TAUGHT)})")


def save_taught():
    # Write a new file, then swap it in: a crash mid-write can't leave a broken taught.json.
    tmp = TAUGHT_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(TAUGHT, f, default=float)
    os.replace(tmp, TAUGHT_PATH)


def parse_name(text):
    """'this is my retainer case' -> 'retainer case'; 'learn this' -> None."""
    t = re.sub(r"[^a-z0-9' ]", " ", text.lower())
    t = re.sub(r"^\s*(please\s+)?(learn|remember|teach( you)?|train( on)?|meet)\b", "", t)
    t = re.sub(r"^\s*(this|that|these|those|it|they)( one)?\s*('s|'re|is|are)?(?=\s|$)", "", t)
    while True:
        t2 = re.sub(r"^\s*(it's|its|it is|called|named|my|a|an|the|our|your|his|her|their)(?=\s|$)", "", t)
        if t2 == t:
            break
        t = t2
    t = " ".join(t.split())
    return t if 0 < len(t.split()) <= 4 else None


def taught_name(text):
    """The taught thing someone means, allowing for speech-to-text mishearing it: "bar" or
    "bahar" for a thing saved as "bhar", or spelled out ("b h a r"). None if nothing's close."""
    import difflib

    text = text.strip()
    if text in TAUGHT:
        return text
    if re.fullmatch(r"([a-z] )+[a-z]", text):  # spelled out
        text = text.replace(" ", "")
    names = list(TAUGHT)
    squashed = {n.replace(" ", ""): n for n in names}
    hit = difflib.get_close_matches(text.replace(" ", ""), list(squashed), n=1, cutoff=0.6)
    return squashed[hit[0]] if hit else None


def label_for_name(name):
    """An existing label if the name is one of its words ('airpods' -> 'earbuds case'),
    else the name itself (something new)."""
    for label, words in OBJECTS.items():
        if name == label or name in words:
            return label
    return name


def _touches(a, b):
    """Do two boxes touch or overlap?"""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _far(a, b):
    """Are two boxes far apart (more than twice the bigger one's size)?"""
    size = max(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])
    return math.dist(((a[0] + a[2]) / 2, (a[1] + a[3]) / 2), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)) > 2 * size


def join_words(words, last="and"):
    """['keys', 'wallet', 'id card'] -> 'keys, wallet and id card'."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + f" {last} {words[-1]}"


def plural(label):
    return label.split()[-1].endswith("s")


def crop_around(frame, box):
    """The box plus half its size around it, for a photo of a taught thing."""
    x1, y1, x2, y2 = (int(v) for v in box)
    pad = max(x2 - x1, y2 - y1) // 2
    h, w = frame.shape[:2]
    return frame[max(0, y1 - pad):min(h, y2 + pad), max(0, x1 - pad):min(w, x2 + pad)]


def teach_request(c):
    """(True, name or None) if c asks to teach something, else (False, None). Allows speech
    to text's versions: "I learned this", "can you learn this", "learn my keys"."""
    verbs = r"(?:learn|learned|learns|teach|train|remember)"
    m = re.search(rf"\b{verbs}\s+(?:my|the|a|an)\s+(.+)$", c)
    if m:
        return True, parse_name(m.group(1))
    m = re.search(rf"\b{verbs}\s+(?:this|that|it|these|those)(?:\s+one)?\b(?:\s+(?:is|are|as)\s+(.+))?", c)
    if m:
        return True, parse_name(m.group(1)) if m.group(1) else None
    if re.match(TEACH_RE, c):
        return True, parse_name(c)
    return False, None


TEACH_RE = r"^(please )?(learn|remember|teach|train|this is|these are|that is|that's|this one is|meet)\b"
CANCEL = {"cancel", "never mind", "nevermind", "stop"}
YES = {"yes", "yeah", "yep", "yup", "correct", "that's it", "that is it", "that's right", "right",
       "yes it is", "yes that's it", "it is", "y"}
NO = {"no", "nope", "wrong", "not that", "that's not it", "no it's not", "no that's wrong",
      "not that one", "n"}


# ------------------------------------------------------------------ memory
class Memory:
    """What it remembers: one row per object (latest place) + a log of every move."""

    COLS = ["label", "x1", "y1", "x2", "y2", "frame_w", "frame_h",
            "last_seen", "moved_at", "snapshot"]

    def __init__(self, path=DB_PATH, snap_dir=SNAP_DIR):
        self.snap_dir = snap_dir
        os.makedirs(snap_dir, exist_ok=True)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS objects (
            label TEXT PRIMARY KEY, x1 INT, y1 INT, x2 INT, y2 INT,
            frame_w INT, frame_h INT, last_seen REAL, moved_at REAL, snapshot TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            ts REAL, label TEXT, cx INT, cy INT, snapshot TEXT)""")
        self.db.commit()
        self._last_write = {}

    def _row(self, row):
        if row is None:
            return None
        d = dict(zip(self.COLS, row))
        d["cx"] = (d["x1"] + d["x2"]) // 2
        d["cy"] = (d["y1"] + d["y2"]) // 2
        return d

    def get(self, label):
        return self._row(self.db.execute(
            "SELECT * FROM objects WHERE label=?", (label,)).fetchone())

    def all(self):
        rows = self.db.execute("SELECT * FROM objects ORDER BY last_seen DESC").fetchall()
        return [self._row(r) for r in rows]

    def observe(self, label, box, frame, now, scenery=False):
        """Called when an object has been sitting still. Returns 'new', 'moved' or None.
        scenery (furniture): just keep its latest box, no snapshots or move log; there can
        be several chairs, and the detector reports whichever it's surest of."""
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        h, w = frame.shape[:2]
        old = self.get(label)
        if old and scenery:
            self.db.execute("UPDATE objects SET last_seen=?, x1=?, y1=?, x2=?, y2=? WHERE label=?",
                            (now, x1, y1, x2, y2, label))
            self.db.commit()
            return None
        if old is None or math.dist((cx, cy), (old["cx"], old["cy"])) > wiggle_room(box, MOVED_FRAC, w):
            snap = self._save_snapshot(label, box, frame, now)
            self.db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (label, x1, y1, x2, y2, w, h, now, now, snap))
            self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (now, label, cx, cy, snap))
            self.db.commit()
            self._last_write[label] = now
            return "new" if old is None else "moved"
        if now - self._last_write.get(label, 0) > 1.0:  # just "still here": refresh once a second
            self.db.execute("UPDATE objects SET last_seen=?, x1=?, y1=?, x2=?, y2=? WHERE label=?",
                            (now, x1, y1, x2, y2, label))
            self.db.commit()
            self._last_write[label] = now
        return None

    def _save_snapshot(self, label, box, frame, now):
        img = frame.copy()
        x1, y1, x2, y2 = box
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 3)
        cv2.putText(img, f"{label}  {time.strftime('%I:%M:%S %p', time.localtime(now))}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        path = os.path.join(self.snap_dir, f"{label.replace(' ', '_')}_{int(now * 1000)}.jpg")
        cv2.imwrite(path, img)
        return path

    def forget(self, label):
        """Forget where one thing was, and its snapshots."""
        for (snap,) in self.db.execute("SELECT snapshot FROM events WHERE label=?", (label,)).fetchall():
            if snap and os.path.exists(snap):
                os.remove(snap)
        self.db.execute("DELETE FROM objects WHERE label=?", (label,))
        self.db.execute("DELETE FROM events WHERE label=?", (label,))
        self.db.commit()
        self._last_write.pop(label, None)

    def wipe(self):
        self.db.execute("DELETE FROM objects")
        self.db.execute("DELETE FROM events")
        self.db.commit()
        self._last_write.clear()
        for f in glob.glob(os.path.join(self.snap_dir, "*.jpg")):
            os.remove(f)


# ------------------------------------------------------------------ words
def find_label(question):
    """'where did I leave my airpods?' -> 'earbuds case'. Longest matching word wins."""
    q = question.lower()
    best = None
    for label, words in OBJECTS.items():
        for word in set(words + [label]):
            if re.search(r"\b" + re.escape(word) + r"\b", q):
                if best is None or len(word) > best[1]:
                    best = (label, len(word))
    return best[0] if best else None


def wiggle_room(box, frac, w):
    """How far (pixels) a box's center may shift: frac of the image width, or SIZE_FRAC of
    the box's own size for big things like furniture."""
    return max(frac * w, SIZE_FRAC * max(box[2] - box[0], box[3] - box[1]))


def ago(secs):
    secs = int(secs)
    if secs < 10:
        return "a few seconds ago"
    if secs < 60:
        return f"{secs} seconds ago"
    if secs < 3600:
        m = secs // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h = secs // 3600
    return f"{h} hour{'s' if h != 1 else ''} ago"


def place_words(cx, cy, w, h):
    """Rough spot in the camera's view. Top of the image = back of the desk."""
    col = "left" if cx < w / 3 else "right" if cx > 2 * w / 3 else "middle"
    if MIRROR_LEFT_RIGHT and col != "middle":
        col = "right" if col == "left" else "left"
    row = "back" if cy < h / 3 else "front" if cy > 2 * h / 3 else "middle"
    if row == "middle" and col == "middle":
        return "in the middle"
    if row == "middle":
        return f"on the {col} side"
    if col == "middle":
        return f"toward the {row}"
    return f"in the {row} {col}"


def box_gap(a, b):
    """Pixels of empty space between two boxes (0 if they touch or overlap)."""
    gx = max(0, b["x1"] - a["x2"], a["x1"] - b["x2"])
    gy = max(0, b["y1"] - a["y2"], a["y1"] - b["y2"])
    return math.hypot(gx, gy)


def box_overlap(a, b):
    """Intersection over union of two (x1, y1, x2, y2) boxes."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / union if union > 0 else 0.0


def nearby_words(obj, others):
    """'just left of the laptop', using the closest thing that was in place at the same time."""
    best = None
    for o in others:
        if o["label"] == obj["label"]:
            continue
        # The landmark must have been there when this object was last seen:
        # seen around then (or since) and not moved since.
        if o["last_seen"] < obj["last_seen"] - 120 or o["moved_at"] > obj["last_seen"] + 5:
            continue
        gap = box_gap(obj, o)
        if gap < 0.25 * obj["frame_w"] and (best is None or gap < best[0]):
            best = (gap, o)
    if best is None:
        return None
    gap, o = best
    dx, dy = obj["cx"] - o["cx"], obj["cy"] - o["cy"]
    if MIRROR_LEFT_RIGHT:
        dx = -dx
    if abs(dx) >= abs(dy):
        rel = f"left of the {o['label']}" if dx < 0 else f"right of the {o['label']}"
    else:
        rel = f"behind the {o['label']}" if dy < 0 else f"in front of the {o['label']}"
    return ("just " + rel) if gap < 0.06 * obj["frame_w"] else rel


def on_what(obj, others):
    """'on the chair' if the object sits inside a piece of furniture's box (seen at the
    same time), preferring the smallest such piece."""
    if obj["label"] in SURFACES:
        return None  # furniture isn't "on" furniture
    best = None
    for o in others:
        if o["label"] not in SURFACES or o["label"] == obj["label"]:
            continue
        if o["last_seen"] < obj["last_seen"] - 120 or o["moved_at"] > obj["last_seen"] + 5:
            continue
        if o["x1"] <= obj["cx"] <= o["x2"] and o["y1"] <= obj["y2"] <= o["y2"] + 0.03 * obj["frame_h"]:
            area = (o["x2"] - o["x1"]) * (o["y2"] - o["y1"])
            if best is None or area < best[0]:
                best = (area, o["label"])
    return f"on the {best[1]}" if best else None


def describe(obj, mem):
    others = mem.all()
    on = on_what(obj, others)
    if on:
        return on
    where = place_words(obj["cx"], obj["cy"], obj["frame_w"], obj["frame_h"])
    near = nearby_words(obj, others)
    return f"{where}, {near}" if near else where


def answer(label, obj, visible, mem, now):
    """The spoken answer, given where the object is (or was) and whether it's in view now."""
    verb = "are" if label.split()[-1].endswith("s") else "is"
    if obj is None:
        return f"I haven't seen your {label} yet."
    where = describe(obj, mem)
    if visible:
        return f"Your {label} {verb} {where}."
    return f"I last saw your {label} {ago(now - obj['last_seen'])}, {where}."


def ring_angle(obj):
    """Which way the object is from the device, as a position on the LED ring: degrees
    along its LEDs, from LED 0 (what the firmware's RF/RS/RA take)."""
    dx = obj["cx"] - obj["frame_w"] / 2
    dy = obj["cy"] - obj["frame_h"] / 2
    # Seen from the front, the ring's right is the camera's left.
    seen = math.degrees(math.atan2(-dy, -dx))
    return ((RING_LED0_DEG - seen) if RING_CLOCKWISE else (seen - RING_LED0_DEG)) % 360


SPEAKER = None  # voice.Speaker once the app is running
CONVERSATION = collections.deque(maxlen=8)  # (time, "Person: ..." / "Device: ...") for Gemini
CONVERSATION_SECS = 120  # older lines are old news
SAID = collections.deque(maxlen=50)  # (time, text) of everything said, for the website


def say(text):
    SAID.append((time.time(), text))
    print(f"\n>> {text}")
    CONVERSATION.append((time.time(), f"Device: {text}"))
    if SPEAKER:
        SPEAKER.say(text)
    else:
        from src.voice import system_say

        system_say(text)  # the computer's own voice


TEACH_STATES = {  # what it's doing while learning something, for Gemini
    "first name": "About to learn something new: it asked what it's called.",
    "naming": "It just learned how something looks and asked what it's called.",
    "looking": "Learning the {name}: waiting to be shown it (held up, or clicked on).",
    "learning": "Learning how the {name} looks. If it's looking at the wrong thing, they say no.",
    "placing": "Learning the {name}: it asked to be shown it in the room.",
    "confirm": "Learning the {name}: it asked if the thing it found is it (yes or no).",
    "click": "Learning the {name}: waiting for a click on it in the video.",
}


def voice_context(app):
    """What Gemini needs to know to understand a question (gemini.describe_context)."""
    t = app.teach
    now = time.time()
    return {"objects": {label: list(words) for label, words in list(OBJECTS.items())},
            "taught": list(TAUGHT),
            "doing": TEACH_STATES.get(t["phase"], "").format(name=t["name"] or "new thing") if t else "",
            "conversation": [line for when, line in list(CONVERSATION) if now - when < CONVERSATION_SECS]}


def voice_commands(meaning):
    """Gemini's actions -> what App.command() understands (the same as typing them).
    A chat reply is said right away."""
    actions = [(a["do"], (a.get("thing") or "").strip().lower()) for a in meaning["actions"]]
    named = [thing for do, thing in actions if do == "name" and thing]
    if named and any(do == "teach" and not thing for do, thing in actions):
        # "learn this, it's my retainer case" can come back as teach + name
        actions = [(do, named[0] if do == "teach" else thing) for do, thing in actions if do != "name"]
    commands = []
    for do, thing in actions:
        if do == "find" and thing:
            commands.append(f"where is my {thing}")
        elif do == "teach":
            commands.append(f"learn my {thing}" if thing else "learn this")
        elif do == "name" and thing:
            commands.append(thing)
        elif do in ("yes", "no", "cancel"):
            commands.append(do)
        elif do == "forget":
            commands.append("forget that" if thing in ("", "that", "it") else f"forget my {thing}")
        elif do == "rename" and thing:
            commands.append(f"call it {thing}")
        elif do == "list":
            commands.append("what have you seen")
        elif do == "chat":
            say(meaning.get("reply") or "Sorry, I didn't catch that.")
    if not commands and not any(do == "chat" for do, _ in actions):
        say("Sorry, I didn't catch that.")
    return commands


# ------------------------------------------------------------------ drawing
def scaled(obj, img):
    h, w = img.shape[:2]
    return int(obj["cx"] * w / obj["frame_w"]), int(obj["cy"] * h / obj["frame_h"])


def draw_overlay(img, app, now):
    rows = app.mem.all()
    here = {r["label"]: app.locate(r["label"], now)[1] for r in rows}

    # Remembered things that are out of sight: gray dot + name
    for r in rows:
        if not here[r["label"]]:
            p = scaled(r, img)
            cv2.circle(img, p, 8, (170, 170, 170), 2)
            cv2.putText(img, f"{r['label']} (last seen)", (p[0] + 12, p[1] + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 170, 170), 1)

    # Memory list panel, top left
    lines = ["MEMORY"] + [
        f"{r['label']}: {'here' if here[r['label']] else ago(now - r['last_seen'])}"
        for r in rows[:10]]
    ph, pw = 14 + 22 * len(lines), 300
    sub = img[0:ph, 0:pw]
    img[0:ph, 0:pw] = (sub * 0.35).astype(sub.dtype)
    for i, text in enumerate(lines):
        cv2.putText(img, text, (10, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 255, 255) if i == 0 else (255, 255, 255), 1)

    # Laser aiming: where it's aiming (cross) and where it last saw the dot (red ring)
    aimer = app.aimer
    if aimer and aimer.state != "idle" and aimer.target:
        sx, sy = img.shape[1] / aimer.size[0], img.shape[0] / aimer.size[1]
        tx, ty = int(aimer.target[0] * sx), int(aimer.target[1] * sy)
        cv2.drawMarker(img, (tx, ty), (0, 255, 0), cv2.MARKER_CROSS, 24, 2)
        if aimer.dot:
            cv2.circle(img, (int(aimer.dot[0] * sx), int(aimer.dot[1] * sy)), 12, (0, 0, 255), 2)

    # The thing you just asked about: pulsing green circle
    if app.highlight and now < app.highlight[1]:
        r, _ = app.locate(app.highlight[0], now)
        if r:
            p = scaled(r, img)
            radius = int(40 + 10 * math.sin(now * 6))
            cv2.circle(img, p, radius, (0, 255, 0), 4)
            cv2.putText(img, r["label"].upper(), (p[0] - 40, p[1] - radius - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)


def draw_tracks(img, tracker, scale, glide=None):
    """The steady objects: the main one of each label bold, taught things in magenta.
    glide: a glide.Glide, to draw boxes where things are now, between detector passes."""
    for t in tracker.shown():
        main = tracker.main.get(t.label) is t
        taught = t.label in TAUGHT
        color = (255, 0, 255) if taught else (255, 200, 0)
        x1, y1, x2, y2 = (int(v * scale) for v in (glide.box(t) if glide else t.box))
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 3 if main else 1)
        name = f"your {t.label}" if taught else t.label
        cv2.putText(img, f"{name} {t.score:.2f}", (x1, max(20, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6 if main else 0.45, color, 2 if main else 1)


def draw_teaching(img, app, detector, scale, now):
    """While learning something: what it's looking at, and what to do."""
    t = app.teach
    if not t:
        return
    h = img.shape[0]
    phase, name = t["phase"], t["name"] or "it"
    tt = detector.teaching or {}
    box = None
    if phase == "looking" and t["laptop"]:
        msg = f"Hold {name} up to the LAPTOP camera ({len(tt.get('pictures', []))} pictures)   say cancel to stop"
    elif phase in ("looking", "learning"):
        box, n = tt.get("box"), len(tt.get("pictures", []))
        if box or phase == "learning":
            left = TEACH_SECS - (now - t["capture_from"]) if t["capture_from"] else TEACH_SECS
            msg = (f"Learning {name}... {n} picture{'s' if n != 1 else ''}, {max(0, left):.0f} s  "
                   f"(wrong thing? say no, or click on the right one)")
        else:
            msg = "Show me: hold it up, or click on it   (say cancel to stop)"
    elif phase in ("naming", "first name"):
        msg = "What is it? Say or type its name, like: my wallet"
    elif phase == "placing":
        msg = f"Show {name} to the room camera: hold it up, or put it down and step back (or click it)"
    elif phase == "confirm":
        box = t["choices"][t["choice"]]
        msg = (f"Are these your {name}?" if plural(name) else f"Is this your {name}?") + \
            " Say yes or no (Y / N), or click on the right one"
    else:  # click
        msg = f"Click on your {name} in this video"
    if box:
        x1, y1, x2, y2 = (int(v * scale) for v in box)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 255), 3)
        if phase == "confirm":
            cv2.putText(img, "?", (x2 + 6, y1 + 24), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 3)
    cv2.rectangle(img, (0, h - 92), (img.shape[1], h - 52), (0, 0, 0), -1)
    cv2.putText(img, msg, (14, h - 63), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)


def show_laptop_view(frame, detector, scale):
    """The laptop camera's window while teaching with it: a "hold it here" box, and a
    yellow box on what it's learning."""
    from src.vision.detector import SHOW_ZONE

    view = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    vh, vw = view.shape[:2]
    tt = detector.teaching or {}
    if tt.get("point") is None:
        zx1, zy1, zx2, zy2 = (int(SHOW_ZONE[0] * vw), int(SHOW_ZONE[1] * vh),
                              int(SHOW_ZONE[2] * vw), int(SHOW_ZONE[3] * vh))
        for x, y, dx, dy in ((zx1, zy1, 1, 1), (zx2, zy1, -1, 1), (zx1, zy2, 1, -1), (zx2, zy2, -1, -1)):
            cv2.line(view, (x, y), (x + 40 * dx, y), (255, 255, 255), 4)  # corner marks
            cv2.line(view, (x, y), (x, y + 40 * dy), (255, 255, 255), 4)
    n = len(tt.get("pictures", []))
    if tt.get("source") == "laptop" and tt.get("box"):
        x1, y1, x2, y2 = (int(v * scale) for v in tt["box"])
        cv2.rectangle(view, (x1, y1), (x2, y2), (0, 255, 255), 3)
    cv2.rectangle(view, (0, 0), (vw, 48), (0, 0, 0), -1)
    cv2.putText(view, f"Hold it inside the corners, close to the camera ({n} pictures)" if n or not tt.get("box")
                else "Hold it still...", (14, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
    cv2.imshow(TEACH_WINDOW, view)
    return view


def wipe_button(img):
    """Where the on-screen "Wipe memory" button sits: (x1, y1, x2, y2) in view pixels."""
    w = img.shape[1]
    return w - 190, 44, w - 14, 82


def draw_wipe_button(img):
    x1, y1, x2, y2 = wipe_button(img)
    cv2.rectangle(img, (x1, y1), (x2, y2), (40, 40, 200), -1)
    cv2.putText(img, "Wipe memory", (x1 + 14, y2 - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                (255, 255, 255), 2)


def draw_desk_area(img, area, corners):
    h, w = img.shape[:2]
    if corners is not None:
        msg = ("Click the desk's first corner" if not corners else "Now the opposite corner") + \
              "   (D again: use the whole picture)"
        cv2.putText(img, msg, (330, h - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        for cx, cy in corners:
            cv2.drawMarker(img, (int(cx * w), int(cy * h)), (0, 255, 255), cv2.MARKER_CROSS, 20, 2)
    elif area:
        cv2.rectangle(img, (int(area[0] * w), int(area[1] * h)), (int(area[2] * w), int(area[3] * h)),
                      (200, 200, 200), 1)


def draw_tile(img, tile, scale, color=(120, 120, 120)):
    """A faint outline of the part of the view the detector is zooming into right now."""
    if tile:
        x1, y1, x2, y2 = (int(v * scale) for v in tile)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 1)


def draw_voice_state(img, state, now):
    """Bottom-left hint: what pressing SPACE will do, or that it's listening."""
    y = img.shape[0] - 20
    if state == "listening":
        cv2.circle(img, (24, y - 6), 10, (0, 0, 255), -1 if int(now * 3) % 2 else 2)
        text, color = "Listening... (SPACE to stop)", (0, 0, 255)
    else:
        text, color = {"loading": ("voice loading...", (170, 170, 170)),
                       "thinking": ("Thinking...", (0, 255, 255)),
                       "off": ("voice off", (170, 170, 170))}.get(state, ("SPACE = ask out loud",
                                                                          (255, 255, 255)))
    cv2.putText(img, text, (44 if state == "listening" else 14, y), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, color, 2)


# ------------------------------------------------------------------ main app
def read_questions(q):
    time.sleep(1.5)
    print('\nAsk here, e.g. "where are my keys?"   (list / wipe / servo 90 120 / laser on / quit)')
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            q.put("quit")
            return
        if line.strip():
            q.put(line.strip())


class App:
    def __init__(self, mem, pointer, aimer=None, detector=None, phone=None):
        from src.vision.tracker import Tracker

        self.mem = mem
        self.pointer = pointer
        self.aimer = aimer     # laser.Aimer: points the laser and nudges it onto the object
        self.detector = detector  # detector.BackgroundDetector, for teaching
        self.phone = phone     # phone.Phone: alerts when you leave home without something
        self.leaving = None    # when the phone said you left (looking for the essentials)
        # Steady objects from the jumpy passes; a taught thing is the only one of its kind.
        self.tracker = Tracker(threshold, single=lambda label: label in TAUGHT, keep=keep_level)
        self.tracks = {}       # label -> {cx, cy, box, streak, hits, seen, frame_w, frame_h}
        self.highlight = None  # (label, until)
        self.save_view = False  # "save" command: write the next live view to a file
        self.teach = None      # while learning something: {"name", "phase", ...}
        self.spots = {}        # taught label -> where it's sitting: {"center", "still", "best"}
        self.last_taught = None  # the label taught last ("forget that", "call it ...")
        self.last_pass = None  # when the latest detector pass started
        self.pass_secs = 0.0   # seconds between passes (~0.4 on the Mac, 3+ on a laptop CPU)

    def update(self, candidates, frame, now, views=(), look=None):
        """candidates: [(label, confidence, (x1, y1, x2, y2))] from one finished detector pass.
        views: [(label, box, embedding, similarity)] for taught things in it. look: that
        pass's detector.Look of the whole view (to see hands)."""
        h, w = frame.shape[:2]
        if self.last_pass is not None and now > self.last_pass:
            gap = now - self.last_pass
            self.pass_secs = gap if not self.pass_secs else 0.8 * self.pass_secs + 0.2 * gap
        self.last_pass = now
        self.tracker.update(candidates, now)
        if views and not self.teach:
            self.learn_spots(views, w, now, look)
        seen = self.tracker.seen_now()
        for label, (conf, box) in seen.items():
            if label in SURFACES and any(  # one piece of furniture, two names: the surer one
                    o != label and o in SURFACES and oc > conf and box_overlap(box, ob) > 0.5
                    for o, (oc, ob) in seen.items()):
                continue
            cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
            t = self.tracks.get(label)
            if t and math.dist((cx, cy), (t["cx"], t["cy"])) <= wiggle_room(box, STILL_FRAC, w):
                t["streak"] += 1
            else:  # new, or moved: start counting stillness again
                t = self.tracks[label] = {"streak": 1, "hits": t["hits"] if t else 0}
            t["hits"] += 1  # sightings in a row, still or moving
            t.update(cx=cx, cy=cy, box=box, seen=now, frame_w=w, frame_h=h)
            if t["streak"] >= STABLE_HITS:
                change = self.mem.observe(label, box, frame, now, scenery=label in SURFACES)
                if change:
                    verb = "Remembered" if change == "new" else "Moved"
                    print(f"\n[memory] {verb}: {label}, {place_words(cx, cy, w, h)}")
        for label in list(self.tracks):
            if now - self.tracks[label]["seen"] > self.visible_secs():
                del self.tracks[label]

    def visible_secs(self):
        """How long since it was seen still counts as in view: VISIBLE_SECS, or as long as
        the tracker keeps boxing something it missed, if the detector is slower than that."""
        from src.vision.tracker import MISS_PASSES

        return max(VISIBLE_SECS, MISS_PASSES * self.pass_secs)

    def in_view(self, seen, now):
        """Was something last seen at `seen` (a pass's start time) in view right now? Counted
        on the detector's clock: a pass's result only arrives a pass after it started, so on
        a slow detector everything would already look stale by wall-clock time. The second
        check notices a detector that has stopped."""
        latest = self.last_pass if self.last_pass is not None else now
        return latest - seen <= self.visible_secs() and now - latest <= self.visible_secs() + self.pass_secs

    def focus_spots(self, frame_size, now):
        """Where to look again. First, small things seen once in the last pass but not
        confirmed yet: found in a zoomed tile, they'd wait for that tile to come around
        again (every 4th pass) for the second sighting; this zooms in on the very next
        pass. Otherwise the last spots of remembered small things that are out of sight
        right now, most recently seen first. Empty when everything is in view."""
        from src.vision.detector import zoom_region

        w, h = frame_size
        small = lambda x1, y1, x2, y2: (x2 - x1) * (y2 - y1) < 0.05 * w * h
        cols, nrows = TILES or (2, 2)
        zw, zh = int(min(w, 1.15 * w / cols)), int(min(h, 1.15 * h / nrows))  # a tile's size
        new = [t.box for t in self.tracker.tracks
               if t.fresh and not t.confirmed and t.label not in SURFACES and small(*t.box)]
        if new:
            return [zoom_region(box, (w, h), (zw, zh)) for box in new[:FOCUS_SPOTS]]
        rows = [r for r in self.mem.all() if r["label"] not in SURFACES
                and small(r["x1"], r["y1"], r["x2"], r["y2"])
                and not self.locate(r["label"], now)[1]]
        spots = []
        for r in rows[:FOCUS_SPOTS]:
            sx, sy = w / r["frame_w"], h / r["frame_h"]
            spots.append(zoom_region((r["x1"] * sx, r["y1"] * sy, r["x2"] * sx, r["y2"] * sy), (w, h), (zw, zh)))
        return spots

    def learn_spots(self, views, w, now, look=None):
        """Learn how taught things look where they've been put down (see LEARN_STILL_PASSES)."""
        hands = []
        if look is not None and len(look.boxes):
            texts = self.detector.detector.texts
            best = look.scores.argmax(1)
            hands = [look.boxes[i] for i in range(len(look.boxes))
                     if texts[best[i]] == "hand" and look.scores[i].max() > 0.12]
        for label, track in self.tracker.main.items():
            if label not in TAUGHT or not track.fresh:
                continue
            mine = [v for v in views if v[0] == label and box_overlap(v[1], track.box) > 0.3]
            if not mine:
                continue
            _, box, picture, sim = max(mine, key=lambda v: v[3])
            spot = self.spots.get(label)
            c = track.center
            if spot is None or math.dist(c, spot["center"]) > wiggle_room(box, STILL_FRAC, w):
                self.spots[label] = {"center": c, "still": 1, "best": sim, "learned": False}
                continue
            spot["still"] += 1
            spot["best"] = max(spot["best"], sim)
            held = any(box_overlap(box, hb) > 0 or _touches(box, hb) for hb in hands)
            # Sat still, not in a hand, recognized for sure here at least once, looks new from
            # here, and still like what was taught.
            if (spot["still"] >= LEARN_STILL_PASSES and spot["best"] >= cutoff(label)
                    and sim < max(0.93, cutoff(label) + 0.03) and not spot["learned"] and not held):
                entry = TAUGHT[label]
                to_taught = float((np.asarray(entry["pictures"], np.float32) @ picture).max())
                if to_taught < cutoff(label) - LEARN_DRIFT:
                    continue
                others = [float((np.asarray(o["pictures"] + o.get("seen", []), np.float32) @ picture).max())
                          for k, o in TAUGHT.items() if k != label and o["pictures"]]
                if others and to_taught < max(others) + LEARN_MARGIN:
                    continue  # it looks about as much like another taught thing
                entry["seen"] = (entry.get("seen", []) + [[round(float(v), 4) for v in picture]])[-LEARNED_PICTURES:]
                self.detector.detector.set_examples(label, entry["pictures"] + entry["seen"])
                save_taught()
                spot["learned"] = True
                print(f"\n[teach] {label}: learned how it looks here ({len(entry['seen'])} room pictures; "
                      f"box {[int(v) for v in box]}, match {sim:.2f}, to taught {to_taught:.2f})")

    # ---- teaching
    # Phases: "looking" (collecting pictures from the laptop or room camera), "naming",
    # then for laptop teaching: "placing" (watching for where it's put down), "confirm"
    # ("is this it?"), "click" (asked to click on it), "learning" (its room-view pictures).
    def start_teach(self, name, point, now):
        """name: what it is, if already said ("this is my wallet"). point: (x, y) clicked."""
        from src.vision.teach import Placement

        if not (self.detector and self.detector.can_teach):
            say("Teaching needs the owl detector.")
            return
        laptop = TEACH_CAMERA is not None and point is None and TEACH_CAMERA != open_camera.source
        self.teach = {"name": name, "phase": "looking", "started": now, "capture_from": None,
                      "laptop": laptop, "close": None, "close_photo": None, "laptop_frame": None,
                      "placement": Placement(None, self.detector.detector.texts) if laptop else None,
                      "background_at": 0.0}
        if laptop and not name:
            # The name first: knowing it's "a water bottle" helps pick it out of the picture.
            self.teach["phase"] = "first name"
            say("What is it?")
            return
        self.detector.start_teach(point, source="laptop" if laptop else "room", name=name)
        if point is not None:
            return
        if laptop:
            say(f"Hold your {name} up to the laptop camera.")
        else:
            say(f"Show me your {name}. Hold it up, or click on it." if name else
                "Show me. Hold it up, or click on it.")

    def laptop_failed(self, now):
        """The laptop camera wouldn't open: teach with the room camera instead."""
        self.teach.update(laptop=False, placement=None)
        self.detector.start_teach(None, source="room")
        say("I can't open the laptop camera. Show it to the room camera, or click on it.")

    def click_teach(self, point, now):
        """A click in the room view while teaching: learn the thing under the click."""
        t = self.teach
        self.detector.start_teach(point, source="room")
        if t["phase"] in ("looking", "naming") and t["close"] is None:
            t.update(phase="looking", laptop=False, placement=None)
        else:
            t["phase"] = "learning"
        t.update(capture_from=None, started=now)

    def click_laptop(self, point, now):
        """A click in the laptop camera's window: it's this thing."""
        if self.teach and self.teach["phase"] == "looking" and self.teach["laptop"]:
            self.detector.point_at(point)
            self.teach.update(capture_from=None, started=now)

    def on_pass(self, frame, look, now):
        """Called for every finished detector pass while teaching (frame: the room view)."""
        t = self.teach
        if not t or not t["placement"] or look is None:
            return
        if t["phase"] in ("looking", "naming") and now - t["background_at"] > 0.5:
            t["placement"].add_background(frame, look)  # the room before it's put down
            t["background_at"] = now
        elif t["phase"] == "confirm":
            pl = t["placement"]
            held = pl._held(look, frame.shape)
            if held and t.get("held") and not _far(held[0], t["choices"][t["choice"]]):
                t["choices"][t["choice"]] = held[0]  # keep the "?" box on it as it moves
                t["likes"][t["choice"]] = max(t["likes"][t["choice"]], pl.likeness[0])
            elif held and pl.likeness[0] >= t["likes"][t["choice"]] + 0.1:
                # Something much more like it just showed up (held up after a wrong guess).
                t.update(choices=list(held), likes=list(pl.likeness), choice=0, held=True, started=now)
                say(f"Wait, are these your {t['name']}?" if plural(t["name"]) else
                    f"Wait, is this your {t['name']}?")
        elif t["phase"] == "placing":
            t["placement"].word = self.detector.words.get(t["name"])
            found = t["placement"].update(frame, look, now)
            if found:  # held up, or put down: ask before learning anything
                t.update(phase="confirm", choices=list(found[1]), likes=list(t["placement"].likeness),
                         choice=0, started=now, held=found[0] == "held")
                say(f"Are these your {t['name']}?" if plural(t["name"]) else f"Is this your {t['name']}?")

    def answer_teach(self, yes, now):
        """Yes or no to "is this it?"."""
        t = self.teach
        if t["phase"] == "learning" and yes:
            return  # already learning it
        if t["phase"] == "confirm" and yes:
            self.detector.start_teach(None, source="room", seed=t["choices"][t["choice"]])
            t.update(phase="learning", capture_from=None, started=now)
            say("Great. Hold still while I learn how it looks from here.")
        elif t["phase"] == "confirm" and t["choice"] + 1 < len(t["choices"]):
            t["choice"] += 1
            t["started"] = now
            say("How about this one?")
        elif t["phase"] in ("confirm", "learning"):
            self.detector.stop_teach()
            t.update(phase="click", started=now)
            say(f"Okay. Click on your {t['name']} in the video.")

    def tick(self, frame, now):
        """Called every frame: moves teaching along."""
        t = self.teach
        if not t:
            return
        if t["phase"] in ("looking", "learning"):
            seen = self.detector.teaching["seen"] if self.detector.teaching else 0
            if seen and t["capture_from"] is None:
                t["capture_from"] = now
            if t["capture_from"] is None and now - t["started"] > 15:
                self.detector.stop_teach()
                if t["phase"] == "learning":
                    t.update(phase="click", started=now)
                    say(f"I can't see it there. Click on your {t['name']} in the video.")
                else:
                    self.teach = None
                    say("I couldn't tell what to look at. Say learn this, then click on it in the video.")
            elif t["capture_from"] is not None and now - t["capture_from"] >= TEACH_SECS:
                got = self.detector.stop_teach()
                if not got or len(got["pictures"]) < 2:
                    if t["phase"] == "learning":
                        t.update(phase="click", started=now)
                        say("I lost it. Click on it again.")
                    else:
                        self.teach = None
                        say("I lost it. Try again, and hold it still for a moment.")
                    return
                if t["phase"] == "learning" or not t["laptop"]:  # room pictures: done
                    t.update(pictures=got["pictures"], box=got["box"], frame=frame.copy(),
                             lookalike=got.get("lookalike", 0.0))
                    if t["name"]:
                        self.finish_teach(t["name"], now)
                    else:
                        t.update(phase="naming", started=now)
                        say("Got it. What is it?")
                    return
                # Close-ups from the laptop: next, where it gets put down.
                t["close"] = np.stack([p.float().cpu().numpy() for p in got["pictures"]])
                t["placement"].close = t["close"]
                if t["laptop_frame"] is not None and got["box"]:
                    t["close_photo"] = crop_around(t["laptop_frame"], got["box"])
                self.start_placing(now)  # (named before the close-ups)
        elif t["phase"] in ("naming", "first name") and now - t["started"] > 30:
            self.teach = None
            say("Okay, never mind.")
        elif t["phase"] == "placing" and now - t["started"] > 90:
            t.update(phase="click", started=now)
            say(f"I don't see where you put it. Click on your {t['name']} in the video.")
        elif t["phase"] in ("confirm", "click") and now - t["started"] > 60:
            self.teach = None
            say("Okay, never mind.")

    def rename_taught(self, old, new, now):
        """It misheard the name: "call it ball" renames the thing just taught."""
        new = label_for_name(new)
        if new == old:
            say(f"It's already called {old}.")
            return
        entry = TAUGHT.pop(old)
        if new in TAUGHT:  # the name of something already taught: add these pictures to it
            TAUGHT[new]["pictures"] = (TAUGHT[new]["pictures"] + entry["pictures"])[-48:]
            TAUGHT[new]["cutoff"] = max(TAUGHT[new].get("cutoff", 0), entry.get("cutoff", 0))
        else:
            entry["words"] = OBJECTS.get(new, name_words(new))
            if entry.get("photo") and os.path.exists(entry["photo"]):
                photo = os.path.join(TAUGHT_DIR, new.replace(" ", "_") + ".jpg")
                os.replace(entry["photo"], photo)
                entry["photo"] = photo
            TAUGHT[new] = entry
        if old not in PROMPTS:  # a name made up for it: no longer one to ask about
            OBJECTS.pop(old, None)
        OBJECTS.setdefault(new, TAUGHT[new]["words"])
        save_taught()
        owl = self.detector.detector
        owl.forget(old)
        owl.cutoffs.pop(old, None)
        owl.set_examples(new, TAUGHT[new]["pictures"] + TAUGHT[new].get("seen", []))
        owl.cutoffs[new] = TAUGHT[new].get("cutoff", PICTURE_MATCH)
        self.mem.db.execute("DELETE FROM objects WHERE label=?", (new,))
        self.mem.db.execute("UPDATE objects SET label=? WHERE label=?", (new, old))
        self.mem.db.execute("UPDATE events SET label=? WHERE label=?", (new, old))
        self.mem.db.commit()
        for t in self.tracker.tracks:
            if t.label == old:
                t.label = new
        if old in self.tracker.main:
            self.tracker.main[new] = self.tracker.main.pop(old)
        if old in self.tracks:
            self.tracks[new] = self.tracks.pop(old)
        self.spots.pop(old, None)
        self.last_taught = new
        say(f"Okay, it's called {new} now.")

    def named(self, name, now):
        t = self.teach
        t["name"] = name
        if t["close"] is not None:  # taught on the laptop: now find it in the room
            self.start_placing(now)
        else:
            self.finish_teach(name, now)

    def start_placing(self, now):
        t = self.teach
        t.update(phase="placing", started=now)
        self.detector.want_word(t["name"])  # also rank what it sees by the name
        say(f"Got it, {t['name']}. Now show {'them' if plural(t['name']) else 'it'} to the room camera.")

    def finish_teach(self, name, now):
        t, self.teach = self.teach, None
        label = label_for_name(name)
        entry = TAUGHT.setdefault(label, {"words": OBJECTS.get(label, name_words(label)),
                                          "pictures": [], "photo": None})
        entry["pictures"] = (entry["pictures"] + [[round(v, 4) for v in p.float().cpu().tolist()]
                                                  for p in t["pictures"]])[-48:]
        self.detector.detector.set_examples(label, entry["pictures"] + entry.get("seen", []))
        self.spots.pop(label, None)
        # Its own cutoff: above the best look-alike elsewhere in the room while it was taught.
        cut = min(0.97, max(PICTURE_MATCH, t.get("lookalike", 0.0) + CUTOFF_MARGIN))
        entry["cutoff"] = round(max(entry.get("cutoff", 0.0), cut), 3)
        self.detector.detector.cutoffs[label] = entry["cutoff"]
        if t.get("close") is not None:  # close-ups: kept for reference, not used to find it
            entry["close"] = (entry.get("close", []) + [[round(float(v), 4) for v in p] for p in t["close"]])[-24:]
        OBJECTS.setdefault(label, entry["words"])
        os.makedirs(TAUGHT_DIR, exist_ok=True)
        entry["photo"] = os.path.join(TAUGHT_DIR, label.replace(" ", "_") + ".jpg")
        cv2.imwrite(entry["photo"], t["close_photo"] if t.get("close_photo") is not None
                    else crop_around(t["frame"], t["box"]))
        save_taught()
        self.last_taught = label
        self.tracker.add(label, t["box"], now)
        self.tracks.pop(label, None)  # whatever the word alone was following before
        self.mem.observe(label, tuple(int(v) for v in t["box"]), t["frame"], now)  # answer right away
        verb = "are" if label.split()[-1].endswith("s") else "is"
        say(f"Got it. Now I know what your {label} look like." if verb == "are" else
            f"Got it. Now I know what your {label} looks like.")
        print(f"[teach] {label}: {len(entry['pictures'])} pictures, cutoff {entry['cutoff']:.2f} "
              f"(best look-alike {t.get('lookalike', 0):.2f}), photo {entry['photo']}")

    # ---- leaving home
    def left_home(self, now):
        """The phone says you left home: look for the essentials for a few seconds."""
        self.leaving = now
        print(f"\n[phone] You left home. Looking for your {join_words(ESSENTIALS)}...")

    def check_leaving(self, now):
        """Called every frame. LEAVE_LOOK_SECS after you left: whatever has been seen
        sitting still in the room since then, you forgot. Something you carried out isn't
        in view, so it isn't mentioned."""
        if self.leaving is None or now - self.leaving < LEAVE_LOOK_SECS:
            return
        since, self.leaving = self.leaving, None
        left = [(label, obj) for label in ESSENTIALS
                for obj in [self.mem.get(label)] if obj and obj["last_seen"] >= since]
        if not left:
            print("[phone] Nothing left behind.")
            if self.phone:
                self.phone.send("Nothing left behind", f"I don't see your {join_words(ESSENTIALS, 'or')} "
                                "in the room.", priority=2, tags=("white_check_mark",))
            return
        names = join_words([label for label, _ in left])
        lines = [f"{label.capitalize()}: {describe(obj, self.mem)}." for label, obj in left]
        print(f"[phone] Forgot: {names}")
        if self.phone:
            self.phone.send(f"You forgot your {names}!", "\n".join(lines))
        say(f"You left without your {names}.")  # for whoever is still in the room
        self.show(*left[0], now, ring="A")

    def show(self, label, obj, now, ring="F"):
        """Circle it in the live view, point the laser at it, and light the LED ring toward
        it (ring: "F" found, "S" last seen there, "A" forgot it)."""
        self.highlight = (label, now + HIGHLIGHT_SECS)
        self.pointer.ring(ring, ring_angle(obj))
        if self.aimer:
            # Close enough = within about a third of the object's size.
            tol = max(10, 0.3 * min(obj["x2"] - obj["x1"], obj["y2"] - obj["y1"]))
            self.aimer.start((obj["cx"], obj["cy"]), tol, (obj["frame_w"], obj["frame_h"]), now)

    def point(self, name, now):
        """Point at it without answering out loud (the website's /api/point)."""
        label = name if name in TAUGHT else find_label(name) or taught_name(name) or name
        obj, _ = self.locate(label, now)
        if obj:
            self.show(label, obj, now)
        else:
            say(f"I haven't seen your {label} yet.")

    def aim_at(self, point, size, now):
        """Point the laser at a spot in the room view: point and size in pixels."""
        self.highlight = None
        if not (self.aimer and self.pointer.connected):
            print("(the laser isn't connected)")
            return
        self.aimer.start(point, max(10, 0.02 * min(size)), size, now)

    def locate(self, label, now):
        """(where it is or was, visible right now?). A live sighting wins over memory,
        so it can answer about something you're holding before it has been remembered.
        The tracker has already confirmed it (two sightings), so in view = boxed in the video."""
        t = self.tracks.get(label)
        if t and self.in_view(t["seen"], now):
            x1, y1, x2, y2 = t["box"]
            return {"label": label, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "frame_w": t["frame_w"], "frame_h": t["frame_h"], "cx": t["cx"], "cy": t["cy"],
                    "last_seen": t["seen"], "moved_at": t["seen"], "snapshot": None}, True
        obj = self.mem.get(label)
        return obj, bool(obj) and self.in_view(obj["last_seen"], now)

    def command(self, text, now):
        """Handle one typed line. Returns False to quit."""
        c = text.lower().strip(" ?!.")
        if c in ("quit", "exit", "q"):
            return False
        if c == "list":
            rows = self.mem.all()
            if not rows:
                print("Memory is empty.")
            for r in rows:
                print(f"  {r['label']}: {describe(r, self.mem)} (seen {ago(now - r['last_seen'])})")
            if TAUGHT:
                print("  Taught: " + ", ".join(f"{k} ({len(v['pictures'])} pictures)" for k, v in TAUGHT.items()))
            return True
        if c in ("what have you seen", "what do you remember"):
            things = [r["label"] for r in self.mem.all() if r["label"] not in SURFACES]
            say(f"I've seen your {join_words(things)}." if things else "I haven't seen anything yet.")
            return True
        if c in ("wipe", "wipe memory", "forget everything", "clear memory"):
            self.mem.wipe()
            self.tracks.clear()
            self.tracker.clear()
            say("Memory wiped.")
            return True
        m = re.match(r"servo\s+(-?[\d.]+)\s+(-?[\d.]+)$", c)
        if m:
            if self.aimer:
                self.aimer.stop()
            self.pointer.move(float(m.group(1)), float(m.group(2)))
            return True
        if c in ("laser on", "laser off"):
            if self.aimer:
                self.aimer.stop()
            self.pointer.send("L1" if c.endswith("on") else "L0")
            return True
        if c == "save":
            self.save_view = True
            return True
        if c in ("phone", "phone test"):
            if not self.phone:
                print("Phone alerts are off (PHONE in src/app.py).")
            elif c == "phone":
                print(self.phone.setup_steps())
            else:
                self.phone.send("Ctrl+F for Your Room", "Test alert: your phone is set up.", priority=3,
                                tags=("tada",))
                print("(test alert sent)")
            return True
        if c == "left home":  # what the phone sends when you leave (or type it to test)
            self.left_home(now)
            return True
        if c == "ring test":
            if not self.pointer.has_ring:
                print("(No LED ring: is the ESP32 connected, with the latest firmware?)")
            else:
                self.pointer.ring("X")
                print("LED 0 is red, LED 1 green, LED 4 blue, for 15 s. Looking at the ring from the "
                      "front, set RING_LED0_DEG (where red is: 0 right, 90 top, 180 left, 270 bottom) "
                      "and RING_CLOCKWISE (is green clockwise from red?).")
            return True

        # Teaching
        phase = self.teach and self.teach["phase"]
        if self.teach and c in CANCEL:
            if self.detector:
                self.detector.stop_teach()
            self.teach = None
            say("Okay, never mind.")
            return True
        if c in CANCEL:  # not teaching: stop pointing
            if self.aimer:
                self.aimer.stop()
            self.highlight = None
            return True
        if phase in ("confirm", "learning") and (c in YES or c in NO):
            self.answer_teach(c in YES, now)
            return True
        if phase == "looking" and not teach_request(c)[0]:
            name = parse_name(c)  # named it while showing it: "it's my wallet"
            if name:
                self.teach["name"] = name
            return True
        if phase in ("naming", "first name"):
            name = parse_name(c)
            if not name:
                say("Sorry, what is it called?")
            elif phase == "first name":
                self.teach.update(name=name, phase="looking", started=now)
                self.detector.start_teach(None, source="laptop", name=name)
                say(f"Okay, {name}. Hold {'them' if plural(name) else 'it'} up to the laptop camera, "
                    "and turn it a little.")
            else:
                self.named(name, now)
            return True
        wants, name = teach_request(c)
        if wants:
            self.start_teach(name, None, now)
            return True
        m = re.match(r"forget (?:what )?(?:my |the |your )?(.+?)(?: looks? like)?$", c)
        if m:
            said = m.group(1)
            if said in ("that", "it", "last one", "the last one", "last thing", "the last thing", "this"):
                label = self.last_taught
            else:
                label = label_for_name(parse_name(said) or said)
                if label not in TAUGHT:
                    label = taught_name(parse_name(said) or said) or label
            if label in TAUGHT:
                photo = TAUGHT.pop(label).get("photo")
                save_taught()
                if photo and os.path.exists(photo):
                    os.remove(photo)
                self.detector.detector.forget(label)
                self.detector.detector.cutoffs.pop(label, None)
                self.mem.forget(label)  # and where it was
                self.tracks.pop(label, None)
                self.spots.pop(label, None)
                self.tracker.tracks = [t for t in self.tracker.tracks if t.label != label]
                self.tracker.main.pop(label, None)
                say(f"Okay, I forgot your {label}.")
            else:
                say(f"I wasn't taught a {label}." if label else "I haven't been taught anything yet.")
            return True
        m = re.match(r"^(?:no,? )?(?:call it|rename it(?: to)?|it's called|its called|it is called|"
                     r"i said|no it's|no its|it's actually|its actually)\s+(?:my |a |an |the )?(.+)$", c)
        if m and self.last_taught in TAUGHT:
            self.rename_taught(self.last_taught, parse_name(m.group(1)) or m.group(1), now)
            return True

        label = find_label(c)
        if not label and TAUGHT:  # maybe a taught name, misheard: "where's my bar" for "bhar"
            m = re.search(r"(?:my|the|your|our) (.+)$", c)
            label = taught_name(m.group(1)) if m else None
        if not label:
            say("Sorry, I don't know that one yet. I can find: " + ", ".join(OBJECTS) + ".")
            self.pointer.ring("N")
            return True
        obj, visible = self.locate(label, now)
        # Small things far away are missed for a few passes at a time: seen just now is "here".
        visible = visible or bool(obj) and now - obj["last_seen"] <= PRESENT_SECS
        say(answer(label, obj, visible, self.mem, now))
        if not obj:
            self.pointer.ring("N")
        else:
            self.show(label, obj, now, ring="F" if visible else "S")
            if not visible and obj["snapshot"] and os.path.exists(obj["snapshot"]):
                snap = cv2.imread(obj["snapshot"])
                s = DISPLAY_W / 2 / snap.shape[1]
                cv2.imshow("Last seen", cv2.resize(snap, None, fx=s, fy=s))
        return True


def load_area():
    """The desk area as fractions of the frame (x1, y1, x2, y2), or None for all of it."""
    try:
        with open(AREA_PATH) as f:
            return tuple(json.load(f))
    except (OSError, ValueError):
        return None


def save_area(area):
    if area:
        with open(AREA_PATH, "w") as f:
            json.dump(list(area), f)
    elif os.path.exists(AREA_PATH):
        os.remove(AREA_PATH)


def main():
    global SPEAKER
    from src.vision.detector import BackgroundDetector, make_detector
    from src.vision.glide import Glide

    print(f"({MACHINE})")
    print(f"Loading the {DETECTOR} detector (the first run downloads it)...")
    model = make_detector(DETECTOR, PROMPTS, DETECT_SIZE)
    governor = machine.Governor(model)
    load_taught(model)
    # The detector reports a bit below each threshold, so the tracker can keep following
    # things whose score dips for a moment.
    detector = BackgroundDetector(model, keep_level, load_area(), TILES, TILE_SIZE)
    cap = open_camera(CAMERA, CAMERA_LAG_SECS)

    from src.vision.laser_tracker import Aimer, Calibration, RoughMap

    pointer = Pointer(POINTER_LINK, POINTER_HOST)
    calib = Calibration.load()
    if calib:
        print(f"(Laser calibration loaded: {len(calib.points)} points, "
              f"typically {calib.error_px:.0f} px off before correcting)")
    elif pointer.connected:
        print("(No calibration.json yet: aiming roughly. Run calibrate.py once the laser is on.)")
    questions = queue.Queue()
    phone = None
    if PHONE:
        from src.phone import Phone

        phone = Phone(lambda: questions.put("left home"))
        print(f'(Phone alerts: ntfy topic {phone.topic}. Type "phone" for the setup.)')
    app = App(Memory(), pointer, Aimer(pointer, calib or RoughMap(PAN_CENTER, PAN_SPAN,
                                                                   TILT_CENTER, TILT_SPAN),
                                       open_camera.lag),
              detector, phone)
    pointer.ring("W")  # the privacy light: the camera is on
    threading.Thread(target=read_questions, args=(questions,), daemon=True).start()

    try:
        from src.voice import Speaker

        SPEAKER = Speaker(use_gemini=GEMINI)
    except ImportError as e:
        print(f"(speaking with the computer's own voice: {e})")
    if GEMINI:
        from src import gemini

        def check_gemini():
            problem = gemini.check()
            print("\n(Gemini: ready)" if problem is None else
                  f"\n(Gemini not working: {problem}. Whisper and the computer's own voice fill in.)")
        threading.Thread(target=check_gemini, daemon=True).start()

    # The website (web/): http://localhost:8000. See src/web.py.
    from src.web import WebBridge
    web = WebBridge(ROOT, SNAP_DIR, TAUGHT_DIR)
    if not web.start():
        web = None
    web_helpers = {"describe": describe, "surfaces": SURFACES, "objects": OBJECTS, "taught": TAUGHT}

    voice = None
    if VOICE:
        try:
            from src.voice import Listener

            def heard(text, meaning):
                if text:
                    CONVERSATION.append((time.time(), f"Person: {text}"))
                if meaning is not None:
                    commands = voice_commands(meaning)
                    print(f'\n(heard: "{text}" -> Gemini: {"; ".join(commands) or "-"})')
                    for c in commands:
                        questions.put(c)
                elif text is None:
                    say("Sorry, I didn't catch that.")
                else:
                    print(f'\n(heard: "{text}")')
                    questions.put(text)
            voice = Listener(heard, (lambda: voice_context(app)) if GEMINI else None)
        except ImportError as e:
            print(f"(voice off: {e}. pip install -r requirements.txt)")

    # For debugging: CTRLF_DET_LOG=<folder> logs every detector pass and a frame every 2 s.
    det_log = os.environ.get("CTRLF_DET_LOG")
    if det_log:
        os.makedirs(det_log, exist_ok=True)
        det_log_file, last_log_frame = open(os.path.join(det_log, "passes.jsonl"), "a"), 0

    running, last_seq, reported_error = True, 0, False
    view = None
    frame_w = 1
    teach_cap, teach_scale = None, 1.0  # the laptop camera, open only while teaching with it
    glide = Glide()  # boxes follow things between detector passes
    last_laptop_log = 0.0
    corners = None  # while choosing the desk area (D key): the corners clicked so far

    def on_click(event, x, y, flags, param):
        nonlocal corners
        if event != cv2.EVENT_LBUTTONDOWN or view is None:
            return
        if corners is not None:  # choosing the desk area: two clicks, opposite corners
            corners.append((x / view.shape[1], y / view.shape[0]))
            if len(corners) == 2:
                (ax, ay), (bx, by) = corners
                area = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
                corners = None
                if area[2] - area[0] > 0.1 and area[3] - area[1] > 0.1:
                    detector.area = area
                    save_area(area)
                    say("Desk area set. I'll only look there.")
                else:
                    say("That area is too small. Press D to try again.")
            return
        x1, y1, x2, y2 = wipe_button(view)
        if x1 <= x <= x2 and y1 <= y <= y2:  # the privacy button: one click forgets everything
            questions.put("wipe")
        elif app.teach and app.teach["phase"] not in ("naming", "first name"):  # learn the thing clicked on
            s = frame_w / view.shape[1]
            app.click_teach((x * s, y * s), time.time())

    def on_laptop_click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            app.click_laptop((x / teach_scale, y / teach_scale), time.time())
    mouse_ready = False
    voice_state = None
    while running:
        ok, frame = cap.read()
        if not ok:
            cap = reconnect_camera(cap, view, WINDOW)
            if cap is None:
                break
            continue
        now = time.time()
        frame_w = frame.shape[1]
        glide.frame(frame, now)

        # The detector works on its own copy in the background, about 2.5 frames a second.
        detector.submit(frame)
        seq, det_frame, candidates, det_time = detector.result()
        if seq != last_seq:
            last_seq = seq
            views = detector.taught_seen[1] if detector.taught_seen[0] == seq else []
            look = detector.last_look[2] if detector.last_look and detector.last_look[0] == seq else None
            app.update(candidates, det_frame, det_time, views, look)
            detector.focus = app.focus_spots(det_frame.shape[1::-1], det_time)
            governor.update(detector, seq)
            glide.anchor(app.tracker.shown(), det_frame.shape[1::-1], det_time)
            if app.teach and detector.last_look and detector.last_look[0] == seq:
                app.on_pass(det_frame, detector.last_look[2], det_time)
            if det_log:
                det_log_file.write(json.dumps({
                    "t": det_time, "tile": detector.tile, "focus": detector.focus_now,
                    "cands": [[k, round(c, 3), [int(v) for v in b]] for k, c, b in candidates],
                    "main": {k: [round(t.score, 3), list(t.int_box()), t.fresh]
                             for k, t in app.tracker.main.items()},
                    "match": {k: round(v, 3) for k, v in detector.best_match.items()},
                    "teach": app.teach and {"phase": app.teach["phase"], "name": app.teach["name"],
                                            "box": (detector.teaching or {}).get("box"),
                                            "asking": app.teach.get("choices") and
                                            [list(app.teach["choices"][app.teach["choice"]]),
                                             app.teach["likes"][app.teach["choice"]]],
                                            "n": len((detector.teaching or {}).get("pictures", []))},
                }) + "\n")
                det_log_file.flush()
                if det_time - last_log_frame > 2:
                    last_log_frame = det_time
                    cv2.imwrite(os.path.join(det_log, f"f{det_time:.2f}.jpg"),
                                cv2.resize(det_frame, None, fx=0.5, fy=0.5))
        if detector.error and not reported_error:
            print(f"\n(detector error: {detector.error!r})")
            reported_error = True

        if web:  # from the website: questions/commands, and push-to-talk
            for kind, text in web.take_actions():
                if kind == "command":
                    print(f'\n(website: "{text}")')
                    questions.put(text)
                elif kind == "listen" and voice:  # like SPACE
                    if SPEAKER:
                        SPEAKER.stop()
                    voice.toggle()
                elif kind == "click" and app.teach and app.teach["phase"] not in ("naming", "first name"):
                    app.click_teach((text[0] * frame.shape[1], text[1] * frame.shape[0]), now)
                elif kind == "teach":
                    print(f'\n(website: teach "{text}")')
                    app.start_teach(text, None, now)
                elif kind == "point":
                    app.point(text, now)
                elif kind == "aim":
                    h, w = frame.shape[:2]
                    app.aim_at((text[0] * w, text[1] * h), (w, h), now)
        while not questions.empty():
            text = questions.get()
            if web:
                web.note_command(text, now)
            if not app.command(text, now):
                running = False
        # Teaching with the laptop camera: open it only while it's needed.
        want_laptop = bool(app.teach and app.teach["laptop"] and app.teach["phase"] == "looking")
        if want_laptop and teach_cap is None:
            teach_cap = capture(TEACH_CAMERA)
            if teach_cap.isOpened() and teach_cap.read()[0]:
                cv2.namedWindow(TEACH_WINDOW)
                cv2.setMouseCallback(TEACH_WINDOW, on_laptop_click)
            else:
                teach_cap.release()
                teach_cap = None
                app.laptop_failed(now)
        elif not want_laptop and teach_cap is not None:
            teach_cap.release()
            teach_cap = None
            cv2.destroyWindow(TEACH_WINDOW)
        laptop_view = None
        if teach_cap is not None:
            ok_t, laptop_frame = teach_cap.read()
            if ok_t:
                app.teach["laptop_frame"] = laptop_frame
                detector.submit_teach(laptop_frame)
                teach_scale = 960 / laptop_frame.shape[1]
                laptop_view = show_laptop_view(laptop_frame, detector, teach_scale)
                if det_log and now - last_laptop_log > 1:  # what the laptop camera saw, for debugging
                    last_laptop_log = now
                    cv2.imwrite(os.path.join(det_log, f"laptop{now:.2f}.jpg"), laptop_view)

        app.tick(frame, now)
        app.check_leaving(now)
        app.aimer.update(frame, now)
        app.pointer.poll()
        if voice and voice.state != voice_state:  # the LED ring shows listening/thinking
            voice_state = voice.state
            app.pointer.ring({"listening": "L", "thinking": "T"}.get(voice_state, "I"))

        s = DISPLAY_W / frame.shape[1]
        view = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        draw_tracks(view, app.tracker, s, glide)
        if web:  # the website gets the boxes and teaching prompts, without the window's panels
            web_view = view.copy()
            draw_teaching(web_view, app, detector, s, now)
            web.tick(app, web_view, laptop_view, voice, detector, SAID, now, web_helpers)
        draw_overlay(view, app, now)
        draw_teaching(view, app, detector, s, now)
        if detector.secs_per_frame:
            cv2.putText(view, f"detector {1 / detector.secs_per_frame:.1f} fps",
                        (view.shape[1] - 210, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        if voice:
            draw_voice_state(view, voice.state, now)
        draw_wipe_button(view)
        draw_desk_area(view, detector.area, corners)
        draw_tile(view, detector.tile, s)
        draw_tile(view, detector.focus_now, s, (0, 160, 255))  # looking closer here
        cv2.imshow(WINDOW, view)
        if not mouse_ready:  # the window exists after the first imshow
            cv2.setMouseCallback(WINDOW, on_click)
            mouse_ready = True
        if app.save_view:
            app.save_view = False
            path = os.path.join(DATA, f"view_{time.strftime('%H%M%S')}.jpg")
            cv2.imwrite(path, view)
            print(f"(saved {path})")
        key = cv2.waitKey(1) & 0xFF
        if key == 27:
            running = False
        elif key == 32 and voice:  # SPACE = push-to-talk
            if SPEAKER:
                SPEAKER.stop()  # don't talk over the question
            voice.toggle()
        elif key in (ord("t"), ord("T")) and not app.teach:  # teach it something
            app.start_teach(None, None, now)
        elif key in (ord("y"), ord("Y"), ord("n"), ord("N")) and app.teach and \
                app.teach["phase"] in ("confirm", "learning"):
            app.answer_teach(key in (ord("y"), ord("Y")), now)
        elif key in (ord("d"), ord("D")):  # choose the desk area; D again = whole frame
            if corners is None:
                corners = []
            else:
                corners = None
                detector.area = None
                save_area(None)
                say("Looking at the whole picture again.")

    if app.pointer.connected:
        app.aimer.stop()
        app.pointer.ring("0")
        app.pointer.send("L0")
        app.pointer.ser.flush()
    if cap is not None:
        cap.release()
    if teach_cap is not None:
        teach_cap.release()
    cv2.destroyAllWindows()
    # The question thread is stuck waiting in input(); exit without waiting for it
    # (a normal exit makes Python crash with "could not acquire lock for <stdin>").
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
