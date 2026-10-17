"""
Object detectors for Ctrl+F for Your Room, shared by camera_test.py and ctrlf.py.

  "owl"  OWLv2 (default). Finds small everyday things: keys, an AirPods case, an ID badge.
         About 0.4 s per frame on an M-series Mac, so it runs in a background thread
         while the live view keeps the camera's full frame rate.
  "yolo" YOLO-World. 20 ms per frame, but in our tests on the Mac camera it never found
         keys or the earbuds case (scores of 0.003), so it's only a fallback for slow laptops.

Both take {label: [prompts]} and return {label: (confidence, (x1, y1, x2, y2))}: the best
box per label, in pixels of the frame passed in.

First run downloads OWLv2 (~620 MB) into ~/.cache/huggingface; after that it works offline.
"""

import os
import threading
import time

import cv2
import numpy as np
import torch

BASE = os.path.dirname(os.path.abspath(__file__))

# Things that get mistaken for our objects (a cross pendant scored 0.26 as "keys").
# Each box takes the label of whichever prompt scores highest; if a decoy wins, it's dropped.
DECOYS = ["person", "face", "hand", "hair", "necklace", "pendant", "cross", "shirt", "watch",
          "bracelet", "earbud in ear", "chair", "ceiling light", "door", "window", "shelf", "sign"]


def best_device():
    if torch.backends.mps.is_available():
        return "mps"  # Apple GPU
    return "cuda" if torch.cuda.is_available() else "cpu"


def _flatten(prompts, decoys):
    """{label: [prompts]} -> parallel lists of prompt texts and the label each belongs to."""
    texts, owner = [], []
    for label, ps in prompts.items():
        for p in [label] + [p for p in ps if p != label]:
            texts.append(p)
            owner.append(label)
    for d in decoys:
        if d not in texts:
            texts.append(d)
            owner.append(None)
    return texts, owner


def _threshold(label, conf):
    return conf.get(label, conf.get("default", 0.25)) if isinstance(conf, dict) else conf


class OwlDetector:
    NAME = "google/owlv2-base-patch16-ensemble"
    SIZE = 960  # OWLv2's input size
    MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32) * 255
    STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32) * 255

    def __init__(self, prompts, decoys=DECOYS, device=None):
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        self.device = device or best_device()
        self.dtype = torch.float32 if self.device == "cpu" else torch.float16
        self.texts, self.owner = _flatten(prompts, decoys)
        proc = Owlv2Processor.from_pretrained(self.NAME)
        self.net = Owlv2ForObjectDetection.from_pretrained(self.NAME, dtype=self.dtype)
        self.net = self.net.to(self.device).eval()
        tokens = proc(text=[[f"a photo of {t}" for t in self.texts]], return_tensors="pt")
        self.text = {k: v.to(self.device) for k, v in tokens.items()}

    def _pixels(self, frame):
        """Like OWLv2's own preprocessing, but much faster: scale the long side to 960,
        pad bottom/right with gray to a square, normalize."""
        h, w = frame.shape[:2]
        s = self.SIZE / max(h, w)
        small = cv2.resize(frame, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        sq = np.full((self.SIZE, self.SIZE, 3), 127.5, np.float32)
        sq[:small.shape[0], :small.shape[1]] = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        x = (sq - self.MEAN) / self.STD
        return torch.from_numpy(x.transpose(2, 0, 1))[None].to(self.device, self.dtype), max(h, w)

    def detect(self, frame, conf=0.25):
        """conf: one threshold, or {label: threshold, "default": threshold}."""
        pixels, side = self._pixels(frame)
        with torch.no_grad():
            out = self.net(pixel_values=pixels, **self.text)
        scores = out.logits[0].float().sigmoid().cpu().numpy()  # (boxes, prompts)
        boxes = out.pred_boxes[0].float().cpu().numpy() * side   # cx, cy, w, h in frame pixels
        best_prompt, best_score = scores.argmax(1), scores.max(1)
        h, w = frame.shape[:2]
        found = {}
        for i in np.argsort(-best_score):
            score = float(best_score[i])
            if score < 0.05:
                break
            label = self.owner[best_prompt[i]]
            if label is None or label in found or score < _threshold(label, conf):
                continue
            cx, cy, bw, bh = boxes[i]
            found[label] = (score, (max(0, int(cx - bw / 2)), max(0, int(cy - bh / 2)),
                                    min(w - 1, int(cx + bw / 2)), min(h - 1, int(cy + bh / 2))))
        return found


class YoloWorldDetector:
    def __init__(self, prompts, decoys=("person",), device=None):
        from ultralytics import YOLOWorld

        self.device = device or best_device()
        self.texts, self.owner = _flatten(prompts, decoys)
        self.model = YOLOWorld(os.path.join(BASE, "yolov8s-worldv2.pt"))
        self.model.set_classes(self.texts)

    def detect(self, frame, conf=0.15):
        lowest = min(conf.values()) if isinstance(conf, dict) else conf
        r = self.model.predict(frame, conf=lowest, device=self.device, verbose=False)[0]
        found = {}
        for b in r.boxes:
            label, score = self.owner[int(b.cls)], float(b.conf)
            if label is None or score < _threshold(label, conf):
                continue
            if label not in found or score > found[label][0]:
                found[label] = (score, tuple(int(v) for v in b.xyxy[0]))
        return found


def make_detector(kind, prompts):
    return (OwlDetector if kind == "owl" else YoloWorldDetector)(prompts)


class BackgroundDetector:
    """Runs a detector on the newest camera frame in its own thread, so the video never waits.

    submit(frame) hands over a frame (older unprocessed ones are dropped);
    result() returns (seq, frame, detections, timestamp) for the newest finished frame,
    where seq goes up by one per finished frame (0 = nothing yet)."""

    def __init__(self, detector, conf):
        self.detector = detector
        self.conf = conf
        self._cv = threading.Condition()
        self._pending = None
        self._result = (0, None, {}, 0.0)
        self.secs_per_frame = 0.0
        self.error = None
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, frame):
        with self._cv:
            self._pending = frame
            self._cv.notify()

    def result(self):
        return self._result

    def _run(self):
        while True:
            with self._cv:
                while self._pending is None:
                    self._cv.wait()
                frame, self._pending = self._pending, None
            t = time.time()
            try:
                dets = self.detector.detect(frame, self.conf)
            except Exception as e:  # keep the app alive; the main loop reports it
                self.error = e
                time.sleep(1)
                continue
            dt = time.time() - t
            self.secs_per_frame = dt if not self.secs_per_frame else 0.8 * self.secs_per_frame + 0.2 * dt
            self._result = (self._result[0] + 1, frame, dets, t)


def draw_detections(img, detections, scale=1.0, color=(255, 200, 0)):
    """Boxes + labels. scale: display size / detection frame size."""
    for label, (conf, box) in detections.items():
        x1, y1, x2, y2 = (int(v * scale) for v in box)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        cv2.putText(img, f"{label} {conf:.2f}", (x1, max(20, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
