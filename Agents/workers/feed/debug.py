"""Packing debug sidecars written beside the project."""

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from end.product_names import UNIT_HEAD_LINE

from Agents.workers.feed.common import (
    H3_SECTIONS,
    TASK,
    _style_units,
    packing_groups,
)
from Agents.workers.feed.style import (
    agreed_style,
    style_lock_line,
)
from Agents.workers.feed.extract import restore_h3_scaffold
from Agents.workers.feed.plan import (
    _h3_locked,
    _machine_text,
)
from Agents.workers.feed.skeleton import (
    _SHOT_HEADING_LINE,
    _field_parts,
)


# Packing diagnosis. This log is written on every generate attempt, including
# when AIVIDEO_DEBUG_TRACE is off. It does not change the draft.
_INLINE_CHARS = 4000


_DEBUG_LOCK = threading.Lock()


_NODE_ORIGINAL = re.compile(r"# 本节点原文[ \t]+(\S+)")


_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?\S+")


_API_KEY = re.compile(
    r"(?i)\b((?:api[_-]?key|x-api-key|access[_-]?key|secret[_-]?key)\s*[=:]\s*)\S+"
)


_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{8,}")


_SK_KEY = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")


_SIGNED_URL = re.compile(
    r"https?://[^\s\"'<>]+(?:X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|"
    r"OSSAccessKeyId|Signature=|x-oss-signature|x-oss-credential)=[^\s\"'<>]*",
    re.I,
)


_META_TEXT_KEYS = (
    "finish_reason",
    "incomplete_reason",
    "response_status",
    "response_error",
)


_META_INT_KEYS = ("input_tokens", "output_tokens", "reasoning_tokens")


def resolve_feed_debug_dir(folder):
    """Project ``debug/`` when a folder was passed, otherwise the repo ``data/`` dir."""
    if str(folder or "").strip():
        return Path(folder) / "debug"
    from end.db import ROOT

    return Path(ROOT) / "data"


def _redact_debug(text):
    raw = str(text or "")
    raw = _SIGNED_URL.sub("[redacted-url]", raw)
    raw = _AUTH_HEADER.sub(r"\1[redacted]", raw)
    raw = _API_KEY.sub(r"\1[redacted]", raw)
    raw = _BEARER.sub("Bearer [redacted]", raw)
    raw = _SK_KEY.sub("[redacted]", raw)
    return raw


def _empty_stop_meta():
    meta = {key: "" for key in _META_TEXT_KEYS}
    for key in _META_INT_KEYS:
        meta[key] = 0
    return meta


def _stop_meta(result):
    meta = _empty_stop_meta()
    if not isinstance(result, dict):
        return meta
    for key in _META_TEXT_KEYS:
        if key in result and result.get(key) is not None:
            meta[key] = _redact_debug(result.get(key))
    for key in _META_INT_KEYS:
        if key not in result or result.get(key) in (None, ""):
            continue
        try:
            meta[key] = int(result.get(key))
        except (TypeError, ValueError):
            meta[key] = 0
    return meta


def _unwrap_complete(complete, role, messages):
    """Ask for stop metadata, but hand ``run_generator`` the same text as before."""
    asked = True
    try:
        result = complete(role, messages, return_meta=True)
    except TypeError as exc:
        if "return_meta" not in str(exc):
            raise
        asked = False
        result = complete(role, messages)
    if asked and isinstance(result, dict) and "text" in result:
        value = result.get("text")
        text = "" if value is None else str(value)
        return text, _stop_meta(result)
    return result, _empty_stop_meta()


def _skill_from_messages(messages):
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        found = _NODE_ORIGINAL.search(str(message.get("content") or ""))
        if found:
            return found.group(1)
    return ""


def _heading_lines(text):
    return [match.group(0).strip() for match in UNIT_HEAD_LINE.finditer(str(text or ""))]


def _display_debug_path(path, folder):
    path = Path(path)
    if str(folder or "").strip():
        try:
            return path.resolve().relative_to(Path(folder).resolve()).as_posix()
        except (ValueError, OSError):
            pass
    try:
        from end.db import ROOT

        return path.resolve().relative_to(Path(ROOT).resolve()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


def _sidecar_name(directory, attempt, label):
    base = "feed-attempt-%s-%s" % (attempt, label)
    candidate = directory / ("%s.txt" % base)
    if not candidate.exists():
        return candidate.name
    index = 2
    while True:
        candidate = directory / ("%s-%s.txt" % (base, index))
        if not candidate.exists():
            return candidate.name
        index += 1


def _store_debug_text(directory, folder, attempt, label, text):
    original = "" if text is None else str(text)
    stored = _redact_debug(original)
    name = _sidecar_name(directory, attempt, label)
    path = directory / name
    path.write_text(stored, encoding="utf-8")
    ref = {
        "chars": len(original),
        "text_path": _display_debug_path(path, folder),
    }
    if len(stored) <= _INLINE_CHARS:
        ref["text"] = stored
    return ref


def _materialize_decision(text, state):
    """Same condition ``correct_unit_durations`` uses before it rewrites units."""
    groups = packing_groups(state or {})
    prepared = restore_h3_scaffold(text, state) if _h3_locked(state) else str(text or "")
    heads = list(UNIT_HEAD_LINE.finditer(str(prepared or "")))
    return bool(groups) and len(heads) != len(groups), len(heads), len(groups)


def packing_debug_snapshot(text, state, *, materialize_units=None, prepared_heading_count=None):
    """Planned count, headings, H3 fields, style lock, and shot headings for one draft."""
    raw = str(text or "")
    groups = packing_groups(state or {})
    planned = len(groups)
    headings = _heading_lines(raw)
    if materialize_units is None:
        materialize_units = bool(groups) and len(headings) != planned
    line = style_lock_line(agreed_style(state or {}))
    units = []
    for index, block in enumerate(_style_units(raw), 1):
        heading = ""
        match = UNIT_HEAD_LINE.search(block)
        if match:
            heading = match.group(0).strip()
        fields = {}
        for name in H3_SECTIONS:
            fields[name] = _field_parts(block, name) is not None
        shots = []
        for row in block.splitlines():
            if _SHOT_HEADING_LINE.match(row):
                shots.append(row.strip())
        units.append({
            "index": index,
            "heading": heading,
            "fields": fields,
            "style_lock": bool(line) and line in block,
            "shots": shots,
        })
    snapshot = {
        "chars": len(raw),
        "planned_units": planned,
        "heading_count": len(headings),
        "unit_headings": headings,
        "materialize_units": bool(materialize_units),
        "style_lock_line": line,
        "units": units,
    }
    if prepared_heading_count is not None:
        snapshot["prepared_heading_count"] = int(prepared_heading_count)
    return snapshot


def _selected_repair_nodes(brief, state):
    from AI.skills.registry import suite_files
    from Agents.workers.generator import _notes, _routing_context, select_nodes

    chosen = list(suite_files(TASK, _routing_context(TASK, brief)))
    return list(select_nodes(chosen, brief, _notes((state or {}).get("partials") or [])))


def write_feed_debug(
    state,
    attempt,
    brief,
    calls,
    *,
    extracted=None,
    planned_text=None,
    error="",
    fold_source="",
):
    """Append one packing generate attempt to ``debug/feed-debug.jsonl``."""
    state = state or {}
    folder = str(state.get("project_folder") or "")
    directory = resolve_feed_debug_dir(folder)
    directory.mkdir(parents=True, exist_ok=True)
    repair = int(attempt or 0) > 1
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_id": str(state.get("project_id") or ""),
        "attempt": int(attempt or 0),
        "repair": repair,
    }
    if error:
        record["error"] = _redact_debug(error)
    brief_ref = _store_debug_text(directory, folder, attempt, "brief", brief)
    record["brief_chars"] = brief_ref["chars"]
    record["brief_path"] = brief_ref["text_path"]
    if "text" in brief_ref:
        record["brief"] = brief_ref["text"]
    logged_calls = []
    for call in calls or []:
        label = str(call.get("label") or call.get("role") or "call")
        body = _store_debug_text(directory, folder, attempt, label, call.get("text") or "")
        item = {
            "role": str(call.get("role") or ""),
            "skill": str(call.get("skill") or ""),
            "chars": body["chars"],
            "unit_headings": _heading_lines(call.get("text") or ""),
            "text_path": body["text_path"],
        }
        if "text" in body:
            item["text"] = body["text"]
        item.update(call.get("meta") or _empty_stop_meta())
        logged_calls.append(item)
    record["calls"] = logged_calls
    if repair:
        try:
            record["selected_nodes"] = _selected_repair_nodes(brief, state)
        except Exception as exc:
            record["selected_nodes"] = []
            record["selected_nodes_error"] = "%s: %s" % (type(exc).__name__, exc)
        record["machine_errors"] = _redact_debug(_machine_text(state.get("machine_report")))
    if extracted is not None:
        extract_snap = packing_debug_snapshot(extracted, state)
        extract_body = _store_debug_text(directory, folder, attempt, "extract", extracted)
        extract_snap["text_path"] = extract_body["text_path"]
        if "text" in extract_body:
            extract_snap["text"] = extract_body["text"]
        record["extract_feed"] = extract_snap
    if planned_text is not None:
        ran, prepared_headings, _planned = _materialize_decision(extracted if extracted is not None else "", state)
        plan_snap = packing_debug_snapshot(
            planned_text,
            state,
            materialize_units=ran,
            prepared_heading_count=prepared_headings,
        )
        plan_body = _store_debug_text(directory, folder, attempt, "plan", planned_text)
        plan_snap["text_path"] = plan_body["text_path"]
        if "text" in plan_body:
            plan_snap["text"] = plan_body["text"]
        if str(fold_source or "").strip():
            plan_snap["fold_source"] = str(fold_source).strip()
        record["apply_packing_plan"] = plan_snap
    path = directory / "feed-debug.jsonl"
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _DEBUG_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    return path


def _safe_feed_debug(state, attempt, brief, calls, **kwargs):
    try:
        write_feed_debug(state, attempt, brief, calls, **kwargs)
    except Exception:
        return
