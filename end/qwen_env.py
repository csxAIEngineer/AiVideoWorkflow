"""Which Qwen host config/qwen.json names. The page only displays it."""

import json
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
_FILE = "qwen.json"


def config_file(root=None):
    return Path(root or ROOT) / "config" / _FILE


def normalize_qwen_env(value):
    return _ALIASES.get(str(value or "").strip().lower(), "")


def read_qwen_env(root=None):
    """test or prod from config/qwen.json. Missing file or field is test."""
    path = config_file(root)
    if not path.is_file():
        return "test"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "test"
    if not isinstance(data, dict):
        return "test"
    return normalize_qwen_env(data.get("env")) or "test"
