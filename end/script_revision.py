"""Read-only revision draft and an explicit save that queues one script review.

Selecting a draft does not adopt it. The official script changes only when
save is called with new text, after a snapshot, and inside the project lock.
"""

import base64
import json
from pathlib import Path

from end.history_keep import ArchiveError, snapshot_project
from end.render_loop import project_lock
from end.script_repair import load_candidate, official_script
from end.script_review import content_hash, load_reviews, save_reviews

_TOKEN_PREFIX = "sr1."
_IDEMPOTENCY_NAME = "剧本修订幂等.json"
_SCRIPT_REL = "_内部_/剧本.md"
_CANDIDATE_REL = "_内部_/剧本候选.json"


class RevisionError(Exception):
    def __init__(self, code, message, reason, retryable, saved=False, retry_same_key=False, official_saved=False):
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.reason = str(reason)
        self.retryable = bool(retryable)
        self.saved = bool(saved)
        self.retry_same_key = bool(retry_same_key)
        self.official_saved = bool(official_saved)


def _fail(code, message, reason, retryable, saved=False, retry_same_key=False, official_saved=False):
    raise RevisionError(code, message, reason, retryable, saved, retry_same_key, official_saved)


def _candidate_stamp(row):
    row = row or {}
    validation = row.get("validation") if isinstance(row.get("validation"), dict) else None
    return {
        "status": row.get("status") or "",
        "reason": row.get("reason") or "",
        "candidate": row.get("candidate") or "",
        "rejected_text": row.get("rejected_text") or "",
        "attempts": int(row.get("attempts") or 0),
        "source_hash": row.get("source_hash") or "",
        "requirements_hash": row.get("requirements_hash") or "",
        "issues_hash": row.get("issues_hash") or "",
        "validation": validation,
    }


def revision_token(project_id, official, row):
    body = {
        "v": 1,
        "id": str(project_id or ""),
        "script": content_hash(official),
        "candidate": content_hash(json.dumps(_candidate_stamp(row), ensure_ascii=False, sort_keys=True)),
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _TOKEN_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_token(token):
    text = str(token or "")
    if not text.startswith(_TOKEN_PREFIX):
        return None
    padded = text[len(_TOKEN_PREFIX):]
    padded += "=" * (-len(padded) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict) or data.get("v") != 1:
        return None
    return data


def _at_script_gate(project):
    progress = project.get("progress") or {}
    question = progress.get("question") or {}
    return (
        project.get("status") == "gate"
        and progress.get("phase") == "gate_script"
        and question.get("id") in ("script", "script_candidate")
    )


def _busy_reason(project):
    status = project.get("status") or ""
    action = str(((project.get("progress") or {}).get("pipeline") or {}).get("action") or "")
    if status == "rendering":
        return "rendering"
    if action == "script_review":
        return "reviewing"
    if action == "opinion_revise":
        return "opinion"
    if status in ("writing", "building") or action in ("script_repair", "rewrite", "write"):
        return "writing"
    return ""


def _draft(row):
    row = row or {}
    candidate = str(row.get("candidate") or "").strip()
    rejected = str(row.get("rejected_text") or "").strip()
    if candidate:
        return candidate, "candidate"
    if rejected:
        return rejected, "rejected_text"
    return "", "none"


def revision_get(project):
    """Read the stop. Does not write and does not call a model."""
    from end.db import project_dir

    folder = project_dir(project["id"])
    official = official_script(folder)
    row = load_candidate(folder)
    draft, source = _draft(row)
    validation = row.get("validation") if isinstance(row.get("validation"), dict) else None
    busy = _busy_reason(project)
    review_state, review_retryable = _review_status(project, folder, official, row)
    if review_state == "pending":
        busy = ""
        phase_reason = "review_pending"
    elif review_state == "queued":
        busy = "reviewing"
        phase_reason = ""
    else:
        phase_reason = "" if _at_script_gate(project) else "invalid_phase"
    editable = _at_script_gate(project) and not busy and review_state != "pending"
    return {
        "id": project["id"],
        "official_text": official,
        "draft_text": draft,
        "draft_source": source,
        "status": row.get("status") or "",
        "reason": row.get("reason") or "",
        "validation": validation,
        "needs_human": bool(row.get("needs_human")),
        "attempts": int(row.get("attempts") or 0),
        "can_edit": editable,
        "can_save": editable,
        "busy": bool(busy),
        "busy_reason": busy,
        "phase_reason": phase_reason,
        "review_state": review_state,
        "review_retryable": review_retryable,
        "base_token": revision_token(project["id"], official, row),
        **_opinion_fields(project, official, row),
    }


def _opinion_fields(project, official, row):
    from end.script_opinion import opinion_view

    return opinion_view(project, official, row)


def _bound_review_record(entries, project_id, official, row):
    """The pending or queued save for this exact manuscript. Older records do not qualify."""
    current = revision_token(project_id, official, row)
    script_hash = content_hash(official)
    fallback = None
    for record in entries.values():
        if not isinstance(record, dict):
            continue
        result = record.get("result") or {}
        if not result.get("saved") or not result.get("review_queued"):
            continue
        if record.get("saved_script_hash") != script_hash or result.get("base_token") != current:
            continue
        if not record.get("queued"):
            return record
        fallback = record
    return fallback


def manual_resume_decision(project):
    """Manual saves resume only when the disk record still matches this manuscript."""
    from end.db import project_dir

    folder = project_dir(project["id"])
    entries = _load_idempotency(folder)
    manual = False
    for record in entries.values():
        if not isinstance(record, dict):
            continue
        result = record.get("result") or {}
        if result.get("saved") and result.get("review_queued"):
            manual = True
            break
    if not manual:
        return "queue"
    official = official_script(folder)
    row = load_candidate(folder)
    if _bound_review_record(entries, project["id"], official, row) is None:
        return "hold"
    return "queue"


def _review_status(project, folder, official, row):
    record = _bound_review_record(_load_idempotency(folder), project["id"], official, row)
    if record is None or _review_settled(project):
        return "none", False
    if record.get("queued"):
        return "queued", False
    return "pending", True


def _idempotency_path(folder):
    return Path(folder) / "_内部_" / _IDEMPOTENCY_NAME


def _load_idempotency(folder):
    path = _idempotency_path(folder)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def _store_idempotency(folder, entries):
    path = _idempotency_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"entries": entries}, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _preimage(folder):
    folder = Path(folder)
    blobs = {}
    for relative in (_SCRIPT_REL, "spec.json", "reviews.json"):
        path = folder / relative
        blobs[str(path)] = path.read_bytes() if path.is_file() else None
    return blobs


def _restore(blobs):
    for name, data in blobs.items():
        path = Path(name)
        if data is None:
            if path.is_file():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _clear_confirmation(folder):
    state = load_reviews(folder)
    state.pop("confirmed_script_hash", None)
    state.pop("confirmed_context_hash", None)
    row = dict(state.get("script") or {})
    for key in ("accepted_suspects", "accepted_by", "accepted_script_hash", "accepted_context_hash"):
        row.pop(key, None)
    if row:
        state["script"] = row
    elif "script" in state:
        state.pop("script", None)
    save_reviews(folder, state)


def _write_script(folder, project, text):
    folder = Path(folder)
    internal = folder / "_内部_"
    internal.mkdir(parents=True, exist_ok=True)
    (internal / "剧本.md").write_text(text + "\n", encoding="utf-8")
    spec_path = folder / "spec.json"
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        spec = {}
    if not isinstance(spec, dict):
        spec = {}
    spec["script"] = text
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project["script"] = text


def _check_token(project_id, token, official, row):
    decoded = _decode_token(token)
    if decoded is None:
        _fail(409, "这个版本标记无法识别", "token_mismatch", False)
    if str(decoded.get("id") or "") != str(project_id):
        _fail(409, "这个版本标记对不上这个项目", "token_mismatch", False)
    script_hash = content_hash(official)
    candidate_hash = content_hash(json.dumps(_candidate_stamp(row), ensure_ascii=False, sort_keys=True))
    if decoded.get("script") != script_hash or decoded.get("candidate") != candidate_hash:
        _fail(409, "稿件版本已经变了，编辑区不会被覆盖", "version_conflict", False)


def revision_save(project, text, base_token, idempotency_key, source="", opinion_id=None, opinion_revision=None):
    """Save a human manuscript and queue one full script review. Does not adopt or generate."""
    from end.db import project_dir, write_project
    from end.server import now, submit_agent_task
    from end.task_queue.queue import Task

    project_id = project["id"]
    folder = project_dir(project_id)
    key = str(idempotency_key or "").strip()
    if not key:
        _fail(400, "没有保存编号", "invalid_request", False)
    body = "" if text is None else str(text)
    if len(body.encode("utf-8")) > 1_000_000:
        _fail(413, "内容太大", "too_large", False)
    stripped = body.strip()
    if not stripped:
        _fail(400, "正文是空的", "empty_text", False)
    text_hash = content_hash(body)
    with project_lock(project_id):
        official = official_script(folder)
        row = load_candidate(folder)
        candidate_bytes = (folder / _CANDIDATE_REL).read_bytes() if (folder / _CANDIDATE_REL).is_file() else None
        entries = _load_idempotency(folder)
        previous = entries.get(key)
        if isinstance(previous, dict):
            if previous.get("text_hash") != text_hash or previous.get("base_token") != str(base_token or ""):
                _fail(409, "同一次保存的正文变了，请换一个新的保存编号", "idempotency_mismatch", False)
            return _finish_queue(project, folder, entries, key, previous, submit_agent_task, Task)
        _check_token(project_id, base_token, official, row)
        busy = _busy_reason(project)
        if busy:
            _fail(409, "正在处理，还不能再保存", "busy", True, False, True)
        if not _at_script_gate(project):
            _fail(409, "现在不是剧本修订停点", "invalid_phase", False)
        from end.script_opinion import attach_confirmed_intent, confirmation_intent

        intent = confirmation_intent(
            folder, project, base_token, official, row, opinion_id, opinion_revision,
        )
        if stripped == official.strip():
            result = {
                "ok": True,
                "saved": False,
                "review_queued": False,
                "unchanged": True,
                "base_token": revision_token(project_id, official, row),
                "message": "稿件没有变化",
            }
            entries[key] = {
                "text_hash": text_hash,
                "base_token": str(base_token or ""),
                "source": str(source or ""),
                "queued": True,
                "result": result,
            }
            _store_idempotency(folder, entries)
            return result
        try:
            snapshot_project(folder, "human-script", [_SCRIPT_REL, _CANDIDATE_REL, "spec.json", "reviews.json"])
        except ArchiveError as error:
            _fail(409, "保存没有完成，正式稿还是原样：%s" % error, "archive_failed", True, False, True)
        _check_token(project_id, base_token, official_script(folder), load_candidate(folder))
        blobs = _preimage(folder)
        try:
            _write_script(folder, project, stripped)
            _clear_confirmation(folder)
            if intent is not None:
                attach_confirmed_intent(folder, official_script(folder), intent)
            fresh_official = official_script(folder)
            fresh_row = load_candidate(folder)
            result = {
                "ok": True,
                "saved": True,
                "review_queued": True,
                "unchanged": False,
                "base_token": revision_token(project_id, fresh_official, fresh_row),
                "message": "已保存，正在重新审稿",
            }
            if candidate_bytes is None:
                if (folder / _CANDIDATE_REL).is_file():
                    raise RuntimeError("保存改动了候选文件")
            elif (folder / _CANDIDATE_REL).read_bytes() != candidate_bytes:
                raise RuntimeError("保存改动了候选文件")
            entries[key] = {
                "text_hash": text_hash,
                "base_token": str(base_token or ""),
                "saved_script_hash": content_hash(fresh_official),
                "source": str(source or ""),
                "queued": False,
                "result": result,
            }
            _store_idempotency(folder, entries)
            pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
            pipeline["action"] = "script_review"
            pipeline["current"] = "script"
            pipeline["passed"] = False
            pipeline["repair"] = ""
            progress = dict(project.get("progress") or {})
            progress.update({
                "percent": int(progress.get("percent") or 48),
                "phase": "writing",
                "message": "正在重新审稿",
                "lock_machine": True,
                "pipeline": pipeline,
                "question": None,
            })
            project["status"] = "writing"
            project["stage"] = "剧本审稿"
            project["progress"] = progress
            project["script"] = stripped
            project["updated_at"] = now()
            write_project(project)
        except RevisionError:
            entries.pop(key, None)
            _restore(blobs)
            raise
        except Exception as error:
            kept = _load_idempotency(folder)
            if key in kept:
                kept.pop(key, None)
                try:
                    _store_idempotency(folder, kept)
                except OSError:
                    pass
            _restore(blobs)
            _fail(409, "保存没有完成，正式稿还是原样：%s" % error, "archive_failed", True, False, True)
        return _finish_queue(project, folder, entries, key, entries[key], submit_agent_task, Task)


def _review_settled(project):
    action = str(((project.get("progress") or {}).get("pipeline") or {}).get("action") or "")
    return project.get("status") != "writing" or action != "script_review"


def _finish_queue(project, folder, entries, key, record, submit_agent_task, Task, hint="key"):
    """Return success only after a review task is actually queued or already finished."""
    result = dict(record.get("result") or {})
    if record.get("queued") or not result.get("review_queued"):
        return result
    again = "请用原来的保存编号再试一次" if hint == "key" else "请用当前版本标记再试一次"
    if not _review_settled(project):
        try:
            submit_agent_task(Task("script_review", project["id"], {}))
        except Exception:
            _fail(
                409,
                "稿已保存，审稿还没排上。%s，不要改编辑区。" % again,
                "review_not_queued",
                True,
                True,
                True,
                True,
            )
    record["queued"] = True
    entries[key] = record
    try:
        _store_idempotency(folder, entries)
    except OSError:
        record["queued"] = False
        entries[key] = record
        _fail(
            409,
            "稿已保存。审稿可能已经排上，但确认记录没写完。%s，不要改编辑区，也不要换新编号。" % again,
            "review_ack_unconfirmed",
            True,
            True,
            True,
            True,
        )
    return result


def revision_retry(project, base_token):
    """Queue the saved manuscript's review once. Does not rewrite or spend a repair budget."""
    from end.db import project_dir
    from end.server import submit_agent_task
    from end.task_queue.queue import Task

    project_id = project["id"]
    folder = project_dir(project_id)
    with project_lock(project_id):
        official = official_script(folder)
        row = load_candidate(folder)
        _check_token(project_id, base_token, official, row)
        busy = _busy_reason(project)
        if busy and busy != "reviewing":
            _fail(409, "正在处理，还不能再保存", "busy", True, False, True)
        entries = _load_idempotency(folder)
        record = _bound_review_record(entries, project_id, official, row)
        current = revision_token(project_id, official, row)
        if record is None:
            _fail(409, "没有待补排的审稿", "nothing_pending", False, False, False)
        if _review_settled(project):
            return _retry_result(current, "done", False, "审稿已结束")
        if record.get("queued"):
            return _retry_result(current, "queued", True, "审稿已在队列里")
        key = next(name for name, item in entries.items() if item is record)
        outcome = _finish_queue(project, folder, entries, key, record, submit_agent_task, Task, hint="token")
        outcome = dict(outcome)
        outcome["review_state"] = "queued"
        outcome["unchanged"] = True
        outcome["message"] = "已保存，正在重新审稿"
        return outcome


def _retry_result(token, state, queued, message):
    return {
        "ok": True,
        "saved": True,
        "review_queued": queued,
        "unchanged": True,
        "review_state": state,
        "base_token": token,
        "message": message,
    }
