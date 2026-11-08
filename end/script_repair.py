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


def _compact(text):
    return re.sub(r"\s+", "", _META.sub("", str(text or "")))


def _strip_label_edit(text):
    """Drop editorial suffixes such as 逻辑修正. Not a story-word whitelist."""
    return re.sub(r"(?<=逻辑)修正", "", _compact(text))


def _basis_ok(basis, requirements, script):
    text = str(basis or "").strip()
    return bool(text) and (text in str(requirements or "") or text in str(script or ""))


def _sentences_of(text):
    return [item.strip() for item in _SENTENCE.findall(str(text or "")) if item.strip()]


def _edge(left, right, tail=False):
    limit = min(len(left), len(right))
    size = 0
    while size < limit:
        a = left[-(size + 1)] if tail else left[size]
        b = right[-(size + 1)] if tail else right[size]
        if a != b:
            break
        size += 1
    return size


def _substitution(sentence, original):
    """A middle replacement of an existing sentence, not a new trailing clause."""
    best = None
    for origin in _sentences_of(original):
        prefix = _edge(sentence, origin, tail=False)
        suffix = _edge(sentence, origin, tail=True)
        if prefix >= 2 and suffix >= 4 and prefix + suffix < len(sentence):
            score = prefix + suffix
            if best is None or score > best[0]:
                best = (score, origin)
    return best[1] if best else ""


def _direction_for(issue, plan):
    if issue.get("severity") not in ("block", "suspect"):
        return ""
    for change in (plan or {}).get("changes") or []:
        if not isinstance(change, dict):
            continue
        if str(change.get("issue_id") or "") != issue.get("id"):
            continue
        direction = str(change.get("direction") or "").strip()
        if direction and direction != "uncertain":
            return direction
    return ""


def _declared_refinement(sentence, original, issues, plan):
    origin = _substitution(sentence, original)
    if not origin:
        return False
    for issue in issues or []:
        evidence = str(issue.get("evidence") or "")
        if len(evidence) < 8 or evidence not in origin and evidence[:8] not in origin:
            continue
        if _direction_for(issue, plan) or (issue.get("severity") in ("block", "suspect") and str(issue.get("revision") or "").strip()):
            if _direction_for(issue, plan) or str(issue.get("revision") or "").strip():
                if _direction_for(issue, plan):
                    return True
                if str(issue.get("revision") or "").strip() and issue.get("severity") in ("block", "suspect"):
                    return True
    return False


def _stale_sound_lines(original, candidate, issues):
    """An evidence sentence changed, but its adjacent sound cue was copied unchanged."""
    rewritten = False
    for issue in issues or []:
        evidence = str(issue.get("evidence") or "")
        if len(evidence) >= 8 and evidence in str(original or "") and evidence not in str(candidate or ""):
            rewritten = True
            break
    if not rewritten:
        return []
    stale = []
    for line in str(original or "").splitlines():
        text = line.strip()
        if text.startswith("音效") and text in str(candidate or ""):
            stale.append(text)
    return stale


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
    reason = str(row.get("reason") or "").strip()
    validation = row.get("validation") if isinstance(row.get("validation"), dict) else {}
    source = str(validation.get("source") or "")
    stage = str(validation.get("stage") or "")
    if source == "missing_requirements" or reason.startswith("缺少创作要求"):
        return "缺少创作要求，不能判断主旨，需要人工确认。正式剧本没有改。", ["保持原稿"]
    saved = str(row.get("rejected_text") or "").strip()
    if row.get("needs_human") or source in _HUMAN:
        detail = reason or "这处改动无法确定"
        evidence = str(validation.get("evidence") or "").strip()
        if evidence and evidence not in detail:
            detail = detail + evidence
        if not detail.endswith("。"):
            detail += "。"
        reply = "需要人工确认。阶段 %s，来源 %s。%s正式剧本没有改。" % (stage or "judge", source or "needs_human", detail)
        return _with_saved(reply, saved), _repair_options(row, saved)
    if status == "passed":
        reply = "修订候选已通过独立审稿。正式剧本还没改，确认后才会换成这一版。\n" + str(row.get("candidate") or "")
        return reply, ["采用这版剧本", "保持原稿"]
    if int(row.get("attempts") or 0) >= MAX_ATTEMPTS:
        reply = "修订没有通过，同一原稿和问题的模型修订次数已用完。正式剧本没有改。这次次数不会清零。\n" + reason
        return _with_saved(reply, saved), _repair_options(row, saved)
    reply = "修订没有通过。正式剧本没有改。\n" + reason
    return _with_saved(reply, saved), _repair_options(row, saved)


_VAGUE_DIRECTION = {"改一下", "修改", "优化", "调整", "改一改", "处理一下"}
_HUMAN = (
    "vague_direction",
    "missing_declaration",
    "missing_basis",
    "missing_change_check",
    "unlinked_change",
    "unbound_declaration",
    "unbound_direction",
    "unanchored",
    "unbound_issue",
    "unclassified",
)


def _verdict(status, reason, source, evidence):
    return status, reason, {"stage": "passed" if status == "passed" else "judge", "source": source, "evidence": evidence}


def _with_saved(reply, saved):
    if saved:
        return reply + "\n未采用的候选还在，可以查看。\n" + saved
    return reply


def _repair_options(row, saved):
    """Model calls stop at the budget. Viewing the saved text does not reset it."""
    options = []
    if int((row or {}).get("attempts") or 0) < MAX_ATTEMPTS:
        options.append("再修一次")
    if saved:
        options.append("查看未采用候选")
    options.append("保持原稿")
    return options


def _direction_ready(direction):
    """A blank or filler direction is not a plan. Wording differences are not decided here."""
    direction = str(direction or "").strip().strip("。！")
    if direction in _VAGUE_DIRECTION or len(direction) < 8:
        return "vague_direction", direction or "方案方向是空的"
    return "", direction


def _bind_delta(delta, original, candidate, issues, plan):
    issue_id = str(delta.get("issue_id") or "")
    issue = next((item for item in issues or [] if item.get("id") == issue_id), None)
    if issue is None or issue.get("severity") not in ("block", "suspect"):
        return "unbound_issue", issue_id, ""
    declaration = str(delta.get("declaration") or "").strip()
    if not declaration:
        return "missing_declaration", "声明是空的，不能把它当成修订建议里的原文", ""
    source, evidence = _direction_ready(_direction_for(issue, plan))
    if source:
        return source, evidence, ""
    before = str(delta.get("before") or "").strip()
    after = str(delta.get("after") or "").strip()
    if not before and not after:
        return "", declaration, ""
    if not before or before not in str(original or "") or not after or after not in str(candidate or ""):
        return "unanchored", before or after, ""
    return "", declaration, after


def _issue_afters(added, issue_id):
    spans = []
    for beat in added or []:
        if not isinstance(beat, dict) or str(beat.get("issue_id") or "") != issue_id:
            continue
        after = str(beat.get("after") or "").strip()
        if after:
            spans.append(after)
    return spans


def _change_checks(issues, added, resolutions, original, candidate):
    """The review must name the change for that issue. Old text is not the change."""
    original = str(original or "")
    candidate = str(candidate or "")
    for item in issues or []:
        if item.get("severity") not in ("block", "suspect"):
            continue
        issue_id = str(item.get("id") or "")
        found = resolutions.get(issue_id) or {}
        change = str(found.get("change") or "").strip()
        if found.get("aligned") is not True or not change:
            return "missing_change_check", "审稿没有把具体改动对应到问题 %s，不能只凭通过和旧文确认" % issue_id, issue_id
        if change not in candidate:
            return "missing_change_check", "审稿标出的改动不在候选里：%s" % issue_id, change[:80]
        afters = _issue_afters(added, issue_id)
        if afters and not any(change in after or after in change for after in afters):
            return "unlinked_change", "审稿标出的改动没有落在问题 %s 申报的片段上" % issue_id, change[:80]
        novel = [after for after in afters if after not in original]
        if novel and change in original:
            return "unlinked_change", "审稿引用的是原稿里已有的文字，不是问题 %s 的改动" % issue_id, change[:80]
        if not afters and change in original:
            return "unlinked_change", "审稿引用的是原稿里已有的文字，不是问题 %s 的改动" % issue_id, change[:80]
    return "", "", ""


def _cited_in_candidate(evidence, candidate, minimum=4):
    """The review must point at text that is actually in the candidate."""
    evidence = str(evidence or "").strip()
    candidate = str(candidate or "")
    if not evidence:
        return []
    found = []
    if len(evidence) >= minimum and evidence in candidate:
        found.append(evidence)
    for match in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9]{%d,}" % minimum, evidence):
        token = match.group(0)
        if token in candidate and token not in found:
            found.append(token)
    return found


def _cover_shared(text, source, covered):
    source = str(source or "")
    index = 0
    while index < len(text):
        length = 0
        while index + length < len(text) and text[index:index + length + 1] in source:
            length += 1
        if length >= 2:
            for cursor in range(index, index + length):
                covered[cursor] = True
            index += length
        else:
            index += 1


def _cover_literal(text, span, covered):
    span = str(span or "")
    if not span:
        return
    start = 0
    while True:
        at = text.find(span, start)
        if at < 0:
            return
        for cursor in range(at, at + len(span)):
            covered[cursor] = True
        start = at + len(span)


def _uncovered_text(candidate, original, requirements, spans):
    covered = [False] * len(candidate)
    _cover_shared(candidate, original, covered)
    _cover_shared(candidate, requirements, covered)
    for span in spans:
        _cover_literal(candidate, span, covered)
    for match in _META.finditer(candidate):
        for cursor in range(match.start(), match.end()):
            covered[cursor] = True
    for match in re.finditer(r"逻辑修正", candidate):
        for cursor in range(match.start() + 2, match.end()):
            covered[cursor] = True
    return "".join(
        char for index, char in enumerate(candidate)
        if not covered[index] and re.match(r"[\u4e00-\u9fffA-Za-z0-9]", char)
    )


def judge_candidate(requirements, original, candidate, issues, added, review, plan=None):
    """Deterministic gate after the independent review call. It does not rewrite."""
    review = review or {}
    plan = plan or {}
    if _META.search(str(candidate or "")):
        return _verdict("blocked", "候选正文带了审稿或机检说明", "meta", _META.search(str(candidate or "")).group(0))
    if not str(candidate or "").strip():
        return _verdict("blocked", "候选是空的", "empty", "")
    preference_ids = {item["id"] for item in issues if item.get("severity") == "preference"}
    accepted = []
    for beat in added or []:
        if not isinstance(beat, dict):
            continue
        if beat.get("issue_id") in preference_ids and (beat.get("before") or beat.get("after") or beat.get("text")):
            return _verdict("blocked", "偏好不能改情节", "preference", str(beat.get("text") or beat.get("after") or ""))
        if beat.get("before") or beat.get("after") or beat.get("declaration"):
            source, evidence, after = _bind_delta(beat, original, candidate, issues, plan)
            if source:
                return _verdict(
                    "blocked",
                    "无法确定这处改动是动作细化还是情节变更，需要人工确认：%s" % evidence,
                    source,
                    evidence,
                )
            accepted.append(after)
            continue
        text = str(beat.get("text") or "").strip()
        if text and _basis_ok(beat.get("basis"), requirements, original):
            accepted.append(text)
        elif text:
            return _verdict(
                "blocked",
                "这条申报只有正文，没有依据也没有声明，不能据此判断是不是新增情节，需要人工确认：%s" % text[:80],
                "missing_basis",
                text[:80],
            )
    needed = [item for item in issues if item.get("severity") in ("block", "suspect")]
    resolutions = {}
    for item in review.get("resolutions") or []:
        if isinstance(item, dict):
            resolutions[str(item.get("id") or "")] = item
    missing = []
    cited = []
    for item in needed:
        found = resolutions.get(item["id"]) or {}
        evidence = str(found.get("evidence") or "").strip()
        landed = _cited_in_candidate(evidence, candidate) if found.get("resolved") else []
        if not found.get("resolved") or not landed:
            missing.append(item["id"])
        else:
            cited.extend(_cited_in_candidate(evidence, candidate, minimum=2))
    if missing:
        return _verdict("blocked", "这些问题没有修改后证据：%s" % "、".join(missing), "missing_evidence", "、".join(missing))
    if review.get("unsupported"):
        return _verdict("blocked", "审稿指出无依据的新机制", "review_unsupported", "、".join(review.get("unsupported") or []))
    if review.get("new_contradictions"):
        shown = review.get("new_contradictions") or []
        return _verdict("blocked", "审稿指出新的因果、动机或连续性问题", "review_contradiction", json.dumps(shown, ensure_ascii=False))
    if not review.get("theme_ok") or not review.get("passed"):
        return _verdict("blocked", "独立审稿没有通过", "review_not_passed", "")
    linked, why, detail = _change_checks(issues, added, resolutions, original, candidate)
    if linked:
        return _verdict("blocked", why, linked, detail)
    uncovered = _uncovered_text(candidate, original, requirements, accepted + cited)
    if uncovered:
        return _verdict(
            "blocked",
            "无法确定这处改动是动作细化还是情节变更，需要人工确认：%s" % uncovered[:80],
            "unclassified",
            uncovered[:80],
        )
    evidence = accepted[0] if accepted else (cited[0] if cited else "")
    return _verdict("passed", "", "refinement" if accepted or cited else "review", evidence)


def run_script_repair(folder, requirements, complete, model=""):
    """Propose a candidate. The official script file is not written."""
    from AI.qwen_api.script_repair_graph import compile_script_repair_graph

    folder = Path(folder)
    before = official_script(folder)
    issues = issue_rows(folder)
    requirements = str(requirements or "")
    identity = budget_identity(before, requirements, issues)
    if not requirements.strip():
        existing = dict(load_candidate(folder) or {})
        if existing.get("attempts") or existing.get("validation") or existing.get("rejected_text") or existing.get("reason"):
            if official_script(folder) != before:
                raise RuntimeError("剧本修订改写了正式稿")
            return existing
        row = {
            "status": "blocked",
            "needs_human": True,
            "theme_checked": False,
            "reason": "缺少创作要求，不能判断主旨，需要人工确认",
            "validation": {"stage": "requirements", "source": "missing_requirements", "evidence": ""},
            "candidate": "",
            "attempts": 0,
            "source_hash": identity["source_hash"],
            "requirements_hash": identity["requirements_hash"],
            "issues_hash": identity["issues_hash"],
        }
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
    validation = {"stage": "graph", "source": status or "error", "evidence": reason}
    if status == "error":
        judged, why = "error", reason or "修订没有完成"
    elif status == "blocked" and not state.get("review_notes"):
        judged, why = "blocked", reason or "修订停住了"
        calls = list(state.get("calls") or [])
        validation = {"stage": calls[-1] if calls else "graph", "source": "graph", "evidence": why}
    else:
        judged, why, validation = judge_candidate(
            requirements,
            before,
            candidate,
            issues,
            state.get("added") or [],
            state.get("review_notes") or {},
            plan=state.get("revision_plan") or {},
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
        "locations": state.get("locations") or [],
        "review": state.get("review_notes") or {},
        "review_raw": str(state.get("review_raw") or "")[:4000],
        "locate_raw": str(state.get("locate_raw") or "")[:4000],
        "validation": validation,
        "needs_human": validation.get("source") in _HUMAN,
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
