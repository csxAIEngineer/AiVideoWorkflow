"""Which MiniMax H3 host the page selected for 出镜. The AI process reads the same file."""

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
_FILE_NAME = "minimax_video"
_HOST_SUFFIXES = ("/compatible-mode/v1", "/api/v1")
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8765"


def choice_file(root=None):
    return Path(root or ROOT) / "data" / _FILE_NAME


def normalize_minimax_video(value):
    return _ALIASES.get(str(value or "").strip().lower(), "")


def read_minimax_video(root=None):
    path = choice_file(root)
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        lines = text.splitlines()
        first = lines[0] if lines else ""
        saved = normalize_minimax_video(first)
        if saved:
            return saved
    named = normalize_minimax_video(os.environ.get("MINIMAX_VIDEO", ""))
    return named or "cloud"


def write_minimax_video(value, root=None):
    name = normalize_minimax_video(value)
    if not name:
        raise ValueError("出镜只能是云端 MiniMax 或本地 MiniMax-H3")
    path = choice_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name + "\n", encoding="utf-8")
    return name


def local_base_url():
    """Host the page shows for local H3. The AI client applies the same default."""
    explicit = str(os.environ.get("MINIMAX_H3_BASE_URL") or "").strip().rstrip("/")
    if not explicit:
        return DEFAULT_LOCAL_BASE_URL
    for suffix in _HOST_SUFFIXES:
        if explicit.endswith(suffix):
            explicit = explicit[: -len(suffix)].rstrip("/")
    return explicit or DEFAULT_LOCAL_BASE_URL
