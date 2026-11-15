"""Feed-packing worker: generate → critique → 投喂机检.

``check_feed`` / ``validate_prompt`` (H3, by segment) is the hard gate. The
duration rule is a hard gate on top of that script: merged shots may share a
unit, the total may not shrink below the storyboard, dialogue may not be
dropped, and a span over 15s is split instead of clamped. The voiceover patch
rewrites unofficial voiceover inside detailed_description onto a <Subject N>
already defined in that unit, and keeps <d> and spoken lines out of
overall_soundscape.

When the model lock is H3, the generator brief already contains each packing
unit's heading, planned duration, and the four Ref2VA section lines. The model
fills those bodies. ``summary`` and ``retention_analysis`` are not written.
If the model still emits them, restore strips those sections before the gate.
A JSON object or a merged ``detailed_description`` is unwrapped back into
that scaffold before the gate runs. An informal ``feed`` string does not
win over the official four fields: labels such as ``Subject`` /
``Soundscape`` / ``Music`` are mapped onto the field names, and a single
set of fields lands in the unit it belongs to. Every planned unit keeps
the heading, duration, four field lines, style-lock sentence, and shot
headings. Empty bodies stay empty.

The episode style is not written by this generator. It is the script line
``画风``, or the project lock when that line is absent. The program writes
that exact sentence as the first line of every unit's ``subject_definitions``.
A missing line, a different sentence, or a second style the model invented
fails the feed gate.

Unit count and each unit's duration are locked from the 15s groups before the
model writes. After it writes, a draft with a different unit count is not
stored as-is: the planned headings and durations are materialized, and a
storyboard spoken line is put back into the unit that owns that shot even
when the model omitted the ``<d>`` tag. A duration or dialogue failure names
the packing writers so the repair runs those nodes again.

Shot count and cut times are not written by this generator. They are known
from the accepted storyboard before packing. Each unit is renumbered locally:
its first shot is ``[Shot 1]`` with no timestamp, and each later shot is
``[Shot N] At MM:SS.mmm`` at the sum of the earlier shots in that unit only.
The brief shows that skeleton. After every draft and every repair, the program
writes those headings back. A unit whose shots, local numbers, or cut times
disagree fails the feed gate. The repair tells the writer to restore the
skeleton, not to invent a new cut.
"""

import importlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
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
    H3_SUBJECT_SOUND_RULE,
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
    "正文只写画面。不要写末态继承、拼接节点、换机位、可变数据。",
))

# Ref2VA order the packing scaffold writes. summary and retention_analysis are
# recognized only so their bodies can be discarded instead of leaking sideways.
H3_SECTIONS = (
    "subject_definitions",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
H3_OMITTED = (
    "summary",
    "retention_analysis",
)
H3_FILL_ONLY = (
    (
        "简报里的 ## 【%s N/M】、duration 和四段字段名是锁定骨架。"
        "只填或修改各段正文，不要自己发明 Ref2VA 版式，也不要输出 JSON。"
        "不要把四段写成 JSON 字段，不要另包一层。"
        "不要改字段名，不要用 Subject、Soundscape、Music 代替这四行。"
        "不要丢掉单元标题、duration、画面风格锁定句或镜号。"
        "不要只写第一个单元。已经填好的单元保留，空着的槽只补正文。"
        "不要写 summary 或 retention_analysis。"
    )
    % UNIT_NAME
    + H3_SUBJECT_SOUND_RULE
    + "若简报里有锁定句，subject_definitions 的第一行只能是那一句。"
    "不要自己写画风，不要翻译，不要另写一种风格。"
    "detailed_description 里的 [Shot N] 和 At 时间也是锁定骨架。"
    "每一段从 [Shot 1] 重新编号，第一镜不写时间。"
    "后面每一镜紧跟简报里的 At MM:SS.mmm，这是这一段前面各镜的时长和，不是全集时间。"
    "不要增删、合并或调换镜头，不要改镜号，不要改这些时间码，也不要自己发明切点。"
    "只在这些标题下写画面和对白。"
)
_H3_FIELD_NAMES = H3_SECTIONS + H3_OMITTED
_H3_NAME_ALT = "|".join(_H3_FIELD_NAMES)
_H3_FIELD_LINE = re.compile(
    r"^[ \t]*(?:【[ \t]*)?(%s)(?:[ \t]*】)?[ \t]*[:：][ \t]*(.*)$" % _H3_NAME_ALT
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
    "评判标准只是机检技能：validate_prompt 的 H1–H12，加上时长不得缩短、画外音闭口必须紧跟画外音、画风锁、镜号锁。",
    "有锁定画风时，每一段 subject_definitions 的第一行必须是同一句。缺这句、写成别的句子、或多写一种风格，都是 blocking。只出现在房间或道具主体上不算。",
    "镜号和切点已经按分镜算好。不从 [Shot 1] 起、首镜带时间、切点不是预算的 At 时间、或多了少了镜头，都是 blocking。不要要求另写切点。",
    "summary 和 retention_analysis 不是封装字段。缺了不算问题，不要要求补上。",
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
    script: str
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
    project_id: str
    project_folder: str


# The program writes this line. The generator does not invent or paraphrase it.
# It is the first line of subject_definitions, inside the Ref2VA four sections.
# It is not a 【画幅风格】 heading and it is not a fifth field.
STYLE_LOCK_LABEL = "画面风格："
_SCRIPT_STYLE = re.compile(
    r"(?m)^[ \t]*(?:[-*+][ \t]+)?(?:#{1,6}[ \t]*)?(?:\*\*|__)?画风(?:\*\*|__)?[ \t]*[:：][ \t]*(.+?)\s*$"
)
_STYLE_SLOT = re.compile(r"^[ \t]*画面风格[ \t]*[:：]")
_OTHER_STYLE_LINE = re.compile(r"(?im)^[ \t]*(?:画风|visual style)[ \t]*[:：][ \t]*\S")
_RENDERED_IN_STYLE = re.compile(r"(?i)\brendered\s+in\s+[^\n]{0,180}?\bstyle\b")
_LIVE_ACTION_STYLE = re.compile(r"(?i)\blive-action\s+style\b|\blive action\s+style\b")
_STYLE_WRITERS = (
    "sd-feed-engine/references/feed-mode-and-gates.md",
    "short-drama-director/references/h3-adapter.md",
    "short-drama-director/references/prompt-feeding-checklist.md",
)
# The repair names these packing writers and tells them to restore the skeleton.
_SKELETON_WRITERS = _STYLE_WRITERS


def _flat_style(text):
    return " ".join(str(text or "").split())


def script_style_line(text):
    """The script's own ``画风：`` line. ``画风正文`` and prose that merely says 画风 do not count."""
    match = _SCRIPT_STYLE.search(str(text or ""))
    if not match:
        return ""
    return _flat_style(match.group(1))


def agreed_style(state=None, script=None, locks=None):
    """The episode style already agreed. An explicit script ``画风`` wins.

    There is no packing default. An empty script line does not wipe
    ``style_look``, and a fallback phrase is not invented here.
    """
    state = state or {}
    if script is None:
        script = state.get("script") or ""
    if locks is None:
        locks = state.get("locks") or {}
    found = script_style_line(script)
    if found:
        return found
    look = _flat_style((locks or {}).get("style_look"))
    if look:
        return look
    return _flat_style((locks or {}).get("style"))


def style_lock_line(style):
    """The exact sentence written into every packed unit."""
    text = _flat_style(style)
    if not text:
        return ""
    return STYLE_LOCK_LABEL + text


def style_lock_rule(style):
    """Pre-written rule. It does not ask the model to compose a style."""
    line = style_lock_line(style)
    if not line:
        return ""
    return "\n".join((
        "本集画风已经锁定。程序会把下面这一句写进每一段 subject_definitions 的第一行。",
        "不要自己写画风，不要翻译，不要改字，不要删掉这句，也不要再写一种风格。",
        "只写在某一个房间或道具的主体上不算。",
        "锁定句：%s" % line,
    ))


def _style_units(text):
    raw = str(text or "")
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if not heads:
        if "subject_definitions" in raw and raw.strip():
            return [raw]
        return []
    units = []
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        units.append(raw[match.start():end])
    return units


def _unit_label(block, index, total):
    match = re.search(r"(封装单元|投喂单元)\s*(\d+)\s*/\s*(\d+)", str(block or ""))
    if match:
        return "%s %s/%s" % (match.group(1), match.group(2), match.group(3))
    return "%s %s/%s" % (UNIT_NAME, index, total)


def _subject_body(block):
    """``(header_index, inline_rest, body_lines, next_index)`` or None.

    ``body_lines`` keep their original endings. The span stops at the next
    Ref2VA field, so the style sentence stays inside subject_definitions.
    """
    lines = str(block or "").splitlines(keepends=True)
    header_at = None
    inline = ""
    for index, raw_line in enumerate(lines):
        core = raw_line.strip()
        field = _H3_FIELD_LINE.match(core)
        if field and field.group(1) == "subject_definitions":
            header_at = index
            inline = field.group(2).strip()
            break
    if header_at is None:
        return None
    body_end = header_at + 1
    while body_end < len(lines):
        field = _H3_FIELD_LINE.match(lines[body_end].strip())
        if field and field.group(1) in _H3_FIELD_NAMES:
            break
        body_end += 1
    return header_at, inline, lines[header_at + 1:body_end], body_end, lines


def _first_body_line(inline, body_lines):
    if inline:
        return inline
    for raw_line in body_lines:
        stripped = raw_line.strip()
        if stripped:
            return stripped
    return ""


def _style_phrase_hits(text, line):
    """Invented style sentences. The exact lock line is not one of them.

    Matching is the whole ``rendered in … style`` phrase or a style label.
    A word inside ordinary prose, such as "washed away", is not a style.
    """
    hits = []
    seen = set()
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped == line:
            continue
        found = ""
        if _STYLE_SLOT.match(stripped) or _OTHER_STYLE_LINE.match(stripped):
            found = stripped
        else:
            rendered = _RENDERED_IN_STYLE.search(stripped)
            live = _LIVE_ACTION_STYLE.search(stripped)
            if rendered:
                found = _flat_style(rendered.group(0))
            elif live:
                found = _flat_style(live.group(0))
        if not found or found == line or found in line or found in seen:
            continue
        seen.add(found)
        hits.append(found)
    return hits


def style_lock_failures(text, style):
    """Fail a packed unit whose style line is missing, different, or extra.

    The pass condition is the exact lock sentence as the first line of
    ``subject_definitions``. A style mention on a room or prop subject does
    not pass. This is not a keyword search: "washed away" does not count as
    the locked style, and it does not fail a unit that already has the sentence.
    """
    line = style_lock_line(style)
    if not line:
        return []
    units = _style_units(text)
    if not units:
        return []
    total = len(units)
    errors = []
    for index, block in enumerate(units, 1):
        label = _unit_label(block, index, total)
        parsed = _subject_body(block)
        first = ""
        if parsed is not None:
            _header, inline, body_lines, _end, _lines = parsed
            first = _first_body_line(inline, body_lines)
        if first != line:
            if first and _STYLE_SLOT.match(first):
                errors.append(
                    "画风锁：%s 用了别的风格「%s」，不是锁定句「%s」。"
                    "不要改写画风。subject_definitions 第一行必须是锁定句。"
                    % (label, first, line)
                )
            else:
                errors.append(
                    "画风锁：%s 缺少锁定句「%s」。"
                    "只写在房间或道具主体上不算。subject_definitions 第一行必须是这一句。"
                    % (label, line)
                )
        for hit in _style_phrase_hits(block, line):
            if hit == first:
                continue
            errors.append(
                "画风锁：%s 多写了风格「%s」。删掉这句，只留锁定句「%s」。不要另拟画风。"
                % (label, hit, line)
            )
    return errors


def _with_newline(raw_line):
    if raw_line.endswith(("\n", "\r")):
        return raw_line
    return raw_line + "\n"


def _inject_block(block, line):
    """Put the lock on the first subject line. Leave a different style sentence in place.

    The first ``画面风格：`` slot is the program's line, so a wrong sentence
    there is replaced. A later style sentence is an extra the gate still sees.
    Exact copies of the lock line are not repeated.
    """
    parsed = _subject_body(block)
    if parsed is None:
        return block
    header_at, inline, body_lines, body_end, lines = parsed
    if not inline and _first_body_line("", body_lines) == line:
        return block
    kept = []
    if inline and inline != line and not _STYLE_SLOT.match(inline):
        kept.append(inline + "\n")
    seen_content = False
    for raw_line in body_lines:
        stripped = raw_line.strip()
        if not stripped:
            kept.append(_with_newline(raw_line))
            continue
        if stripped == line:
            continue
        if not inline and not seen_content and _STYLE_SLOT.match(stripped):
            seen_content = True
            continue
        seen_content = True
        kept.append(_with_newline(raw_line))
    while kept and not kept[0].strip():
        kept.pop(0)
    rebuilt = lines[:header_at] + ["subject_definitions:\n", line + "\n"] + kept + lines[body_end:]
    return "".join(rebuilt)


def inject_style_lock(text, style):
    """Write the same lock sentence into every unit that has subject_definitions.

    A model-written ``画面风格：`` line in that slot is replaced. Other prose,
    including an invented ``rendered in … style`` sentence, is left for the gate.
    """
    line = style_lock_line(style)
    raw = str(text or "")
    if not line or "subject_definitions" not in raw:
        return raw
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if not heads:
        return _inject_block(raw, line)
    parts = [raw[: heads[0].start()]]
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        parts.append(_inject_block(raw[match.start():end], line))
    return "".join(parts)


def _looks_like_feed(text):
    raw = str(text or "")
    return mentions_unit(raw) or "subject_definitions" in raw or '"feed"' in raw


def _join_fold_source(*parts):
    found = []
    for part in parts:
        for item in str(part or "").split("+"):
            if item and item not in found:
                found.append(item)
    return "+".join(found)


def _has_official_field_line(text):
    for line in str(text or "").splitlines():
        field = _H3_FIELD_LINE.match(line.strip())
        if field and field.group(1) in H3_SECTIONS:
            return True
    return False


# Informal labels the director used instead of the four Ref2VA field names.
# ``画面风格：`` is the program lock, not one of these labels.
_INFORMAL_FIELD = (
    (
        "subject_definitions",
        re.compile(
            r"^[ \t]*(?:subjects?|主体)(?:[ \t]*\d+)?[ \t]*[:：][ \t]*(.*)$",
            re.I,
        ),
    ),
    (
        "overall_soundscape",
        re.compile(
            r"^[ \t]*(?:soundscape|overall[ _-]?soundscape|环境音)[ \t]*[:：][ \t]*(.*)$",
            re.I,
        ),
    ),
    (
        "non_diegetic_music",
        re.compile(
            r"^[ \t]*(?:music|non[-_ ]?diegetic(?:[ _-]?music)?|配乐)[ \t]*[:：][ \t]*(.*)$",
            re.I,
        ),
    ),
    (
        "detailed_description",
        re.compile(
            r"^[ \t]*(?:detailed[ _-]?description|description|画面描述)[ \t]*[:：][ \t]*(.*)$",
            re.I,
        ),
    ),
)


def _informal_field(line):
    if _STYLE_SLOT.match(str(line or "").strip()):
        return None
    for name, pattern in _INFORMAL_FIELD:
        found = pattern.match(line)
        if found:
            return name, found
    return None


def _has_informal_label(text):
    for line in str(text or "").splitlines():
        if _informal_field(line):
            return True
    return False


def _string_field(data, keys):
    if not isinstance(data, dict):
        return ""
    for key in keys:
        candidate = data.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _units_from_informal(text):
    """Map ``Subject`` / ``Soundscape`` / ``Music`` lines onto the four fields.

    Unit headings and shot lines stay. This does not invent a body for a
    label the model did not write.
    """
    raw = str(text or "").strip()
    if not raw or not _has_informal_label(raw):
        return None
    blocks = _split_blocks(raw) or [raw]
    units = []
    for block in blocks:
        sections = {name: [] for name in H3_SECTIONS}
        current = None
        duration = ""
        loose = []
        for line in block.splitlines():
            if UNIT_HEAD_ANY.match(line) or _LOOSE_UNIT_LINE.match(line):
                continue
            if current is None:
                duration_line = _UNIT_DURATION_LINE.match(line)
                if duration_line:
                    duration = duration_line.group(2).strip()
                    continue
            field = _H3_FIELD_LINE.match(line)
            if field and field.group(1) in H3_SECTIONS:
                current = field.group(1)
                rest = field.group(2).strip()
                if rest:
                    sections[current].append(rest)
                continue
            if field and field.group(1) in H3_OMITTED:
                current = None
                continue
            informal = _informal_field(line)
            if informal:
                name, found = informal
                current = name
                if name == "subject_definitions":
                    sections[name].append(line.strip())
                elif found.group(1).strip():
                    sections[name].append(found.group(1).strip())
                continue
            if current in H3_SECTIONS:
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
        units.append({"duration": duration, "sections": bodies})
    kept = [
        unit for unit in units
        if unit.get("duration") or any(str(body or "").strip() for body in unit["sections"].values())
    ]
    return kept or None


def _merge_official_into_units(units, official):
    """Keep per-unit prose. Official JSON fills a field the unit left empty.

    When both wrote the same field, the official body stays and the unit
    prose follows it, so a feed string cannot drop the four fields.
    """
    merged = []
    for unit in units or []:
        sections = {
            name: str((unit.get("sections") or {}).get(name) or "").strip()
            for name in H3_SECTIONS
        }
        merged.append({
            "duration": str(unit.get("duration") or "").strip(),
            "sections": sections,
        })
    if not merged:
        return merged
    for name in H3_SECTIONS:
        body = str((official or {}).get(name) or "").strip()
        if not body or _looks_like_many_units(body):
            continue
        current = merged[0]["sections"].get(name) or ""
        if not current:
            merged[0]["sections"][name] = body
        elif body not in current:
            merged[0]["sections"][name] = body + "\n" + current
    return merged


def _payload_without_feed(data):
    """Official fields only. A sibling ``feed`` string is not a section."""
    return {
        key: value
        for key, value in data.items()
        if key not in ("feed", "text", "markdown", "body", "content")
    }


def _compose_director_draft(data):
    """Markdown for one director JSON object, and why it was folded.

    A ``feed`` / ``text`` string is kept only when it already uses the
    official field names. Otherwise official JSON fields and informal
    labels are both mapped onto units. Raw JSON is not returned.
    """
    if not isinstance(data, dict):
        return "", ""
    feed = _string_field(data, ("feed", "text", "markdown", "body", "content"))
    if feed and _json_document(feed):
        nested = _load_feed_json(feed)
        if isinstance(nested, dict):
            return _compose_director_draft(nested)
    official = _section_dict(data)
    if feed and _has_official_field_line(feed):
        return feed, ""
    structured = _units_from_informal(feed) if feed else None
    if not structured and feed and mentions_unit(feed):
        structured = _units_from_unit_text(feed)
    if structured and any(official.values()):
        rendered = _render_h3_units(_merge_official_into_units(structured, official), None)
        if rendered.strip():
            source = "json_fields+informal_feed" if _has_informal_label(feed) else "json_fields"
            return rendered.strip(), source
    if structured and _has_informal_label(feed):
        rendered = _render_h3_units(structured, None)
        if rendered.strip():
            return rendered.strip(), "informal_feed"
    if any(official.values()):
        units = _units_from_payload(_payload_without_feed(data))
        if not units:
            units = [_unit_from_sections(official)]
        rendered = _render_h3_units(units, None)
        if rendered.strip():
            return rendered.strip(), "json_fields"
    if feed and not _json_document(feed):
        return feed, ""
    return "", ""


def _fold_director_text(text):
    """Fold a director payload into markdown. Official markdown stays put."""
    raw = str(text or "")
    loaded = _load_feed_json(raw)
    if isinstance(loaded, dict):
        drafted, source = _compose_director_draft(loaded)
        if drafted.strip():
            return drafted.strip(), source
        return raw, source
    if isinstance(loaded, list):
        drafted, source = _compose_director_draft({"segments": loaded})
        if drafted.strip():
            return drafted.strip(), source
    if _has_informal_label(raw) and not _has_official_field_line(raw):
        units = _units_from_informal(raw)
        if units:
            rendered = _render_h3_units(units, None)
            if rendered.strip():
                return rendered.strip(), "informal_feed"
    return raw, ""


def _extract_feed_source(raw, previous=""):
    data = extract_json_object(raw)
    if isinstance(data, dict):
        text, source = _compose_director_draft(data)
        if text.strip():
            return text.strip(), source
        return str(previous or ""), source
    cleaned = strip_fence(raw)
    if _json_document(cleaned):
        loaded = _load_feed_json(cleaned)
        if isinstance(loaded, (dict, list)):
            payload = loaded if isinstance(loaded, dict) else {"segments": loaded}
            text, source = _compose_director_draft(payload)
            if text.strip():
                return text.strip(), source
        return str(previous or ""), ""
    folded, source = _fold_director_text(cleaned)
    if source and folded.strip():
        return folded.strip(), source
    if mentions_unit(cleaned) or _has_official_field_line(cleaned):
        return cleaned.strip(), ""
    return str(previous or ""), ""


def extract_feed(raw, previous=""):
    text, _source = _extract_feed_source(raw, previous)
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
    """The same 15s packing the machine check uses. The model does not regroup.

    Merge is allowed. D-lock marks do not exist yet, so they do not split a unit.
    A span over 15 seconds is split, not clamped.
    """
    runner = importlib.import_module("end.stage_runner")
    durations = shot_duration_list(state)
    return runner.group_shot_durations(durations, limit)


def _h3_locked(state):
    """Missing model lock stays on H3, the same default as the feed gate."""
    model = str(((state or {}).get("locks") or {}).get("model") or "h3").strip().lower()
    return model == "h3"


def _generator_system(state):
    if not _h3_locked(state):
        return GENERATOR_SYSTEM
    system = GENERATOR_SYSTEM + "\n" + H3_FILL_ONLY
    rule = style_lock_rule(agreed_style(state))
    if rule:
        system += "\n" + rule
    return system


def _section_lines(style, shots=None):
    line = style_lock_line(style)
    rows = []
    for name in H3_SECTIONS:
        if name == "subject_definitions" and line:
            rows.append("subject_definitions:\n%s" % line)
        elif name == "detailed_description" and shots:
            rows.append("detailed_description:\n%s" % "\n".join(shot["heading"] for shot in shots))
        else:
            rows.append("%s:" % name)
    return "\n".join(rows)


def h3_packing_scaffold(groups, style=""):
    """Headings, planned durations, the shot skeleton, and the four section lines.

    The model fills the bodies. It does not invent another Ref2VA layout,
    and it does not write summary or retention_analysis. When ``style`` is
    the agreed episode style, the lock sentence is already the first line of
    subject_definitions. The model does not write that sentence. The
    ``[Shot N]`` lines and their ``At MM:SS.mmm`` cuts are already written.
    The model does not add, drop, reorder, or retime them.
    """
    runner = importlib.import_module("end.stage_runner")
    groups = list(groups or [])
    if not groups:
        return ""
    total = len(groups)
    header = [
        "H3 的 Ref2VA 骨架已经按%s写好。只填每个字段名下面的正文。" % UNIT_NAME,
        "不要改 ## 标题，不要改 duration，不要调换这四段的顺序。",
        "不要写 summary 或 retention_analysis。",
        "不要输出 JSON，不要把四个字段写成 JSON 键，也不要把多个单元写进同一个 detailed_description。",
        "不要改字段名，不要用 Subject、Soundscape、Music 代替这四行。",
        "不要只写第一个单元。空着的单元也留下这四个字段名、锁定句和镜号。",
        "只填或修改字段名下面的正文。已经写好的正文保留，只补还空着的槽。",
        "镜号和切点已经按这一段里的分镜时长算好，不是模型写的。"
        "每一段从 [Shot 1] 起，第一镜不写时间。"
        "后面每一镜紧跟预先算好的 At MM:SS.mmm，只累加这一段里前面各镜的时长。"
        "不要增删、合并或调换镜头，不要改本地镜号，不要改这些时间码，也不要自己发明切点。"
        "只在这些标题下补画面、对白和声音。",
        H3_SUBJECT_SOUND_RULE,
    ]
    rule = style_lock_rule(style)
    if rule:
        header.append(rule)
    blocks = ["\n".join(header)]
    for index, group in enumerate(groups, 1):
        blocks.append("\n".join((
            unit_heading(index, total),
            "duration: %s" % runner.format_seconds(sum(group)),
            _section_lines(style, _unit_shots(group)),
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
        skeleton = packing_shot_skeleton(state) if _h3_locked(state) else []
        for index, group in enumerate(groups, 1):
            start = cursor
            end = cursor + len(group) - 1
            cursor = end + 1
            shots = "镜头 %s" % start if start == end else "镜头 %s-%s" % (start, end)
            formula = "+".join(runner.format_seconds(item) for item in group)
            shown = runner.format_seconds(sum(group))
            lines.append("【%s %s/%s】%s：%s=%s" % (UNIT_NAME, index, count, shots, formula, shown))
            if index <= len(skeleton):
                unit = skeleton[index - 1]
                ident = "、".join(item["id"] for item in unit)
                rendered = " / ".join(item["heading"] for item in unit)
                lines.append("骨架（%s）：%s" % (ident, rendered))
        parts.append("\n".join(lines))
        if _h3_locked(state):
            draft = str(state.get("draft") or "").strip()
            if draft:
                folded = apply_packing_plan(draft, state).strip()
                parts.append("\n".join((
                    "当前骨架如下。只填或修改字段名下面的正文。",
                    "不要另起 JSON，不要改字段名，不要丢掉单元标题、duration、画面风格锁定句或镜号。",
                    "不要只写第一个单元。已经填好的单元保留，空着的槽只补正文。",
                    "修补时只改机检点名的问题。",
                    folded,
                )))
            else:
                parts.append(h3_packing_scaffold(groups, agreed_style(state)))
    if spoken:
        spoken_lines = [
            "对白和旁白必须逐字出现，包括角色前缀和表演提示。"
            "写在 <d> 里时，标签内也必须是这一整句，不能只留去掉前缀后的一句：",
        ]
        for line in spoken:
            spoken_lines.append("- %s" % line)
        parts.append("\n".join(spoken_lines))
    return "\n\n".join(part for part in parts if part)


# A duration or dialogue failure does not name a skill file. These originals
# own unit length and spoken lines, so the repair brief names them and
# select_nodes runs those writers again.
_UNIT_WRITERS = (
    "sd-feed-engine/references/feed-mode-and-gates.md",
    "short-drama-director/references/prompt-feeding-checklist.md",
)
_DIALOGUE_WRITERS = (
    "sd-feed-engine/SKILL.md",
    "short-drama-director/references/prompt-feeding-checklist.md",
)
_H3_UNIT_WRITER = "short-drama-director/references/h3-adapter.md"
_BOARD_HEAD = re.compile(r"^##\s*(?:镜号\s*)?(S\d+)\b", re.I)
_BOARD_SPOKEN = re.compile(r"^(?:对白|旁白)\s*[:：]\s*(.+)$")
_BOARD_DURATION = re.compile(r"^时长\s*[:：]\s*(\d+(?:\.\d+)?)")
_SHOT_BOUNDARY = re.compile(r"(?=\[Shot\s+\d+\])")
_H3_FIELD_MARK = re.compile(
    r"(?m)^(?P<name>%s):[ \t]*(?P<rest>.*)$" % "|".join(H3_SECTIONS)
)


def correct_unit_durations(text, groups, h3=None):
    """Write the planned heading and duration onto every unit.

    The sums are the 15s groups. A draft that used a shot length, or that
    closed early as one unit, is rewritten to this count. A span over 15s is
    not clamped. This does not invent a different grouping. When ``h3`` is
    set, a rebuilt unit keeps the four field lines even if a body is empty.
    """
    planned_groups = [list(group) for group in groups or []]
    planned = [sum(group) for group in planned_groups]
    raw = str(text or "")
    if not planned:
        return raw
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if len(heads) != len(planned):
        return _materialize_units(raw, planned_groups, h3=h3)
    runner = importlib.import_module("end.stage_runner")
    parts = [raw[: heads[0].start()]]
    total = len(planned)
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        block = _rewrite_heading(raw[match.start() : end], index + 1, total)
        parts.append(_write_unit_duration(block, planned[index], runner))
    return "".join(parts)


def _rewrite_heading(block, index, total):
    match = UNIT_HEAD_LINE.search(block)
    if not match:
        return block
    return block[: match.start()] + unit_heading(index, total) + block[match.end() :]


def _json_document(text):
    """A Seedance JSON blob has no line-start unit headings. Leave it wrapped."""
    peeled = _LEADING_DURATION.sub("", str(text or ""), count=1).strip()
    peeled = strip_fence(peeled).strip()
    return bool(peeled) and peeled[0] in "{["


def _draft_uses_h3(text):
    raw = str(text or "")
    return any(name in raw for name in H3_SECTIONS)


def _source_units(raw, heads):
    if not heads:
        text = str(raw or "").strip()
        if not text:
            return []
        return [_parse_one_block(text)]
    units = []
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        units.append(_parse_one_block(raw[match.start() : end]))
    return units


def _description_pieces(unit):
    sections = unit.get("sections") or {}
    desc = str(sections.get("detailed_description") or "")
    parts = _SHOT_BOUNDARY.split(desc)
    shots = [part.strip() for part in parts[1:] if part.strip()]
    if shots:
        lead = parts[0].strip()
        if lead:
            shots[0] = lead + "\n" + shots[0]
        return shots
    if desc.strip():
        return [desc.strip()]
    return []


def _assign_source_units(sources, groups):
    """Shot-marked prose follows the plan's shot counts. Other prose stays in order."""
    flat = []
    shot_mode = False
    for unit in sources:
        pieces = _description_pieces(unit)
        sections = unit.get("sections") or {}
        if any("[Shot" in piece for piece in pieces):
            shot_mode = True
        if not pieces:
            if any(str(sections.get(name) or "").strip() for name in H3_SECTIONS if name != "detailed_description"):
                flat.append(("", sections))
            continue
        for piece in pieces:
            flat.append((piece, sections))
    buckets = [[] for _ in groups]
    if not groups:
        return buckets
    if not shot_mode:
        for index, item in enumerate(flat):
            buckets[min(index, len(groups) - 1)].append(item)
        return buckets
    cursor = 0
    last = len(groups) - 1
    for index, group in enumerate(groups):
        if index == last:
            buckets[index] = flat[cursor:]
        else:
            take = len(group)
            buckets[index] = flat[cursor : cursor + take]
            cursor += take
    return buckets


def _render_planned_unit(index, total, seconds, items, h3, runner):
    lines = [
        unit_heading(index, total),
        "duration: %s" % runner.format_seconds(seconds),
    ]
    carried = {name: [] for name in H3_SECTIONS}
    seen = {name: set() for name in H3_SECTIONS}
    descriptions = []
    for piece, source in items:
        if str(piece or "").strip():
            descriptions.append(str(piece).strip())
        for name in H3_SECTIONS:
            if name == "detailed_description":
                continue
            body = str((source or {}).get(name) or "").strip()
            if body and body not in seen[name]:
                seen[name].add(body)
                carried[name].append(body)
    rendered = {name: "\n\n".join(carried[name]).strip() for name in H3_SECTIONS}
    rendered["detailed_description"] = "\n\n".join(descriptions).strip()
    if h3:
        for name in H3_SECTIONS:
            body = rendered[name]
            lines.append("%s:" % name)
            if body:
                lines.append(body)
    elif rendered["detailed_description"]:
        lines.append(rendered["detailed_description"])
    return "\n".join(lines)


def _materialize_units(raw, groups, h3=None):
    """Build one block per planned group. Durations are the group sums.

    An H3 unit always gets the four field lines. An empty body stays empty
    so the program does not invent section prose.
    """
    if not groups:
        return raw
    if not UNIT_HEAD_LINE.search(raw) and _json_document(raw):
        return raw
    if not str(raw or "").strip():
        return raw
    runner = importlib.import_module("end.stage_runner")
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    prefix = raw[: heads[0].start()] if heads else ""
    buckets = _assign_source_units(_source_units(raw, heads), groups)
    if h3 is None:
        h3 = _draft_uses_h3(raw)
    else:
        h3 = bool(h3) or _draft_uses_h3(raw)
    blocks = []
    total = len(groups)
    for index, group in enumerate(groups):
        blocks.append(_render_planned_unit(
            index + 1,
            total,
            sum(group),
            buckets[index],
            h3,
            runner,
        ))
    body = "\n\n".join(blocks)
    if body and not body.endswith("\n"):
        body += "\n"
    if str(prefix or "").strip():
        return prefix.rstrip() + "\n\n" + body
    return body


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


def _storyboard_shot_spoken(text):
    """Spoken lines in storyboard order, one bucket per shot heading."""
    shots = []
    current = None
    loose = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if _BOARD_HEAD.match(stripped):
            if current is not None:
                shots.append(current)
            current = {"spoken": [], "duration": None}
            continue
        spoken = _BOARD_SPOKEN.match(stripped)
        if spoken:
            uttered = spoken.group(1).strip()
            if not uttered:
                continue
            if current is None:
                loose.append(uttered)
            else:
                current["spoken"].append(uttered)
            continue
        duration = _BOARD_DURATION.match(stripped)
        if duration and current is not None and current["duration"] is None:
            current["duration"] = float(duration.group(1))
    if current is not None:
        shots.append(current)
    if loose:
        if shots:
            shots[0]["spoken"] = loose + shots[0]["spoken"]
        else:
            shots.append({"spoken": loose, "duration": None})
    return shots


def _positive_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _spoken_buckets(state):
    """One spoken-line list per positive shot the packing groups consumed."""
    shots = _storyboard_shot_spoken(_storyboard_text(state))
    durations = shot_duration_list(state)
    if not shots:
        return []
    positive = [item for item in durations if _positive_float(item) is not None]
    timed = [shot for shot in shots if _positive_float(shot.get("duration")) is not None]
    leftover = []
    if timed and len(timed) == len(positive):
        timed_ids = {id(shot) for shot in timed}
        for shot in shots:
            if id(shot) not in timed_ids:
                leftover.extend(shot["spoken"])
        shots = timed
        durations = positive
    elif len(shots) != len(durations):
        buckets = []
        pending = []
        used = 0
        for seconds in durations:
            lines = list(shots[used]["spoken"]) if used < len(shots) else []
            used += 1
            pending.extend(lines)
            if _positive_float(seconds) is None:
                continue
            buckets.append(pending)
            pending = []
        for shot in shots[used:]:
            pending.extend(shot["spoken"])
        if pending:
            if buckets:
                buckets[-1].extend(pending)
            else:
                buckets.append(pending)
        return buckets
    buckets = []
    pending = []
    for shot, seconds in zip(shots, durations):
        pending.extend(shot["spoken"])
        if _positive_float(seconds) is None:
            continue
        buckets.append(list(pending))
        pending = []
    pending.extend(leftover)
    if pending:
        if buckets:
            buckets[-1].extend(pending)
        else:
            buckets.append(pending)
    return buckets


def spoken_lines_for_units(state, groups):
    """Storyboard lines for each planned unit, in shot order."""
    buckets = _spoken_buckets(state)
    units = [[] for _ in (groups or [])]
    if not units:
        return units
    cursor = 0
    for index, group in enumerate(groups):
        for _shot in group:
            if cursor < len(buckets):
                units[index].extend(buckets[cursor])
            cursor += 1
    if cursor < len(buckets):
        for extra in buckets[cursor:]:
            units[-1].extend(extra)
    return units


def _append_to_field(body, name, snippet):
    matches = list(_H3_FIELD_MARK.finditer(body))
    target = None
    for index, match in enumerate(matches):
        if match.group("name") == name:
            target = index
            break
    if target is None:
        return None
    if target + 1 < len(matches):
        at = matches[target + 1].start()
        return body[:at].rstrip() + "\n" + snippet + "\n" + body[at:]
    return body.rstrip() + "\n" + snippet


def _insert_field(body, name, snippet):
    section = "%s:\n%s" % (name, snippet)
    matches = list(_H3_FIELD_MARK.finditer(body))
    for match in matches:
        if match.group("name") == "overall_soundscape":
            return body[: match.start()] + section + "\n" + body[match.start() :]
    return body.rstrip() + "\n" + section


def _insert_spoken(block, line, h3_fields):
    """Put the exact storyboard line in this unit, outside overall_soundscape."""
    spoken = str(line or "").strip()
    if not spoken or spoken in block:
        return block
    snippet = "<d>[Chinese] %s</d>" % spoken
    trailing = block[len(block.rstrip()) :]
    body = block.rstrip()
    placed = _append_to_field(body, "detailed_description", snippet)
    if placed is None and h3_fields:
        placed = _insert_field(body, "detailed_description", snippet)
    if placed is None:
        placed = body + "\n" + snippet
    return placed + trailing


def place_owned_spoken_lines(text, state):
    """Write each storyboard line into the unit that owns its shot.

    A line the model never wrapped in ``<d>`` is still written. A line already
    in that unit is left where it is. Lines are not copied into
    overall_soundscape.
    """
    groups = packing_groups(state)
    owned = spoken_lines_for_units(state, groups)
    if not groups or not any(owned):
        return text
    raw = str(text or "")
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if len(heads) != len(groups):
        return raw
    h3_fields = _draft_uses_h3(raw)
    parts = [raw[: heads[0].start()]]
    changed = False
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        block = raw[match.start() : end]
        updated = block
        for line in owned[index]:
            updated = _insert_spoken(updated, line, h3_fields)
        if updated != block:
            changed = True
        parts.append(updated)
    if not changed:
        return raw
    return "".join(parts)


def _machine_text(machine_report):
    report = machine_report or {}
    errors = "\n".join(str(item) for item in (report.get("errors") or []))
    if not errors.strip():
        errors = str(report.get("report") or "")
    return errors


def packing_repair_targets(machine_report, h3=False):
    """Skill paths a duration, dialogue, style-lock, or shot-skeleton failure reruns.

    Other failures name nothing here. ``select_nodes`` then leaves them on
    the director, which is what an unnamed H1 report already does.
    A style-lock failure names the packing writers so they put the locked
    sentence back. It does not ask them to compose a new style.
    A shot-skeleton failure names those same writers so they restore the
    precomputed local headings. It does not ask them to invent a cut.
    """
    errors = _machine_text(machine_report)
    found = []
    duration = any(mark in errors for mark in ("时长被压缩", "不能钳成", "必须把这一镜拆开"))
    dialogue = "对白被压缩" in errors
    if duration:
        found.extend(_UNIT_WRITERS)
        if h3:
            found.append(_H3_UNIT_WRITER)
    if dialogue:
        found.extend(_DIALOGUE_WRITERS)
    if h3 and "画风锁" in errors:
        found.extend(_STYLE_WRITERS)
    if h3 and "镜号锁" in errors:
        found.extend(_SKELETON_WRITERS)
    ordered = []
    seen = set()
    for relative in found:
        if relative in seen:
            continue
        seen.add(relative)
        ordered.append(relative)
    return ordered


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
            if current in H3_OMITTED:
                continue
            rest = field.group(2).strip()
            if rest:
                sections[current].append(rest)
            continue
        if current in H3_OMITTED:
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
        for name in H3_SECTIONS:
            body = str(sections.get(name) or "").strip()
            lines.append("%s:" % name)
            if body:
                lines.append(body)
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return "\n\n".join(blocks).strip() + "\n"


def restore_h3_scaffold(text, state=None):
    """Turn a JSON blob or a merged description back into per-unit H3 markdown.

    A draft that is already markdown keeps prose that already sits in the
    four sections. ``summary`` and ``retention_analysis`` are stripped when
    the model still emits them. Spoken lines are not invented here. Missing
    field lines are put back later, with empty bodies.
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
    runner = importlib.import_module("end.stage_runner")
    if not units:
        return runner.strip_omitted_h3_sections(raw)
    rendered = _render_h3_units(units, packing_groups(state or {}))
    if not rendered.strip():
        return runner.strip_omitted_h3_sections(raw)
    return runner.strip_omitted_h3_sections(rendered)


def format_cut_timecode(seconds):
    """``MM:SS.mmm`` from a second count. The unit timeline starts at 00:00.000."""
    total_ms = int(round(float(seconds) * 1000.0))
    if total_ms < 0:
        total_ms = 0
    minutes, remainder = divmod(total_ms, 60000)
    secs, millis = divmod(remainder, 1000)
    return "%02d:%02d.%03d" % (minutes, secs, millis)


def _unit_shots(durations, ids=None):
    """Local headings for one packed unit. Shot 1 has no cut time."""
    shots = []
    elapsed = 0.0
    for index, raw in enumerate(durations or [], 1):
        seconds = float(raw)
        cut = None if index == 1 else format_cut_timecode(elapsed)
        shot = {
            "local": index,
            "duration": seconds,
            "cut": cut,
            "heading": "[Shot 1]" if index == 1 else "[Shot %d] At %s" % (index, cut),
        }
        if ids is not None and index - 1 < len(ids):
            shot["id"] = ids[index - 1]
        shots.append(shot)
        elapsed += seconds
    return shots


def _canonical_shot_id(raw, fallback_index):
    match = re.search(r"(\d+)", str(raw or ""))
    if match:
        return "S%02d" % int(match.group(1))
    return "S%02d" % int(fallback_index)


def _storyboard_heading_ids(text):
    found = []
    for line in str(text or "").splitlines():
        match = _BOARD_HEAD.match(line.strip())
        if match:
            found.append(_canonical_shot_id(match.group(1), len(found) + 1))
    return found


def _storyboard_duration_rows(text):
    """One row per ``时长`` line, in the same order as ``storyboard_duration_list``."""
    rows = []
    current = ""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        head = _BOARD_HEAD.match(stripped)
        if head:
            current = _canonical_shot_id(head.group(1), len(rows) + 1)
            continue
        duration = _BOARD_DURATION.match(stripped)
        if not duration:
            continue
        shot_id = current or _canonical_shot_id("", len(rows) + 1)
        current = ""
        rows.append({"id": shot_id, "duration": float(duration.group(1))})
    return rows


def _packing_shot_rows(state):
    """Positive shots in the same order ``packing_groups`` consumes.

    Canvas durations win when any shot declares one, matching
    ``shot_duration_list``. Ids come from the shot, otherwise the storyboard
    heading in that same order.
    """
    state = state or {}
    canvas = []
    for shot in state.get("shots") or []:
        if not isinstance(shot, dict) or shot.get("duration") in (None, ""):
            continue
        try:
            seconds = float(shot.get("duration"))
        except (TypeError, ValueError):
            continue
        if seconds <= 0:
            continue
        canvas.append((shot, seconds))
    if canvas:
        board_ids = _storyboard_heading_ids(_storyboard_text(state))
        rows = []
        board_at = 0
        for index, (shot, seconds) in enumerate(canvas):
            raw = shot.get("id") if shot.get("id") not in (None, "") else shot.get("shot_id")
            if str(raw or "").strip():
                shot_id = _canonical_shot_id(raw, index + 1)
            elif board_at < len(board_ids):
                shot_id = board_ids[board_at]
                board_at += 1
            else:
                shot_id = "S%02d" % (index + 1)
            rows.append({"id": shot_id, "duration": seconds})
        return rows
    return [row for row in _storyboard_duration_rows(_storyboard_text(state)) if row["duration"] > 0]


def packing_shot_skeleton(state, limit=15):
    """Local ``[Shot N]`` headings for each packed unit.

    Numbers restart at 1 inside the unit. ``[Shot 1]`` has no cut. Each later
    cut is the sum of the earlier shots in that unit, as ``MM:SS.mmm``.
    A span over 15 seconds stays its own unit at its real length. It is not
    clamped, and it is not given a cut time by shortening it.
    """
    rows = _packing_shot_rows(state)
    if not rows:
        return []
    runner = importlib.import_module("end.stage_runner")
    groups = runner.group_shot_durations([row["duration"] for row in rows], limit)
    skeleton = []
    cursor = 0
    for group in groups:
        ids = [rows[cursor + offset]["id"] for offset in range(len(group))]
        cursor += len(group)
        skeleton.append(_unit_shots(group, ids))
    return skeleton


def format_shot_skeleton(skeleton):
    """One line per unit: storyboard ids, then the local headings the writer fills."""
    lines = []
    units = list(skeleton or [])
    total = len(units)
    for index, shots in enumerate(units, 1):
        ident = "、".join(str(shot.get("id") or "") for shot in shots if shot.get("id"))
        rendered = " / ".join(shot["heading"] for shot in shots)
        if ident:
            lines.append("【%s %s/%s】%s：%s" % (UNIT_NAME, index, total, ident, rendered))
        else:
            lines.append("【%s %s/%s】%s" % (UNIT_NAME, index, total, rendered))
    return "\n".join(lines)


_SHOT_HEADING_LINE = re.compile(r"^[ \t]*\[Shot\s+(\d+)\]([^\n]*)$")
_CLOCK_PREFIX = re.compile(
    r"^[ \t]{0,14}(?:At|在)[ \t]*\d{1,2}:\d{2}\.\d{3}(?:[ \t]*处)?[ \t]*[,，]?[ \t]*",
    re.IGNORECASE,
)
_OFFICIAL_CUT = re.compile(r"^[ \t]+At[ \t]+(\d{2}:\d{2}\.\d{3})(?!\d)")
_EARLY_CLOCK = re.compile(
    r"^[^\n]{0,14}?(?:At|在)[ \t]*\d{1,2}:\d{2}",
    re.IGNORECASE,
)


def _field_parts(block, name):
    lines = str(block or "").splitlines(keepends=True)
    header_at = None
    inline = ""
    for index, raw_line in enumerate(lines):
        field = _H3_FIELD_LINE.match(raw_line.strip())
        if field and field.group(1) == name:
            header_at = index
            inline = field.group(2).strip()
            break
    if header_at is None:
        return None
    body_end = header_at + 1
    while body_end < len(lines):
        field = _H3_FIELD_LINE.match(lines[body_end].strip())
        if field and field.group(1) in _H3_FIELD_NAMES:
            break
        body_end += 1
    return header_at, inline, lines[header_at + 1:body_end], body_end, lines


def _detailed_description_text(block):
    parsed = _field_parts(block, "detailed_description")
    if parsed is None:
        return None
    _header, inline, body_lines, _end, _lines = parsed
    parts = []
    if inline:
        parts.append(inline)
    for raw in body_lines:
        parts.append(raw.rstrip("\r\n"))
    return "\n".join(parts)


def _shot_headings(block):
    body = _detailed_description_text(block)
    if body is None:
        return []
    found = []
    for line in body.splitlines():
        match = _SHOT_HEADING_LINE.match(line)
        if match:
            found.append((int(match.group(1)), match.group(2)))
    return found


def shot_skeleton_failures(text, state=None):
    """Fail a packed unit whose local shots disagree with the storyboard skeleton.

    The skeleton is known before packing. ``[Shot 1]`` has no timestamp.
    Each later heading is ``[Shot N] At MM:SS.mmm`` at the sum of the earlier
    shots in that unit only. Episode-wide numbers, a missing or extra
    ``[Shot N]``, a timestamp on the first shot, or any other cut time fails.
    This does not invent a cut. H2 still checks each unit on its own; this
    gate does not weaken that rule.
    """
    skeleton = packing_shot_skeleton(state)
    if not skeleton:
        return []
    units = _style_units(text)
    errors = []
    if len(units) != len(skeleton):
        errors.append(
            "镜号锁：封装稿有 %s 段，分镜骨架是 %s 段。不要自己另分组，不要自己发明切点。"
            % (len(units), len(skeleton))
        )
    total = max(len(units), len(skeleton))
    for index, shots in enumerate(skeleton):
        if index >= len(units):
            break
        block = units[index]
        label = _unit_label(block, index + 1, total)
        found = _shot_headings(block)
        expected_numbers = [shot["local"] for shot in shots]
        found_numbers = [number for number, _tail in found]
        if found_numbers != expected_numbers:
            shown = "、".join("[Shot %s]" % number for number in found_numbers) or "（没有 [Shot N]）"
            want = "、".join(shot["heading"] for shot in shots)
            errors.append(
                "镜号锁：%s 的镜号是 %s，必须按这个本地骨架写：%s。"
                "不要沿用全集镜号，不要增删、合并或调换镜头，不要自己发明切点。"
                % (label, shown, want)
            )
            continue
        for shot, (_number, tail) in zip(shots, found):
            if shot["local"] == 1:
                if _EARLY_CLOCK.search(tail or ""):
                    errors.append(
                        "镜号锁：%s 的 [Shot 1] 不能带时间戳（写了 At 也不行，包括 At 00:00.000）。"
                        "首镜没有切点。" % label
                    )
                continue
            match = _OFFICIAL_CUT.match(tail or "")
            got = match.group(1) if match else ""
            if got != shot["cut"]:
                errors.append(
                    "镜号锁：%s 的 [Shot %s] 切点必须是 At %s，紧跟在镜号后面。"
                    "不要自己改切点，也不要漏掉时间码。"
                    % (label, shot["local"], shot["cut"])
                )
    return errors


def _strip_clock(tail, keep_comma):
    match = _CLOCK_PREFIX.match(tail or "")
    if not match:
        return tail or ""
    rest = tail[match.end():]
    if keep_comma and rest.strip() and re.search(r"[,，]", match.group(0)):
        if rest.startswith((" ", "\t")):
            return "," + rest
        return ", " + rest
    return rest.lstrip(" \t")


def _join_heading(heading, prose):
    if not str(prose or "").strip():
        return heading
    if prose[0].isspace() or prose[0] in ",，":
        return heading + prose
    return heading + " " + prose


def _rewrite_shot_lines(lines, shots):
    """Renumber the headings already present. Append any heading that is missing.

    Prose that followed a heading stays with that heading. Nothing new is
    written after a heading the model did not already fill. Extra headings
    past the skeleton are left for the gate to name.
    """
    lines = list(lines)
    indexes = [index for index, line in enumerate(lines) if _SHOT_HEADING_LINE.match(line)]
    paired = min(len(indexes), len(shots))
    for slot in range(paired):
        line_index = indexes[slot]
        match = _SHOT_HEADING_LINE.match(lines[line_index])
        indent = re.match(r"[ \t]*", lines[line_index]).group(0)
        prose = _strip_clock(match.group(2) or "", shots[slot]["local"] != 1)
        lines[line_index] = _join_heading(indent + shots[slot]["heading"], prose)
    if paired >= len(shots):
        return lines
    missing = [shot["heading"] for shot in shots[paired:]]
    if paired == 0 and any(line.strip() for line in lines):
        lines.insert(0, shots[0]["heading"])
        lines.extend(missing[1:])
        return lines
    if paired == 0:
        return missing
    lines.extend(missing)
    return lines


def _replace_description(parsed, shots):
    header_at, inline, body_lines, body_end, lines = parsed
    content = []
    if inline:
        content.append(inline)
    for raw in body_lines:
        content.append(raw.rstrip("\r\n"))
    rewritten = _rewrite_shot_lines(content, shots)
    new_lines = ["detailed_description:\n"]
    for line in rewritten:
        new_lines.append(line + "\n")
    return "".join(lines[:header_at] + new_lines + lines[body_end:])


def _insert_description(block, shots):
    snippet = "\n".join(shot["heading"] for shot in shots)
    trailing = "\n" if str(block).endswith("\n") else ""
    placed = _insert_field(str(block).rstrip("\n"), "detailed_description", snippet)
    if trailing and not placed.endswith("\n"):
        placed += "\n"
    return placed


def _patch_unit_block(block, shots):
    if not shots:
        return block
    parsed = _field_parts(block, "detailed_description")
    if parsed is None:
        if "subject_definitions" not in block and "overall_soundscape" not in block:
            return block
        return _insert_description(block, shots)
    return _replace_description(parsed, shots)


def inject_shot_skeleton(text, skeleton):
    """Write the precomputed local headings back into each packed unit.

    Existing shot prose stays on the heading it already followed, in order.
    The first heading loses any timestamp. Later headings get the exact
    ``At MM:SS.mmm``. A missing heading is inserted with no new scene text.
    An extra heading is left in place so the gate can name it. This runs
    after the model writes and again after a repair.
    """
    skeleton = [list(unit or []) for unit in (skeleton or [])]
    raw = str(text or "")
    if not skeleton or not any(skeleton):
        return raw
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if not heads:
        if len(skeleton) == 1 and (
            "subject_definitions" in raw or "detailed_description" in raw
        ):
            return _patch_unit_block(raw, skeleton[0])
        return raw
    if len(heads) != len(skeleton):
        return raw
    parts = [raw[: heads[0].start()]]
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        parts.append(_patch_unit_block(raw[match.start():end], skeleton[index]))
    return "".join(parts)


def _rebuild_unit_fields(block):
    """Put the four field lines back. Bodies that are already there stay.

    A unit that already has every field name is left byte for byte, so a
    second pass does not rewrap prose. Loose lines land in
    ``detailed_description``. Nothing is invented for an empty field.
    """
    if all(_field_parts(block, name) is not None for name in H3_SECTIONS):
        return block
    parsed = _parse_one_block(block)
    head = UNIT_HEAD_LINE.search(block)
    lines = []
    if head:
        lines.append(head.group(0).strip())
    duration = str(parsed.get("duration") or "").strip()
    if duration:
        lines.append("duration: %s" % duration)
    for name in H3_SECTIONS:
        body = str((parsed.get("sections") or {}).get(name) or "").strip()
        lines.append("%s:" % name)
        if body:
            lines.append(body)
    text = "\n".join(lines)
    if str(block).endswith("\n\n"):
        return text + "\n\n"
    return text + "\n"


def _ensure_h3_fields(text):
    raw = str(text or "")
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if not heads:
        return raw
    parts = [raw[: heads[0].start()]]
    changed = False
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        block = raw[match.start():end]
        rebuilt = _rebuild_unit_fields(block)
        if rebuilt != block:
            changed = True
        parts.append(rebuilt)
    if not changed:
        return raw
    return "".join(parts)


def apply_packing_plan(text, state):
    """Unwrap an H3 blob, lock the planned units, then put spoken lines back.

    Durations come from the groups. A missing storyboard line is written into
    the unit that owns that shot, even when the model never opened a ``<d>``.
    The agreed style sentence is written into every H3 unit after that, including
    a repair that dropped it. The local shot skeleton is written after that,
    again on a repair, so ``[Shot N]`` and ``At MM:SS.mmm`` match the storyboard.
    Director JSON is folded onto that skeleton first. ``last_fold_source``
    records ``json_fields``, ``informal_feed``, and ``materialize`` when one
    of those paths ran.
    """
    runner = importlib.import_module("end.stage_runner")
    h3 = _h3_locked(state)
    source = ""
    incoming = str(text or "")
    if h3:
        incoming, source = _fold_director_text(incoming)
    prepared = restore_h3_scaffold(incoming, state) if h3 else incoming
    groups = packing_groups(state)
    heads_before = len(list(UNIT_HEAD_LINE.finditer(str(prepared or ""))))
    corrected = correct_unit_durations(prepared, groups, h3=h3)
    if groups and heads_before != len(groups):
        source = _join_fold_source(source, "materialize")
    if h3 and groups:
        corrected = _ensure_h3_fields(corrected)
    spoken = runner.storyboard_spoken_lines(_storyboard_text(state))
    restored = restore_spoken_lines(corrected, spoken)
    placed = place_owned_spoken_lines(restored, state)
    if not h3:
        apply_packing_plan.last_fold_source = source
        return placed
    styled = inject_style_lock(placed, agreed_style(state))
    result = inject_shot_skeleton(styled, packing_shot_skeleton(state))
    apply_packing_plan.last_fold_source = source
    return result


apply_packing_plan.last_fold_source = ""


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
    errors = _machine_text(state.get("machine_report"))
    h3 = _h3_locked(state)
    length = []
    if any(mark in errors for mark in ("时长被压缩", "不能钳成", "必须把这一镜拆开")):
        length.extend(_UNIT_WRITERS)
        if h3:
            length.append(_H3_UNIT_WRITER)
    if "对白被压缩" in errors:
        length.extend(_DIALOGUE_WRITERS)
    length_paths = []
    seen = set()
    for relative in length:
        if relative in seen:
            continue
        seen.add(relative)
        length_paths.append(relative)
    if length_paths:
        user = user.rstrip() + "\n\n" + "\n".join((
            "时长或对白没过投喂机检。重跑这些节点原文，按已经算好的单元数和 duration 重写画面与对白：",
            *length_paths,
        ))
    if h3 and "画风锁" in errors:
        line = style_lock_line(agreed_style(state))
        user = user.rstrip() + "\n\n" + "\n".join((
            "画风锁没过。不要改写画风，不要翻译，也不要新写一种风格。",
            "每一段 subject_definitions 的第一行必须原样是这一句：",
            line or "（锁定句）",
            "删掉别处自拟的风格句，包括只写在房间或道具主体上的那句。",
            "重跑这些节点时只把锁定句补回去：",
            *_STYLE_WRITERS,
        ))
    if h3 and "镜号锁" in errors:
        skeleton = packing_shot_skeleton(state)
        user = user.rstrip() + "\n\n" + "\n".join((
            "镜号锁没过。切点和镜号已经按分镜算好。不要自己发明切点，不要沿用全集镜号。",
            "每一段从 [Shot 1] 起。第一镜不写时间。后面每一镜必须紧跟下面的 At MM:SS.mmm。",
            "不要增删、合并或调换镜头，不要改这些时间码。把这个骨架原样补回去，只保留已有的画面和对白：",
            format_shot_skeleton(skeleton) or "（这一段没有分镜骨架）",
            "重跑这些节点时只恢复这个骨架：",
            *_SKELETON_WRITERS,
        ))
    return [system, {"role": "user", "content": user}]


def critic_messages(state):
    notes = []
    if _h3_locked(state):
        rule = style_lock_rule(agreed_style(state))
        if rule:
            notes.append(
                "对照锁定句审查。缺这句、第一行不是这句、或还有别的风格句，severity 写 blocking。"
                "不要改稿，不要另写画风。\n%s" % rule
            )
        skeleton = packing_shot_skeleton(state)
        if skeleton:
            notes.append(
                "对照这个镜号骨架审查。不从 [Shot 1] 起、首镜带时间、切点不是下面的 At 时间、"
                "或多了少了镜头，severity 写 blocking。不要改稿，不要另写切点。\n%s"
                % format_shot_skeleton(skeleton)
            )
    notes.append("待审%s：\n%s" % (DRAFT_NAME, str(state.get("draft") or "").strip()))
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": critic_user(TASK, *notes)},
    ]


# Packing diagnosis. This log is written on every generate attempt, including
# when AIVIDEO_DEBUG_TRACE is off. It does not change the draft.
_INLINE_CHARS = 4000
_DEBUG_LOCK = threading.Lock()
_NODE_ORIGINAL = re.compile(r"# 本节点原文[ \t]+(\S+)")
_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?\S+")
_API_KEY = re.compile(
    r"(?i)\b((?:api[_-]?key|x-api-key|access[_-]?key|secret[_-]?key)\s*[=:]\s*)\S+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{8,}")
_SK_KEY = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_SIGNED_URL = re.compile(
    r"https?://[^\s\"'<>]+(?:X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|"
    r"OSSAccessKeyId|Signature=|x-oss-signature|x-oss-credential)=[^\s\"'<>]*",
    re.I,
)
_META_TEXT_KEYS = (
    "finish_reason",
    "incomplete_reason",
    "response_status",
    "response_error",
)
_META_INT_KEYS = ("input_tokens", "output_tokens", "reasoning_tokens")


def resolve_feed_debug_dir(folder):
    """Project ``debug/`` when a folder was passed, otherwise the repo ``data/`` dir."""
    if str(folder or "").strip():
        return Path(folder) / "debug"
    from end.db import ROOT

    return Path(ROOT) / "data"


def _redact_debug(text):
    raw = str(text or "")
    raw = _SIGNED_URL.sub("[redacted-url]", raw)
    raw = _AUTH_HEADER.sub(r"\1[redacted]", raw)
    raw = _API_KEY.sub(r"\1[redacted]", raw)
    raw = _BEARER.sub("Bearer [redacted]", raw)
    raw = _SK_KEY.sub("[redacted]", raw)
    return raw


def _empty_stop_meta():
    meta = {key: "" for key in _META_TEXT_KEYS}
    for key in _META_INT_KEYS:
        meta[key] = 0
    return meta


def _stop_meta(result):
    meta = _empty_stop_meta()
    if not isinstance(result, dict):
        return meta
    for key in _META_TEXT_KEYS:
        if key in result and result.get(key) is not None:
            meta[key] = _redact_debug(result.get(key))
    for key in _META_INT_KEYS:
        if key not in result or result.get(key) in (None, ""):
            continue
        try:
            meta[key] = int(result.get(key))
        except (TypeError, ValueError):
            meta[key] = 0
    return meta


def _unwrap_complete(complete, role, messages):
    """Ask for stop metadata, but hand ``run_generator`` the same text as before."""
    asked = True
    try:
        result = complete(role, messages, return_meta=True)
    except TypeError as exc:
        if "return_meta" not in str(exc):
            raise
        asked = False
        result = complete(role, messages)
    if asked and isinstance(result, dict) and "text" in result:
        value = result.get("text")
        text = "" if value is None else str(value)
        return text, _stop_meta(result)
    return result, _empty_stop_meta()


def _skill_from_messages(messages):
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        found = _NODE_ORIGINAL.search(str(message.get("content") or ""))
        if found:
            return found.group(1)
    return ""


def _heading_lines(text):
    return [match.group(0).strip() for match in UNIT_HEAD_LINE.finditer(str(text or ""))]


def _display_debug_path(path, folder):
    path = Path(path)
    if str(folder or "").strip():
        try:
            return path.resolve().relative_to(Path(folder).resolve()).as_posix()
        except (ValueError, OSError):
            pass
    try:
        from end.db import ROOT

        return path.resolve().relative_to(Path(ROOT).resolve()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


def _sidecar_name(directory, attempt, label):
    base = "feed-attempt-%s-%s" % (attempt, label)
    candidate = directory / ("%s.txt" % base)
    if not candidate.exists():
        return candidate.name
    index = 2
    while True:
        candidate = directory / ("%s-%s.txt" % (base, index))
        if not candidate.exists():
            return candidate.name
        index += 1


def _store_debug_text(directory, folder, attempt, label, text):
    original = "" if text is None else str(text)
    stored = _redact_debug(original)
    name = _sidecar_name(directory, attempt, label)
    path = directory / name
    path.write_text(stored, encoding="utf-8")
    ref = {
        "chars": len(original),
        "text_path": _display_debug_path(path, folder),
    }
    if len(stored) <= _INLINE_CHARS:
        ref["text"] = stored
    return ref


def _materialize_decision(text, state):
    """Same condition ``correct_unit_durations`` uses before it rewrites units."""
    groups = packing_groups(state or {})
    prepared = restore_h3_scaffold(text, state) if _h3_locked(state) else str(text or "")
    heads = list(UNIT_HEAD_LINE.finditer(str(prepared or "")))
    return bool(groups) and len(heads) != len(groups), len(heads), len(groups)


def packing_debug_snapshot(text, state, *, materialize_units=None, prepared_heading_count=None):
    """Planned count, headings, H3 fields, style lock, and shot headings for one draft."""
    raw = str(text or "")
    groups = packing_groups(state or {})
    planned = len(groups)
    headings = _heading_lines(raw)
    if materialize_units is None:
        materialize_units = bool(groups) and len(headings) != planned
    line = style_lock_line(agreed_style(state or {}))
    units = []
    for index, block in enumerate(_style_units(raw), 1):
        heading = ""
        match = UNIT_HEAD_LINE.search(block)
        if match:
            heading = match.group(0).strip()
        fields = {}
        for name in H3_SECTIONS:
            fields[name] = _field_parts(block, name) is not None
        shots = []
        for row in block.splitlines():
            if _SHOT_HEADING_LINE.match(row):
                shots.append(row.strip())
        units.append({
            "index": index,
            "heading": heading,
            "fields": fields,
            "style_lock": bool(line) and line in block,
            "shots": shots,
        })
    snapshot = {
        "chars": len(raw),
        "planned_units": planned,
        "heading_count": len(headings),
        "unit_headings": headings,
        "materialize_units": bool(materialize_units),
        "style_lock_line": line,
        "units": units,
    }
    if prepared_heading_count is not None:
        snapshot["prepared_heading_count"] = int(prepared_heading_count)
    return snapshot


def _selected_repair_nodes(brief, state):
    from AI.skills.registry import suite_files
    from Agents.workers.generator import _notes, _routing_context, select_nodes

    chosen = list(suite_files(TASK, _routing_context(TASK, brief)))
    return list(select_nodes(chosen, brief, _notes((state or {}).get("partials") or [])))


def write_feed_debug(
    state,
    attempt,
    brief,
    calls,
    *,
    extracted=None,
    planned_text=None,
    error="",
    fold_source="",
):
    """Append one packing generate attempt to ``debug/feed-debug.jsonl``."""
    state = state or {}
    folder = str(state.get("project_folder") or "")
    directory = resolve_feed_debug_dir(folder)
    directory.mkdir(parents=True, exist_ok=True)
    repair = int(attempt or 0) > 1
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_id": str(state.get("project_id") or ""),
        "attempt": int(attempt or 0),
        "repair": repair,
    }
    if error:
        record["error"] = _redact_debug(error)
    brief_ref = _store_debug_text(directory, folder, attempt, "brief", brief)
    record["brief_chars"] = brief_ref["chars"]
    record["brief_path"] = brief_ref["text_path"]
    if "text" in brief_ref:
        record["brief"] = brief_ref["text"]
    logged_calls = []
    for call in calls or []:
        label = str(call.get("label") or call.get("role") or "call")
        body = _store_debug_text(directory, folder, attempt, label, call.get("text") or "")
        item = {
            "role": str(call.get("role") or ""),
            "skill": str(call.get("skill") or ""),
            "chars": body["chars"],
            "unit_headings": _heading_lines(call.get("text") or ""),
            "text_path": body["text_path"],
        }
        if "text" in body:
            item["text"] = body["text"]
        item.update(call.get("meta") or _empty_stop_meta())
        logged_calls.append(item)
    record["calls"] = logged_calls
    if repair:
        try:
            record["selected_nodes"] = _selected_repair_nodes(brief, state)
        except Exception as exc:
            record["selected_nodes"] = []
            record["selected_nodes_error"] = "%s: %s" % (type(exc).__name__, exc)
        record["machine_errors"] = _redact_debug(_machine_text(state.get("machine_report")))
    if extracted is not None:
        extract_snap = packing_debug_snapshot(extracted, state)
        extract_body = _store_debug_text(directory, folder, attempt, "extract", extracted)
        extract_snap["text_path"] = extract_body["text_path"]
        if "text" in extract_body:
            extract_snap["text"] = extract_body["text"]
        record["extract_feed"] = extract_snap
    if planned_text is not None:
        ran, prepared_headings, _planned = _materialize_decision(extracted if extracted is not None else "", state)
        plan_snap = packing_debug_snapshot(
            planned_text,
            state,
            materialize_units=ran,
            prepared_heading_count=prepared_headings,
        )
        plan_body = _store_debug_text(directory, folder, attempt, "plan", planned_text)
        plan_snap["text_path"] = plan_body["text_path"]
        if "text" in plan_body:
            plan_snap["text"] = plan_body["text"]
        if str(fold_source or "").strip():
            plan_snap["fold_source"] = str(fold_source).strip()
        record["apply_packing_plan"] = plan_snap
    path = directory / "feed-debug.jsonl"
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _DEBUG_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    return path


def _safe_feed_debug(state, attempt, brief, calls, **kwargs):
    try:
        write_feed_debug(state, attempt, brief, calls, **kwargs)
    except Exception:
        return


def compile_feed_graph(complete, checker=None):
    check = checker or check_feed_draft

    def generate(state):
        attempt = int(state.get("attempt") or 0) + 1
        brief = generator_messages(state)[-1]["content"]
        calls = []
        specialist_index = {"n": 0}

        def traced(role, messages):
            text, meta = _unwrap_complete(complete, role, messages)
            try:
                if role == "specialist":
                    specialist_index["n"] += 1
                    label = "specialist-%s" % specialist_index["n"]
                else:
                    label = role or "call"
                logged = text if isinstance(text, str) else "" if text is None else str(text)
                calls.append({
                    "role": role,
                    "label": label,
                    "skill": _skill_from_messages(messages),
                    "text": logged,
                    "meta": meta,
                })
            except Exception:
                pass
            if text is None:
                return ""
            return text

        try:
            produced = run_generator(
                TASK,
                brief,
                role=_generator_system(state),
                complete=traced,
                partials=state.get("partials") or [],
                repair_text=brief if attempt > 1 else "",
            )
        except Exception as exc:
            message = "%s: %s" % (type(exc).__name__, exc)
            _safe_feed_debug(state, attempt, brief, calls, error=message)
            return {
                "attempt": attempt,
                "error": message,
                "generator_note": "model_error",
            }
        raw = produced.get("raw") or ""
        previous = str(state.get("draft") or "")
        extracted, extract_source = _extract_feed_source(raw, previous)
        planned_text = apply_packing_plan(extracted, state)
        fold_source = _join_fold_source(extract_source, apply_packing_plan.last_fold_source)
        draft = _patch_voiceover(planned_text)
        _safe_feed_debug(
            state,
            attempt,
            brief,
            calls,
            extracted=extracted,
            planned_text=planned_text,
            fold_source=fold_source,
        )
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
    script="",
    opening="",
    prior_draft="",
    skill_pack="",
    model=None,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    client=None,
    timeout=None,
    complete=None,
    checker=None,
    project_id="",
    project_folder="",
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
            "script": str(script or ""),
            "shots": list(shots or []),
            "skill_pack": str(skill_pack or ""),
            "project_id": str(project_id or ""),
            "project_folder": str(project_folder or ""),
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
