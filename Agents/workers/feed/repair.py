"""Fold a named repair onto the last complete packed draft."""

import re
from end.product_names import UNIT_HEAD_LINE

from Agents.workers.feed.plan import _machine_text
from Agents.workers.feed.wan_format import is_wan_shaped
from Agents.workers.feed.skeleton import (
    _shot_headings,
    _shot_sections,
    _subject_tag_missing,
    packing_shot_skeleton,
)


_REPAIR_UNIT = re.compile(
    r"\[段\s*(\d+)\]|(?:封装单元|投喂单元)\s*(\d+)\s*/\s*\d+|(?:封装单元|投喂单元)\s*(\d+)\b"
)


def named_repair_units(machine_report):
    """1-based unit numbers the machine report actually names."""
    found = []
    for match in _REPAIR_UNIT.finditer(_machine_text(machine_report)):
        number = next(group for group in match.groups() if group)
        index = int(number)
        if index > 0 and index not in found:
            found.append(index)
    return found


def _unit_spans(text):
    raw = str(text or "")
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if not heads:
        return raw, []
    prefix = raw[: heads[0].start()]
    spans = []
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        spans.append(raw[match.start():end])
    return prefix, spans


def repair_unit_collapsed(block):
    """A repair unit that must not replace a complete one.

    No shot headings is a collapse. On a leftover H3 draft, ``subject_definitions``
    with no ``<Subject N>`` (the style-lock line alone) is a collapse too. A Wan
    unit has no subjects, so its shot bodies decide instead.
    """
    if not _shot_headings(block):
        return True
    if is_wan_shaped(block):
        return not any(str(prose or "").strip() for _number, prose in _shot_sections(block))
    return _subject_tag_missing(block)


def draft_is_complete(text, state):
    """Every packed unit has a body under every skeleton shot.

    A leftover H3 unit also needs ``<Subject N>``; a Wan unit is judged by its
    分镜 headings being complete and every shot having a body. Another failure,
    such as H7, does not make the draft incomplete. A collapsed draft is not a
    merge base.
    """
    skeleton = packing_shot_skeleton(state)
    if not skeleton:
        return False
    _prefix, spans = _unit_spans(text)
    if len(spans) != len(skeleton):
        return False
    for block, shots in zip(spans, skeleton):
        wan = is_wan_shaped(block)
        if not wan and _subject_tag_missing(block):
            return False
        if wan and [number for number, _tail in _shot_headings(block)] != [shot["local"] for shot in shots]:
            return False
        sections = {number: prose for number, prose in _shot_sections(block)}
        for shot in shots:
            if not str(sections.get(shot["local"]) or "").strip():
                return False
    return True


def last_complete_draft(state):
    """The latest history draft that still has subjects and shot bodies.

    ``state["draft"]`` is included when it is newer than the recorded history.
    A later collapsed draft does not become the base.
    """
    state = state or {}
    candidates = []
    for item in state.get("history") or []:
        if isinstance(item, dict):
            candidates.append(str(item.get("draft") or ""))
    current = str(state.get("draft") or "")
    if current and (not candidates or candidates[-1] != current):
        candidates.append(current)
    found = ""
    for text in candidates:
        if text.strip() and draft_is_complete(text, state):
            found = text
    return found


def merge_targeted_repair(base, incoming, machine_report, state=None):
    """Fold named units from a repair onto the last complete draft.

    Unnamed units stay byte for byte. A named unit that came back collapsed
    is discarded and the complete unit stays. A report that names no unit
    leaves the incoming text alone, so an unnamed duration repair can still
    replace the whole feed.
    """
    del state
    named = set(named_repair_units(machine_report))
    if not str(base or "").strip() or not named:
        return incoming
    prefix, base_spans = _unit_spans(base)
    _incoming_prefix, incoming_spans = _unit_spans(incoming)
    if not base_spans or len(incoming_spans) != len(base_spans):
        return base
    parts = [prefix]
    for index, block in enumerate(base_spans):
        use_incoming = (
            (index + 1) in named
            and not repair_unit_collapsed(incoming_spans[index])
        )
        parts.append(incoming_spans[index] if use_incoming else block)
    return "".join(parts)
