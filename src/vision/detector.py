"""
Object detectors for Ctrl+F for Your Room, shared by camera_test.py and ctrlf.py.

  "owl"  OWLv2 (default). Finds small everyday things: keys, an AirPods case, an ID badge.
         About 0.4 s per frame on an M-series Mac, so it runs in a background thread
         while the live view keeps the camera's full frame rate. It can also be taught
         objects by showing them (see OwlDetector.teach): it then matches their pictures.
  "yolo" YOLO-World. 20 ms per frame, but in our tests on the Mac camera it never found
         keys or the earbuds case (scores of 0.003), so it's only a fallback for slow laptops.

Both take {label: [prompts]} and return candidates: [(label, confidence, (x1, y1, x2, y2))],
a few boxes per label at most, in pixels of the frame passed in. tracker.py turns these
into steady objects.

First run downloads OWLv2 (~620 MB) into ~/.cache/huggingface; after that it works offline.
"""

import os
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

# Things that get mistaken for our objects (a cross pendant scored 0.26 as "keys").
# Each box takes the label of whichever prompt scores highest; if a decoy wins, it's dropped.
DECOYS = ["person", "face", "hand", "hair", "necklace", "pendant", "cross", "shirt", "watch",
          "bracelet", "earbud in ear", "ceiling light", "door", "window", "sign", "reflection"]
NOT_SHOWN = {"person", "face", "hand", "hair", "shirt"}  # never the thing being shown to it
FURNITURE = {"table", "kitchen table", "coffee table", "desk", "couch", "sofa", "chair", "bed",
             "shelf", "bookshelf", "ledge", "window sill", "tv", "television"}

# Taught objects are found by their pictures: a box counts as one when its picture is this
# similar (cosine) to one of the taught pictures. On a recording of a room, the real keys
# scored 0.97-1.0 (same spot as taught), the closest look-alike (a servo with orange wires,
# which the word "keys" picked over the real keys) 0.67, anything else 0.69 at most.
# That's the lowest cutoff: each taught thing gets its own, measured while it's taught (how
# much the rest of the room looks like it, plus CUTOFF_MARGIN). A small orange ball seen
# from across the room matched a dark patch on a shelf at 0.90.
PICTURE_MATCH = 0.80
CUTOFF_MARGIN = 0.03
PER_LABEL = 3  # boxes kept per label per pass (there can be two chairs)
# While being taught: a box must look this much like the first pictures to be added, so
# a moving hand doesn't make it learn the background instead.
FOLLOW_MATCH = 0.75
FOCUS_SIZE = 640   # input size for the look at where something missing was last seen
# Teaching with the laptop camera: hold the thing inside this part of the picture
# (fractions x1, y1, x2, y2; the laptop window draws it).
SHOW_ZONE = (0.28, 0.12, 0.72, 0.9)


def picture_score(cos, cutoff=PICTURE_MATCH):
    """Picture similarity on the same 0-1 scale as word scores: the cutoff -> 0.5."""
    return np.clip(0.5 + 0.5 * (cos - cutoff) / (1 - cutoff), 0, 1)


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
    if callable(conf):
        return conf(label)
    return conf.get(label, conf.get("default", 0.25)) if isinstance(conf, dict) else conf


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _near_box(a, b):
    """Is box a on or around box b (part of it, the hand holding it...)?"""
    pad = 0.5 * max(b[2] - b[0], b[3] - b[1])
    return a[0] < b[2] + pad and a[2] > b[0] - pad and a[1] < b[3] + pad and a[3] > b[1] - pad


def _jumped(a, b):
    """Did the held thing's box jump somewhere else (rather than move with the hand)?"""
    size = max(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])
    return np.hypot((a[0] + a[2] - b[0] - b[2]) / 2, (a[1] + a[3] - b[1] - b[3]) / 2) > 1.5 * size


def keep_best(cands, per_label=PER_LABEL, overlap=0.5):
    """Highest scores first; drop boxes that mostly overlap a better one with the same label."""
    kept = []
    for c in sorted(cands, key=lambda c: -c[1]):
        same = [k for k in kept if k[0] == c[0]]
        if len(same) < per_label and all(iou(c[2], k[2]) <= overlap for k in same):
            kept.append(c)
    return kept


@dataclass
class Look:
    """Everything OWLv2 saw in one image, one row per box."""
    boxes: np.ndarray       # (n, 4) x1, y1, x2, y2 in frame pixels
    scores: np.ndarray      # (n, prompts) word scores
    embeds: torch.Tensor    # (n, d) picture embeddings, unit length (on the model's device)
    objectness: np.ndarray  # (n,) how much each box looks like a separate thing, 0-1
    shift: np.ndarray = None  # (n,) OWLv2's per-box score shift and scale, to score new words
    scale: np.ndarray = None

    def word_scores(self, query):
        """How much each box looks like a word (query: from OwlDetector.word())."""
        emb = self.embeds if isinstance(self.embeds, np.ndarray) else self.embeds.float().cpu().numpy()
        return 1 / (1 + np.exp(-(emb @ query + self.shift) * self.scale))


class OwlDetector:
    NAME = "google/owlv2-base-patch16-ensemble"
    MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32) * 255
    STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32) * 255

    def __init__(self, prompts, decoys=DECOYS, device=None, size=960):
        """size: input resolution. OWLv2 was trained at 960; 640 is about 3x faster and
        fine when the image is already cropped to the desk."""
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        self.device = device or best_device()
        self.dtype = torch.float32 if self.device == "cpu" else torch.float16
        self.size = size
        self.texts, self.owner = _flatten(prompts, decoys)
        self.examples = {}  # taught label -> (n, d) tensor of its pictures' embeddings
        self.cutoffs = {}   # taught label -> its own picture-match cutoff
        self.best_match = {}  # taught label -> best picture similarity in the last image
        self.taught_seen = []  # [(label, box, embedding, similarity)] for taught things just found
        self.proc = proc = Owlv2Processor.from_pretrained(self.NAME)
        self.net = Owlv2ForObjectDetection.from_pretrained(self.NAME, dtype=self.dtype)
        self.net = self.net.to(self.device).eval()
        tokens = proc(text=[[f"a photo of {t}" for t in self.texts]], return_tensors="pt")
        tokens = {k: v.to(self.device) for k, v in tokens.items()}
        # The names never change, so encode them once instead of on every frame.
        blank = torch.zeros((1, 3, 960, 960), dtype=self.dtype, device=self.device)
        with torch.no_grad():
            self.queries = self.net(pixel_values=blank, **tokens).text_embeds
        self.query_mask = torch.ones(self.queries.shape[:2], dtype=torch.bool, device=self.device)

    def _pixels(self, frame, size):
        """Like OWLv2's own preprocessing, but much faster: scale the long side to `size`,
        pad bottom/right with gray to a square, normalize."""
        h, w = frame.shape[:2]
        s = size / max(h, w)
        small = cv2.resize(frame, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        sq = np.full((size, size, 3), 127.5, np.float32)
        sq[:small.shape[0], :small.shape[1]] = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        x = (sq - self.MEAN) / self.STD
        return torch.from_numpy(x.transpose(2, 0, 1))[None].to(self.device, self.dtype), max(h, w)

    def look(self, frame, size=None, offset=(0, 0)):
        """Run OWLv2 on frame. offset: where frame sits in the full picture, if it's a crop."""
        size = size or self.size
        pixels, side = self._pixels(frame, size)
        resized = size != 960  # off OWLv2's native grid: stretch its position codes
        with torch.no_grad():
            fmap = self.net.image_embedder(pixel_values=pixels, interpolate_pos_encoding=resized)[0]
            b, gh, gw, c = fmap.shape
            feats = fmap.reshape(b, gh * gw, c)
            logits, embeds = self.net.class_predictor(feats, self.queries, self.query_mask)
            boxes = self.net.box_predictor(feats, fmap, resized)
            objectness = self.net.objectness_predictor(feats)
            head = self.net.class_head
            shift = head.logit_shift(feats)[0, :, 0].float().cpu().numpy()
            scale = (head.elu(head.logit_scale(feats)) + 1)[0, :, 0].float().cpu().numpy()
        cx, cy, bw, bh = (boxes[0].float().cpu().numpy() * side).T  # frame pixels
        h, w = frame.shape[:2]
        inside = (cx < w) & (cy < h)  # boxes centered on the gray padding are nothing
        xyxy = np.stack([np.clip(cx - bw / 2, 0, w - 1) + offset[0], np.clip(cy - bh / 2, 0, h - 1) + offset[1],
                         np.clip(cx + bw / 2, 0, w - 1) + offset[0], np.clip(cy + bh / 2, 0, h - 1) + offset[1]], 1)
        keep = torch.from_numpy(np.where(inside)[0]).to(self.device)
        return Look(xyxy[inside], logits[0].float().sigmoid().cpu().numpy()[inside],
                    embeds[0].index_select(0, keep),
                    objectness[0].float().sigmoid().cpu().numpy()[inside], shift[inside], scale[inside])

    def word(self, name):
        """A new word to score boxes with (Look.word_scores), e.g. the name of something taught."""
        tokens = self.proc(text=[[f"a photo of {name}"]], return_tensors="pt")
        tokens = {k: v.to(self.device) for k, v in tokens.items()}
        blank = torch.zeros((1, 3, 960, 960), dtype=self.dtype, device=self.device)
        with torch.no_grad():
            q = self.net(pixel_values=blank, **tokens).text_embeds[0, 0].float().cpu().numpy()
        return q / (np.linalg.norm(q) + 1e-6)

    def candidates(self, look, conf):
        """Label every box with its best word, or with a taught object if its picture
        matches better; keep the ones above their label's threshold."""
        best_prompt, best = look.scores.argmax(1), look.scores.max(1).copy()
        labels = [self.owner[i] for i in best_prompt]  # None = a decoy won
        self.best_match = {}
        taught_rows = {}
        if self.examples:
            names = list(self.examples)
            sims = torch.cat([self.examples[n] for n in names]).to(look.embeds.dtype)
            cos = (look.embeds @ sims.T).float().cpu().numpy()  # (boxes, all examples)
            starts = np.cumsum([0] + [len(self.examples[n]) for n in names])
            per = np.stack([cos[:, starts[k]:starts[k + 1]].max(1) for k in range(len(names))], 1)
            cuts = np.array([self.cutoffs.get(n, PICTURE_MATCH) for n in names])
            pic = picture_score(per, cuts[None, :])
            k, pk = pic.argmax(1), pic.max(1)
            for i in np.where(pk > best)[0]:
                labels[i], best[i] = names[k[i]], pk[i]
                taught_rows[i] = float(per[i, k[i]])
            self.best_match = {n: float(per[:, j].max()) for j, n in enumerate(names)} if len(per) else {}
        cands = []
        for i in np.argsort(-best):
            if best[i] < 0.05:
                break
            if labels[i] is not None and best[i] >= _threshold(labels[i], conf):
                cands.append((labels[i], float(best[i]), tuple(int(v) for v in look.boxes[i])))
                if i in taught_rows:
                    taught_rows[cands[-1][2]] = (look.embeds[i], taught_rows[i])
        cands = keep_best(cands)
        self.taught_seen = [(label, box, taught_rows[box][0].float().cpu().numpy(), taught_rows[box][1])
                            for label, _, box in cands if box in taught_rows]
        return cands

    def detect(self, frame, conf=0.25, size=None):
        """conf: one threshold, {label: threshold, "default": threshold}, or a function of
        the label. size: input resolution for this call (default: the one given at startup)."""
        return self.candidates(self.look(frame, size), conf)

    # ---- teaching: pictures of a particular object, found by showing it
    def teach(self, label, embeds):
        """Add pictures (embeddings from look()) of an object, so it's found by how it looks."""
        old = self.examples.get(label)
        new = torch.stack(list(embeds)) if isinstance(embeds, (list, tuple)) else embeds
        new = new.to(self.device, self.dtype)
        self.examples[label] = new if old is None else torch.cat([old, new])[-48:]

    def forget(self, label):
        self.examples.pop(label, None)

    def set_examples(self, label, pictures):
        """Replace all of a taught thing's pictures ((n, d) array)."""
        self.examples[label] = torch.as_tensor(np.asarray(pictures, np.float32)).to(self.device, self.dtype)

    def shown_thing(self, look, frame_size, point=None):
        """Index of the box for the thing being shown, or None. point: (x, y) someone clicked
        on; otherwise the most object-like thing next to a hand."""
        b, n = look.boxes, len(look.boxes)
        if not n:
            return None
        w, h = frame_size
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        word = [self.texts[i] for i in look.scores.argmax(1)]
        ok = (area < 0.15 * w * h) & np.array([t not in NOT_SHOWN for t in word])
        obj = look.objectness
        if point is not None:
            px, py = point
            hit = ok & (b[:, 0] <= px) & (px <= b[:, 2]) & (b[:, 1] <= py) & (py <= b[:, 3])
            if not hit.any():
                return None
            # The most object-like box under the click, but not a big one around it (a
            # chair around the keys): among boxes nearly as object-like, the smallest.
            top = obj[hit].max()
            pick = hit & (obj >= 0.5 * top)
            return int(np.argmin(np.where(pick, area, np.inf)))
        # Held up: next to a hand, not a decoy (the wristband next to a held ID card scored
        # higher than the card), and not much bigger than the hand.
        hand_rows = [i for i in range(n) if word[i] == "hand" and look.scores[i].max() > 0.15]
        thing = ok & np.array([self.owner[i] is not None for i in look.scores.argmax(1)])
        best, best_score = None, 0.0
        for j in hand_rows:
            hx, hy = (b[j, 0] + b[j, 2]) / 2, (b[j, 1] + b[j, 3]) / 2
            reach = 1.2 * max(b[j, 2] - b[j, 0], b[j, 3] - b[j, 1])
            cx, cy = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2
            close = (thing & (np.hypot(cx - hx, cy - hy) < reach) & (np.arange(n) != j)
                     & (area < 2.5 * area[j]))
            for i in np.where(close)[0]:
                if obj[i] > best_score:
                    best, best_score = int(i), float(obj[i])
        return best


    def in_zone(self, look, frame_size, zone=SHOW_ZONE, word=None, moving=None):
        """Index of the thing held up to the laptop camera, or None. Candidates: centered
        inside zone (the laptop window's "hold it here" corners; big things held close stick
        out of it), not a person, face, hair, decoy or furniture, and either overlapping a
        hand or moving. Ranked by how object-like they are, how much they look like their
        name (word, from .word(); a green bottle got lost in favor of its cap and the window
        behind it before the name was used) and how much of them moved (moving: a 0/1 mask
        of what changed since the last laptop frame; the background doesn't move)."""
        b = look.boxes
        if not len(b):
            return None
        w, h = frame_size
        zx1, zy1, zx2, zy2 = zone[0] * w, zone[1] * h, zone[2] * w, zone[3] * h
        zarea = (zx2 - zx1) * (zy2 - zy1)
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        cx, cy = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2
        centered = (cx > zx1) & (cx < zx2) & (cy > zy1) & (cy < zy2)
        best_prompt = look.scores.argmax(1)
        word_of = [self.texts[i] for i in best_prompt]
        # A held thing's box often scores best as "hand" (fingers in it), so that's allowed.
        thing = np.array([self.owner[i] is not None or word_of[k] == "hand" for k, i in enumerate(best_prompt)])
        not_person = np.array([t not in ("person", "face", "hair", "shirt") and t not in FURNITURE for t in word_of])
        ok = centered & (area > 0.01 * zarea) & (area < 1.5 * zarea) & thing & not_person
        hands = [j for j in range(len(b)) if word_of[j] == "hand" and look.scores[j].max() > 0.1]
        held = np.zeros(len(b), bool)
        for j in hands:
            jx = np.clip(np.minimum(b[:, 2], b[j, 2]) - np.maximum(b[:, 0], b[j, 0]), 0, None)
            jy = np.clip(np.minimum(b[:, 3], b[j, 3]) - np.maximum(b[:, 1], b[j, 1]), 0, None)
            held |= (jx * jy > 0) & (np.arange(len(b)) != j)
        moved = np.zeros(len(b))
        if moving is not None:
            mh, mw = moving.shape
            for i in np.where(ok)[0]:
                x1, y1, x2, y2 = (int(v) for v in (b[i, 0] * mw / w, b[i, 1] * mh / h, b[i, 2] * mw / w, b[i, 3] * mh / h))
                part = moving[max(0, y1):y2 + 1, max(0, x1):x2 + 1]
                moved[i] = part.mean() if part.size else 0
        ok &= held | (moved > 0.3)
        if not ok.any():
            return None
        score = look.objectness + moved
        if word is not None and look.shift is not None:
            score = score + 2 * look.word_scores(word)
        # Of the nearly-best, the biggest: the whole bunch of keys, not one key.
        top = score[ok].max()
        pick = ok & (score >= top - 0.25)
        return int(np.argmax(np.where(pick, area, -1)))

    def find_again(self, look, pictures, frame_size, min_match=FOLLOW_MATCH, near=None):
        """Index of the box that looks most like pictures (of the thing being shown), or None
        if nothing is similar enough. Follows it by looks, so a moving hand can't drag it
        onto the background. near: its last box; then only boxes close to it count (a raised
        hand's wrist and a chair leg looked alike: it jumped from one to the other)."""
        if not len(look.boxes):
            return None
        w, h = frame_size
        b = look.boxes
        small = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]) < 0.15 * w * h
        if near is not None:
            reach = 1.5 * max(near[2] - near[0], near[3] - near[1], 0.03 * w)
            cx, cy = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2
            small &= np.hypot(cx - (near[0] + near[2]) / 2, cy - (near[1] + near[3]) / 2) < reach
        sims = torch.stack(list(pictures)).to(look.embeds.dtype)
        cos = (look.embeds @ sims.T).float().cpu().numpy().max(1)
        cos[~small] = -1
        i = int(cos.argmax())
        return i if cos[i] >= min_match else None


class YoloWorldDetector:
    def __init__(self, prompts, decoys=("person",), device=None):
        from ultralytics import YOLOWorld

        self.device = device or best_device()
        self.texts, self.owner = _flatten(prompts, decoys)
        self.model = YOLOWorld(os.path.join(ROOT, "weights", "yolov8s-worldv2.pt"))
        self.model.set_classes(self.texts)
        self.examples, self.best_match = {}, {}  # (teaching needs OWLv2)

    def detect(self, frame, conf=0.15, size=None):
        lowest = min(conf.values()) if isinstance(conf, dict) else 0.05 if callable(conf) else conf
        r = self.model.predict(frame, conf=lowest, device=self.device, verbose=False)[0]
        cands = []
        for b in r.boxes:
            label, score = self.owner[int(b.cls)], float(b.conf)
            if label is not None and score >= _threshold(label, conf):
                cands.append((label, score, tuple(int(v) for v in b.xyxy[0])))
        return keep_best(cands)


def make_detector(kind, prompts, size=768):
    return OwlDetector(prompts, size=size) if kind == "owl" else YoloWorldDetector(prompts)


def tile_regions(region, cols, rows, overlap=0.15):
    """Split (x1, y1, x2, y2) into cols x rows overlapping parts, in pixels."""
    x1, y1, x2, y2 = region
    tw, th = (x2 - x1) / cols, (y2 - y1) / rows
    return [(int(max(x1, x1 + c * tw - overlap * tw)), int(max(y1, y1 + r * th - overlap * th)),
             int(min(x2, x1 + (c + 1) * tw + overlap * tw)), int(min(y2, y1 + (r + 1) * th + overlap * th)))
            for r in range(rows) for c in range(cols)]


def zoom_region(box, frame_size, region_size):
    """A region_size (w, h) window centered on box, kept inside the frame."""
    (w, h), (rw, rh) = frame_size, region_size
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    x1 = int(min(max(0, cx - rw / 2), w - rw))
    y1 = int(min(max(0, cy - rh / 2), h - rh))
    return x1, y1, x1 + rw, y1 + rh


class BackgroundDetector:
    """Runs a detector on the newest camera frame in its own thread, so the video never waits.

    submit(frame) hands over a frame (older unprocessed ones are dropped);
    result() returns (seq, frame, candidates, timestamp) for the newest finished frame,
    where seq goes up by one per finished frame (0 = nothing yet).
    area: None for the whole frame, or (x1, y1, x2, y2) as fractions of it to look only
    at the desk. tiles: (columns, rows) to also zoom into one part of the view per pass,
    taking turns, at tile_size. Boxes always come back in whole-frame pixels.

    Teaching: start_teach() makes each pass also find the thing being shown and collect
    its pictures (from the whole view and zoomed in, like the passes see it);
    stop_teach() returns them. With source="laptop", the pictures come from frames handed
    over with submit_teach() (another camera) instead of the room view.
    last_look: (seq, frame, Look with numpy embeddings) of the latest whole-view pass."""

    def __init__(self, detector, conf, area=None, tiles=None, tile_size=640):
        self.detector = detector
        self.conf = conf
        self.area = area
        self.tiles, self.tile_size = tiles, tile_size
        self.next_tile = 0
        self.tile = None  # the part zoomed into last, (x1, y1, x2, y2) in pixels, for drawing
        # Where remembered things were last seen, (x1, y1, x2, y2) in pixels, most wanted
        # first (set by the app); each pass also zooms into one of them, taking turns.
        self.focus = []
        self.next_focus = 0
        self.focus_now = None  # the one looked at last, for drawing
        self.teaching = None  # while being shown something: {"point", "box", "pictures", ...}
        self.best_match = {}  # taught label -> best picture similarity in the last pass
        self.last_look = None
        self.taught_seen = (0, [])  # (seq, [(label, box, embedding, similarity)])
        self.words = {}  # name -> query vector (OwlDetector.word), made in this thread
        self._want_word = None
        self._cv = threading.Condition()
        self._pending = None
        self._result = (0, None, [], 0.0)
        self.secs_per_frame = 0.0
        self.error = None
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def can_teach(self):
        return hasattr(self.detector, "look")

    def submit(self, frame):
        with self._cv:
            self._pending = frame
            self._cv.notify()

    def result(self):
        return self._result

    def start_teach(self, point=None, source="room", seed=None, name=None):
        """point: (x, y) in frame pixels that was clicked, or None for what's being held up.
        source: "room" (the main camera) or "laptop" (frames from submit_teach).
        seed: a box (x1, y1, x2, y2) known to be it, e.g. found where it was put down.
        name: what it is, if known (helps find it; see want_word)."""
        self.want_word(name)
        self.teaching = {"point": point, "box": None, "pictures": [], "seen": 0, "passes": 0,
                         "source": source, "seed": seed, "frame": None, "name": name}

    def submit_teach(self, frame):
        """A frame from the teaching camera (source="laptop")."""
        if self.teaching is not None:
            self.teaching["frame"] = frame

    def point_at(self, point):
        """While teaching: no, it's this one (a click). Then it follows that one instead of
        the "hold it here" box."""
        if self.teaching is not None:
            self.teaching.update(point=point, box=None, pictures=[], seen=0, seed=None)

    def want_word(self, name):
        """Make a word query for name in the detector's thread (see .words)."""
        if name and name not in self.words and self.can_teach:
            self._want_word = name

    def stop_teach(self):
        t, self.teaching = self.teaching, None
        return t

    def _region(self, frame):
        h, w = frame.shape[:2]
        a = self.area or (0, 0, 1, 1)
        return int(a[0] * w), int(a[1] * h), int(a[2] * w), int(a[3] * h)

    def _detect(self, frame):
        h, w = frame.shape[:2]
        region = self._region(frame)
        tile = None
        if self.tiles:
            tile = tile_regions(region, *self.tiles)[self.next_tile % (self.tiles[0] * self.tiles[1])]
            self.next_tile += 1
        teaching = self.teaching
        if not self.can_teach:
            found = self._detect_in(frame, region)
            if tile:
                found += self._detect_in(frame, tile, self.tile_size)
            self.tile = tile
            return keep_best(found)
        d = self.detector
        whole = self._look(frame, region)
        found = d.candidates(whole, self.conf)
        match, seen = dict(d.best_match), list(d.taught_seen)
        focus = list(self.focus)
        spot = focus[self.next_focus % len(focus)] if focus else None
        self.next_focus += 1
        for region, size in ((tile, self.tile_size), (spot, FOCUS_SIZE)):
            if region:
                found += d.candidates(self._look(frame, region, size), self.conf)
                seen += d.taught_seen
                for k, v in d.best_match.items():
                    match[k] = max(v, match.get(k, 0))
        self.focus_now = spot
        self.taught_seen = (self._result[0] + 1, seen)
        self.tile, self.best_match = tile, match
        found = self._one_name(found)
        self.last_look = (self._result[0] + 1, frame,
                          Look(whole.boxes, whole.scores, whole.embeds.float().cpu().numpy(), whole.objectness,
                               whole.shift, whole.scale))
        if teaching is not None and teaching["source"] == "room":
            self._teach_step(frame, whole, teaching)
        return keep_best(found)

    def _one_name(self, found):
        """Two taught things can't be the same thing: where boxes of two taught labels
        mostly overlap, keep only the surer one (a ball at 0.96 was also called "duck" at
        0.53, and the duck then learned the ball as one of its own views)."""
        taught = self.detector.examples
        drop = set()
        for i, (la, sa, ba) in enumerate(found):
            for j, (lb, sb, bb) in enumerate(found):
                if la != lb and la in taught and lb in taught and sa < sb and iou(ba, bb) > 0.5:
                    drop.add(i)
        return [c for k, c in enumerate(found) if k not in drop]

    def _look(self, frame, region, size=None):
        x1, y1, x2, y2 = region
        return self.detector.look(frame[y1:y2, x1:x2], size, offset=(x1, y1))

    def _detect_in(self, frame, region, size=None):
        x1, y1, x2, y2 = region
        found = self.detector.detect(frame[y1:y2, x1:x2], self.conf, size)
        return [(label, conf, (bx1 + x1, by1 + y1, bx2 + x1, by2 + y1))
                for label, conf, (bx1, by1, bx2, by2) in found]

    def _teach_step(self, frame, whole, t):
        h, w = frame.shape[:2]
        if whole is None:  # a frame from the teaching camera
            whole = self._look(frame, (0, 0, w, h))
        t["passes"] += 1
        d = self.detector
        follow_near = t["box"]
        if t["source"] == "laptop" and t["point"] is None:
            # Held up to the laptop: whatever is in the "hold it here" box, every pass.
            small = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY), (5, 5), 0)
            prev, t["prev_small"] = t.get("prev_small"), small
            moving = (cv2.absdiff(small, prev) > 18).astype(np.float32) if prev is not None else None
            i = d.in_zone(whole, (w, h), word=self.words.get(t.get("name")), moving=moving)
            if i is not None and t["box"] is not None and _jumped(whole.boxes[i], t["box"]):
                t.update(pictures=[], seen=0)  # something else now: start over with it
                t["box"] = tuple(float(v) for v in whole.boxes[i])
                return
        elif not t["pictures"] and t["seed"] is not None:
            overlap = np.array([iou(b, t["seed"]) for b in whole.boxes]) if len(whole.boxes) else np.zeros(0)
            i = int(overlap.argmax()) if len(overlap) and overlap.max() > 0.5 else None
        elif not t["pictures"]:
            i = d.shown_thing(whole, (w, h), t["point"])
        else:
            i = d.find_again(whole, t["pictures"], (w, h), near=follow_near)
        if i is None:
            return
        box = tuple(float(v) for v in whole.boxes[i])
        pictures = [whole.embeds[i]]
        looks = [whole]
        # Zoomed in the way a tile pass sees it, so it's recognized at both scales.
        if self.tiles and t["source"] == "room":
            region = self._region(frame)
            tw, th = (region[2] - region[0]) / self.tiles[0] * 1.3, (region[3] - region[1]) / self.tiles[1] * 1.3
            zoom = zoom_region(box, (w, h), (int(min(tw, w)), int(min(th, h))))
            near = self._look(frame, zoom, self.tile_size)
            looks.append(near)
            j = d.find_again(near, pictures + t["pictures"], (w, h), FOLLOW_MATCH - 0.1)
            if j is not None and iou(near.boxes[j], box) > 0.4:  # the same thing, not a part of it
                pictures.append(near.embeds[j])
        t["box"], t["seen"] = box, t["seen"] + 1
        t["pictures"] += [p.detach().clone() for p in pictures]
        if t["source"] == "room":  # how much the rest of the room looks like it
            sims = torch.stack(t["pictures"])
            for look in looks:
                lb = look.boxes
                far = np.array([not _near_box(bb, box) for bb in lb]) & \
                    ((lb[:, 2] - lb[:, 0]) * (lb[:, 3] - lb[:, 1]) < 0.15 * w * h)
                if far.any():
                    c = (look.embeds[torch.from_numpy(np.where(far)[0]).to(look.embeds.device)]
                         @ sims.to(look.embeds.dtype).T).float().max().item()
                    t["lookalike"] = max(t.get("lookalike", 0.0), c)

    def _run(self):
        while True:
            with self._cv:
                while self._pending is None:
                    self._cv.wait()
                frame, self._pending = self._pending, None
            start = time.time()
            try:
                dets = self._detect(frame)
                if self._want_word:
                    name, self._want_word = self._want_word, None
                    self.words[name] = self.detector.word(name)
                teaching = self.teaching
                if teaching is not None and teaching["source"] == "laptop" and teaching["frame"] is not None:
                    f, teaching["frame"] = teaching["frame"], None
                    self._teach_step(f, None, teaching)
                dt = time.time() - start
                self.secs_per_frame = dt if not self.secs_per_frame else 0.8 * self.secs_per_frame + 0.2 * dt
                self._result = (self._result[0] + 1, frame, dets, start)
            except Exception as e:  # keep the thread alive; the main loop reports it
                self.error = e
                time.sleep(1)


def draw_detections(img, detections, scale=1.0, color=(255, 200, 0)):
    """Boxes + labels for [(label, conf, box)]. scale: display size / detection frame size."""
    for label, conf, box in detections:
        x1, y1, x2, y2 = (int(v * scale) for v in box)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        cv2.putText(img, f"{label} {conf:.2f}", (x1, max(20, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
