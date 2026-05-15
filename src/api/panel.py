"""Admin panel HTML loader."""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def get_panel_html() -> str:
    return Path(__file__).with_name("panel.html").read_text(encoding="utf-8")
