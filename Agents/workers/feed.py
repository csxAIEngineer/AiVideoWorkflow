"""Feed-packing worker: generate → critique → 投喂机检.

``check_feed`` / ``validate_prompt`` (H3, by segment) is the hard gate. The
duration rule is a hard gate on top of that script: merged shots may share a
unit, the total may not shrink below the storyboard, dialogue may not be
dropped, and a span over 15s is split instead of clamped. The voiceover patch
attaches the official closed-lips phrase to that unit's voiceover.

When the model lock is H3, the generator brief already contains each packing
unit's heading, planned duration, and the six Ref2VA section lines. The model
fills those bodies. A JSON object or a merged ``detailed_description`` is
unwrapped back into that scaffold before the gate runs.
"""

import importlib
import json
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
from end.product_names import (
    DRAFT_NAME,
    HEADING_RULE,
    UNIT_HEAD_ANY,
    UNIT_HEAD_LINE,
    UNIT_NAME,
    UNIT_TOKEN,
    mentions_unit,
    unit_heading,
)

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

# Ref2VA order. The structure check and validate_prompt H1 use this same sequence.
H3_SECTIONS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
H3_FILL_ONLY = (
    "简报里的 ## 【%s N/M】、duration 和六段字段名是锁定骨架。"
    "只填各段正文，不要自己发明 Ref2VA 版式，也不要输出 JSON。"
) % UNIT_NAME
_H3_FIELD_LINE = re.compile(
    r"^[ \t]*(?:【[ \t]*)?(%s)(?:[ \t]*】)?[ \t]*[:：][ \t]*(.*)$" % "|".join(H3_SECTIONS)
)
_H3_SUBJECT_LINE = re.compile(r"(?m)^[ \t]*subject_definitions[ \t]*[:：]")
_LEADING_DURATION = re.compile(
    r"^(?:[ \t]*(?:duration|duration_seconds)[ \t]*[:=：][^\n]*(?:\n|$))+",
    re.I,
)
_UNIT_DURATION_LINE = re.compile(
    r"^[ \t]*(duration_seconds|duration)[ \t]*[:=：][ \t]*(.*)$",
    re.I,
)
_LOOSE_UNIT_LINE = re.compile(
    r"(?m)^[ \t]*【[ \t]*%s[ \t]+\d+[ \t]*/[ \t]*\d+[^】\n]*】[ \t]*$" % UNIT_TOKEN
)

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


def _h3_locked(state):
    """Missing model lock stays on H3, the same default as the feed gate."""
    model = str(((state or {}).get("locks") or {}).get("model") or "h3").strip().lower()
    return model == "h3"


def _generator_system(state):
    if not _h3_locked(state):
        return GENERATOR_SYSTEM
    return GENERATOR_SYSTEM + "\n" + H3_FILL_ONLY


def h3_packing_scaffold(groups):
    """Headings, planned durations, and the six empty section lines.

    The model fills the bodies. It does not invent another Ref2VA layout.
    """
    runner = importlib.import_module("end.stage_runner")
    groups = list(groups or [])
    if not groups:
        return ""
    total = len(groups)
    blocks = [
        "\n".join((
            "H3 的 Ref2VA 骨架已经按%s写好。只填每个字段名下面的正文。" % UNIT_NAME,
            "不要改 ## 标题，不要改 duration，不要调换这六段的顺序。",
            "不要输出 JSON，不要把多个单元写进同一个 detailed_description。",
        ))
    ]
    for index, group in enumerate(groups, 1):
        fields = "\n".join("%s:" % name for name in H3_SECTIONS)
        blocks.append("\n".join((
            unit_heading(index, total),
            "duration: %s" % runner.format_seconds(sum(group)),
            fields,
        )))
    return "\n\n".join(blocks)


def packing_plan_note(state):
    """Unit sums, the H3 scaffold, and exact spoken lines, known before the model runs."""
    runner = importlib.import_module("end.stage_runner")
    groups = packing_groups(state)
    spoken = runner.storyboard_spoken_lines(_storyboard_text(state))
    if not groups and not spoken:
        return ""
    parts = []
    if groups:
        total = sum(sum(group) for group in groups)
        lines = [
            "分镜时长合计 %ss。可以合并镜头。总时长不得短于这个数，对白不得删。"
            "超过 15 秒就拆成下一单元，禁止钳成 15。"
            % runner.format_seconds(total),
            "这些单元的 duration 已经算好，按这个写，不要自己另分组，也不要把时长写短：",
        ]
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
        parts.append("\n".join(lines))
        if _h3_locked(state):
            parts.append(h3_packing_scaffold(groups))
    if spoken:
        spoken_lines = [
            "对白和旁白必须逐字出现，包括角色前缀和表演提示。"
            "写在 <d> 里时，标签内也必须是这一整句，不能只留去掉前缀后的一句：",
        ]
        for line in spoken:
            spoken_lines.append("- %s" % line)
        parts.append("\n".join(spoken_lines))
    return "\n\n".join(part for part in parts if part)


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


def _section_name(key):
    text = str(key or "").strip()
    text = re.sub(r"^[【\[]\s*", "", text)
    text = re.sub(r"\s*[】\]]$", "", text)
    text = re.sub(r"\s*[:：]\s*$", "", text)
    return text.strip()


def _as_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _section_dict(obj):
    found = {}
    if not isinstance(obj, dict):
        return found
    for key, value in obj.items():
        name = _section_name(key)
        if name not in H3_SECTIONS or name in found:
            continue
        text = _as_text(value)
        if text:
            found[name] = text
    return found


def _load_feed_json(value):
    if isinstance(value, (dict, list)):
        return value
    raw = strip_fence(str(value or "")).strip()
    if not raw:
        return None
    peeled = strip_fence(_LEADING_DURATION.sub("", raw, count=1).strip()).strip()
    if not peeled or peeled[0] not in "{[":
        return None
    try:
        data = json.loads(peeled)
    except json.JSONDecodeError:
        try:
            data, _end = json.JSONDecoder().raw_decode(peeled)
        except json.JSONDecodeError:
            return None
    if isinstance(data, (dict, list)):
        return data
    return None


def _heading_count(text):
    raw = str(text or "")
    return len(UNIT_HEAD_ANY.findall(raw)) + len(_LOOSE_UNIT_LINE.findall(raw))


def _subject_count(text):
    return len(_H3_SUBJECT_LINE.findall(str(text or "")))


def _looks_like_many_units(text):
    return _heading_count(text) >= 2 or _subject_count(text) >= 2


def _marked_slices(raw, marks):
    pieces = []
    for index, match in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(raw)
        piece = raw[match.start():end].strip()
        if piece:
            pieces.append(piece)
    return pieces


def _split_blocks(text):
    raw = str(text or "").strip()
    if not raw:
        return []
    heads = list(UNIT_HEAD_ANY.finditer(raw))
    if len(heads) < 2:
        loose = list(_LOOSE_UNIT_LINE.finditer(raw))
        if len(loose) >= 2 or (len(loose) == 1 and not heads):
            heads = loose
    if len(heads) >= 2 or (len(heads) == 1 and heads[0].start() == 0):
        return _marked_slices(raw, heads)
    marks = list(_H3_SUBJECT_LINE.finditer(raw))
    if len(marks) >= 2 or (len(marks) == 1 and marks[0].start() == 0):
        return _marked_slices(raw, marks)
    if heads:
        return _marked_slices(raw, heads)
    if marks:
        return _marked_slices(raw, marks)
    if any(name in raw for name in H3_SECTIONS):
        return [raw]
    return []


def _parse_one_block(block):
    sections = {name: [] for name in H3_SECTIONS}
    current = None
    loose = []
    duration = ""
    for line in str(block or "").splitlines():
        if UNIT_HEAD_ANY.match(line) or _LOOSE_UNIT_LINE.match(line):
            continue
        if current is None:
            duration_line = _UNIT_DURATION_LINE.match(line)
            if duration_line:
                duration = duration_line.group(2).strip()
                continue
        field = _H3_FIELD_LINE.match(line)
        if field:
            current = field.group(1)
            rest = field.group(2).strip()
            if rest:
                sections[current].append(rest)
            continue
        if current:
            sections[current].append(line)
        else:
            loose.append(line)
    bodies = {name: "\n".join(sections[name]).strip() for name in H3_SECTIONS}
    extra = "\n".join(loose).strip()
    if extra:
        if bodies["detailed_description"]:
            bodies["detailed_description"] = extra + "\n" + bodies["detailed_description"]
        else:
            bodies["detailed_description"] = extra
    return {"duration": duration, "sections": bodies}


def _merge_parent(unit, parent):
    sections = {}
    own = unit.get("sections") or {}
    for name in H3_SECTIONS:
        body = str(own.get(name) or "").strip()
        if body:
            sections[name] = body
            continue
        value = parent.get(name) if isinstance(parent, dict) else ""
        text = str(value or "").strip()
        if text and not _looks_like_many_units(text):
            sections[name] = text
        else:
            sections[name] = ""
    return {"duration": str(unit.get("duration") or "").strip(), "sections": sections}


def _units_from_unit_text(text):
    blocks = _split_blocks(text)
    if not blocks:
        return None
    units = [_parse_one_block(block) for block in blocks]
    kept = []
    for unit in units:
        if unit.get("duration") or any(str(body or "").strip() for body in unit["sections"].values()):
            kept.append(unit)
    return kept or None


def _unit_from_sections(sections):
    stored = {name: str(sections.get(name) or "").strip() for name in H3_SECTIONS}
    return {"duration": "", "sections": stored}


def _units_from_payload(data, depth=0):
    """Packing units inside a JSON object, including colon-suffixed keys."""
    if depth > 5 or data is None:
        return None
    if isinstance(data, list):
        units = []
        for item in data:
            found = _units_from_payload(item, depth + 1)
            if not found:
                return None
            units.extend(found)
        return units or None
    if isinstance(data, str):
        loaded = _load_feed_json(data)
        if isinstance(loaded, (dict, list)):
            found = _units_from_payload(loaded, depth + 1)
            if found:
                return found
        return _units_from_unit_text(data)
    if not isinstance(data, dict):
        return None
    sections = _section_dict(data)
    detailed = sections.get("detailed_description") or ""
    if detailed:
        nested = _units_from_unit_text(detailed)
        if nested and len(nested) >= 2:
            return [_merge_parent(unit, sections) for unit in nested]
    best = None
    for key in ("feed", "text", "markdown", "body", "content"):
        if key not in data:
            continue
        found = _units_from_payload(data[key], depth + 1)
        if found and (best is None or len(found) > len(best)):
            best = found
    if best and len(best) >= 2:
        return best
    segments = data.get("segments")
    if isinstance(segments, list) and segments:
        units = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            inner = segment.get("content") if isinstance(segment.get("content"), dict) else segment
            found = _units_from_payload(inner, depth + 1)
            if found:
                units.extend(found)
        if units:
            return units
    if best:
        return best
    if any(sections.values()):
        return [_unit_from_sections(sections)]
    return None


def _peel_trailing_parent(units):
    """Fields that show up only on the last slice belong to the outer shell."""
    if len(units) < 2:
        return units, {}
    last = units[-1]
    earlier = units[:-1]
    stolen = {}
    for name in H3_SECTIONS:
        if name == "detailed_description":
            continue
        body = str(last["sections"].get(name) or "").strip()
        if not body:
            continue
        if any(str(unit["sections"].get(name) or "").strip() for unit in earlier):
            continue
        stolen[name] = body
        last["sections"][name] = ""
    return units, stolen


def _units_from_merged_markdown(text):
    """One preamble of the six fields, with every unit inside detailed_description."""
    raw = str(text or "").strip()
    if not raw or raw[0] in "{[":
        return None
    heads = list(UNIT_HEAD_ANY.finditer(raw))
    if len(heads) < 2 or heads[0].start() == 0:
        return None
    preamble = raw[: heads[0].start()]
    if "subject_definitions" not in preamble and "detailed_description" not in preamble:
        return None
    parent = _parse_one_block(preamble)["sections"]
    units = [_parse_one_block(block) for block in _marked_slices(raw, heads)]
    units, stolen = _peel_trailing_parent(units)
    for name, body in stolen.items():
        if not str(parent.get(name) or "").strip():
            parent[name] = body
    if len(units) < 2:
        return None
    return [_merge_parent(unit, parent) for unit in units]


def _shown_duration(token, runner):
    text = str(token or "").strip()
    match = re.match(r"^(\d+(?:\.\d+)?)(?:\s*[sS秒])?$", text)
    if not match:
        return text
    return runner.format_seconds(float(match.group(1)))


def _render_h3_units(units, groups):
    runner = importlib.import_module("end.stage_runner")
    units = list(units or [])
    if not units:
        return ""
    planned = None
    if groups and len(groups) == len(units):
        planned = [sum(group) for group in groups]
    total = len(units)
    blocks = []
    for index, unit in enumerate(units, 1):
        lines = [unit_heading(index, total)]
        if planned is not None:
            lines.append("duration: %s" % runner.format_seconds(planned[index - 1]))
        elif unit.get("duration"):
            shown = _shown_duration(unit.get("duration"), runner)
            if shown:
                lines.append("duration: %s" % shown)
        sections = unit.get("sections") or {}
        wrote = False
        for name in H3_SECTIONS:
            body = str(sections.get(name) or "").strip()
            if not body:
                continue
            lines.append("%s:" % name)
            lines.append(body)
            wrote = True
        if not wrote and not unit.get("duration"):
            continue
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return "\n\n".join(blocks).strip() + "\n"


def restore_h3_scaffold(text, state=None):
    """Turn a JSON blob or a merged description back into per-unit H3 markdown.

    A draft that is already markdown, including a prose unit, is left as written.
    Empty section lines are not invented, so a missing field still fails H1.
    Spoken lines are not invented here.
    """
    raw = str(text or "")
    if not raw.strip():
        return raw
    units = None
    loaded = _load_feed_json(raw)
    if loaded is not None:
        units = _units_from_payload(loaded)
    if not units:
        units = _units_from_merged_markdown(raw)
    if not units:
        return raw
    rendered = _render_h3_units(units, packing_groups(state or {}))
    if not rendered.strip():
        return raw
    return rendered


def apply_packing_plan(text, state):
    """Unwrap an H3 blob, fill planned durations, then restore stripped lines."""
    runner = importlib.import_module("end.stage_runner")
    prepared = restore_h3_scaffold(text, state) if _h3_locked(state) else str(text or "")
    corrected = correct_unit_durations(prepared, packing_groups(state))
    spoken = runner.storyboard_spoken_lines(_storyboard_text(state))
    return restore_spoken_lines(corrected, spoken)


def generator_messages(state):
    system = {"role": "system", "content": _generator_system(state)}
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
        draft = _patch_voiceover(apply_packing_plan(extract_feed(raw, previous), state))
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
