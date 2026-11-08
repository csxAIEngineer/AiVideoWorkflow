"""Copy files before the active projection is retired.

The copy lives under ``history/<stamp>-<reason>/`` with ``manifest.json``.
Nothing here removes the archive. Callers may then drop the active path so a
new run cannot treat the old bytes as the current result.
"""

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def _safe(reason):
    text = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(reason or "retire"))
    return text.strip("-")[:40] or "retire"


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def archive_paths(folder, reason, relative_paths):
    """Copy existing files. Returns the archive directory, or None when nothing was copied."""
    folder = Path(folder)
    copied = []
    seen = set()
    for relative in relative_paths or []:
        rel = str(relative or "").strip().lstrip("/")
        if not rel or rel in seen or rel.startswith("history/"):
            continue
        seen.add(rel)
        source = folder / rel
        if not source.is_file():
            continue
        copied.append((rel, source))
    if not copied:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = folder / "history" / ("%s-%s" % (stamp, _safe(reason)))
    dest.mkdir(parents=True, exist_ok=True)
    manifest = []
    for rel, source in copied:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest.append({
            "path": rel,
            "bytes": source.stat().st_size,
            "sha256": _sha256(source),
        })
    (dest / "manifest.json").write_text(
        json.dumps({"reason": str(reason or ""), "files": manifest}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return dest


def archive_file(folder, path, reason):
    """Copy one file if it sits inside ``folder``."""
    folder = Path(folder)
    path = Path(path)
    if not path.is_file():
        return None
    try:
        relative = str(path.resolve().relative_to(folder.resolve()))
    except ValueError:
        return None
    return archive_paths(folder, reason, [relative])
