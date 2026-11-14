"""Shared generate → critique → machine_check loop.

The critic reports. It does not rewrite. Only the generator writes the draft.
A failed machine check returns to generate while attempts remain. Warnings do
not fail the gate. A ``needs_human`` report stops without another generate.
"""

import json

from langgraph.graph import END, START, StateGraph

from Agents.text import complete_text, resolved_model

DEFAULT_MAX_ATTEMPTS = 3
_REWRITE_KEYS = (
    "draft",
    "rewrite",
    "rewritten",
    "patched",
    "patch",
    "text",
    "replacement",
    "revised",
    "revised_draft",
    "markdown",
    "content",
    "script",
    "storyboard",
    "feed",
    "image_prompts",
    "seam_ledger",
)


def empty_critic_report():
    return {"verdict": "revise", "issues": [], "stripped_rewrite": False}


def empty_machine_report():
    return {
        "ok": False,
        "fatal": "",
        "exit_code": None,
        "errors": [],
        "warnings": [],
        "stats": {},
        "blocks": 0,
        "report": "",
        "needs_human": False,
    }


def compile_worker(state_type, generate, critique, machine_check):
    """Wire generate → critique → machine_check, with a retry edge."""
    graph = StateGraph(state_type)
    graph.add_node("generate", generate)
    graph.add_node("critique", critique)
    graph.add_node("machine_check", machine_check)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "critique")
    graph.add_edge("critique", "machine_check")
    graph.add_conditional_edges(
        "machine_check",
        route_after_check,
        {"generate": "generate", "halt": END},
    )
    return graph.compile()


def route_after_check(state):
    """Return to generate only while the machine check fails and budget remains."""
    status, _reason = decide(state, state.get("machine_report") or {})
    if status == "running":
        return "generate"
    return "halt"


def decide(state, report, default_max=DEFAULT_MAX_ATTEMPTS):
    report = report or {}
    if report.get("ok"):
        return "pass", "machine_check_pass"
    if report.get("needs_human"):
        return "fail", "needs_human"
    if report.get("fatal"):
        return "fail", str(report.get("fatal"))
    attempt = int(state.get("attempt") or 0)
    budget = int(state.get("max_attempts") or default_max)
    if attempt >= budget:
        return "fail", "budget_exhausted"
    return "running", ""


def critic_only(report):
    """The critique node may update the report and nothing else."""
    update = {"critic_report": report}
    leaked = [
        key
        for key in ("draft", "script", "storyboard", "feed", "image_prompts", "title", "partials")
        if key in update
    ]
    if leaked:
        raise RuntimeError("审查节点不能改稿：%s" % "、".join(leaked))
    return update


def normalize_machine(report):
    base = empty_machine_report()
    if not isinstance(report, dict):
        base["errors"] = ["机检没有返回报告"]
        base["report"] = base["errors"][0]
        return base
    base.update(report)
    base["ok"] = bool(report.get("ok")) and not list(report.get("errors") or [])
    base["errors"] = [str(item) for item in (report.get("errors") or [])]
    base["warnings"] = [str(item) for item in (report.get("warnings") or [])]
    base["fatal"] = str(report.get("fatal") or "")
    base["needs_human"] = bool(report.get("needs_human"))
    if base["errors"]:
        base["ok"] = False
    return base


def parse_critic(raw, looks_like_draft):
    """Keep issues. Drop any rewritten draft the model tried to return."""
    data = extract_json_object(raw)
    stripped = bool(looks_like_draft(raw))
    if not isinstance(data, dict):
        return {
            "verdict": "revise",
            "issues": [{
                "target": "",
                "severity": "blocking",
                "detail": "审查员没有返回审查报告。",
            }],
            "stripped_rewrite": stripped,
        }
    if any(key in data for key in _REWRITE_KEYS):
        stripped = True
    issues = []
    for item in data.get("issues") or []:
        if isinstance(item, str):
            detail, stripped = _clean_detail(item, stripped, looks_like_draft)
            if detail:
                issues.append({"target": "", "severity": "blocking", "detail": detail})
            continue
        if not isinstance(item, dict):
            continue
        detail, stripped = _clean_detail(
            item.get("detail") or item.get("message") or "",
            stripped,
            looks_like_draft,
        )
        if not detail:
            continue
        severity = item.get("severity") or "blocking"
        if severity not in ("blocking", "note"):
            severity = "blocking"
        target = str(item.get("target") or item.get("figure") or "")
        issues.append({"target": target, "severity": severity, "detail": detail})
    blocking = any(issue["severity"] == "blocking" for issue in issues)
    verdict = data.get("verdict")
    if verdict not in ("accept", "revise"):
        verdict = "revise" if blocking else "accept"
    if verdict == "accept" and blocking:
        verdict = "revise"
    if verdict == "revise" and not blocking:
        verdict = "accept"
    return {"verdict": verdict, "issues": issues, "stripped_rewrite": stripped}


def format_critic(report):
    report = report or {}
    lines = ["verdict: %s" % (report.get("verdict") or "revise")]
    for issue in report.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        lines.append(
            "[%s] %s %s" % (
                issue.get("severity") or "blocking",
                issue.get("target") or "",
                issue.get("detail") or "",
            )
        )
    if report.get("stripped_rewrite"):
        lines.append("审查员附带了改写稿，已丢弃。不要寻找或采用那份稿。")
    return "\n".join(lines).strip()


def format_machine(report):
    report = report or {}
    lines = []
    for error in report.get("errors") or []:
        lines.append(str(error))
    for warning in report.get("warnings") or []:
        lines.append("WARN %s" % warning)
    if not lines:
        text = str(report.get("report") or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def revision_brief(stage, task_line, draft, critic_report, machine_report):
    """What the next generate sees. The critic's rewrite is not in here."""
    sections = [
        "任务：建议修补",
        "阶段：%s" % stage,
        "机检没过。只改点名的失败项，没有点名的段落原样保留。",
        "不要整篇另起一套。审查员没有改稿权，审查报告里夹带的稿作废。",
    ]
    if stage == "script":
        sections.insert(0, task_line)
    if stage == "storyboard":
        sections.append("按失败项补上点名的字段。不要把整份分镜换成一次「补全」。")
    if stage == "feed":
        sections.append(
            "时长不够就按分镜时长拆单元写回，禁止把超过 15 秒的跨度钳成 15，禁止缩短总时长或删对白。"
        )
    if stage == "seam":
        sections.append("按失败项改点名的那条段缝。不要把 ① 当成每条缝的默认。")
    body = str(draft or "").strip()
    if body:
        sections.append("上一稿：\n%s" % body)
    critic = format_critic(critic_report)
    if critic:
        sections.append("审查报告（只读）：\n%s" % critic)
    machine = format_machine(machine_report)
    if machine:
        sections.append("机检失败与警告（FAIL 必须清掉。只有警告、没有 FAIL 不算失败）：\n%s" % machine)
    return "\n\n".join(sections)


def extract_json_object(raw):
    text = strip_fence(raw)
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def strip_fence(raw):
    cleaned = str(raw or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def resolve_skill_pack(skill_pack, *, template=None, context="", stage="P4"):
    """Use the caller pack, or load the stage skill. Empty rules are not allowed."""
    pack = str(skill_pack or "").strip()
    if pack:
        return pack
    from AI.skills.registry import load_skill_pack

    try:
        loaded = load_skill_pack(template, context=str(context or ""), stage=stage)
    except Exception as exc:
        raise RuntimeError("缺少技能原文：%s" % exc)
    loaded = str(loaded or "").strip()
    if not loaded:
        raise RuntimeError("缺少技能原文")
    return loaded


def skill_system(role, skill_pack):
    """Role wrapper plus the loaded skill. The skill text is the domain rules."""
    pack = str(skill_pack or "").strip()
    if not pack:
        raise RuntimeError("缺少技能原文")
    return "%s\n\n技能原文（领域规则只以这份为准）：\n%s" % (str(role or "").strip(), pack)


def skill_reference(skill_pack):
    """Read-only skill text for the critic. It is not a draft."""
    pack = str(skill_pack or "").strip()
    if not pack:
        return ""
    return "技能原文（只读。不要改稿，不要把原文交成一份新稿）：\n%s" % pack


def missing_skill_state(state, error):
    state["status"] = "fail"
    state["stop_reason"] = "missing_skill_pack"
    state["passed"] = False
    state["error"] = str(error or "缺少技能原文")
    return state


def bound_complete(model, client, timeout):
    def complete(role, messages):
        return complete_text(role, messages, model=model, client=client, timeout=timeout)

    return complete


def base_state(stage, task, source, *, opening="", prior_draft="", model=None, max_attempts=DEFAULT_MAX_ATTEMPTS, extra=None):
    budget = int(max_attempts)
    if budget < 1:
        raise ValueError("max_attempts must be >= 1")
    state = {
        "stage": stage,
        "task": task,
        "source": str(source or ""),
        "opening": str(opening or ""),
        "draft": str(prior_draft or ""),
        "attempt": 0,
        "max_attempts": budget,
        "model": resolved_model(model),
        "critic_report": empty_critic_report(),
        "machine_report": empty_machine_report(),
        "history": [],
        "status": "running",
        "stop_reason": "",
        "passed": False,
        "generator_note": "",
        "error": "",
    }
    for key, value in (extra or {}).items():
        state[key] = value
    return state


def result_from_state(state, *, draft_key, extra_keys=()):
    draft = str(state.get("draft") or "")
    payload = {
        "stage": state.get("stage") or "",
        "task": state.get("task") or "",
        "status": state.get("status") or "fail",
        "passed": bool(state.get("passed")),
        "stop_reason": state.get("stop_reason") or "",
        "draft": draft,
        draft_key: draft,
        "attempts": int(state.get("attempt") or 0),
        "max_attempts": int(state.get("max_attempts") or DEFAULT_MAX_ATTEMPTS),
        "model": state.get("model") or resolved_model(None),
        "critic_report": dict(state.get("critic_report") or empty_critic_report()),
        "machine_report": dict(state.get("machine_report") or empty_machine_report()),
        "history": list(state.get("history") or []),
        "generator_note": state.get("generator_note") or "",
        "error": state.get("error") or "",
    }
    for key in extra_keys:
        if key in state:
            payload[key] = state.get(key)
    return payload


def run_compiled(app, state, *, missing_reason, require_text=True):
    if require_text and not str(state.get("source") or "").strip() and not str(state.get("opening") or "").strip() and not str(state.get("draft") or "").strip():
        state["status"] = "fail"
        state["stop_reason"] = missing_reason
        state["passed"] = False
        return state
    return app.invoke(
        state,
        config={"recursion_limit": int(state.get("max_attempts") or DEFAULT_MAX_ATTEMPTS) * 4 + 4},
    )


def _clean_detail(detail, stripped, looks_like_draft):
    text = str(detail or "").strip()
    if looks_like_draft(text):
        return "审查意见夹带了稿件正文，已丢弃。", True
    if len(text) > 500:
        text = text[:500].rstrip()
    return text, stripped
