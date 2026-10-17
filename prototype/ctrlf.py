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
  - If the ESP32 pointer is plugged in, it swings the laser toward the object.
    That aim is rough for now; camera-guided aiming comes once the laser arrives.

Setup (once, in Terminal, inside this folder). Use Python 3.11, not Homebrew's 3.14:
    ~/.pyenv/versions/3.11.8/bin/python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

Run it from Terminal.app. macOS only gives the camera to apps that ask for permission,
and Terminal does (approve it the first time):
    source venv/bin/activate
    python ctrlf.py
The first run downloads OWLv2 (~620 MB); after that it works offline.

Ask out loud: press SPACE in the video window, then ask (see voice.py).
Or type in the Terminal window (not the video window):
    where are my keys?     ask about anything in OBJECTS below
    list                   show everything it remembers
    wipe                   forget everything (the privacy button)
    servo 90 60            point the head: pan, tilt (hardware testing)
    laser on / laser off   hardware testing
    save                   save the live view as a picture (view_<time>.jpg)
    quit                   (or press Esc in the video window)

"""

import glob
import math
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time

import cv2

# ------------------------------------------------------------------ settings
CAMERA = 0                 # 0 = Mac's built-in camera, 1 = USB webcam (usually)
MIRROR_LEFT_RIGHT = False  # True if the camera faces you, so "left" means YOUR left
DETECTOR = "owl"           # "owl" finds keys and earbuds; "yolo" is faster but misses them
DISPLAY_W = 1280           # width of the live view window
VOICE = True               # SPACE in the video window = push-to-talk (needs mlx-whisper)

# What it looks for -> words people might use when asking.
# Add anything: the detector recognizes objects from their names.
# Big everyday things (laptop, mug, book) are landmarks for "left of the laptop".
OBJECTS = {
    "keys": ["keys", "key", "keychain", "car keys"],
    "wallet": ["wallet"],
    "earbuds case": ["earbuds", "earbud", "ear buds", "airpods", "air pods", "earbuds case",
                     "headphones"],
    "phone": ["phone", "iphone", "cell phone"],
    "glasses": ["glasses", "sunglasses", "spectacles"],
    "charger": ["charger", "charging cable", "power brick", "cable"],
    "water bottle": ["water bottle", "bottle", "hydro flask"],
    "remote": ["remote", "tv remote", "controller"],
    "id card": ["id", "id card", "student id", "gator 1", "card"],
    "laptop": ["laptop", "computer", "macbook"],
    "mug": ["mug", "cup", "coffee"],
    "book": ["book", "notebook"],
}

# Extra ways to describe an object to the detector (its label is always used too).
LOOKS_LIKE = {
    "keys": ["keychain", "a bunch of keys"],
    "earbuds case": ["airpods case"],
    "id card": ["badge"],
    "remote": ["remote control"],
}
PROMPTS = {label: LOOKS_LIKE.get(label, []) for label in OBJECTS}

# Lower = finds more; higher = fewer false alarms. Picked from a test recording on the Mac
# camera: at 0.30 keys were found in 85% of frames with no false alarms; at 0.25 "remote"
# and "glasses" started showing up on chair legs and a whiteboard drawing.
# Re-tune these once the demo camera is in place: false alarms depend on the background.
CONFIDENCE = {"default": 0.30, "keys": 0.25, "remote": 0.40, "glasses": 0.35, "laptop": 0.35}
STABLE_HITS = 3       # detections in a row in the same spot before it's remembered (~1 s)
STILL_FRAC = 0.03     # how far it can wiggle (fraction of image width) and still count as still
MOVED_FRAC = 0.05     # how far it must move (fraction of image width) for a new snapshot
VISIBLE_SECS = 3.0    # seen this recently = "it's here right now"
HIGHLIGHT_SECS = 6    # how long the green circle stays after a question

# Rough laser aim until we calibrate with the real laser (next step).
# If the head moves the wrong way, flip the sign of that SPAN.
PAN_CENTER, PAN_SPAN = 90, 60    # pan degrees at image center; degrees across the image width
TILT_CENTER, TILT_SPAN = 70, 45  # tilt degrees at image center; degrees across the image height

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "memory.db")
SNAP_DIR = os.path.join(BASE, "snapshots")
WINDOW = "Ctrl+F for Your Room (Esc to quit)"


# ------------------------------------------------------------------ memory
class Memory:
    """What it remembers: one row per object (latest place) + a log of every move."""

    COLS = ["label", "x1", "y1", "x2", "y2", "frame_w", "frame_h",
            "last_seen", "moved_at", "snapshot"]

    def __init__(self, path=DB_PATH, snap_dir=SNAP_DIR):
        self.snap_dir = snap_dir
        os.makedirs(snap_dir, exist_ok=True)
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

    def observe(self, label, box, frame, now):
        """Called when an object has been sitting still. Returns 'new', 'moved' or None."""
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        h, w = frame.shape[:2]
        old = self.get(label)
        if old is None or math.dist((cx, cy), (old["cx"], old["cy"])) > MOVED_FRAC * w:
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


def describe(obj, mem):
    where = place_words(obj["cx"], obj["cy"], obj["frame_w"], obj["frame_h"])
    near = nearby_words(obj, mem.all())
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


def say(text):
    print(f"\n>> {text}")
    if sys.platform == "darwin":
        subprocess.Popen(["say", text])  # the Mac's built-in voice


# ------------------------------------------------------------------ laser pointer (ESP32)
class Pointer:
    """Talks to the ESP32 over USB. Optional: everything else works without it."""

    def __init__(self):
        self.ser = None
        try:
            import serial
        except ImportError:
            print("(pyserial not installed: laser control off)")
            return
        ports = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
                       + glob.glob("/dev/cu.wchusbserial*"))
        if not ports:
            print("(No ESP32 plugged in: laser control off. That's fine for now.)")
            return
        try:
            self.ser = serial.Serial(ports[0], 115200, timeout=0)
        except Exception as e:
            print(f"(Couldn't open {ports[0]}: {e})")
            return
        # Opening the port can reboot the ESP32 (it wiggles the servos for ~2 s first),
        # so keep asking for its position until it answers.
        reply, t0, asked = "", time.time(), 0
        while time.time() - t0 < 4 and "OK P" not in reply:
            if time.time() - asked > 0.5:
                self.ser.write(b"?\n")
                asked = time.time()
            time.sleep(0.05)
            reply += self.ser.read(self.ser.in_waiting or 1).decode(errors="ignore")
        if "OK P" in reply:
            print(f"(Pointer connected on {ports[0]})")
        else:  # some other sketch: don't send it anything
            print(f"(Something is on {ports[0]} but it doesn't answer like esp32_pointer. "
                  f"Is the sketch uploaded? Laser control off.)")
            self.ser.close()
            self.ser = None

    def send(self, cmd):
        if not self.ser:
            print("(no ESP32 pointer connected)")
            return
        self.ser.write((cmd + "\n").encode())

    def poll(self):
        """Print anything the ESP32 says (e.g. 'laser auto-off')."""
        if self.ser and self.ser.in_waiting:
            for line in self.ser.read(self.ser.in_waiting).decode(errors="ignore").splitlines():
                if line.strip():
                    print(f"[esp32] {line.strip()}")

    def aim(self, obj):
        if not self.ser:
            return
        fx, fy = obj["cx"] / obj["frame_w"], obj["cy"] / obj["frame_h"]
        pan = round(PAN_CENTER + (0.5 - fx) * PAN_SPAN)
        tilt = round(TILT_CENTER + (fy - 0.5) * TILT_SPAN)
        self.send(f"P{pan} T{tilt} L1")


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

    # The thing you just asked about: pulsing green circle
    if app.highlight and now < app.highlight[1]:
        r, _ = app.locate(app.highlight[0], now)
        if r:
            p = scaled(r, img)
            radius = int(40 + 10 * math.sin(now * 6))
            cv2.circle(img, p, radius, (0, 255, 0), 4)
            cv2.putText(img, r["label"].upper(), (p[0] - 40, p[1] - radius - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)


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
    print('\nAsk here, e.g. "where are my keys?"   (list / wipe / servo 90 60 / laser on / quit)')
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            q.put("quit")
            return
        if line.strip():
            q.put(line.strip())


class App:
    def __init__(self, mem, pointer):
        self.mem = mem
        self.pointer = pointer
        self.tracks = {}       # label -> {cx, cy, box, streak, hits, seen, frame_w, frame_h}
        self.highlight = None  # (label, until)
        self.save_view = False  # "save" command: write the next live view to a file

    def update(self, detections, frame, now):
        """detections: {label: (confidence, (x1, y1, x2, y2))}, best box per label,
        for one finished detector frame."""
        h, w = frame.shape[:2]
        for label, (conf, box) in detections.items():
            cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
            t = self.tracks.get(label)
            if t and math.dist((cx, cy), (t["cx"], t["cy"])) <= STILL_FRAC * w:
                t["streak"] += 1
            else:  # new, or moved: start counting stillness again
                t = self.tracks[label] = {"streak": 1, "hits": t["hits"] if t else 0}
            t["hits"] += 1  # sightings in a row, still or moving
            t.update(cx=cx, cy=cy, box=box, seen=now, frame_w=w, frame_h=h)
            if t["streak"] >= STABLE_HITS:
                change = self.mem.observe(label, box, frame, now)
                if change:
                    verb = "Remembered" if change == "new" else "Moved"
                    print(f"\n[memory] {verb}: {label}, {place_words(cx, cy, w, h)}")
        for label in list(self.tracks):
            if now - self.tracks[label]["seen"] > VISIBLE_SECS:
                del self.tracks[label]

    def locate(self, label, now):
        """(where it is or was, visible right now?). A live sighting wins over memory,
        so it can answer about something you're holding before it has been remembered."""
        t = self.tracks.get(label)
        if t and t["hits"] >= 2 and now - t["seen"] <= VISIBLE_SECS:
            x1, y1, x2, y2 = t["box"]
            return {"label": label, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "frame_w": t["frame_w"], "frame_h": t["frame_h"], "cx": t["cx"], "cy": t["cy"],
                    "last_seen": t["seen"], "moved_at": t["seen"], "snapshot": None}, True
        obj = self.mem.get(label)
        return obj, bool(obj) and now - obj["last_seen"] <= VISIBLE_SECS

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
            return True
        if c in ("wipe", "wipe memory", "forget everything", "clear memory"):
            self.mem.wipe()
            self.tracks.clear()
            say("Memory wiped.")
            return True
        m = re.match(r"servo\s+(-?\d+)\s+(-?\d+)$", c)
        if m:
            self.pointer.send(f"P{m.group(1)} T{m.group(2)}")
            return True
        if c in ("laser on", "laser off"):
            self.pointer.send("L1" if c.endswith("on") else "L0")
            return True
        if c == "save":
            self.save_view = True
            return True

        label = find_label(c)
        if not label:
            say("Sorry, I don't know that one yet. I can find: " + ", ".join(OBJECTS) + ".")
            return True
        obj, visible = self.locate(label, now)
        say(answer(label, obj, visible, self.mem, now))
        if obj:
            self.highlight = (label, now + HIGHLIGHT_SECS)
            self.pointer.aim(obj)
            if not visible and obj["snapshot"] and os.path.exists(obj["snapshot"]):
                snap = cv2.imread(obj["snapshot"])
                s = DISPLAY_W / 2 / snap.shape[1]
                cv2.imshow("Last seen", cv2.resize(snap, None, fx=s, fy=s))
        return True


def open_camera(index):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SystemExit("Can't open the camera. Run this from Terminal.app and allow camera "
                         "access (System Settings > Privacy & Security > Camera), then retry.")
    return cap


def main():
    from detector import BackgroundDetector, draw_detections, make_detector

    print(f"Loading the {DETECTOR} detector (the first run downloads it)...")
    detector = BackgroundDetector(make_detector(DETECTOR, PROMPTS), CONFIDENCE)
    cap = open_camera(CAMERA)

    app = App(Memory(), Pointer())
    questions = queue.Queue()
    threading.Thread(target=read_questions, args=(questions,), daemon=True).start()

    voice = None
    if VOICE:
        try:
            from voice import Listener

            def heard(text):
                if text is None:
                    say("Sorry, I didn't catch that.")
                else:
                    print(f'\n(heard: "{text}")')
                    questions.put(text)
            voice = Listener(heard)
        except ImportError as e:
            print(f"(voice off: {e}. pip install mlx-whisper sounddevice)")

    running, last_seq, reported_error = True, 0, False
    detections = {}
    while running:
        ok, frame = cap.read()
        if not ok:
            print("Camera stopped sending frames.")
            break
        now = time.time()

        # The detector works on its own copy in the background, about 2.5 frames a second.
        detector.submit(frame)
        seq, det_frame, detections, det_time = detector.result()
        if seq != last_seq:
            last_seq = seq
            app.update(detections, det_frame, det_time)
        if detector.error and not reported_error:
            print(f"\n(detector error: {detector.error!r})")
            reported_error = True

        while not questions.empty():
            if not app.command(questions.get(), now):
                running = False
        app.pointer.poll()

        s = DISPLAY_W / frame.shape[1]
        view = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        draw_detections(view, detections, scale=s)
        draw_overlay(view, app, now)
        if detector.secs_per_frame:
            cv2.putText(view, f"detector {1 / detector.secs_per_frame:.1f} fps",
                        (view.shape[1] - 210, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        if voice:
            draw_voice_state(view, voice.state, now)
        cv2.imshow(WINDOW, view)
        if app.save_view:
            app.save_view = False
            path = os.path.join(BASE, f"view_{time.strftime('%H%M%S')}.jpg")
            cv2.imwrite(path, view)
            print(f"(saved {path})")
        key = cv2.waitKey(1) & 0xFF
        if key == 27:
            running = False
        elif key == 32 and voice:  # SPACE = push-to-talk
            voice.toggle()

    if app.pointer.ser:
        app.pointer.send("L0")
        app.pointer.ser.flush()
    cap.release()
    cv2.destroyAllWindows()
    # The question thread is stuck waiting in input(); exit without waiting for it
    # (a normal exit makes Python crash with "could not acquire lock for <stdin>").
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
