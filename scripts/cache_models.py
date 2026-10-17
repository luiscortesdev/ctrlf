"""Download the models once (OWLv2 ~620 MB, Whisper ~480 MB on the Mac / ~140 MB
elsewhere), so the app works offline afterwards: python scripts/cache_models.py"""

import sys

from huggingface_hub import snapshot_download

WHISPER = ("mlx-community/whisper-small.en-mlx" if sys.platform == "darwin" else
           "Systran/faster-whisper-base.en")

for name in ("google/owlv2-base-patch16-ensemble", WHISPER):
    print("caching", name)
    snapshot_download(name)
