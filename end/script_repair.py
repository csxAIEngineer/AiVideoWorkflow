"""Run the script repair graph and keep the official script until the user accepts."""

import json
import re
from pathlib import Path

from end.script_review import content_hash, load_reviews, save_reviews

MAX_ATTEMPTS = 2
_META = re.compile(
    r"【[^】\n]{0,80}(?:机检|审稿|阻断|语义审查|修订说明|修复元)[^】\n]*】"
    r"|〔[^〕\n]{0,80}(?:机检|审稿|阻断|语义审查|修订说明)[^〕\n]*〕"
    r"|机检.{0,12}阻断"
    r"|阻断.{0,12}修复"
    r"|审稿"
    r"|语义审查"
    r"|机检没过"
    r"|只改点名"
    r"|修复元"
    r"|pipeline\.repair"
)
_SENTENCE = re.compile(r"[^。！？\n]{8,}[。！？]?")


def candidate_path(folder):
    return Path(folder) / "_内部_" / "剧本候选.json"


def official_script(folder):
    path = Path(folder) / "_内部_" / "剧本.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_candidate(folder):
    path = candidate_path(folder)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_candidate(folder, row):
    path = candidate_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return row


def issue_rows(folder):
    """Structured issues from the current script review. Ids are stable."""
    row = (load_reviews(folder) or {}).get("script") or {}
    issues = []
    for index, item in enumerate(row.get("issues") or [], start=1):
        if not isinstance(item, dict):
            continue
        issues.append({
            "id": str(item.get("id") or "S%02d" % index),
            "location": str(item.get("location") or ""),
            "evidence": str(item.get("evidence") or ""),
            "category": str(item.get("category") or ""),
            "severity": str(item.get("severity") or ""),
            "reason": str(item.get("reason") or ""),
            "owner": "script",
            "revision": str(item.get("revision") or ""),
        })
    return issues


def _new_sentences(original, candidate):
    found = []
    for sentence in _SENTENCE.findall(str(candidate or "")):
        body = sentence.strip()
        if body and body not in str(original or ""):
            found.append(body)
    return found


def _basis_ok(basis, requirements, script):
    text = str(basis or "").strip()
    return bool(text) and (text in str(requirements or "") or text in str(script or ""))


def budget_identity(script, requirements, issues):
    """One budget for this manuscript, these requirements, and this issue list."""
    return {
        "source_hash": content_hash(script),
        "requirements_hash": content_hash(requirements),
        "issues_hash": content_hash(json.dumps(issues, ensure_ascii=False, sort_keys=True)),
    }


def same_budget(row, identity):
    row = row or {}
    return (
        row.get("source_hash") == identity["source_hash"]
        and row.get("requirements_hash") == identity["requirements_hash"]
        and row.get("issues_hash") == identity["issues_hash"]
    )


def attempts_used(folder, requirements):
    """Rounds already spent on this manuscript and issue list. A changed manuscript starts over."""
    identity = budget_identity(official_script(folder), requirements, issue_rows(folder))
    row = load_candidate(folder)
    if not same_budget(row, identity):
        return 0
    return int(row.get("attempts") or 0)


def repair_budget_open(folder, requirements):
    return attempts_used(folder, requirements) < MAX_ATTEMPTS


def candidate_question(row):
    """Chat copy for a saved candidate. Keeping the original does not reset the count."""
    row = row or {}
    status = row.get("status") or "blocked"
    reason = str(row.get("reason") or "")
    if row.get("needs_human"):
        return "缺少创作要求，不能判断主旨，需要人工确认。正式剧本没有改。", ["保持原稿"]
    if status == "passed":
        reply = "修订候选已通过独立审稿。正式剧本还没改，确认后才会换成这一版。\n" + str(row.get("candidate") or "")
        return reply, ["采用这版剧本", "保持原稿"]
    if int(row.get("attempts") or 0) >= MAX_ATTEMPTS:
        reply = "修订没有通过，同一原稿和问题的次数已用完。正式剧本没有改。改了原稿或问题之后才有新的次数。\n" + reason
        return reply, ["保持原稿"]
    reply = "修订没有通过。正式剧本没有改。\n" + reason
    return reply, ["再修一次", "保持原稿"]


def judge_candidate(requirements, original, candidate, issues, added, review):
    """Deterministic gate after the independent review call. It does not rewrite."""
    review = review or {}
    if _META.search(str(candidate or "")):
        return "blocked", "候选正文带了审稿或机检说明"
    if not str(candidate or "").strip():
        return "blocked", "候选是空的"
    preference_ids = {item["id"] for item in issues if item.get("severity") == "preference"}
    declared = []
    for beat in added or []:
        if not isinstance(beat, dict):
            continue
        if beat.get("issue_id") in preference_ids:
            return "blocked", "偏好不能改情节"
        text = str(beat.get("text") or "").strip()
        if text and not _basis_ok(beat.get("basis"), requirements, original):
            return "blocked", "新增情节没有原文依据：%s" % text
        if text:
            declared.append(text)
    for sentence in _new_sentences(original, candidate):
        if not any(sentence in item or item in sentence for item in declared):
            return "blocked", "有未申报的新增情节"
    needed = [item for item in issues if item.get("severity") in ("block", "suspect")]
    resolutions = {}
    for item in review.get("resolutions") or []:
        if isinstance(item, dict):
            resolutions[str(item.get("id") or "")] = item
    missing = []
    for item in needed:
        found = resolutions.get(item["id"]) or {}
        evidence = str(found.get("evidence") or "").strip()
        if not found.get("resolved") or not evidence or evidence not in str(candidate or ""):
            missing.append(item["id"])
    if missing:
        return "blocked", "这些问题没有修改后证据：%s" % "、".join(missing)
    if review.get("unsupported"):
        return "blocked", "审稿指出无依据的新机制"
    if review.get("new_contradictions"):
        return "blocked", "审稿指出新的因果、动机或连续性问题"
    if not review.get("theme_ok") or not review.get("passed"):
        return "blocked", "独立审稿没有通过"
    return "passed", ""


def run_script_repair(folder, requirements, complete, model=""):
    """Propose a candidate. The official script file is not written."""
    from AI.qwen_api.script_repair_graph import compile_script_repair_graph

    folder = Path(folder)
    before = official_script(folder)
    issues = issue_rows(folder)
    requirements = str(requirements or "")
    identity = budget_identity(before, requirements, issues)
    if not requirements.strip():
        row = dict(load_candidate(folder) or {})
        if not same_budget(row, identity):
            row["attempts"] = 0
        row.update({
            "status": "blocked",
            "needs_human": True,
            "theme_checked": False,
            "reason": "缺少创作要求，不能判断主旨，需要人工确认",
            "candidate": "",
            "source_hash": identity["source_hash"],
            "requirements_hash": identity["requirements_hash"],
            "issues_hash": identity["issues_hash"],
        })
        saved = _save_candidate(folder, row)
        if official_script(folder) != before:
            raise RuntimeError("剧本修订改写了正式稿")
        return saved
    if not repair_budget_open(folder, requirements):
        row = dict(load_candidate(folder) or {})
        row["reason"] = "同一原稿和问题的修订已到上限"
        return row
    if not before or not issues:
        row = _save_candidate(folder, {
            "status": "blocked",
            "reason": "没有原稿或没有结构化问题，不能修订",
            "source_hash": content_hash(before),
            "requirements_hash": content_hash(requirements),
            "issues_hash": content_hash(json.dumps(issues, ensure_ascii=False, sort_keys=True)),
            "attempts": attempts_used(folder, requirements) + 1,
        })
        return row
    app = compile_script_repair_graph(complete)
    try:
        state = app.invoke({
            "requirements": str(requirements or ""),
            "script": before,
            "issues": issues,
            "model": str(model or ""),
            "calls": [],
            "status": "",
        })
    except Exception as error:
        state = {"status": "error", "reason": str(error), "calls": []}
    candidate = str(state.get("candidate") or "")
    status = state.get("status") or ""
    reason = str(state.get("reason") or "")
    if status == "error":
        judged, why = "error", reason or "修订没有完成"
    elif status == "blocked" and not state.get("review_notes"):
        judged, why = "blocked", reason or "修订停住了"
    else:
        judged, why = judge_candidate(
            requirements,
            before,
            candidate,
            issues,
            state.get("added") or [],
            state.get("review_notes") or {},
        )
    attempts = attempts_used(folder, requirements) + 1
    row = _save_candidate(folder, {
        "status": judged,
        "reason": why,
        "candidate": candidate if judged == "passed" else "",
        "rejected_text": candidate if judged != "passed" else "",
        "source_hash": content_hash(before),
        "requirements_hash": content_hash(requirements),
        "issues_hash": content_hash(json.dumps(issues, ensure_ascii=False, sort_keys=True)),
        "issues": issues,
        "plan": state.get("revision_plan") or {},
        "calls": list(state.get("calls") or []),
        "model": str(state.get("model") or model or ""),
        "attempts": attempts,
    })
    if official_script(folder) != before:
        raise RuntimeError("剧本修订改写了正式稿")
    return row


def promote_candidate(folder, requirements, project_id=""):
    """Replace the official script only when this candidate still matches.

    The hash check and the write share the project lock. A manuscript that
    changes while this waits is read again and rejected.
    """
    from end.render_loop import project_lock

    folder = Path(folder)
    lock = project_lock(str(project_id or folder))
    with lock:
        row = load_candidate(folder)
        if row.get("status") != "passed" or not row.get("candidate"):
            return False, row.get("reason") or "没有可确认的候选"
        current = official_script(folder)
        issues = issue_rows(folder)
        if row.get("source_hash") != content_hash(current):
            return False, "原稿已经变了，旧候选不能用"
        if row.get("requirements_hash") != content_hash(requirements):
            return False, "创作要求已经变了，旧候选不能用"
        if row.get("issues_hash") != content_hash(json.dumps(issues, ensure_ascii=False, sort_keys=True)):
            return False, "问题清单已经变了，旧候选不能用"
        text = str(row.get("candidate") or "").strip()
        script_path = folder / "_内部_" / "剧本.md"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(text + "\n", encoding="utf-8")
        spec_path = folder / "spec.json"
        if spec_path.is_file():
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                spec = {}
            if not isinstance(spec, dict):
                spec = {}
            spec["script"] = text
            spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state = load_reviews(folder)
        state.pop("confirmed_script_hash", None)
        state.pop("confirmed_context_hash", None)
        save_reviews(folder, state)
        row = dict(row)
        row["promoted"] = True
        _save_candidate(folder, row)
        return True, ""


def default_complete(model):
    """Production transport. Tests pass their own complete and do not call this."""

    def complete(node, messages):
        from end.ai_client import ask_text

        return ask_text(messages, model=model or None, timeout=90, max_output_tokens=2500)

    return complete
