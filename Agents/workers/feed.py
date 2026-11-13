"""Feed-packing worker: generate → critique → 投喂机检.

``check_feed`` / ``validate_prompt`` (H3, by segment) is the hard gate. The
duration rule is a hard gate on top of that script: merged shots may share a
unit, the total may not shrink below the storyboard, dialogue may not be
dropped, and a span over 15s is split instead of clamped. The voiceover patch
attaches the official closed-lips phrase to that unit's voiceover.
"""

import importlib
import re
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
    strip_fence,
)
from Agents.workers.critic import critic_user
from Agents.workers.generator import run_generator
from end.product_names import DRAFT_NAME, HEADING_RULE, UNIT_HEAD_LINE, UNIT_NAME, mentions_unit

STAGE = "feed"
TASK = "feed"

GENERATOR_SYSTEM = "\n".join((
    "你是提示词封装的生成员。你只写%s，放在 feed 字符串。" % DRAFT_NAME,
    "投喂机检是这一环的硬门。审查员只交报告，不会替你改稿。",
    "H3 怎么写，以技能原文为准。不要跑在还没封装的剧本或分镜上。",
    "%s。" % HEADING_RULE,
    "可以把分镜合并进同一个%s，但各单元 duration 相加不得短于分镜总时长。" % UNIT_NAME,
    "简报里已经写好每个%s的 duration，和对白旁白的原句。duration 用那些和，对白逐字保留。" % UNIT_NAME,
    "对白和旁白必须原样留下。禁止为了压进 15 秒删情节或对白。",
    "一个单元若按分镜时长会超过 15 秒，拆成下一个单元，禁止把声明时长钳成 15。",
    "画外音的闭口证据必须紧跟这一单元里的画外音，不要接到出声对白后面，也不要接到文末。",
    "审查报告里夹带的稿作废。",
))

CRITIC_SYSTEM = "\n".join((
    "你是%s审查员。你只审查，只交报告。" % DRAFT_NAME,
    "禁止改写%s，禁止输出 feed、draft、rewrite、text。" % DRAFT_NAME,
    "整段回复只能是一个 JSON 对象：",
    '{"verdict":"accept 或 revise","issues":[{"target":"%s","severity":"blocking 或 note","detail":"一句话"}]}' % UNIT_NAME,
    "没有要改的地方时 verdict 为 accept，issues 为空数组。",
    "detail 只指出问题，不写替换后的单元。",
    "评判标准只是机检技能：validate_prompt 的 H1–H12，加上时长不得缩短、画外音闭口必须紧跟画外音。",
    "不要用写作技能审查。机检会在你之后再跑。单独的 WARN 不算失败。",
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
    return mentions_unit(raw) or "subject_definitions" in raw or '"feed"' in raw


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
        if mentions_unit(cleaned) or "subject_definitions" in cleaned:
            text = cleaned.strip()
    if not text:
        return str(previous or "")
    return text


def _patch_voiceover(text):
    if "subject_definitions" not in str(text or ""):
        return text
    runner = importlib.import_module("end.stage_runner")
    return runner.patch_h3_voiceover(text)


_DURATION_LINE_VALUE = re.compile(
    r"(?m)^(duration_seconds|duration)\s*[:=：]\s*[^\n]*"
)
_D_TAG = re.compile(r"(?is)(<d\b[^>]*>)(.*?)(</d>)")
_LANG_TAG = re.compile(r"^(\[[^\]\n]+\])\s*")
_SPEAKER_PREFIX = re.compile(r"^([^：:\n]{1,40})[：:]\s*")
_PERFORMANCE_CUE = re.compile(r"^[（(][^）)\n]*[）)]\s*")


def _storyboard_text(state):
    state = state or {}
    board = state.get("storyboard")
    if str(board or "").strip():
        return str(board)
    return str(state.get("source") or "")


def shot_duration_list(state):
    """Shot lengths the feed gate uses: canvas shots, otherwise the storyboard."""
    runner = importlib.import_module("end.stage_runner")
    durations = []
    for shot in (state or {}).get("shots") or []:
        if isinstance(shot, dict) and shot.get("duration") not in (None, ""):
            try:
                durations.append(float(shot.get("duration")))
            except (TypeError, ValueError):
                continue
    if not durations:
        durations = runner.storyboard_duration_list(_storyboard_text(state))
    return durations


def packing_groups(state, limit=15):
    """The same 15s packing the machine check uses. The model does not regroup."""
    runner = importlib.import_module("end.stage_runner")
    return runner.group_shot_durations(shot_duration_list(state), limit)


def packing_plan_note(state):
    """Unit sums and exact spoken lines, known before the model runs."""
    runner = importlib.import_module("end.stage_runner")
    groups = packing_groups(state)
    spoken = runner.storyboard_spoken_lines(_storyboard_text(state))
    if not groups and not spoken:
        return ""
    lines = []
    if groups:
        total = sum(sum(group) for group in groups)
        lines.append(
            "分镜时长合计 %ss。可以合并镜头。总时长不得短于这个数，对白不得删。"
            "超过 15 秒就拆成下一单元，禁止钳成 15。"
            % runner.format_seconds(total)
        )
        lines.append("这些单元的 duration 已经算好，按这个写，不要自己另分组，也不要把时长写短：")
        cursor = 1
        count = len(groups)
        for index, group in enumerate(groups, 1):
            start = cursor
            end = cursor + len(group) - 1
            cursor = end + 1
            shots = "镜头 %s" % start if start == end else "镜头 %s-%s" % (start, end)
            formula = "+".join(runner.format_seconds(item) for item in group)
            shown = runner.format_seconds(sum(group))
            lines.append("【%s %s/%s】%s：%s=%s" % (UNIT_NAME, index, count, shots, formula, shown))
    if spoken:
        lines.append(
            "对白和旁白必须逐字出现，包括角色前缀和表演提示。"
            "写在 <d> 里时，标签内也必须是这一整句，不能只留去掉前缀后的一句："
        )
        for line in spoken:
            lines.append("- %s" % line)
    return "\n".join(lines)


def correct_unit_durations(text, groups):
    """Overwrite each unit duration when the draft has exactly these units.

    A different unit count is left alone. The machine check reports that.
    This does not invent another grouping.
    """
    planned = [sum(group) for group in groups or []]
    raw = str(text or "")
    if not planned:
        return raw
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if len(heads) != len(planned):
        return raw
    runner = importlib.import_module("end.stage_runner")
    parts = [raw[: heads[0].start()]]
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        parts.append(_write_unit_duration(raw[match.start() : end], planned[index], runner))
    return "".join(parts)


def _write_unit_duration(block, seconds, runner):
    shown = runner.format_seconds(seconds)
    match = _DURATION_LINE_VALUE.search(block)
    if match:
        line = "%s: %s" % (match.group(1), shown)
        return block[: match.start()] + line + block[match.end() :]
    head = UNIT_HEAD_LINE.search(block)
    if not head:
        return block
    return block[: head.end()] + "\nduration: %s" % shown + block[head.end() :]


def _spoken_core(line):
    """Utterance after the speaker prefix and leading performance cues."""
    text = str(line or "").strip()
    match = _SPEAKER_PREFIX.match(text)
    if match:
        text = text[match.end() :]
    while True:
        cue = _PERFORMANCE_CUE.match(text)
        if not cue:
            break
        text = text[cue.end() :]
    return text.strip()


def restore_spoken_lines(text, lines):
    """Put the storyboard string back into a ``<d>`` tag that kept only the core.

    A line that is already present stays as written. A line that does not
    appear even as that core is not invented.
    """
    raw = str(text or "")
    for spoken in lines or []:
        spoken = str(spoken or "").strip()
        if not spoken or spoken in raw:
            continue
        core = _spoken_core(spoken)
        if not core or core == spoken:
            continue
        raw = _restore_core_in_d_tags(raw, spoken, core)
    return raw


def _restore_core_in_d_tags(raw, spoken, core):
    def repl(match):
        inner = match.group(2)
        rewritten = _rewrite_d_inner(inner, spoken, core)
        if rewritten is None:
            return match.group(0)
        return match.group(1) + rewritten + match.group(3)

    return _D_TAG.sub(repl, raw)


def _rewrite_d_inner(inner, spoken, core):
    leading = inner[: len(inner) - len(inner.lstrip())]
    trailing = inner[len(inner.rstrip()) :]
    body = inner.strip()
    language = ""
    match = _LANG_TAG.match(body)
    if match:
        language = match.group(1)
        body = body[match.end() :].strip()
    if body != core:
        return None
    rebuilt = "%s %s" % (language, spoken) if language else spoken
    return leading + rebuilt + trailing


def apply_packing_plan(text, state):
    """Fill planned durations when the headings match, then restore stripped lines."""
    runner = importlib.import_module("end.stage_runner")
    corrected = correct_unit_durations(text, packing_groups(state))
    spoken = runner.storyboard_spoken_lines(_storyboard_text(state))
    return restore_spoken_lines(corrected, spoken)


def generator_messages(state):
    system = {"role": "system", "content": GENERATOR_SYSTEM}
    note = packing_plan_note(state)
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
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": critic_user(
            TASK,
            "待审%s：\n%s" % (DRAFT_NAME, str(state.get("draft") or "").strip()),
        )},
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
        draft = apply_packing_plan(_patch_voiceover(extract_feed(raw, previous)), state)
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
