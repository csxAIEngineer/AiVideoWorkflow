"""Opinion-driven draft. It does not write the official script or the repair candidate."""

import json
import uuid
from pathlib import Path

from end.render_loop import project_lock
from end.script_review import content_hash

MODEL_LIMIT = 2
_FILE = "剧情意见.json"
_MODEL = None


def set_opinion_model(fn):
    global _MODEL
    _MODEL = fn


def _path(folder):
    return Path(folder) / "_内部_" / _FILE


def _load(folder):
    path = _path(folder)
    if not path.is_file():
        return {"active_id": "", "calls": {}, "items": {}, "confirmed": {}, "idempotency": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active_id": "", "calls": {}, "items": {}, "confirmed": {}, "idempotency": {}}
    if not isinstance(data, dict):
        return {"active_id": "", "calls": {}, "items": {}, "confirmed": {}, "idempotency": {}}
    data.setdefault("active_id", "")
    data.setdefault("calls", {})
    data.setdefault("items", {})
    data.setdefault("confirmed", {})
    data.setdefault("idempotency", {})
    return data


def _store(folder, data):
    path = _path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _fail(code, message, reason, retryable, saved=False, retry_same_key=False, model_remaining=None):
    from end.script_revision import RevisionError

    error = RevisionError(code, message, reason, retryable, saved, retry_same_key, False)
    error.model_remaining = model_remaining
    raise error


def intent_for_script(folder, script_hash):
    data = _load(folder)
    row = (data.get("confirmed") or {}).get(script_hash)
    return dict(row) if isinstance(row, dict) else None


def _requirements_hash(project):
    return content_hash(project.get("brief") or "")


def _base_text(official, row, base):
    from end.script_revision import _draft

    if base == "official":
        return official, "official"
    if base == "draft":
        text, source = _draft(row)
        if source == "none" or not text:
            _fail(409, "没有未采用的候选可以当底稿", "no_draft", False)
        return text, "draft"
    _fail(400, "底稿只能是正式稿或未采用候选", "invalid_request", False)


def _identity(base, base_hash, requirements_hash, opinion):
    return content_hash("\n".join((base, base_hash, requirements_hash, opinion)))


def _active(data):
    item = (data.get("items") or {}).get(data.get("active_id") or "")
    return item if isinstance(item, dict) else None


def _stale(item, token, requirements_hash):
    if not item:
        return False
    return item.get("base_token") != token or item.get("requirements_hash") != requirements_hash


def opinion_view(project, official, row):
    from end.db import project_dir
    from end.script_revision import _at_script_gate, _busy_reason, revision_token

    folder = project_dir(project["id"])
    data = _load(folder)
    item = _active(data)
    token = revision_token(project["id"], official, row)
    requirements_hash = _requirements_hash(project)
    stale = bool(item) and _stale(item, token, requirements_hash)
    state = "none"
    if item:
        state = item.get("state") or "none"
        if state not in ("pending", "running", "ready", "failed"):
            state = "none"
    busy = _busy_reason(project)
    identity = "" if not item else item.get("identity") or ""
    spent = int((data.get("calls") or {}).get(identity) or 0) if identity else 0
    remaining = MODEL_LIMIT if not identity else max(0, MODEL_LIMIT - spent)
    can_confirm = (
        state == "ready"
        and not stale
        and not busy
        and _at_script_gate(project)
    )
    model_retryable = (
        state == "failed"
        and not stale
        and remaining > 0
        and not busy
        and _at_script_gate(project)
    )
    return {
        "opinion_id": "" if not item else item.get("opinion_id") or "",
        "opinion_revision": 0 if not item else int(item.get("opinion_revision") or 0),
        "opinion_state": state,
        "opinion_stale": stale,
        "opinion_base": "" if not item else item.get("base") or "",
        "opinion_text": "" if not item else item.get("opinion") or "",
        "opinion_candidate_text": "" if not item or stale else item.get("candidate_text") or "",
        "opinion_error": "" if not item else item.get("error") or "",
        "opinion_retryable": bool(item) and not stale and state == "pending",
        "opinion_model_remaining": remaining,
        "opinion_model_retryable": model_retryable,
        "can_confirm_opinion": can_confirm,
    }


def confirmation_intent(folder, project, base_token, official, row, opinion_id, opinion_revision):
    """Explicit confirm. source is not consulted. None means a plain manual save."""
    from end.script_revision import revision_token

    missing_id = opinion_id is None or str(opinion_id) == ""
    missing_rev = opinion_revision is None or str(opinion_revision) == ""
    if missing_id and missing_rev:
        return None
    if missing_id or missing_rev:
        _fail(400, "确认意见需要同时给出 opinion_id 和 opinion_revision", "invalid_request", False)
    data = _load(folder)
    item = (data.get("items") or {}).get(str(opinion_id))
    if not isinstance(item, dict):
        _fail(409, "没有这轮意见", "nothing_pending", False)
    token = revision_token(project["id"], official, row)
    requirements_hash = _requirements_hash(project)
    if str(base_token or "") != token or _stale(item, token, requirements_hash):
        _fail(409, "这轮意见已经过期，不能确认", "opinion_stale", False)
    if item.get("state") != "ready" or int(item.get("opinion_revision") or 0) != int(opinion_revision):
        _fail(409, "意见候选版本已经变了，不能确认", "opinion_stale", False)
    return {
        "opinion_id": item.get("opinion_id"),
        "opinion_revision": int(item.get("opinion_revision") or 0),
        "opinion": item.get("opinion") or "",
        "requirements_hash": item.get("requirements_hash") or "",
        "base": item.get("base") or "",
        "base_hash": item.get("base_hash") or "",
        "intent": "user_directed",
    }


def attach_confirmed_intent(folder, script_text, intent):
    data = _load(folder)
    data.setdefault("confirmed", {})
    data["confirmed"][content_hash(script_text)] = dict(intent)
    _store(folder, data)


def _payload_hash(base, opinion, token):
    return content_hash(json.dumps({"base": base, "opinion": opinion, "base_token": token}, ensure_ascii=False, sort_keys=True))


def submit_opinion(project, base, opinion, base_token, idempotency_key):
    from end.db import project_dir, write_project
    from end.script_repair import load_candidate, official_script
    from end.script_revision import _at_script_gate, _busy_reason, _check_token
    from end.server import submit_agent_task
    from end.task_queue.queue import Task

    project_id = project["id"]
    folder = project_dir(project_id)
    key = str(idempotency_key or "").strip()
    if not key:
        _fail(400, "没有保存编号", "invalid_request", False)
    text = str(opinion or "").strip()
    if not text:
        _fail(400, "意见是空的", "empty_opinion", False)
    base_name = str(base or "").strip()
    with project_lock(project_id):
        official = official_script(folder)
        row = load_candidate(folder)
        _check_token(project_id, base_token, official, row)
        base_text, base_kind = _base_text(official, row, base_name)
        requirements_hash = _requirements_hash(project)
        base_hash = content_hash(base_text)
        identity = _identity(base_kind, base_hash, requirements_hash, text)
        payload = _payload_hash(base_kind, text, str(base_token or ""))
        data = _load(folder)
        previous = (data.get("idempotency") or {}).get(key)
        if isinstance(previous, dict):
            if previous.get("payload") != payload:
                _fail(409, "同一次意见的内容变了，请换一个新的保存编号", "idempotency_mismatch", False)
            return _queue_existing(project, folder, data, previous, submit_agent_task, Task, str(base_token or ""))
        if _busy_reason(project):
            _fail(409, "正在处理，还不能提交意见", "busy", True, False, True)
        if not _at_script_gate(project):
            _fail(409, "现在不是剧本修订停点", "invalid_phase", False)
        spent = int((data.get("calls") or {}).get(identity) or 0)
        if spent >= MODEL_LIMIT:
            _fail(409, "这组底稿和意见的模型次数已用完", "budget_exhausted", False)
        opinion_id = "op" + uuid.uuid4().hex[:12]
        prior = _active(data)
        item = {
            "opinion_id": opinion_id,
            "opinion_revision": 0,
            "opinion": text,
            "base": base_kind,
            "base_hash": base_hash,
            "requirements_hash": requirements_hash,
            "base_token": str(base_token or ""),
            "identity": identity,
            "state": "pending",
            "queued": False,
            "model_spent": False,
            "generation": int((prior or {}).get("generation") or 0) + 1,
            "candidate_text": "",
            "error": "",
        }
        data["items"][opinion_id] = item
        data["active_id"] = opinion_id
        result = {
            "ok": True,
            "official_saved": False,
            "opinion_state": "running",
            "unchanged": False,
            "opinion_id": opinion_id,
            "base_token": str(base_token or ""),
            "message": "正在按意见改稿",
        }
        data["idempotency"][key] = {"payload": payload, "opinion_id": opinion_id, "result": result}
        _store(folder, data)
        pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
        pipeline["action"] = "opinion_revise"
        pipeline["repair"] = ""
        progress = dict(project.get("progress") or {})
        progress["pipeline"] = pipeline
        project["progress"] = progress
        try:
            write_project(project)
            submit_agent_task(Task("opinion_revise", project_id, {"opinion_id": opinion_id, "generation": item["generation"]}))
        except Exception:
            fresh = _load(folder)
            current = (fresh.get("items") or {}).get(opinion_id) or item
            current["state"] = "pending"
            current["queued"] = False
            fresh["items"][opinion_id] = current
            _store(folder, fresh)
            _fail(
                409,
                "意见已记下，改稿还没排上。请用原来的保存编号再试一次。",
                "opinion_not_queued",
                True,
                True,
                True,
            )
        fresh = _load(folder)
        current = fresh["items"][opinion_id]
        current["state"] = "running"
        current["queued"] = True
        fresh["items"][opinion_id] = current
        try:
            _store(folder, fresh)
        except OSError:
            _fail(
                409,
                "意见已记下。改稿可能已经排上，但确认记录没写完。请用原来的保存编号再试一次，不要换新编号。",
                "opinion_ack_unconfirmed",
                True,
                True,
                True,
            )
        return result


def _queue_existing(project, folder, data, previous, submit_agent_task, Task, token):
    opinion_id = previous.get("opinion_id")
    item = (data.get("items") or {}).get(opinion_id)
    if not isinstance(item, dict):
        _fail(409, "没有这轮意见", "nothing_pending", False)
    if item.get("state") in ("running", "ready"):
        return dict(previous.get("result") or {})
    if item.get("state") == "failed" or item.get("model_spent"):
        result = dict(previous.get("result") or {})
        result["opinion_state"] = "failed"
        result["message"] = item.get("error") or "意见改稿没有完成"
        return result
    try:
        submit_agent_task(Task("opinion_revise", project["id"], {"opinion_id": opinion_id, "generation": item.get("generation")}))
    except Exception:
        _fail(409, "意见已记下，改稿还没排上。请用原来的保存编号再试一次。", "opinion_not_queued", True, True, True)
    item["state"] = "running"
    item["queued"] = True
    data["items"][opinion_id] = item
    result = dict(previous.get("result") or {})
    result.update({"opinion_state": "running", "message": "正在按意见改稿", "base_token": token, "official_saved": False})
    for row in data.get("idempotency", {}).values():
        if isinstance(row, dict) and row.get("opinion_id") == opinion_id:
            row["result"] = result
    try:
        _store(folder, data)
    except OSError:
        _fail(409, "意见已记下。改稿可能已经排上，但确认记录没写完。请用原来的保存编号再试一次，不要换新编号。", "opinion_ack_unconfirmed", True, True, True)
    return result


def retry_opinion(project, base_token):
    from end.db import project_dir
    from end.script_repair import load_candidate, official_script
    from end.script_revision import _busy_reason, _check_token
    from end.server import submit_agent_task
    from end.task_queue.queue import Task

    project_id = project["id"]
    folder = project_dir(project_id)
    with project_lock(project_id):
        official = official_script(folder)
        row = load_candidate(folder)
        _check_token(project_id, base_token, official, row)
        busy = _busy_reason(project)
        if busy and busy != "opinion":
            _fail(409, "正在处理，还不能补排意见", "busy", True, False, True)
        data = _load(folder)
        item = _active(data)
        requirements_hash = _requirements_hash(project)
        if not item or _stale(item, str(base_token or ""), requirements_hash):
            _fail(409, "没有可补排的意见", "nothing_pending", False)
        state = item.get("state")
        if state == "ready":
            return {"ok": True, "official_saved": False, "opinion_state": "ready", "unchanged": True, "opinion_id": item.get("opinion_id"), "base_token": str(base_token or ""), "message": "意见候选已写好"}
        if state == "running":
            return {"ok": True, "official_saved": False, "opinion_state": "running", "unchanged": True, "opinion_id": item.get("opinion_id"), "base_token": str(base_token or ""), "message": "正在按意见改稿"}
        if state == "failed":
            identity = item.get("identity") or ""
            remaining = max(0, MODEL_LIMIT - int((data.get("calls") or {}).get(identity) or 0))
            _fail(
                409,
                "这次意见改稿没有完成。补排不会再调模型。次数还剩 %d 次的话，用新的保存编号再提交同一条意见。" % remaining,
                "opinion_failed",
                False,
                True,
                False,
                model_remaining=remaining,
            )
        if item.get("model_spent"):
            return {"ok": True, "official_saved": False, "opinion_state": state or "failed", "unchanged": True, "opinion_id": item.get("opinion_id"), "base_token": str(base_token or ""), "message": "意见改稿没有自动重试"}
        try:
            submit_agent_task(Task("opinion_revise", project_id, {"opinion_id": item.get("opinion_id"), "generation": item.get("generation")}))
        except Exception:
            _fail(409, "意见已记下，改稿还没排上。请用当前版本标记再试一次。", "opinion_not_queued", True, True, True)
        item["state"] = "running"
        item["queued"] = True
        data["items"][item["opinion_id"]] = item
        try:
            _store(folder, data)
        except OSError:
            _fail(409, "意见已记下。改稿可能已经排上，但确认记录没写完。请用当前版本标记再试一次。", "opinion_ack_unconfirmed", True, True, True)
        return {"ok": True, "official_saved": False, "opinion_state": "running", "unchanged": True, "opinion_id": item.get("opinion_id"), "base_token": str(base_token or ""), "message": "正在按意见改稿"}


def run_opinion_task(project_id, opinion_id, generation):
    from end.db import project_dir, read_project, write_project
    from end.script_repair import load_candidate, official_script

    project = read_project(project_id)
    if project is None:
        return
    folder = project_dir(project_id)
    with project_lock(project_id):
        data = _load(folder)
        item = (data.get("items") or {}).get(str(opinion_id or ""))
        if not isinstance(item, dict) or int(item.get("generation") or 0) != int(generation or 0):
            return
        if item.get("state") == "ready" or item.get("model_spent"):
            return
        identity = item.get("identity") or ""
        spent = int((data.get("calls") or {}).get(identity) or 0)
        if spent >= MODEL_LIMIT:
            item["state"] = "failed"
            item["error"] = "这组底稿和意见的模型次数已用完"
            data["items"][item["opinion_id"]] = item
            _store(folder, data)
            return
        data["calls"][identity] = spent + 1
        item["model_spent"] = True
        data["items"][item["opinion_id"]] = item
        _store(folder, data)
        official = official_script(folder)
        row = load_candidate(folder)
        base_text, _kind = _base_text(official, row, item.get("base") or "official")
        messages = [
            {"role": "system", "content": "按用户这轮意见改底稿。意见可以改变主题或机制，这是有意的，不要压回旧主题。意见不必已经出现在旧稿里。没点到的段落保持原样。只返回改后的全文，不要解释。"},
            {"role": "user", "content": "底稿：\n%s\n\n意见：\n%s" % (base_text, item.get("opinion") or "")},
        ]
    try:
        text = _complete(messages)
    except Exception as error:
        text = ""
        failure = "意见改稿没有完成：%s" % error
    else:
        failure = ""
    with project_lock(project_id):
        data = _load(folder)
        item = (data.get("items") or {}).get(str(opinion_id or ""))
        if not isinstance(item, dict) or int(item.get("generation") or 0) != int(generation or 0):
            return
        if failure or not str(text or "").strip():
            item["state"] = "failed"
            item["error"] = failure or "意见改稿没有产出正文"
        else:
            item["state"] = "ready"
            item["candidate_text"] = str(text).strip()
            item["opinion_revision"] = int(item.get("opinion_revision") or 0) + 1
            item["error"] = ""
        data["items"][item["opinion_id"]] = item
        if data.get("active_id") == item["opinion_id"]:
            pipeline = dict((project.get("progress") or {}).get("pipeline") or {})
            if pipeline.get("action") == "opinion_revise":
                pipeline["action"] = "wait"
                progress = dict(project.get("progress") or {})
                progress["pipeline"] = pipeline
                project["progress"] = progress
                write_project(project)
        _store(folder, data)


def _complete(messages):
    if _MODEL is not None:
        return _MODEL(messages)
    from end.ai_client import ask_text

    return ask_text(messages, timeout=90, max_output_tokens=2500)
