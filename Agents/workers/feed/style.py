"""The locked episode style sentence written into every packed unit."""

import re
from end.product_names import UNIT_HEAD_LINE

from Agents.workers.feed.common import (
    _H3_FIELD_LINE,
    _H3_FIELD_NAMES,
    _style_units,
    _unit_label,
)


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
    "short-drama-director/references/wan-adapter.md",
    "short-drama-director/references/prompt-feeding-checklist.md",
)


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
