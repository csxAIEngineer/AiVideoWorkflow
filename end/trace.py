"""Save one write at a time, then restore that output and continue forward.

A process is one vision read or one stage write, including each repair.
The full prompt is appended by the AI process; this module stores the brief
and the body that landed on disk.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

_NAME = {
    "vision": "读图",
    "recon": "对标",
    "script": "剧本",
    "asset": "资产",
    "storyboard": "分镜",
    "feed": "投喂",
}
_WRITE_LABEL = {
    "recon": "写对标",
    "script": "写剧本",
    "asset": "写资产",
    "storyboard": "写分镜",
    "feed": "写投喂稿",
}
_FILES = {
    "recon": ("对标情报卡.md",),
    "script": ("剧本.md", "剧本过程.md"),
    "asset": ("assets.md",),
    "storyboard": ("分镜.md", "previs.md"),
    "feed": ("feed.md",),
}
_AFTER = {
    "vision": ("recon", "script", "asset", "storyboard", "feed"),
    "recon": ("script", "asset", "storyboard", "feed"),
    "script": ("asset", "storyboard", "feed"),
    "asset": ("storyboard", "feed"),
    "storyboard": ("feed",),
    "feed": (),
}
_SPEC_KEYS = {
    "script": ("script", "internal"),
    "asset": ("image_prompts", "assets"),
    "storyboard": ("storyboard", "previs"),
    "feed": ("feed", "shots"),
}
_ACTIVE = {}


def trace_enabled():
    """True when writes should be stored under debug/trace."""
    try:
        from AI.qwen_api.client import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    import os

    flag = (os.environ.get("AIVIDEO_DEBUG_TRACE") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _trace_dir(folder):
    path = Path(folder) / "debug" / "trace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_path(folder, number):
    return _trace_dir(folder) / ("%s.json" % number)


def _read_record(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_record(folder, number, record):
    path = _record_path(folder, number)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _next_id(folder):
    highest = 0
    directory = Path(folder) / "debug" / "trace"
    if directory.is_dir():
        for path in directory.glob("*.json"):
            if path.stem.isdigit():
                highest = max(highest, int(path.stem))
    return "%04d" % (highest + 1)


def _attempt(folder, stage):
    count = 0
    directory = Path(folder) / "debug" / "trace"
    if not directory.is_dir():
        return 1
    for path in directory.glob("*.json"):
        record = _read_record(path)
        if record and record.get("stage") == stage:
            count += 1
    return count + 1


def label_for(stage, attempt, repair):
    name = _NAME.get(stage, stage)
    if stage == "vision":
        return name if attempt == 1 else "%s %s" % (name, attempt)
    if repair or attempt > 1:
        return "%s返修 %s" % (name, attempt)
    return name


def _active_key(folder):
    return str(Path(folder).resolve())


def begin_trace(folder, stage, repair, brief, label=None, step=""):
    """Reserve the next record and return the prompt sidecar path."""
    if not trace_enabled():
        _ACTIVE.pop(_active_key(folder), None)
        return ""
    folder = Path(folder)
    number = _next_id(folder)
    attempt = _attempt(folder, stage)
    label = label or label_for(stage, attempt, bool(repair))
    prompts = _trace_dir(folder) / ("%s.prompts.jsonl" % number)
    record = {
        "id": number,
        "at": _now(),
        "stage": stage,
        "attempt": attempt,
        "step": str(step or ""),
        "label": label,
        "pending": True,
        "input": {"brief": str(brief or ""), "prompts": []},
        "output": {},
        "error": "",
    }
    _write_record(folder, number, record)
    _ACTIVE[_active_key(folder)] = {"id": number, "folder": str(folder), "prompts": str(prompts)}
    return str(prompts)


def stage_output(stage, result):
    """The body stored for this stage, after keep-missing has patched it."""
    result = result or {}
    if stage == "recon":
        return {"intel": result.get("intel") or result.get("script") or ""}
    if stage == "script":
        return {
            "title": result.get("title") or "",
            "script": result.get("script") or "",
            "internal": result.get("internal") or "",
        }
    if stage == "asset":
        return {
            "image_prompts": result.get("image_prompts") or "",
            "assets": result.get("assets") if isinstance(result.get("assets"), list) else [],
        }
    if stage == "storyboard":
        return {
            "storyboard": result.get("storyboard") or "",
            "previs": result.get("previs") or "",
        }
    if stage == "feed":
        return {
            "feed": result.get("feed") or "",
            "shots": result.get("shots") if isinstance(result.get("shots"), list) else [],
        }
    return {}


def _load_prompts(path):
    prompts = []
    file_path = Path(path)
    if not file_path.is_file():
        return prompts
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return prompts
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            prompts.append(line)
            continue
        if isinstance(item, dict):
            prompts.append(str(item.get("prompt") or ""))
        else:
            prompts.append(str(item))
    try:
        file_path.unlink()
    except OSError:
        pass
    return prompts


def finish_trace(folder, output, error=""):
    """Fill the open record with prompts and the saved body."""
    active = _ACTIVE.pop(_active_key(folder), None)
    if not active:
        return None
    path = _record_path(active["folder"], active["id"])
    record = _read_record(path) or {}
    stored_input = record.get("input") if isinstance(record.get("input"), dict) else {}
    record["input"] = {
        "brief": str(stored_input.get("brief") or ""),
        "prompts": _load_prompts(active["prompts"]),
    }
    record["output"] = output if isinstance(output, dict) else {}
    record["error"] = str(error or "")
    record["pending"] = False
    record["at"] = record.get("at") or _now()
    _write_record(active["folder"], active["id"], record)
    return record


def list_traces(folder):
    """Metadata for the project page. Full prompts stay in the file."""
    if not trace_enabled():
        return []
    directory = Path(folder) / "debug" / "trace"
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        if not path.stem.isdigit():
            continue
        record = _read_record(path)
        if not record:
            continue
        rows.append(
            {
                "id": str(record.get("id") or path.stem),
                "label": str(record.get("label") or ""),
                "stage": str(record.get("stage") or ""),
                "attempt": int(record.get("attempt") or 0),
                "step": str(record.get("step") or ""),
                "at": str(record.get("at") or ""),
                "error": str(record.get("error") or ""),
                "pending": bool(record.get("pending")),
            }
        )
    return rows


def _put_text(folder, name, text):
    path = Path(folder) / "_内部_" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = str(text or "").strip()
    if not body:
        if path.is_file():
            path.unlink()
        return
    path.write_text(body + "\n", encoding="utf-8")


def _drop_file(folder, name):
    path = Path(folder) / "_内部_" / name
    if path.is_file():
        path.unlink()
    if name == "feed.md":
        published = Path(folder) / "feed.md"
        if published.is_file():
            published.unlink()


def _load_spec(folder):
    path = Path(folder) / "spec.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_spec(folder, spec):
    path = Path(folder) / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")


def _restore_vision(folder, output):
    path = Path(folder) / "intake.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, json.JSONDecodeError):
        items = []
    if not isinstance(items, list):
        items = []
    readings = {}
    for row in output.get("readings") or []:
        if isinstance(row, dict) and row.get("id"):
            readings[str(row.get("id"))] = str(row.get("reading") or "")
    for item in items:
        if isinstance(item, dict) and str(item.get("id") or "") in readings:
            item["reading"] = readings[str(item.get("id"))]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _restore_output(folder, stage, output):
    output = output or {}
    spec = _load_spec(folder)
    if stage == "vision":
        _restore_vision(folder, output)
        return
    if stage == "recon":
        _put_text(folder, "对标情报卡.md", output.get("intel") or "")
    elif stage == "script":
        _put_text(folder, "剧本.md", output.get("script") or "")
        _put_text(folder, "剧本过程.md", output.get("internal") or "")
        spec["script"] = output.get("script") or ""
        spec["internal"] = output.get("internal") or ""
        if output.get("title"):
            spec["title"] = output.get("title")
    elif stage == "asset":
        _put_text(folder, "assets.md", output.get("image_prompts") or "")
        spec["image_prompts"] = output.get("image_prompts") or ""
        spec["assets"] = output.get("assets") if isinstance(output.get("assets"), list) else []
    elif stage == "storyboard":
        _put_text(folder, "分镜.md", output.get("storyboard") or "")
        _put_text(folder, "previs.md", output.get("previs") or "")
        spec["storyboard"] = output.get("storyboard") or ""
        spec["previs"] = output.get("previs") or ""
        from end.board_shots import apply_storyboard_shots

        spec = apply_storyboard_shots(spec)
    elif stage == "feed":
        _put_text(folder, "feed.md", output.get("feed") or "")
        spec["feed"] = output.get("feed") or ""
        incoming = output.get("shots") if isinstance(output.get("shots"), list) else []
        from end.board_shots import apply_storyboard_shots, shots_are_placeholder

        if incoming and not shots_are_placeholder(incoming):
            spec["shots"] = incoming
        spec = apply_storyboard_shots(spec)
        from end.board_shots import apply_feed_binding

        spec = apply_feed_binding(spec)
        _put_text(folder, "feed.md", spec.get("feed") or "")
    _save_spec(folder, spec)


def _clear_stage(folder, stage):
    for name in _FILES.get(stage, ()):
        _drop_file(folder, name)
    spec = _load_spec(folder)
    for key in _SPEC_KEYS.get(stage, ()):
        spec.pop(key, None)
    _save_spec(folder, spec)


def _restore_script_step(folder, step, output):
    """Put one screenwriter step back and drop every later chain file."""
    from end.stage_runner import SCRIPT_STEPS

    rows = list(SCRIPT_STEPS)
    index = next((i for i, row in enumerate(rows) if row[0] == str(step)), None)
    if index is None:
        raise ValueError("没有这个过程")
    body = str(output.get("text") or output.get("script") or output.get("internal") or "").strip()
    chain = Path(folder) / "_内部_" / "剧本链"
    chain.mkdir(parents=True, exist_ok=True)
    (chain / rows[index][2]).write_text((body + "\n") if body else "", encoding="utf-8")
    for later in rows[index + 1 :]:
        path = chain / later[2]
        if path.is_file():
            path.unlink()
    spec = _load_spec(folder)
    if str(step) == "5":
        script = str(output.get("script") or output.get("text") or "").strip()
        _put_text(folder, "剧本.md", script)
        _drop_file(folder, "剧本过程.md")
        spec["script"] = script
        spec.pop("internal", None)
        if output.get("title"):
            spec["title"] = output.get("title")
    elif str(step) == "6":
        internal = str(output.get("text") or output.get("internal") or "").strip()
        _put_text(folder, "剧本过程.md", internal)
        spec["internal"] = internal
    else:
        _drop_file(folder, "剧本.md")
        _drop_file(folder, "剧本过程.md")
        spec.pop("script", None)
        spec.pop("internal", None)
    _save_spec(folder, spec)
    if index + 1 < len(rows):
        return rows[index + 1]
    return None


def _script_replay_plan(number, record, stages, nxt_row):
    before = stages[: stages.index("script")] if "script" in stages else []
    if nxt_row:
        return {
            "id": number,
            "stage": "script",
            "label": str(record.get("label") or ""),
            "accepted": before,
            "current": "script",
            "action": "write",
            "next": "script",
            "write_label": "写%s" % nxt_row[1],
            "script_from": nxt_row[0],
        }
    accepted = stages[: stages.index("script") + 1] if "script" in stages else before + ["script"]
    return {
        "id": number,
        "stage": "script",
        "label": str(record.get("label") or ""),
        "accepted": accepted,
        "current": "script",
        "action": "wait",
        "next": "",
        "write_label": "",
    }


def restore_trace(folder, trace_id, locks, only):
    """Write the saved output back and drop every later stage body."""
    from end.stage_runner import next_pending, stages_for

    number = str(trace_id or "").strip()
    if not number.isdigit():
        raise ValueError("没有这个过程")
    number = "%04d" % int(number)
    record = _read_record(_record_path(folder, number))
    if not record:
        raise ValueError("没有这个过程")
    if record.get("pending"):
        raise ValueError("这一次还没写完")
    if record.get("error"):
        raise ValueError("这一次没有写完，不能重放")
    stage = str(record.get("stage") or "")
    if stage in ("route", "qc", "board"):
        raise ValueError("这一次不能重放")
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    step = str(record.get("step") or "")
    if stage == "script" and step:
        nxt_row = _restore_script_step(folder, step, output)
        for later in _AFTER.get("script", ()):
            _clear_stage(folder, later)
        return _script_replay_plan(number, record, stages_for(locks, only), nxt_row)
    _restore_output(folder, stage, output)
    for later in _AFTER.get(stage, ()):
        _clear_stage(folder, later)
    stages = stages_for(locks, only)
    if stage == "vision":
        accepted = []
        nxt = next_pending(stages, [])
    elif stage in stages:
        index = stages.index(stage)
        accepted = stages[: index + 1]
        nxt = stages[index + 1] if index + 1 < len(stages) else ""
    else:
        accepted = []
        nxt = ""
    return {
        "id": number,
        "stage": stage,
        "label": str(record.get("label") or ""),
        "accepted": accepted,
        "current": nxt or (stage if stage != "vision" else (nxt or "script")),
        "action": "write" if nxt else "wait",
        "next": nxt,
        "write_label": _WRITE_LABEL.get(nxt, "下一段"),
    }
