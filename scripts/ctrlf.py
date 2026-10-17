r"""
Ctrl+F for Your Room: start the app (see src/app.py). In a terminal, in the repo:
    source venv/bin/activate        (Mac/Linux; Windows: .venv\Scripts\activate)
    python scripts/ctrlf.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo

from src.app import main  # noqa: E402

if __name__ == "__main__":
    main()
