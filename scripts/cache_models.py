"""Download the models once, so the app works offline afterwards:
    python scripts/cache_models.py
OWLv2 (~620 MB) and the Whisper this computer uses (src/voice.py: ~480 MB on a Mac,
~150 MB on a Windows/Linux laptop without an NVIDIA GPU)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

from huggingface_hub import snapshot_download  # noqa: E402

from src import voice  # noqa: E402

if sys.platform == "darwin":
    whisper = voice.MODEL
else:
    import torch

    whisper = voice.GPU_MODEL if torch.cuda.is_available() else voice.CPU_MODEL
for name in ("google/owlv2-base-patch16-ensemble", whisper):
    print("caching", name)
    snapshot_download(name)
