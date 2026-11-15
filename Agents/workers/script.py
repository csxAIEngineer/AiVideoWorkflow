"""Script worker: generate → machine-check critic → semantic-review critic.

The machine-check critic is the existing hard gate: an empty draft, an
unverified image claim, or an invented upload id. FAIL retries. Warnings
alone still pass. Semantic review runs only after that gate passes. It
reviews and does not rewrite. A blocked or failed semantic report goes back
to the generator. Preferences and suspects do not fail the loop. This loop
does not add a style-review gate and does not run ``validate_prompt``.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from Agents.checks import check_script_draft
from Agents.text import resolved_model
from Agents.worker import (
    DEFAULT_MAX_ATTEMPTS,
    base_state,
    bound_complete,
    critic_only,
    decide,
    extract_json_object,
    missing_skill_state,
    normalize_machine,
    parse_critic,
    resolve_skill_pack,
    result_from_state,
    revision_brief,
    run_compiled,
    strip_fence,
)
from Agents.workers.critic import critic_user
from Agents.workers.generator import run_generator

STAGE = "script"
TASK = "script"

GENERATOR_SYSTEM = "\n".join((
    "你是剧本生成员。你只写剧本。",
    "审查员只交报告，不会替你改稿。机检只查空稿、没看见图却写已核验、以及编造的上传图编号。",
    "怎么写剧本，以技能原文为准。不要自己加文风门，也不要跑封装稿的 validate_prompt。剧本还没有封装。",
    "整段回复是一个 JSON 对象。正式剧本放在 script，标题放在 title。圆桌意见放在 internal。",
    "机检失败或语义审查失败时，只改点名的问题。没有点名的段落原样保留。",
    "审查报告里如果夹了改写稿，那份稿作废。",
))

CRITIC_SYSTEM = "\n".join((
    "你是剧本审查员。你只审查，只交报告。",
    "禁止改写剧本，禁止输出 script、draft、rewrite、text。",
    "整段回复只能是一个 JSON 对象：",
    '{"verdict":"accept 或 revise","issues":[{"target":"位置","severity":"blocking 或 note","detail":"一句话"}]}',
    "没有要改的地方时 verdict 为 accept，issues 为空数组。",
    "detail 只指出问题，不写替换后的正文。",
    "评判标准只是机检技能：空稿、没看见图却写已核验、编造的上传图编号。",
    "不要用文风或写作技能审查。机检会在你之后再跑。你只交报告。",
))


class ScriptState(TypedDict, total=False):
    stage: str
    task: str
    source: str
    opening: str
    draft: str
    title: str
    internal: str
    partials: list
    locks: dict
    intake: list
    skill_pack: str
    partials: list
    attempt: int
    max_attempts: int
    model: str
    critic_report: dict
    machine_report: dict
    semantic_report: dict
    review_context: str
    history: list
    status: str
    stop_reason: str
    passed: bool
    generator_note: str
    error: str


def _looks_like_script(text):
    raw = str(text or "")
    if "\"script\"" in raw or '"script"' in raw:
        return True
    return len(raw) > 80 and "verdict" not in raw and ("场" in raw or "剧本" in raw)


def extract_script(raw, previous=""):
    data = extract_json_object(raw)
    text = ""
    if isinstance(data, dict):
        text = str(data.get("script") or "").strip()
    if not text:
        cleaned = strip_fence(raw)
        if cleaned and not cleaned.startswith("{"):
            text = cleaned.strip()
    if not text:
        return str(previous or "")
    return text


def absorb_script(raw):
    data = extract_json_object(raw)
    if not isinstance(data, dict):
        return {}
    payload = {}
    title = str(data.get("title") or "").strip()
    if title:
        payload["title"] = title
    internal = str(data.get("internal") or "").strip()
    if internal:
        payload["internal"] = internal
    partials = data.get("partials")
    if isinstance(partials, list):
        payload["partials"] = partials
    return payload


def _semantic_retry(state):
    """True when the machine check passed and semantic review must be repaired."""
    machine = state.get("machine_report") or {}
    semantic = state.get("semantic_report") or {}
    if not machine.get("ok"):
        return False
    return str(semantic.get("status") or "") in ("blocked", "failed")


def semantic_revision_brief(draft, report):
    """Repair text for a blocked or failed semantic review. Not a new draft."""
    report = report or {}
    sections = [
        "任务：剧本",
        "任务：建议修补",
        "阶段：script",
        "机检已过。语义审查未通过。只改审查点名的问题，没有点名的段落原样保留。",
        "不要整篇另起一套。语义审查只交报告，没有改稿权。报告里夹带的稿作废。",
        "偏好不阻断。单独的可疑项也不当作这次的失败项。阻断和审查失败要处理。",
    ]
    body = str(draft or "").strip()
    if body:
        sections.append("上一稿：\n%s" % body)
    reason = str(report.get("reason") or "").strip()
    if not reason:
        from end.script_review import format_issues

        reason = format_issues(report.get("issues") or [])
    if reason:
        sections.append("语义审查报告（只读，按它改）：\n%s" % reason)
    return "\n\n".join(sections)


def generator_messages(state):
    system = {"role": "system", "content": GENERATOR_SYSTEM}
    if int(state.get("attempt") or 0) < 1 and str(state.get("opening") or "").strip():
        return [system, {"role": "user", "content": state.get("opening")}]
    if int(state.get("attempt") or 0) < 1:
        story = str(state.get("source") or "").strip()
        return [system, {"role": "user", "content": "任务：剧本\n\n用户原文\n%s" % story}]
    if _semantic_retry(state):
        return [system, {"role": "user", "content": semantic_revision_brief(
            state.get("draft") or "",
            state.get("semantic_report"),
        )}]
    user = revision_brief(
        STAGE,
        "任务：剧本",
        state.get("draft") or "",
        state.get("critic_report"),
        state.get("machine_report"),
    )
    return [system, {"role": "user", "content": user}]


def critic_messages(state):
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": critic_user(
            TASK,
            "阶段：剧本审查。只交 JSON 报告，不要改稿。",
            "待审稿：\n%s" % str(state.get("draft") or "").strip(),
        )},
    ]


_REWRITE_KEYS = ("script", "draft", "rewrite", "rewritten", "text", "replacement", "revised", "content")


def default_script_reviewer(draft, state):
    """Semantic review through the existing rubric. Tests can replace this."""
    from end.script_review import review_script_text

    context = str((state or {}).get("review_context") or (state or {}).get("source") or "")
    return review_script_text(draft, context)


def _semantic_status(report):
    """Blocked and failed stop the loop. Suspects and preferences do not."""
    report = report or {}
    issues = [item for item in (report.get("issues") or []) if isinstance(item, dict)]
    severities = {str(item.get("severity") or "") for item in issues}
    status = str(report.get("status") or "")
    if "block" in severities or status == "blocked":
        return "blocked", issues
    if status == "failed":
        return "failed", issues
    if "suspect" in severities or status == "suspect":
        return "suspect", issues
    if status == "passed":
        return "passed", issues
    return "failed", issues


def _clean_semantic(report):
    status, issues = _semantic_status(report if isinstance(report, dict) else {})
    source = report if isinstance(report, dict) else {}
    cleaned = {
        "status": status,
        "reason": str(source.get("reason") or ""),
        "issues": issues,
        "script_hash": str(source.get("script_hash") or ""),
        "context_hash": str(source.get("context_hash") or ""),
    }
    if source.get("cache_key"):
        cleaned["cache_key"] = source.get("cache_key")
    if source.get("attempts"):
        cleaned["attempts"] = source.get("attempts")
    for key in _REWRITE_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _route_after_machine(state):
    """Retry a machine FAIL. A pass continues to semantic review. A hard stop ends."""
    status, _reason = decide(state, state.get("machine_report") or {})
    if status == "running":
        return "generate"
    if status == "pass":
        return "semantic_review"
    return "halt"


def _route_after_semantic(state):
    if state.get("status") == "running":
        return "generate"
    return "halt"


def compile_script_graph(complete, checker=None, reviewer=None):
    check = checker or check_script_draft
    review = reviewer or default_script_reviewer

    def generate(state):
        attempt = int(state.get("attempt") or 0) + 1
        brief = generator_messages(state)[-1]["content"]
        try:
            produced = run_generator(
                TASK,
                brief,
                role=GENERATOR_SYSTEM,
                complete=complete,
                partials=state.get("partials") or [],
                repair_text=brief if attempt > 1 else "",
            )
        except Exception as exc:
            return {
                "attempt": attempt,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "generator_note": "model_error",
            }
        raw = produced.get("raw") or ""
        previous = str(state.get("draft") or "")
        draft = extract_script(raw, previous)
        note = "revision_kept_previous" if previous.strip() and draft.strip() == previous.strip() and attempt > 1 else ""
        update = {"attempt": attempt, "draft": draft, "error": "", "generator_note": note}
        update.update(absorb_script(raw))
        update["partials"] = produced.get("partials") or []
        return update

    def critique(state):
        if state.get("generator_note") == "model_error":
            return critic_only({
                "verdict": "revise",
                "issues": [{
                    "target": "",
                    "severity": "note",
                    "detail": "生成没有返回新稿：%s" % (state.get("error") or ""),
                }],
                "stripped_rewrite": False,
            })
        try:
            raw = complete("critic", critic_messages(state))
        except Exception as exc:
            return critic_only({
                "verdict": "revise",
                "issues": [{
                    "target": "",
                    "severity": "note",
                    "detail": "审查调用失败：%s" % exc,
                }],
                "stripped_rewrite": False,
            })
        return critic_only(parse_critic(raw, _looks_like_script))

    def machine_check(state):
        try:
            produced = check(state.get("draft") or "", state)
        except Exception as exc:
            produced = report_unavailable(exc)
        produced = normalize_machine(produced)
        status, reason = decide(state, produced)
        history = list(state.get("history") or [])
        history.append({
            "attempt": int(state.get("attempt") or 0),
            "draft": str(state.get("draft") or ""),
            "critic_report": dict(state.get("critic_report") or {}),
            "machine_report": produced,
            "generator_note": str(state.get("generator_note") or ""),
        })
        return {
            "machine_report": produced,
            "status": status,
            "stop_reason": reason,
            "passed": status == "pass",
            "history": history,
        }

    def semantic_review(state):
        """Review only. The returned update has no draft."""
        try:
            produced = review(str(state.get("draft") or ""), dict(state))
        except Exception as exc:
            produced = {
                "status": "failed",
                "reason": "语义审查没有完成：%s" % exc,
                "issues": [],
            }
        report = _clean_semantic(produced)
        failing = report["status"] in ("blocked", "failed")
        attempt = int(state.get("attempt") or 0)
        budget = int(state.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
        if failing and attempt < budget:
            status, reason, passed = "running", "", False
        elif failing:
            status = "fail"
            reason = "semantic_blocked" if report["status"] == "blocked" else "semantic_failed"
            passed = False
        else:
            status, reason, passed = "pass", "script_gate_pass", True
        history = list(state.get("history") or [])
        entry = {
            "attempt": attempt,
            "draft": str(state.get("draft") or ""),
            "critic_report": dict(state.get("critic_report") or {}),
            "machine_report": dict(state.get("machine_report") or {}),
            "semantic_report": report,
            "generator_note": str(state.get("generator_note") or ""),
        }
        if history and history[-1].get("attempt") == attempt:
            history[-1] = entry
        else:
            history.append(entry)
        update = {
            "semantic_report": report,
            "status": status,
            "stop_reason": reason,
            "passed": passed,
            "history": history,
        }
        if "draft" in update or "script" in update:
            raise RuntimeError("语义审查不能改稿")
        return update

    graph = StateGraph(ScriptState)
    graph.add_node("generate", generate)
    graph.add_node("critique", critique)
    graph.add_node("machine_check", machine_check)
    graph.add_node("semantic_review", semantic_review)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "critique")
    graph.add_edge("critique", "machine_check")
    graph.add_conditional_edges(
        "machine_check",
        _route_after_machine,
        {"generate": "generate", "semantic_review": "semantic_review", "halt": END},
    )
    graph.add_conditional_edges(
        "semantic_review",
        _route_after_semantic,
        {"generate": "generate", "halt": END},
    )
    return graph.compile()


def report_unavailable(exc):
    return {
        "ok": False,
        "fatal": "checker_unavailable",
        "exit_code": 2,
        "errors": ["机检没有跑起来：%s" % exc],
        "warnings": [],
        "report": "机检没有跑起来：%s" % exc,
        "needs_human": False,
    }


def run_script_loop(
    story,
    *,
    locks=None,
    intake=None,
    opening="",
    prior_draft="",
    skill_pack="",
    model=None,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    client=None,
    timeout=None,
    complete=None,
    checker=None,
    reviewer=None,
):
    """Run the script loop once. ``complete``, ``checker``, and ``reviewer`` are test seams."""
    state = base_state(
        STAGE,
        TASK,
        story,
        opening=opening,
        prior_draft=prior_draft,
        model=model,
        max_attempts=max_attempts,
        extra={
            "locks": dict(locks or {}),
            "intake": list(intake or []),
            "skill_pack": str(skill_pack or ""),
            "semantic_report": {},
            "review_context": "",
        },
    )
    if not str(story or "").strip() and not str(opening or "").strip() and not str(prior_draft or "").strip():
        state["status"] = "fail"
        state["stop_reason"] = "missing_script"
        state["passed"] = False
        return _result(state)
    try:
        state["skill_pack"] = resolve_skill_pack(
            skill_pack,
            context=opening or story,
            stage="P1",
        )
    except RuntimeError as exc:
        return _result(missing_skill_state(state, exc))
    app = compile_script_graph(
        complete or bound_complete(model, client, timeout),
        checker=checker,
        reviewer=reviewer,
    )
    final = run_compiled(app, state, missing_reason="missing_script", require_text=False)
    return _result(final)


def _result(state):
    payload = result_from_state(
        state,
        draft_key="script",
        extra_keys=("title", "internal", "partials"),
    )
    payload["title"] = state.get("title") or ""
    payload["internal"] = state.get("internal") or ""
    payload["partials"] = list(state.get("partials") or [])
    payload["semantic_report"] = dict(state.get("semantic_report") or {})
    payload["model"] = state.get("model") or resolved_model(None)
    return payload
