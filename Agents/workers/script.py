"""Script worker: generate → critique → the existing script machine checks.

The hard gate is an empty draft, an unverified image claim, or an invented
upload id. Taste, polish, and the roundtable stay with the person. This loop
does not add a style-review gate and does not run ``validate_prompt``.
"""

from typing import TypedDict

from Agents.checks import check_script_draft
from Agents.text import resolved_model
from Agents.worker import (
    DEFAULT_MAX_ATTEMPTS,
    base_state,
    bound_complete,
    compile_worker,
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
    skill_reference,
    skill_system,
    strip_fence,
)

STAGE = "script"
TASK = "script"

GENERATOR_SYSTEM = "\n".join((
    "你是剧本生成员。你只写剧本。",
    "审查员只交报告，不会替你改稿。机检只查空稿、没看见图却写已核验、以及编造的上传图编号。",
    "怎么写剧本，以技能原文为准。不要自己加文风门，也不要跑投喂稿的 validate_prompt。剧本还没有封装。",
    "整段回复是一个 JSON 对象。正式剧本放在 script，标题放在 title。圆桌意见放在 internal。",
    "机检失败时只改点名的句子。没有点名的段落原样保留。",
    "审查报告里如果夹了改写稿，那份稿作废。",
))

CRITIC_SYSTEM = "\n".join((
    "你是剧本审查员。你只审查，只交报告。",
    "禁止改写剧本，禁止输出 script、draft、rewrite、text。",
    "整段回复只能是一个 JSON 对象：",
    '{"verdict":"accept 或 revise","issues":[{"target":"位置","severity":"blocking 或 note","detail":"一句话"}]}',
    "没有要改的地方时 verdict 为 accept，issues 为空数组。",
    "detail 只指出问题，不写替换后的正文。",
    "你不是机检。空稿、未核验声称和编造上传编号由机检判定。",
    "不要用文风意见挡住机检已经通过的稿。",
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
    attempt: int
    max_attempts: int
    model: str
    critic_report: dict
    machine_report: dict
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


def generator_messages(state):
    system = {"role": "system", "content": skill_system(GENERATOR_SYSTEM, state.get("skill_pack"))}
    if int(state.get("attempt") or 0) < 1 and str(state.get("opening") or "").strip():
        return [system, {"role": "user", "content": state.get("opening")}]
    if int(state.get("attempt") or 0) < 1:
        story = str(state.get("source") or "").strip()
        return [system, {"role": "user", "content": "任务：剧本\n\n用户原文\n%s" % story}]
    user = revision_brief(
        STAGE,
        "任务：剧本",
        state.get("draft") or "",
        state.get("critic_report"),
        state.get("machine_report"),
    )
    return [system, {"role": "user", "content": user}]


def critic_messages(state):
    sections = [
        "阶段：剧本审查。只交 JSON 报告，不要改稿。",
    ]
    reference = skill_reference(state.get("skill_pack"))
    if reference:
        sections.append(reference)
    sections.append("待审稿：\n%s" % str(state.get("draft") or "").strip())
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def compile_script_graph(complete, checker=None):
    check = checker or check_script_draft

    def generate(state):
        attempt = int(state.get("attempt") or 0) + 1
        try:
            raw = complete("generator", generator_messages(state))
        except Exception as exc:
            return {
                "attempt": attempt,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "generator_note": "model_error",
            }
        previous = str(state.get("draft") or "")
        draft = extract_script(raw, previous)
        note = "revision_kept_previous" if previous.strip() and draft.strip() == previous.strip() and attempt > 1 else ""
        update = {"attempt": attempt, "draft": draft, "error": "", "generator_note": note}
        update.update(absorb_script(raw))
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

    return compile_worker(ScriptState, generate, critique, machine_check)


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
):
    """Run the script loop once. ``complete`` and ``checker`` are test seams."""
    state = base_state(
        STAGE,
        TASK,
        story,
        opening=opening,
        prior_draft=prior_draft,
        model=model,
        max_attempts=max_attempts,
        extra={"locks": dict(locks or {}), "intake": list(intake or []), "skill_pack": str(skill_pack or "")},
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
    payload["model"] = state.get("model") or resolved_model(None)
    return payload
