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


class ArchiveError(RuntimeError):
    """A snapshot did not land. Callers must not delete or rewrite the originals."""


def _fingerprint(path):
    """Hash and size. A size change while reading is a failed snapshot."""
    path = Path(path)
    try:
        size = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        if path.stat().st_size != size:
            raise ArchiveError("源在读取期间变了，已停止删除")
    except ArchiveError:
        raise
    except OSError as error:
        raise ArchiveError("归档读文件失败，已停止删除：%s" % error) from error
    return {"bytes": size, "sha256": digest.hexdigest()}


def _same(left, right):
    return left["bytes"] == right["bytes"] and left["sha256"] == right["sha256"]


def snapshot_project(folder, reason, relative_paths):
    """Copy every existing path, then check each copy against the pre-copy source.

    The source hash and size are recorded before ``copy2``. After every file
    has been copied, each copy's hash and size must match that record, and the
    source must still match it. Any miss raises. This function does not delete
    or rewrite the sources.
    """
    folder = Path(folder)
    existing = []
    seen = set()
    for relative in relative_paths or []:
        rel = str(relative or "").strip().lstrip("/")
        if not rel or rel in seen or rel.startswith("history/"):
            continue
        seen.add(rel)
        if (folder / rel).is_file():
            existing.append(rel)
    if not existing:
        return None
    before = {rel: _fingerprint(folder / rel) for rel in existing}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = folder / "history" / ("%s-%s" % (stamp, _safe(reason)))
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for rel in existing:
            source = folder / rel
            if not _same(_fingerprint(source), before[rel]):
                raise ArchiveError("源在复制前变了，已停止删除：%s" % rel)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        verified = []
        for rel in existing:
            if not _same(_fingerprint(folder / rel), before[rel]):
                raise ArchiveError("源在复制期间变了，已停止删除：%s" % rel)
            target = dest / rel
            if not target.is_file():
                raise ArchiveError("归档缺文件，已停止删除：%s" % rel)
            if not _same(_fingerprint(target), before[rel]):
                raise ArchiveError("副本与复制前的源不一致，已停止删除：%s" % rel)
            verified.append({"path": rel, "bytes": before[rel]["bytes"], "sha256": before[rel]["sha256"]})
        (dest / "manifest.json").write_text(
            json.dumps({"reason": str(reason or ""), "files": verified}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except ArchiveError:
        raise
    except OSError as error:
        raise ArchiveError("归档失败，已停止删除：%s" % error) from error
    return dest


def archive_paths(folder, reason, relative_paths):
    """Copy existing files and verify the copies. Returns None when nothing existed."""
    return snapshot_project(folder, reason, relative_paths)


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
