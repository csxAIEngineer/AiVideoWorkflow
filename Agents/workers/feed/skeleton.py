"""Local shot headings, cut times, and the gates for empty packed shots."""

import importlib
import re
from end.product_names import (
    UNIT_HEAD_LINE,
    UNIT_NAME,
)

from Agents.workers.feed.common import (
    _BOARD_DURATION,
    _BOARD_HEAD,
    _H3_FIELD_LINE,
    _H3_FIELD_NAMES,
    _insert_field,
    _storyboard_text,
    _style_units,
    _unit_label,
)
from Agents.workers.feed.wan_format import (
    WAN_FIELD_LINE,
    WAN_SECTIONS,
    WAN_SHOT_HEADING,
    canonical_wan_name,
    is_wan_shaped,
)
from Agents.workers.feed.style import (
    _STYLE_WRITERS,
    _subject_body,
)

# The repair names these packing writers and tells them to restore the skeleton.
_SKELETON_WRITERS = _STYLE_WRITERS


def format_cut_timecode(seconds):
    """``MM:SS.mmm`` from a second count. The unit timeline starts at 00:00.000."""
    total_ms = int(round(float(seconds) * 1000.0))
    if total_ms < 0:
        total_ms = 0
    minutes, remainder = divmod(total_ms, 60000)
    secs, millis = divmod(remainder, 1000)
    return "%02d:%02d.%03d" % (minutes, secs, millis)


def _unit_shots(durations, ids=None):
    """Local headings for one packed unit, as official Wan start-end 分镜 lines."""
    from Agents.workers.feed.wan_format import wan_shot_heading

    shots = []
    elapsed = 0.0
    for index, raw in enumerate(durations or [], 1):
        seconds = float(raw)
        start = elapsed
        end = elapsed + seconds
        shot = {
            "local": index,
            "duration": seconds,
            "start": start,
            "end": end,
            "cut": format_cut_timecode(start),
            "heading": wan_shot_heading(index, start, end),
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


def packing_shot_skeleton(state, limit=None):
    """Local ``[Shot N]`` headings for each packed unit.

    Numbers restart at 1 inside the unit. ``[Shot 1]`` has no cut. Each later
    cut is the sum of the earlier shots in that unit, as ``MM:SS.mmm``.
    A span over the clip limit stays its own unit at its real length. It is
    not clamped, and it is not given a cut time by shortening it.
    """
    from end.locks import clip_limit

    rows = _packing_shot_rows(state)
    if not rows:
        return []
    if limit is None:
        limit = clip_limit(locks=(state or {}).get("locks"))
    runner = importlib.import_module("end.stage_runner")
    groups = runner.group_shot_durations(
        [row["duration"] for row in rows], limit, locks=(state or {}).get("locks")
    )
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


def _match_section(line):
    raw = str(line or "").strip()
    field = _H3_FIELD_LINE.match(raw)
    if field:
        return field.group(1), field.group(2)
    field = WAN_FIELD_LINE.match(raw)
    if field:
        return canonical_wan_name(field.group("name")), field.group("rest")
    return None


def _known_section_names():
    return set(_H3_FIELD_NAMES) | set(WAN_SECTIONS)


def _field_parts(block, name):
    lines = str(block or "").splitlines(keepends=True)
    header_at = None
    inline = ""
    for index, raw_line in enumerate(lines):
        field = _match_section(raw_line)
        if field and field[0] == name:
            header_at = index
            inline = str(field[1] or "").strip()
            break
    if header_at is None:
        return None
    body_end = header_at + 1
    while body_end < len(lines):
        field = _match_section(lines[body_end])
        if field and field[0] in _known_section_names():
            break
        body_end += 1
    return header_at, inline, lines[header_at + 1:body_end], body_end, lines


def _detailed_description_text(block):
    parsed = _field_parts(block, "分镜") or _field_parts(block, "detailed_description")
    if parsed is None:
        return None
    _header, inline, body_lines, _end, _lines = parsed
    parts = []
    if inline:
        parts.append(inline)
    for raw in body_lines:
        parts.append(raw.rstrip("\r\n"))
    return "\n".join(parts)


_BARE_WAN_SHOT = re.compile(
    r"^[ \t]*分镜\s*(?P<num>\d+)\s*[:：](?P<tail>.*)$"
)


def _heading_match(line):
    raw = str(line or "")
    wan = WAN_SHOT_HEADING.match(raw.strip())
    if wan:
        return (
            int(wan.group("num")),
            "（%s-%s）" % (wan.group("start"), wan.group("end")),
            wan.group("tail") or "",
        )
    bare = _BARE_WAN_SHOT.match(raw.strip())
    if bare:
        return int(bare.group("num")), "", bare.group("tail") or ""
    match = _SHOT_HEADING_LINE.match(raw)
    if match:
        return int(match.group(1)), match.group(2) or "", match.group(2) or ""
    return None


def _shot_headings(block):
    body = _detailed_description_text(block)
    if body is None:
        return []
    found = []
    for line in body.splitlines():
        match = _heading_match(line)
        if match:
            found.append((match[0], match[1]))
    return found


_SUBJECT_TAG = re.compile(r"<Subject\s+\d+\b", re.I)


def _definition_lines(block):
    parsed = _subject_body(block)
    if parsed is None:
        return []
    _header, inline, body_lines, _end, _lines = parsed[:5]
    lines = []
    if inline:
        lines.append(inline)
    for raw in body_lines:
        stripped = raw.strip()
        if stripped:
            lines.append(stripped)
    return lines


def _subject_tag_missing(block):
    """True when subject_definitions has no ``<Subject N>``.

    The style-lock line alone does not count. An empty field does not either.
    """
    return _SUBJECT_TAG.search("\n".join(_definition_lines(block))) is None


def _soundscape_empty(block):
    parsed = _field_parts(block, "overall_soundscape")
    if parsed is None:
        return True
    _header, inline, body_lines, _end, _lines = parsed
    if str(inline or "").strip():
        return False
    return not any(raw.strip() for raw in body_lines)


def shot_skeleton_failures(text, state=None):
    """Fail a packed unit whose local shots disagree with the storyboard skeleton.

    The skeleton is known before packing. ``[Shot 1]`` has no timestamp.
    Each later heading is ``[Shot N] At MM:SS.mmm`` at the sum of the earlier
    shots in that unit only. Episode-wide numbers, a missing or extra
    ``[Shot N]``, a timestamp on the first shot, or any other cut time fails.
    A heading with no body fails too. ``subject_definitions`` that is only the
    style-lock line, and an empty ``overall_soundscape``, fail the same gate.
    This does not invent a cut. H2 and H7 still check each unit on their own;
    this gate does not weaken those rules.
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
        wan = is_wan_shaped(block)
        if not wan and _subject_tag_missing(block):
            errors.append(
                "镜号锁：%s 的 subject_definitions 没有 <Subject N>。"
                "只有画面风格锁定句不算主体。把这一段的主体定义写回去，不要只留锁定句。"
                % label
            )
        if not wan and _soundscape_empty(block):
            errors.append(
                "镜号锁：%s 的 overall_soundscape 是空的。写上这一段的环境声，不要留空。"
                % label
            )
        found = _shot_headings(block)
        expected_numbers = [shot["local"] for shot in shots]
        found_numbers = [number for number, _tail in found]
        if found_numbers != expected_numbers:
            shown = "、".join("分镜%s" % number for number in found_numbers) or "（没有分镜）"
            want = "、".join(shot["heading"] for shot in shots)
            errors.append(
                "镜号锁：%s 的镜号是 %s，必须按这个本地骨架写：%s。"
                "不要沿用全集镜号，不要增删、合并或调换镜头，不要自己发明切点。"
                % (label, shown, want)
            )
            continue
        for shot, (_number, tail) in zip(shots, found):
            want = "（%s-%s）" % (
                format_cut_timecode(shot.get("start", 0.0)),
                format_cut_timecode(
                    shot.get("end", shot.get("start", 0.0) + float(shot.get("duration") or 0))
                ),
            )
            if want in (tail or "") or want in (shot.get("heading") or "") and (tail or "").startswith("（"):
                if want not in (tail or ""):
                    errors.append(
                        "镜号锁：%s 的分镜%s 起止秒必须是 %s。"
                        "不要自己改切点，也不要漏掉时间码。"
                        % (label, shot["local"], want)
                    )
                continue
            # Leftover H3 drafts still use At MM:SS.mmm after [Shot N].
            if not wan:
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
                continue
            errors.append(
                "镜号锁：%s 的分镜%s 起止秒必须是 %s。"
                "不要自己改切点，也不要漏掉时间码。"
                % (label, shot["local"], want)
            )
        for number, prose in _shot_sections(block):
            if not str(prose or "").strip():
                errors.append(
                    "镜号锁：%s 的 [Shot %s] 没有正文。"
                    "把这一镜的画面和对白写在预计算的骨架下面，不要只留空标题。"
                    % (label, number)
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


def _heading_prose(tail, following):
    """Scene text under one heading. A cut time by itself is not a body."""
    rest = _strip_clock(tail or "", False).strip().lstrip(",，").strip()
    more = [line.strip() for line in following if str(line or "").strip()]
    return "\n".join(part for part in ([rest] if rest else []) + more if part).strip()


def _shot_sections(block):
    body = _detailed_description_text(block)
    if body is None:
        return []
    lines = body.splitlines()
    indexes = [index for index, line in enumerate(lines) if _heading_match(line)]
    sections = []
    for pos, index in enumerate(indexes):
        match = _heading_match(lines[index])
        end = indexes[pos + 1] if pos + 1 < len(indexes) else len(lines)
        prose = _heading_prose(match[2] if len(match) > 2 else match[1], lines[index + 1:end])
        sections.append((match[0], prose))
    return sections


def _rewrite_shot_lines(lines, shots):
    """Renumber the headings already present. Append any heading that is missing.

    Prose that followed a heading stays with that heading. Nothing new is
    written after a heading the model did not already fill. Extra headings
    past the skeleton are left for the gate to name. A blob with no headings
    stays a blob: it is not hung under ``[Shot 1]`` with empty later heads.
    """
    lines = list(lines)
    indexes = [index for index, line in enumerate(lines) if _heading_match(line)]
    paired = min(len(indexes), len(shots))
    for slot in range(paired):
        line_index = indexes[slot]
        match = _heading_match(lines[line_index])
        indent = re.match(r"[ \t]*", lines[line_index]).group(0)
        tail = match[2] if len(match) > 2 else match[1]
        prose = _strip_clock(tail or "", False)
        lines[line_index] = _join_heading(indent + shots[slot]["heading"], prose)
    if paired >= len(shots):
        return lines
    missing = [shot["heading"] for shot in shots[paired:]]
    if paired == 0:
        if not any(line.strip() for line in lines):
            return missing
        # One local shot can take the prose. A multi-shot blob must not be
        # hung under [Shot 1] with empty later headings appended.
        if len(shots) == 1:
            lines.insert(0, shots[0]["heading"])
            return lines
        return lines
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
    new_lines = ["分镜:\n"]
    for line in rewritten:
        new_lines.append(line + "\n")
    return "".join(lines[:header_at] + new_lines + lines[body_end:])


def _insert_description(block, shots):
    snippet = "\n".join(shot["heading"] for shot in shots)
    trailing = "\n" if str(block).endswith("\n") else ""
    placed = _insert_field(str(block).rstrip("\n"), "分镜", snippet)
    if trailing and not placed.endswith("\n"):
        placed += "\n"
    return placed


def _patch_unit_block(block, shots):
    if not shots:
        return block
    parsed = _field_parts(block, "分镜") or _field_parts(block, "detailed_description")
    if parsed is None:
        if "subject_definitions" not in block and "overall_soundscape" not in block:
            return block
        return _insert_description(block, shots)
    return _replace_description(parsed, shots)


def inject_shot_skeleton(text, skeleton):
    """Write the precomputed local headings back into each packed unit.

    Existing shot prose stays on the heading it already followed, in order.
    The first heading loses any timestamp. Later headings get the exact
    ``At MM:SS.mmm``. A missing heading is inserted with no new scene text
    when some headings are already there. A prose blob with no headings is
    left as-is, so the gate can ask the writer to fill the skeleton. An
    extra heading is left in place so the gate can name it. This runs
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
            or "分镜:" in raw or "分镜：" in raw
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
