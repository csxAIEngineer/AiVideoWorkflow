"""Durations, spoken lines, asset lines, and apply_packing_plan."""

import importlib
import re
from end.product_names import (
    H3_SUBJECT_SOUND_RULE,
    UNIT_HEAD_LINE,
    UNIT_NAME,
    unit_heading,
)

from Agents.workers.feed.common import (
    GENERATOR_SYSTEM,
    H3_FILL_ONLY,
    H3_SECTIONS,
    _BOARD_DURATION,
    _BOARD_HEAD,
    _BOARD_SPOKEN,
    _UNIT_DURATION_LINE,
    _append_to_field,
    _insert_field,
    _storyboard_text,
    packing_groups,
    shot_duration_list,
)
from Agents.workers.feed.style import (
    _STYLE_WRITERS,
    agreed_style,
    inject_style_lock,
    style_lock_line,
    style_lock_rule,
)
from Agents.workers.feed.extract import (
    _fold_director_text,
    _join_fold_source,
    _json_document,
    _parse_one_block,
    restore_h3_scaffold,
)
from Agents.workers.feed.skeleton import (
    _SKELETON_WRITERS,
    _field_parts,
    _unit_shots,
    inject_shot_skeleton,
    packing_shot_skeleton,
)


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
        "若这一段已经写了资产：，那一行是程序按分镜资产栏写的，格式与分镜相同。"
        "不要改这行，不要另写一份资产列表，也不要靠角色名或英文单词补图号。",
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
            "每段尽量 8 到 10 秒，方便前后帧接长镜。硬区间 4 到 30 秒。"
            "单镜最长 30 秒，超过 30 秒就拆成下一单元，禁止钳成 30，也禁止留下短于 4 秒的段。"
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
                    "不要另起 JSON，不要改字段名，不要丢掉单元标题、duration、画面风格锁定句、镜号或资产：。",
                    "不要只写第一个单元。已经填好的单元保留，空着的槽只补正文。",
                    "修补时只改机检点名的问题。",
                    folded,
                )))
            else:
                scaffold = h3_packing_scaffold(groups, agreed_style(state))
                parts.append(inject_unit_assets(scaffold, packing_unit_asset_lines(state)))
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


_SHOT_BOUNDARY = re.compile(r"(?=\[Shot\s+\d+\])")


def correct_unit_durations(text, groups, h3=None):
    """Write the planned heading and duration onto every unit.

    The sums are the 8–10s groups (hard range 4–30 on Wan 3.0). A draft that used a shot
    length, or that closed early as one unit, is rewritten to this count. A
    span over the clip limit is not clamped. This does not invent a different grouping. When ``h3`` is
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


def packing_unit_asset_lines(state):
    """One ``资产：`` line per packed unit, from the storyboard 资产栏.

    Empty when those shots cannot be placed. A unit whose shots name nothing
    is an empty string, so a model-written list can be removed.
    """
    from end.seam_ledger import unit_storyboard_images
    from end.storyboard_contract import format_asset_line

    groups = packing_groups(state)
    if not groups:
        return []
    unions = unit_storyboard_images(
        {
            "shots": (state or {}).get("shots") or [],
            "storyboard": _storyboard_text(state),
        },
        len(groups),
    )
    if unions is None:
        return []
    return [format_asset_line(ids) for ids in unions]


_ASSET_LINE = re.compile(r"^[ \t]*资产[ \t]*[:：]")


def _patch_asset_line(block, line):
    """Drop every ``资产：`` line, then write the program line after duration."""
    raw_lines = str(block or "").splitlines(keepends=True)
    kept = [raw for raw in raw_lines if not _ASSET_LINE.match(raw.rstrip("\r\n"))]
    line = str(line or "").strip()
    if not line:
        return "".join(kept)
    insert_at = 0
    for index, raw in enumerate(kept):
        core = raw.strip()
        if _UNIT_DURATION_LINE.match(core):
            insert_at = index + 1
            break
        if UNIT_HEAD_LINE.match(core):
            insert_at = index + 1
    if insert_at > 0 and not kept[insert_at - 1].endswith(("\n", "\r")):
        kept[insert_at - 1] = kept[insert_at - 1] + "\n"
    kept.insert(insert_at, line + "\n")
    return "".join(kept)


def inject_unit_assets(text, lines):
    """Write the program ``资产：`` line into each packed unit.

    A line the model wrote is removed, including one inside a section body.
    An empty program line removes a model line and does not invent ids.
    """
    planned = list(lines or [])
    raw = str(text or "")
    if not planned:
        return raw
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if not heads:
        if len(planned) == 1 and (
            "subject_definitions" in raw or "detailed_description" in raw
        ):
            return _patch_asset_line(raw, planned[0])
        return raw
    if len(heads) != len(planned):
        return raw
    parts = [raw[: heads[0].start()]]
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        parts.append(_patch_asset_line(raw[match.start():end], planned[index]))
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
    a repair that dropped it.     The local shot skeleton is written after that,
    again on a repair, so ``[Shot N]`` and ``At MM:SS.mmm`` match the storyboard.
    The ``资产：`` line is written last, from the storyboard 资产栏 of the shots
    in that unit, so a model list does not replace it.
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
    result = inject_unit_assets(result, packing_unit_asset_lines(state))
    apply_packing_plan.last_fold_source = source
    return result


apply_packing_plan.last_fold_source = ""
