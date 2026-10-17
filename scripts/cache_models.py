"""Download the models once (OWLv2 ~620 MB, Whisper small.en ~480 MB), so the app works
offline afterwards: python scripts/cache_models.py"""

from huggingface_hub import snapshot_download

for name in ("google/owlv2-base-patch16-ensemble", "mlx-community/whisper-small.en-mlx"):
    print("caching", name)
    snapshot_download(name)
