"""Cloud or local fallback for 出镜 when a route does not name a platform.

The value is config/video_catalog.json default_video. The page only displays it.
"""

import json
import os
from pathlib import Path

from end.db import ROOT

_ALIASES = {
    "cloud": "cloud",
    "qwen": "cloud",
    "dashscope": "cloud",
    "云端": "cloud",
    "local": "local",
    "localhost": "local",
    "本地": "local",
}
_HOST_SUFFIXES = ("/compatible-mode/v1", "/api/v1")
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8765"


def catalog_file(root=None):
    return Path(root or ROOT) / "config" / "video_catalog.json"


def normalize_minimax_video(value):
    return _ALIASES.get(str(value or "").strip().lower(), "")


def read_minimax_video(root=None):
    """cloud or local. Missing file or field is cloud."""
    path = catalog_file(root)
    if not path.is_file():
        return "cloud"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "cloud"
    if not isinstance(data, dict):
        return "cloud"
    return normalize_minimax_video(data.get("default_video")) or "cloud"


def local_base_url():
    """Host the page shows for local H3. The AI client applies the same default."""
    if "MINIMAX_H3_BASE_URL" not in os.environ:
        from AI.qwen_api.client import load_dotenv

        load_dotenv()
    explicit = str(os.environ.get("MINIMAX_H3_BASE_URL") or "").strip().rstrip("/")
    if not explicit:
        return DEFAULT_LOCAL_BASE_URL
    for suffix in _HOST_SUFFIXES:
        if explicit.endswith(suffix):
            explicit = explicit[: -len(suffix)].rstrip("/")
    return explicit or DEFAULT_LOCAL_BASE_URL
