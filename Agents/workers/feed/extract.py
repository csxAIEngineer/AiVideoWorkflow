"""Fold director JSON and informal labels back onto the four-field feed."""

import importlib
import json
import re
from Agents.worker import (
    extract_json_object,
    strip_fence,
)
from end.product_names import (
    UNIT_HEAD_ANY,
    mentions_unit,
    unit_heading,
)

from Agents.workers.feed.common import (
    H3_OMITTED,
    H3_SECTIONS,
    _H3_FIELD_LINE,
    _H3_SUBJECT_LINE,
    _LEADING_DURATION,
    _LOOSE_UNIT_LINE,
    _UNIT_DURATION_LINE,
    packing_groups,
)
from Agents.workers.feed.style import _STYLE_SLOT


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


def _json_document(text):
    """A Seedance JSON blob has no line-start unit headings. Leave it wrapped."""
    peeled = _LEADING_DURATION.sub("", str(text or ""), count=1).strip()
    peeled = strip_fence(peeled).strip()
    return bool(peeled) and peeled[0] in "{["


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
