"""Explicit human acceptance of one named manuscript. Does not pass the review or render.

The editor buffer is not read unless source is editor and confirm_unreviewed_save is true.
That path writes the text and does not queue a script review. Existing save still does.
"""

import json
from pathlib import Path

from end.history_keep import ArchiveError, snapshot_project
from end.render_loop import project_lock
from end.script_review import content_hash, issue_fingerprint, load_reviews, save_reviews, story_context

_ACCEPT_NAME = "按稿继续幂等.json"
_SOURCES = ("official", "draft", "opinion", "editor")
_BLOCKING = ("suspect", "blocked", "failed")


def _fail(code, message, reason, retryable, saved=False, retry_same_key=False, official_saved=False):
    from end.script_revision import RevisionError

    raise RevisionError(code, message, reason, retryable, saved, retry_same_key, official_saved)


def _path(folder):
    return Path(folder) / "_内部_" / _ACCEPT_NAME


def confirmed_continue(folder, script_hash):
    """True only when this acceptance durably recorded that a write was accepted."""
    for item in _load(folder).values():
        if isinstance(item, dict) and item.get("saved_script_hash") == script_hash and item.get("queued"):
            return True
    return False


def _load(folder):
    path = _path(folder)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def _store(folder, entries):
    path = _path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"entries": entries}, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _public_issue(issue):
    issue = issue or {}
    return {
        "fingerprint": issue_fingerprint(issue),
        "location": issue.get("location") or "",
        "evidence": issue.get("evidence") or "",
        "category": issue.get("category") or "",
        "severity": issue.get("severity") or "",
        "reason": issue.get("reason") or "",
        "owner": issue.get("owner") or "",
    }


def _review_for(folder, script_hash, context_hash):
    state = load_reviews(folder)
    row = state.get("script") if isinstance(state.get("script"), dict) else {}
    if not row:
        return "missing", "", []
    if row.get("script_hash") != script_hash or row.get("context_hash") != context_hash:
        return "other", row.get("reason") or "", []
    status = row.get("status") or "none"
    issues = [_public_issue(item) for item in (row.get("issues") or []) if isinstance(item, dict)]
    return "match", status, issues


def _candidate_risk(row):
    row = row or {}
    status = row.get("status") or ""
    reason = row.get("reason") or ""
    validation = row.get("validation") if isinstance(row.get("validation"), dict) else None
    if not status and not reason and not validation:
        return None
    return {"from": "repair_candidate", "status": status, "reason": reason, "validation": validation}


def _source_issues(mapped, status, issues, risk):
    """Never use an empty issue list to mean there is no risk."""
    if mapped == "match" and status not in ("", "none"):
        return "reviewed", issues, risk
    if risk is not None:
        return "unreviewed", None, risk
    if mapped == "other":
        return "unknown", None, None
    return "unreviewed", None, None


def _gate_open(project, busy, review_state):
    from end.script_revision import _at_script_gate

    return _at_script_gate(project) and not busy and review_state != "pending"


def _continue_view(project, folder, official, context_hash):
    state = load_reviews(folder)
    accept = state.get("human_accept") if isinstance(state.get("human_accept"), dict) else None
    script_hash = content_hash(official)
    if not accept or accept.get("acknowledged") is not True:
        return "none", "none", False, "", "", []
    if accept.get("script_hash") != script_hash or accept.get("context_hash") != context_hash:
        return "none", "none", False, "", "", []
    entries = _load(folder)
    record = None
    for item in entries.values():
        if isinstance(item, dict) and item.get("saved_script_hash") == script_hash:
            record = item
    status = project.get("status") or ""
    phase = (project.get("progress") or {}).get("phase") or ""
    current = ((project.get("progress") or {}).get("pipeline") or {}).get("current") or ""
    queued = bool(record and record.get("queued"))
    left_script = status == "gate" and current not in ("", "script") and phase != "gate_script"
    if left_script:
        continue_state, retryable = "done", False
    elif status == "error":
        continue_state, retryable = "failed", False
    elif record and not queued and status in ("gate", "writing"):
        continue_state, retryable = "pending", True
    elif status == "writing" and queued:
        continue_state, retryable = "queued", False
    elif queued:
        continue_state, retryable = "done", False
    else:
        continue_state, retryable = "pending", status == "gate"
    raw_issues = accept.get("issues")
    if isinstance(raw_issues, list):
        shown = [_public_issue(item) for item in raw_issues if isinstance(item, dict)]
    else:
        shown = None
    return (
        "accepted",
        continue_state,
        retryable,
        accept.get("source") or "",
        script_hash,
        shown,
    )


def accept_view(project, official, row):
    """Read which stored manuscripts can be accepted. Does not look at an editor buffer."""
    from end.db import project_dir
    from end.script_opinion import opinion_view
    from end.script_revision import _busy_reason, _draft, _review_status

    folder = project_dir(project["id"])
    official = official or ""
    context_hash = content_hash(story_context(folder, project.get("brief") or ""))
    script_hash = content_hash(official)
    busy = _busy_reason(project)
    review_state, _retryable = _review_status(project, folder, official, row)
    open_gate = _gate_open(project, busy, review_state)
    mapped, status, issues = _review_for(folder, script_hash, context_hash)
    official_state, official_issues, _official_risk = _source_issues(mapped, status, issues, None)
    official_reason = ""
    official_can = False
    if not official.strip():
        official_reason = "empty"
    elif not open_gate:
        official_reason = "closed"
    elif official_state == "reviewed" and status == "passed":
        official_reason = "review_passed"
    elif official_state in ("reviewed", "unreviewed", "unknown"):
        official_can = official_state != "reviewed" or status in _BLOCKING
    else:
        official_reason = "closed"
    if official_state == "reviewed" and status == "passed":
        official_can = False
    draft_text, draft_source = _draft(row)
    draft_hash = content_hash(draft_text) if draft_text else ""
    draft_mapped, draft_status, draft_issues = _review_for(folder, draft_hash, context_hash) if draft_text else ("missing", "", [])
    draft_risk = _candidate_risk(row)
    draft_state, draft_issue_list, draft_risk = _source_issues(draft_mapped, draft_status, draft_issues, draft_risk)
    draft_can = bool(draft_text) and open_gate
    opinion = opinion_view(project, official, row)
    opinion_text = opinion.get("opinion_candidate_text") or ""
    opinion_hash = content_hash(opinion_text) if opinion_text and not opinion.get("opinion_stale") else ""
    opinion_mapped, opinion_status, opinion_issues = _review_for(folder, opinion_hash, context_hash) if opinion_hash else ("missing", "", [])
    opinion_state, opinion_issue_list, _opinion_risk = _source_issues(opinion_mapped, opinion_status, opinion_issues, None)
    opinion_can = bool(opinion.get("can_confirm_opinion")) and bool(opinion_text) and open_gate
    related = None
    if mapped == "match":
        related = {"applies_to": "official", "script_hash": script_hash, "status": status, "reason": ""}
    accept_state, continue_state, continue_retryable, accepted_source, accepted_hash, accepted_issues = _continue_view(
        project, folder, official, context_hash,
    )
    return {
        "accept_sources": [
            {
                "source": "official",
                "available": bool(official.strip()),
                "text_hash": script_hash if official.strip() else "",
                "issue_state": official_state if official.strip() else "unreviewed",
                "review_status": status if official_state == "reviewed" else "",
                "issues": official_issues,
                "risk": None,
                "related_review": None,
                "can_accept": official_can,
                "unavailable_reason": official_reason,
            },
            {
                "source": "draft",
                "available": bool(draft_text),
                "draft_source": draft_source,
                "text_hash": draft_hash,
                "issue_state": draft_state if draft_text else "unknown",
                "review_status": draft_status if draft_state == "reviewed" else "",
                "issues": draft_issue_list,
                "risk": draft_risk,
                "related_review": None if draft_hash == script_hash else related,
                "can_accept": draft_can,
                "unavailable_reason": "" if draft_can or not draft_text else "closed",
            },
            {
                "source": "opinion",
                "available": bool(opinion_text) and not opinion.get("opinion_stale"),
                "opinion_id": opinion.get("opinion_id") or "",
                "opinion_revision": int(opinion.get("opinion_revision") or 0),
                "text_hash": opinion_hash,
                "issue_state": opinion_state if opinion_hash else "unknown",
                "review_status": opinion_status if opinion_state == "reviewed" else "",
                "issues": opinion_issue_list,
                "risk": None,
                "related_review": None if opinion_hash == script_hash else related,
                "can_accept": opinion_can,
                "unavailable_reason": "" if opinion_can else ("stale" if opinion.get("opinion_stale") else "not_ready"),
            },
        ],
        "editor_accept": {
            "source": "editor",
            "label": "接受尚未审查的编辑稿",
            "can_accept": open_gate,
            "atomic": True,
            "requires": ["text", "text_hash", "accept_unreviewed_edit", "acknowledge_risk", "idempotency_key", "base_token"],
            "saves_without_review": True,
            "drops_candidate_risk": False,
        },
        "accept_state": accept_state,
        "continue_state": continue_state,
        "continue_retryable": continue_retryable,
        "accepted_source": accepted_source,
        "accepted_script_hash": accepted_hash,
        "accepted_issues": accepted_issues,
        "human_accepted": accept_state == "accepted",
        "review_passed": status == "passed",
    }


def _request_hash(source, text_hash, baseline, editor_hash, opinion_id, opinion_revision, confirm_edit, text):
    body = {
        "source": source,
        "text_hash": text_hash,
        "editor_baseline": baseline,
        "editor_text_hash": editor_hash,
        "opinion_id": opinion_id,
        "opinion_revision": opinion_revision,
        "accept_unreviewed_edit": confirm_edit,
        "text_hash_of_body": content_hash(text) if source == "editor" else "",
    }
    return content_hash(json.dumps(body, ensure_ascii=False, sort_keys=True))


def _result(source, token, stage, official_saved, message, issues):
    return {
        "ok": True,
        "saved": True,
        "official_saved": bool(official_saved),
        "review_queued": False,
        "accept_state": "accepted",
        "continue_state": "queued",
        "continue_stage": stage,
        "continue_paid": False,
        "source": source,
        "base_token": token,
        "message": message,
        "unresolved_issues": issues,
        "unreviewed_edit": source == "editor",
    }


def _replay_or_mismatch(record, request_hash, official_saved):
    if record.get("request_hash") != request_hash:
        _fail(409, "同一次继续的选择变了，请换一个新的编号", "idempotency_mismatch", False, False, False, official_saved)
    result = dict(record.get("result") or {})
    if record.get("queued"):
        return result
    return None


def _chosen_text(folder, project, official, row, source, text_hash, opinion_id, opinion_revision, text):
    from end.script_opinion import confirmation_intent
    from end.script_revision import _draft, revision_token

    if source == "official":
        if not official.strip():
            _fail(409, "没有正式稿", "nothing_to_accept", False)
        if content_hash(official) != text_hash:
            _fail(409, "所选正式稿和给出的摘要不一致，编辑区不会被改写", "text_mismatch", False)
        return official.strip(), False, None
    if source == "draft":
        draft, _kind = _draft(row)
        if not draft:
            _fail(409, "没有未采用候选", "nothing_to_accept", False)
        if content_hash(draft) != text_hash:
            _fail(409, "所选候选和给出的摘要不一致，编辑区不会被改写", "text_mismatch", False)
        return draft, True, None
    if source == "opinion":
        intent = confirmation_intent(folder, project, revision_token(project["id"], official, row), official, row, opinion_id, opinion_revision)
        from end.script_opinion import _load

        item = (_load(folder).get("items") or {}).get(str(opinion_id))
        body = str((item or {}).get("candidate_text") or "").strip()
        if not body or content_hash(body) != text_hash:
            _fail(409, "所选意见候选和给出的摘要不一致，编辑区不会被改写", "text_mismatch", False)
        return body, True, intent
    body = "" if text is None else str(text).strip()
    if not body:
        _fail(400, "编辑区正文是空的", "empty_text", False)
    if len(str(text or "").encode("utf-8")) > 1_000_000:
        _fail(413, "内容太大", "too_large", False)
    if content_hash(body) != text_hash:
        _fail(409, "编辑区正文和给出的摘要不一致", "text_mismatch", False)
    if content_hash(body) == content_hash(official):
        _fail(409, "编辑区正文和正式稿一样。请改用 source=official", "use_official", False)
    draft, _kind = _draft(row)
    if draft and content_hash(body) == content_hash(draft):
        _fail(409, "编辑区正文就是未采用候选。请改用 source=draft，候选上的未通过记录会保留", "use_draft", False)
    from end.script_opinion import _load

    opinion_item = _load(folder).get("items") or {}
    for item in opinion_item.values():
        if isinstance(item, dict) and content_hash(str(item.get("candidate_text") or "")) == content_hash(body) and str(item.get("candidate_text") or "").strip():
            _fail(409, "编辑区正文就是意见候选。请改用 source=opinion，不要把它标成未审编辑", "use_opinion", False)
    return body, True, None


def _validate(params):
    source = str((params or {}).get("source") or "").strip()
    if source not in _SOURCES:
        _fail(400, "要写明按哪一稿：official、draft、opinion 或 editor", "invalid_request", False)
    if params.get("acknowledge_risk") is not True:
        _fail(400, "要按这一稿继续，需要明确确认审查没通过的风险", "risk_not_acknowledged", False)
    text_hash = str(params.get("text_hash") or "").strip()
    editor_hash = str(params.get("editor_text_hash") or "").strip()
    if not text_hash:
        _fail(400, "缺少所选稿摘要", "invalid_request", False)
    key = str(params.get("idempotency_key") or "").strip()
    if not key:
        _fail(400, "没有保存编号", "invalid_request", False)
    confirm_save = params.get("accept_unreviewed_edit") is True
    opinion_id = params.get("opinion_id")
    opinion_revision = params.get("opinion_revision")
    has_opinion = not (opinion_id is None or str(opinion_id) == "") or not (opinion_revision is None or str(opinion_revision) == "")
    has_text = "text" in params and params.get("text") not in (None, "")
    baseline = str(params.get("editor_baseline") or "").strip()
    has_baseline = bool(baseline)
    has_editor_hash = bool(editor_hash)
    if has_baseline != has_editor_hash:
        _fail(400, "编辑区基准和编辑区摘要要一起给出，或都不给", "invalid_request", False)
    if baseline and baseline not in ("official", "draft", "opinion"):
        _fail(400, "编辑区基准只能是 official、draft 或 opinion", "invalid_request", False)
    if source == "editor":
        if not confirm_save:
            _fail(400, "这是尚未审查的编辑稿。需要明确接受，才会原子保存并按同一份正文继续，并且不会因此重新审稿", "confirm_save_required", False)
        if has_opinion or has_baseline or has_editor_hash:
            _fail(400, "接受尚未审查的编辑稿不带意见编号，也不借编辑区基准丢掉候选风险", "invalid_request", False)
        if not has_text:
            _fail(400, "编辑区正文是空的", "empty_text", False)
    else:
        if confirm_save or has_text:
            _fail(400, "这个来源不接收正文，也不会静默保存编辑区", "text_forbidden", False)
        if source == "opinion":
            if opinion_id is None or str(opinion_id) == "" or opinion_revision is None or str(opinion_revision) == "":
                _fail(400, "按意见候选需要同时给出 opinion_id 和 opinion_revision", "invalid_request", False)
        elif has_opinion:
            _fail(400, "这个来源不能带意见编号", "invalid_request", False)
    return source, text_hash, baseline, editor_hash, key, confirm_save


def _baseline_text(folder, project, official, row, baseline):
    from end.script_opinion import _active, _load
    from end.script_revision import _draft, revision_token

    if baseline == "official":
        return official
    if baseline == "draft":
        text, _kind = _draft(row)
        return text
    item = _active(_load(folder))
    if not item or item.get("base_token") != revision_token(project["id"], official, row):
        return ""
    return str(item.get("candidate_text") or "")


def _require_clean_baseline(folder, project, official, row, baseline, editor_hash):
    text = _baseline_text(folder, project, official, row, baseline)
    if not str(text or "").strip():
        _fail(409, "编辑区基准没有正文，这次没有保存，也没有继续", "unsaved_edit", False)
    if editor_hash != content_hash(text):
        _fail(409, "编辑区相对「%s」还有未保存的字。这次没有保存，也没有继续。要按编辑区这份未审正文，请单独接受尚未审查的编辑稿" % baseline, "unsaved_edit", False)


def _write_chosen(folder, project, text):
    from end.script_revision import _write_script

    try:
        snapshot_project(folder, "human-script", ["_内部_/剧本.md", "_内部_/剧本候选.json", "spec.json", "reviews.json"])
    except ArchiveError as error:
        _fail(409, "保存没有完成，正式稿还是原样：%s" % error, "archive_failed", True)
    _write_script(folder, project, text)


def _record_accept(folder, project, official, source, wrote, intent, previous_status):
    fresh = official.strip()
    script_hash = content_hash(fresh)
    context_hash = content_hash(story_context(folder, project.get("brief") or ""))
    mapped, status, issues = _review_for(folder, script_hash, context_hash)
    if mapped == "match" and status == "passed":
        _fail(409, "这份正式稿的审稿已经通过。按这一稿不使用这个接口", "review_passed", False, False, False, wrote)
    if mapped == "match" and status in _BLOCKING:
        issue_state = "reviewed"
        review_status = status
    else:
        issues = None
        issue_state = "unreviewed"
        review_status = previous_status
    state = load_reviews(folder)
    script_row = state.get("script")
    state["human_accept"] = {
        "acknowledged": True,
        "script_hash": script_hash,
        "context_hash": context_hash,
        "source": source,
        "issue_state": issue_state,
        "review_status": review_status,
        "issues": issues,
    }
    if script_row is not None:
        state["script"] = script_row
    save_reviews(folder, state)
    if intent is not None:
        from end.script_opinion import attach_confirmed_intent

        attach_confirmed_intent(folder, fresh, intent)
    return script_hash, issues


def _finish(folder, entries, key, record, continue_fn, project, official_saved, source, issues):
    from end.script_revision import revision_token
    from end.script_repair import load_candidate, official_script

    if record.get("queued"):
        return dict(record.get("result") or {})
    from end.script_revision import RevisionError
    try:
        outcome = continue_fn(project)
    except RevisionError:
        raise
    except Exception:
        record["queued"] = False
        entries[key] = record
        try:
            _store(folder, entries)
        except OSError:
            pass
        _fail(409, "已记下按这一稿继续，下一段还没排上。请用原来的编号再试一次，不要改稿", "continue_not_queued", True, True, True, official_saved)
    if not outcome.get("queued") and not outcome.get("already"):
        record["queued"] = False
        entries[key] = record
        _store(folder, entries)
        _fail(409, "已记下按这一稿继续，下一段还没排上。请用原来的编号再试一次，不要改稿", "continue_not_queued", True, True, True, official_saved)
    record["queued"] = True
    fresh = official_script(folder)
    row = load_candidate(folder)
    result = _result(
        source,
        revision_token(project["id"], fresh, row),
        outcome.get("continue_stage") or "",
        official_saved,
        "下一段已在队列里。原来的审稿结论还在，没有出图。" if outcome.get("already") else "已按这一稿继续。原来的审稿结论还在，没有改成通过，也没有出图。",
        issues,
    )
    record["result"] = result
    entries[key] = record
    try:
        _store(folder, entries)
    except OSError:
        record["queued"] = False
        entries[key] = record
        _fail(409, "已记下按这一稿继续。下一段可能已经排上，但确认记录没写完。请用原来的编号再试，不要改稿", "continue_ack_unconfirmed", True, True, True, official_saved)
    return result


def accept_as_is(project, params, continue_fn):
    from end.db import project_dir, write_project
    from end.script_repair import load_candidate, official_script
    from end.script_revision import _at_script_gate, _busy_reason, _check_token, _review_status, revision_token

    source, text_hash, baseline, editor_hash, key, confirm_save = _validate(params or {})
    project_id = project["id"]
    folder = project_dir(project_id)
    with project_lock(project_id):
        official = official_script(folder)
        row = load_candidate(folder)
        _check_token(project_id, params.get("base_token"), official, row)
        request_hash = _request_hash(
            source, text_hash, baseline, editor_hash, str(params.get("opinion_id") or ""), str(params.get("opinion_revision") or ""), confirm_save, params.get("text"),
        )
        entries = _load(folder)
        previous = entries.get(key)
        if isinstance(previous, dict):
            replay = _replay_or_mismatch(previous, request_hash, False)
            if replay is not None:
                return replay
        if baseline:
            _require_clean_baseline(folder, project, official, row, baseline, editor_hash)
        if not _at_script_gate(project):
            _fail(409, "现在不是剧本停点", "invalid_phase", False)
        busy = _busy_reason(project)
        review_state, _retryable = _review_status(project, folder, official, row)
        if busy or review_state == "pending":
            _fail(409, "正在处理，还不能按这一稿继续", "busy", True)
        if isinstance(previous, dict):
            return _finish(folder, entries, key, previous, continue_fn, _reload(project), bool((previous.get("result") or {}).get("official_saved")), source, (previous.get("result") or {}).get("unresolved_issues") or [])
        chosen, must_write, intent = _chosen_text(
            folder, project, official, row, source, text_hash, params.get("opinion_id"), params.get("opinion_revision"), params.get("text"),
        )
        wrote = False
        blobs = None
        old_script = project.get("script")
        from end.script_revision import RevisionError, _preimage, _restore

        if must_write and content_hash(chosen) != content_hash(official):
            before = load_reviews(folder).get("script")
            try:
                snapshot_project(folder, "human-script", ["_内部_/剧本.md", "_内部_/剧本候选.json", "spec.json", "reviews.json"])
            except ArchiveError as error:
                _fail(409, "保存没有完成，正式稿还是原样：%s" % error, "archive_failed", True)
            blobs = _preimage(folder)
            try:
                from end.script_revision import _write_script

                _write_script(folder, project, chosen)
                write_project(project)
            except Exception:
                _restore(blobs)
                project["script"] = old_script
                write_project(project)
                _fail(409, "保存没有完成，正式稿已退回原样", "archive_failed", True)
            wrote = True
            if load_reviews(folder).get("script") != before:
                _restore(blobs)
                project["script"] = old_script
                write_project(project)
                _fail(409, "保存改动了原审稿结论，正式稿已退回原样", "archive_failed", True)
        try:
            fresh = official_script(folder)
            previous_status = ""
            script_hash, issues = _record_accept(folder, project, fresh, source, wrote, intent, previous_status)
            if load_candidate(folder).get("attempts") != (row or {}).get("attempts"):
                raise RevisionError(409, "按这一稿改动了修订次数", "archive_failed", True, False, False, wrote)
            record = {
                "request_hash": request_hash,
                "base_token": revision_token(project_id, fresh, load_candidate(folder)),
                "saved_script_hash": script_hash,
                "queued": False,
                "result": {},
            }
            entries[key] = record
            _store(folder, entries)
        except Exception:
            if blobs is not None:
                _restore(blobs)
                project["script"] = old_script
                try:
                    write_project(project)
                except Exception:
                    pass
            raise
        return _finish(folder, entries, key, record, continue_fn, project, wrote, source, issues)


def _reload(project):
    from end.db import read_project

    return read_project(project["id"]) or project


def accept_retry(project, base_token, continue_fn):
    """Queue a stored acceptance whose continue was not confirmed. Does not rewrite or render."""
    from end.db import project_dir
    from end.script_repair import load_candidate, official_script
    from end.script_revision import _check_token

    project_id = project["id"]
    folder = project_dir(project_id)
    with project_lock(project_id):
        official = official_script(folder)
        row = load_candidate(folder)
        _check_token(project_id, base_token, official, row)
        context_hash = content_hash(story_context(folder, project.get("brief") or ""))
        script_hash = content_hash(official)
        state = load_reviews(folder)
        accept = state.get("human_accept") if isinstance(state.get("human_accept"), dict) else None
        if not accept or accept.get("script_hash") != script_hash or accept.get("context_hash") != context_hash:
            _fail(409, "没有待补排的按稿继续", "nothing_pending", False)
        record = None
        key = ""
        for name, item in _load(folder).items():
            if isinstance(item, dict) and item.get("saved_script_hash") == script_hash:
                record, key = item, name
        if record is None:
            _fail(409, "没有待补排的按稿继续", "nothing_pending", False)
        status = project.get("status") or ""
        phase = (project.get("progress") or {}).get("phase") or ""
        current = ((project.get("progress") or {}).get("pipeline") or {}).get("current") or ""
        if status == "error":
            _fail(409, "下一段没有写完。按这一稿不会自动重排，也不会出图", "continue_failed", False, True, False, False)
        if status == "gate" and current not in ("", "script") and phase != "gate_script":
            result = dict(record.get("result") or {})
            result["continue_state"] = "done"
            result["message"] = "已经离开剧本停点"
            result["continue_paid"] = False
            return result
        if record.get("queued") and status == "writing":
            result = dict(record.get("result") or {})
            result["continue_state"] = "queued"
            result["message"] = "下一段已在队列里"
            return result
        if record.get("queued") and status != "gate":
            result = dict(record.get("result") or {})
            result["continue_state"] = "done"
            result["message"] = "已经离开剧本停点"
            return result
        if record.get("queued"):
            result = dict(record.get("result") or {})
            result["continue_state"] = "queued"
            result["message"] = "下一段已在队列里"
            return result
        entries = _load(folder)
        return _finish(
            folder, entries, key, record, continue_fn, project,
            bool((record.get("result") or {}).get("official_saved")),
            accept.get("source") or "",
            (record.get("result") or {}).get("unresolved_issues") or accept.get("issues") or [],
        )
