"""Local convenience wrapper for scaffold generation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scaffold.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
