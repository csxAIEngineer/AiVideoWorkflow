"""Feed-packing worker: generate → critique → 投喂机检.

``check_feed`` / ``validate_prompt`` (H3, by segment) is the hard gate. The
duration rule is a hard gate on top of that script: merged shots may share a
unit, the total may not shrink below the storyboard, dialogue may not be
dropped, and a span over 15s is split instead of clamped. The voiceover patch
attaches the official closed-lips phrase to that unit's voiceover.
"""

import importlib
from typing import TypedDict

from Agents.checks import check_feed_draft
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

STAGE = "feed"
TASK = "feed"

GENERATOR_SYSTEM = "\n".join((
    "你是提示词封装的生成员。你只写已经封装的投喂稿，放在 feed 字符串。",
    "投喂机检是这一环的硬门。审查员只交报告，不会替你改稿。",
    "H3 怎么写，以技能原文为准。不要跑在还没封装的剧本或分镜上。",
    "可以把分镜合并进同一个投喂单元，但各单元 duration 相加不得短于分镜总时长。",
    "对白和旁白必须原样留下。禁止为了压进 15 秒删情节或对白。",
    "一个单元若按分镜时长会超过 15 秒，拆成下一个单元，禁止把声明时长钳成 15。",
    "画外音的闭口证据必须紧跟这一单元里的画外音，不要接到出声对白后面，也不要接到文末。",
    "审查报告里夹带的稿作废。",
))

CRITIC_SYSTEM = "\n".join((
    "你是投喂稿审查员。你只审查，只交报告。",
    "禁止改写投喂稿，禁止输出 feed、draft、rewrite、text。",
    "整段回复只能是一个 JSON 对象：",
    '{"verdict":"accept 或 revise","issues":[{"target":"投喂单元","severity":"blocking 或 note","detail":"一句话"}]}',
    "没有要改的地方时 verdict 为 accept，issues 为空数组。",
    "detail 只指出问题，不写替换后的单元。",
    "你不是机检。H3 字段、时长有没有被缩短、画外音闭口，由机检判定。",
))


class FeedState(TypedDict, total=False):
    stage: str
    task: str
    source: str
    opening: str
    draft: str
    locks: dict
    storyboard: str
    shots: list
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


def _looks_like_feed(text):
    raw = str(text or "")
    return "投喂单元" in raw or "subject_definitions" in raw or '"feed"' in raw


def extract_feed(raw, previous=""):
    data = extract_json_object(raw)
    text = ""
    if isinstance(data, dict):
        for key in ("feed", "text"):
            candidate = data.get(key)
            if isinstance(candidate, str) and candidate.strip():
                text = candidate.strip()
                break
    if not text:
        cleaned = strip_fence(raw)
        if "投喂单元" in cleaned or "subject_definitions" in cleaned:
            text = cleaned.strip()
    if not text:
        return str(previous or "")
    return text


def _patch_voiceover(text):
    if "subject_definitions" not in str(text or ""):
        return text
    runner = importlib.import_module("end.stage_runner")
    return runner.patch_h3_voiceover(text)


def _duration_note(state):
    runner = importlib.import_module("end.stage_runner")
    durations = []
    for shot in state.get("shots") or []:
        if isinstance(shot, dict) and shot.get("duration") not in (None, ""):
            try:
                durations.append(float(shot.get("duration")))
            except (TypeError, ValueError):
                continue
    if not durations:
        durations = runner.storyboard_duration_list(state.get("storyboard") or "")
    if not durations:
        return ""
    groups = runner.group_shot_durations(durations)
    rendered = "；".join(
        "%s=%s" % (
            "+".join(runner.format_seconds(item) for item in group),
            runner.format_seconds(sum(group)),
        )
        for group in groups
    )
    return (
        "分镜时长合计 %ss。可以合并镜头。总时长不得短于这个数，对白不得删。"
        "超过 15 秒就拆成下一单元，禁止钳成 15。按这些单元写 duration：%s。"
        % (runner.format_seconds(sum(durations)), rendered)
    )


def generator_messages(state):
    system = {"role": "system", "content": GENERATOR_SYSTEM}
    note = _duration_note(state)
    if int(state.get("attempt") or 0) < 1 and str(state.get("opening") or "").strip():
        user = state.get("opening")
        if note:
            user = user.rstrip() + "\n\n" + note
        return [system, {"role": "user", "content": user}]
    if int(state.get("attempt") or 0) < 1:
        pieces = ["任务：提示词封装", "分镜：\n%s" % str(state.get("source") or "").strip()]
        if note:
            pieces.append(note)
        return [system, {"role": "user", "content": "\n\n".join(pieces)}]
    user = revision_brief(
        STAGE,
        "任务：提示词封装",
        state.get("draft") or "",
        state.get("critic_report"),
        state.get("machine_report"),
    )
    if note:
        user = user + "\n\n" + note
    return [system, {"role": "user", "content": user}]


def critic_messages(state):
    sections = ["待审投喂稿：\n%s" % str(state.get("draft") or "").strip()]
    reference = skill_reference(state.get("skill_pack"))
    if reference:
        sections.insert(0, reference)
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def compile_feed_graph(complete, checker=None):
    check = checker or check_feed_draft

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
        draft = _patch_voiceover(extract_feed(raw, previous))
        note = ""
        if previous.strip() and draft.strip() == previous.strip() and attempt > 1:
            note = "revision_kept_previous"
        return {
            "attempt": attempt,
            "draft": draft,
            "partials": produced.get("partials") or [],
            "error": "",
            "generator_note": note,
        }

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
        return critic_only(parse_critic(raw, _looks_like_feed))

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

    return compile_worker(FeedState, generate, critique, machine_check)


def run_feed_loop(
    storyboard,
    *,
    locks=None,
    shots=None,
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
    """Run the feed-packing loop once. 投喂机检 is this loop's hard gate."""
    state = base_state(
        STAGE,
        TASK,
        storyboard,
        opening=opening,
        prior_draft=prior_draft,
        model=model,
        max_attempts=max_attempts,
        extra={
            "locks": dict(locks or {}),
            "storyboard": str(storyboard or ""),
            "shots": list(shots or []),
            "skill_pack": str(skill_pack or ""),
        },
    )
    if not str(storyboard or "").strip() and not str(opening or "").strip() and not str(prior_draft or "").strip():
        state["status"] = "fail"
        state["stop_reason"] = "missing_storyboard"
        state["passed"] = False
        return result_from_state(state, draft_key="feed")
    try:
        state["skill_pack"] = resolve_skill_pack(
            skill_pack,
            context=opening or storyboard,
            stage="P4",
        )
    except RuntimeError as exc:
        return result_from_state(missing_skill_state(state, exc), draft_key="feed")
    app = compile_feed_graph(
        complete or bound_complete(model, client, timeout),
        checker=checker,
    )
    final = run_compiled(app, state, missing_reason="missing_storyboard", require_text=False)
    return result_from_state(final, draft_key="feed")
