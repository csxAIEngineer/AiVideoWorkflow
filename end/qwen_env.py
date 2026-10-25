"""Which Qwen host the page selected. The AI process reads the same file."""

import os
from pathlib import Path

from end.db import ROOT

_ALIASES = {
    "test": "test",
    "testing": "test",
    "测试": "test",
    "prod": "prod",
    "production": "prod",
    "生产": "prod",
}
_FILE_NAME = "qwen_env"


def env_file(root=None):
    return Path(root or ROOT) / "data" / _FILE_NAME


def normalize_qwen_env(value):
    return _ALIASES.get(str(value or "").strip().lower(), "")


def read_qwen_env(root=None):
    path = env_file(root)
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        first = text.splitlines()[0] if text.splitlines() else ""
        saved = normalize_qwen_env(first)
        if saved:
            return saved
    named = normalize_qwen_env(os.environ.get("QWEN_ENV", ""))
    return named or "test"


def write_qwen_env(value, root=None):
    name = normalize_qwen_env(value)
    if not name:
        raise ValueError("千问环境只能是生产或测试")
    path = env_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name + "\n", encoding="utf-8")
    return name
