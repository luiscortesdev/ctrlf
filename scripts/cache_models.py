from ultralytics import YOLO
from faster_whisper import WhisperModel

print("1/2: Downloading YOLO-World weights...")
YOLO("yolov8s-worldv2.pt")

print("2/2: Downloading Faster-Whisper base.en weights...")
WhisperModel("base.en", device="cpu", compute_type="int8")

print("\nDone! All core models are cached locally.")