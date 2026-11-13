"""Storyboard worker: generate → critique → field and contract checks.

Named gaps (``S01 缺时长``) go back to the generator. An empty board or a shot
list that disagrees with the manuscript stops for a person. The generator
repairs the named fields. It does not replace the whole board the way 「补全」 does.
"""

from typing import TypedDict

from Agents.checks import check_storyboard_draft
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
    strip_fence,
)
from Agents.workers.generator import run_generator

STAGE = "storyboard"
TASK = "storyboard"

GENERATOR_SYSTEM = "\n".join((
    "你是分镜生成员。你只写一份完整分镜，放在 storyboard 字符串。",
    "审查员只交报告，不会替你改稿。机检查空稿、镜号和字段是否齐。",
    "分镜怎么写，以技能原文为准。",
    "机检点名缺哪一镜的哪个字段，就只补那个字段。其他镜原样保留。",
    "不要把整份分镜换成一次「补全」。不要写预演故事板。",
    "审查报告里如果夹了改写稿，那份稿作废。",
))

CRITIC_SYSTEM = "\n".join((
    "你是分镜审查员。你只审查，只交报告。",
    "禁止改写分镜，禁止输出 storyboard、draft、rewrite、text。",
    "整段回复只能是一个 JSON 对象：",
    '{"verdict":"accept 或 revise","issues":[{"target":"S01","severity":"blocking 或 note","detail":"一句话"}]}',
    "没有要改的地方时 verdict 为 accept，issues 为空数组。",
    "detail 只指出缺的字段，不写替换后的整镜。",
    "你不是机检。镜号和字段由机检判定。",
))


class StoryboardState(TypedDict, total=False):
    stage: str
    task: str
    source: str
    opening: str
    draft: str
    locks: dict
    intake: list
    known_ids: list
    assets: list
    shots: list
    supplied_shots: list
    hold: str
    skill_pack: str
    partials: list
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


def _looks_like_board(text):
    raw = str(text or "")
    return "## S" in raw or "\"storyboard\"" in raw or '"storyboard"' in raw


def absorb_storyboard(raw):
    data = extract_json_object(raw)
    if isinstance(data, dict) and isinstance(data.get("shots"), list):
        return {"supplied_shots": data.get("shots")}
    return {}


def extract_storyboard(raw, previous=""):
    data = extract_json_object(raw)
    text = ""
    if isinstance(data, dict):
        text = str(data.get("storyboard") or "").strip()
        if not text:
            candidate = str(data.get("text") or "").strip()
            if candidate.startswith("##"):
                text = candidate
    if not text:
        cleaned = strip_fence(raw)
        if "## S" in cleaned or cleaned.startswith("##"):
            text = cleaned.strip()
    if not text:
        return str(previous or "")
    return text


def generator_messages(state):
    system = {"role": "system", "content": GENERATOR_SYSTEM}
    if int(state.get("attempt") or 0) < 1 and str(state.get("opening") or "").strip():
        return [system, {"role": "user", "content": state.get("opening")}]
    if int(state.get("attempt") or 0) < 1:
        return [system, {"role": "user", "content": "任务：分镜\n\n剧本：\n%s" % str(state.get("source") or "").strip()}]
    user = revision_brief(
        STAGE,
        "任务：分镜",
        state.get("draft") or "",
        state.get("critic_report"),
        state.get("machine_report"),
    )
    return [system, {"role": "user", "content": user}]


def critic_messages(state):
    sections = ["待审分镜：\n%s" % str(state.get("draft") or "").strip()]
    reference = skill_reference(state.get("skill_pack"))
    if reference:
        sections.insert(0, reference)
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def compile_storyboard_graph(complete, checker=None):
    check = checker or check_storyboard_draft

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
        draft = extract_storyboard(raw, previous)
        note = ""
        if previous.strip() and draft.strip() == previous.strip() and attempt > 1:
            note = "revision_kept_previous"
        update = {"attempt": attempt, "draft": draft, "error": "", "generator_note": note}
        update.update(absorb_storyboard(raw))
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
        return critic_only(parse_critic(raw, _looks_like_board))

    def machine_check(state):
        try:
            produced = check(state.get("draft") or "", state)
        except Exception as exc:
            produced = {
                "ok": False,
                "fatal": "checker_unavailable",
                "exit_code": 2,
                "errors": ["机检没有跑起来：%s" % exc],
                "warnings": [],
                "report": "机检没有跑起来：%s" % exc,
                "needs_human": False,
            }
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

    return compile_worker(StoryboardState, generate, critique, machine_check)


def run_storyboard_loop(
    script,
    *,
    locks=None,
    intake=None,
    known_ids=None,
    assets=None,
    shots=None,
    hold="",
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
    """Run the storyboard loop once. ``complete`` and ``checker`` are test seams."""
    state = base_state(
        STAGE,
        TASK,
        script,
        opening=opening,
        prior_draft=prior_draft,
        model=model,
        max_attempts=max_attempts,
        extra={
            "locks": dict(locks or {}),
            "intake": list(intake or []),
            "known_ids": list(known_ids or []),
            "assets": list(assets or []),
            "shots": list(shots or []),
            "hold": str(hold or ""),
            "skill_pack": str(skill_pack or ""),
        },
    )
    if not str(script or "").strip() and not str(opening or "").strip() and not str(prior_draft or "").strip():
        state["status"] = "fail"
        state["stop_reason"] = "missing_script"
        state["passed"] = False
        return result_from_state(state, draft_key="storyboard")
    try:
        state["skill_pack"] = resolve_skill_pack(
            skill_pack,
            context=opening or script,
            stage="P3",
        )
    except RuntimeError as exc:
        return result_from_state(missing_skill_state(state, exc), draft_key="storyboard")
    app = compile_storyboard_graph(
        complete or bound_complete(model, client, timeout),
        checker=checker,
    )
    final = run_compiled(app, state, missing_reason="missing_script", require_text=False)
    return result_from_state(final, draft_key="storyboard", extra_keys=("supplied_shots",))
