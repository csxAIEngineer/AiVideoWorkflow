"""Build the canvas shot list from a storyboard body."""

import ast
import json
import re
import time

from end.prompt_gate import delivery_units
from end.workflow_from_spec import MAX_SHOT_REFERENCES, _clamp_duration

PLACEHOLDER_PROMPT = "按这张资产图演这一镜"
_NAME_STOP = {
    "角色",
    "四格",
    "九宫",
    "宫格",
    "场景",
    "母版",
    "设定",
    "图板",
    "纯图",
    "关键",
    "道具",
    "特效",
    "元素",
    "女主",
    "男主",
    "参考",
    "素材",
    "状态",
    "锁定",
}
_PROMPT_KEYS = ("camera_spec", "visual_content", "blocking_note")


def storyboard_rows(value):
    """Shot dicts from a list, a JSON text, or a Python literal."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("shots", "storyboard", "items"):
            found = value.get(key)
            if isinstance(found, list):
                return storyboard_rows(found)
        if value.get("visual_content") or value.get("shot_id") or value.get("camera_spec"):
            return [value]
        return []
    text = str(value or "").strip()
    if not text or text[0] not in "[{":
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, (dict, list)):
        try:
            data = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return []
    return storyboard_rows(data)


def shots_are_placeholder(shots):
    """True when the canvas has no shot, or only the upload stand-in."""
    if not isinstance(shots, list) or not shots:
        return True
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        prompt = str(shot.get("prompt") or "").strip()
        if prompt and prompt != PLACEHOLDER_PROMPT:
            return False
    return True


def _shot_id(row, index):
    raw = str(row.get("shot_id") or row.get("id") or "")
    match = re.search(r"(\d+)", raw)
    number = int(match.group(1)) if match else index + 1
    return "S%02d" % number


def _dialogue(row):
    text = str(row.get("dialogue") or "").strip()
    if text in ("无", "无。", "无对白"):
        return ""
    return text


def _prompt(row):
    written = str(row.get("prompt") or "").strip()
    parts = []
    for key in _PROMPT_KEYS:
        text = str(row.get(key) or "").strip()
        if text and text not in ("无", "无。"):
            parts.append(text)
    description = str(row.get("description") or "").strip()
    if description and description not in parts:
        parts.insert(0, description)
    for key in ("action_phase", "vfx"):
        text = str(row.get(key) or "").strip()
        if text and text not in ("无", "无。") and text not in parts:
            parts.append(text)
    joined = "\n".join(parts)
    if written and len(written) >= len(joined):
        return written
    return joined


def _duration_value(row):
    raw = row.get("duration")
    if raw in (None, ""):
        raw = row.get("duration_sec")
    if raw not in (None, ""):
        return raw
    match = re.search(
        r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})",
        str(row.get("time_range") or ""),
    )
    if not match:
        return None
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    if end <= start:
        return None
    return end - start


def _ref_ids(row):
    found = []
    for key in ("images", "scene_ref", "character_refs"):
        value = row.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = str(item or "").strip()
            if text and text not in found:
                found.append(text)
    return found


def _needles(asset):
    raw = "%s %s" % (asset.get("title") or "", asset.get("subject") or "")
    parts = re.split(r"[·•|/\\\s（）()【】\[\]、，,：:《》\"“”']+", raw)
    found = []
    for part in parts:
        part = part.strip()
        if len(part) >= 2 and part not in _NAME_STOP and part not in found:
            found.append(part)
        cjk = "".join(re.findall(r"[\u4e00-\u9fff]", part))
        for offset in range(0, len(cjk) - 1, 2):
            word = cjk[offset : offset + 2]
            if word not in _NAME_STOP and word not in found:
                found.append(word)
    return found


def _images(row, assets, blob):
    listed = []
    for name in _ref_ids(row):
        if name not in listed:
            listed.append(name)
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("id") or "").strip()
        if not asset_id or asset_id in listed:
            continue
        if any(needle in blob for needle in _needles(asset)):
            listed.append(asset_id)
    return listed[:MAX_SHOT_REFERENCES]


# Longer keys first so 女主 is not read as 男, and 药盒 is not only 药.
_TITLE_KEYS = (
    ("女主", ("female protagonist", "female", "woman")),
    ("男主", ("male protagonist", "male", "man")),
    ("怪物", ("monster", "creature")),
    ("客厅", ("living room", "apartment")),
    ("公寓", ("living room", "apartment")),
    ("胶囊", ("capsule", "medicine", "pill", "box")),
    ("药盒", ("medicine box", "medicine", "capsule", "box")),
    ("药", ("medicine", "capsule", "pill", "box")),
)
_H3_FIELD = re.compile(
    r"(?m)^(subject_definitions|summary|retention_analysis|detailed_description|"
    r"overall_soundscape|non_diegetic_music)\s*[:：][ \t]*$"
)
_SUBJECT_LINE = re.compile(r"^(\s*<Subject\s+(\d+)\b[^>]*>)(.*)$")
_PICTURE_LINE = re.compile(r"^\s*<Picture\s+\d+\b")


def _asset_blob(asset):
    return "%s %s" % (asset.get("title") or "", asset.get("subject") or "")


def _is_frame_asset(asset):
    """A Picture tag only owns an image when that image is a real keyframe."""
    blob = _asset_blob(asset)
    return any(word in blob for word in ("关键帧", "首帧", "尾帧"))


def _aliases_for(asset):
    title = _asset_blob(asset)
    found = []
    for key, phrases in _TITLE_KEYS:
        if key not in title:
            continue
        if key not in found:
            found.append(key)
        for phrase in phrases:
            if phrase not in found:
                found.append(phrase)
    return found


def _asset_rank(asset):
    kind = str(asset.get("subject_kind") or "")
    title = _asset_blob(asset)
    if kind == "character" or any(word in title for word in ("角色", "女主", "男主", "怪物")):
        return 0
    if kind == "location" or any(word in title for word in ("九宫", "母版", "场景", "客厅")):
        return 1
    return 2


def _board_order(asset):
    """Scene master before the nine-grid of the same room."""
    return 1 if "九宫" in _asset_blob(asset) else 0


def _hit_at(text, aliases):
    raw = str(text or "")
    lowered = raw.lower()
    best = None
    for alias in aliases:
        if re.search(r"[\u4e00-\u9fff]", alias):
            pos = raw.find(alias)
        else:
            match = re.search(r"(?<!\w)%s(?!\w)" % re.escape(alias.lower()), lowered)
            pos = match.start() if match else -1
        if pos >= 0 and (best is None or pos < best):
            best = pos
    return best


def _match_assets(text, assets):
    """Asset ids whose title words show up in ``text``, master before nine-grid."""
    ranked = []
    for index, asset in enumerate(assets or []):
        if not isinstance(asset, dict) or _is_frame_asset(asset):
            continue
        asset_id = str(asset.get("id") or "").strip()
        if not asset_id:
            continue
        pos = _hit_at(text, _aliases_for(asset))
        if pos is None:
            continue
        ranked.append((pos, _asset_rank(asset), _board_order(asset), index, asset_id))
    ranked.sort()
    found = []
    for _pos, _rank, _board, _index, asset_id in ranked:
        if asset_id not in found:
            found.append(asset_id)
    return found[:MAX_SHOT_REFERENCES]


def _h3_fields(text):
    matches = list(_H3_FIELD.finditer(str(text or "")))
    if not matches:
        return None
    fields = {"__preamble": text[: matches[0].start()].strip(), "__order": []}
    for index, match in enumerate(matches):
        name = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        fields[name] = text[match.end() : end].strip()
        fields["__order"].append(name)
    return fields


def _source_text(unit):
    fields = _h3_fields(unit)
    if fields and str(fields.get("subject_definitions") or "").strip():
        return fields["subject_definitions"]
    return str(unit or "")


def _frame_ids(unit, rows):
    """Keyframe stills named by this unit. Other units do not inherit them."""
    if "<Picture" not in str(unit or ""):
        return []
    source = _source_text(unit)
    found = []
    for asset in rows:
        if not isinstance(asset, dict) or not _is_frame_asset(asset):
            continue
        if _hit_at(source, _aliases_for(asset)) is None:
            continue
        asset_id = str(asset.get("id") or "").strip()
        if asset_id and asset_id not in found:
            found.append(asset_id)
    return found


def images_for_feed_unit(unit, assets):
    """Stills for one delivery unit. A Picture tag takes a slot only for its own keyframe."""
    rows = [item for item in (assets or []) if isinstance(item, dict)]
    frames = _frame_ids(unit, rows)
    stills = [asset_id for asset_id in _match_assets(_source_text(unit), rows) if asset_id not in frames]
    return (frames + stills)[:MAX_SHOT_REFERENCES]


def _tag_with_id(tag, rest, asset_id):
    mark = "(%s)" % asset_id
    if mark in tag or mark in rest:
        return tag + rest
    return "%s %s%s" % (tag, mark, rest)


def _head_kind(head):
    low = str(head or "").lower()
    if any(word in low for word in ("environment", "场景", "客厅", "公寓")):
        return 1
    if any(word in low for word in ("prop", "medicine", "capsule", "道具", "胶囊")):
        return 2
    if any(word in low for word in ("monster", "creature", "怪物", "female", "male", "woman", "man", "女主", "男主")):
        return 0
    return None


def _choice_for_line(line, assets):
    """Role in the label wins. The sentence after the colon does not retag the subject."""
    head = line.split(":", 1)[0]
    chosen = _match_assets(head, assets)
    if chosen:
        return chosen
    whole = _match_assets(line, assets)
    kind = _head_kind(head)
    if kind is None:
        return whole
    by_id = {str(asset.get("id") or ""): asset for asset in assets or [] if isinstance(asset, dict)}
    narrowed = [asset_id for asset_id in whole if _asset_rank(by_id.get(asset_id) or {}) == kind]
    return narrowed or whole


def _strip_pictures(body):
    lines = []
    for line in str(body or "").splitlines():
        if _PICTURE_LINE.match(line):
            continue
        cleaned = re.sub(r"<Picture\s+\d+>", "", line)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).rstrip()
        if cleaned.strip():
            lines.append(cleaned)
    return "\n".join(lines).strip()


def _rewrite_subjects(body, assets, keep_pictures):
    lines = []
    numbers = []
    for line in str(body or "").splitlines():
        if _PICTURE_LINE.match(line):
            if keep_pictures and line.strip():
                lines.append(line.rstrip())
            continue
        if not keep_pictures:
            line = _strip_pictures(line)
            if not line:
                continue
        match = _SUBJECT_LINE.match(line)
        if not match:
            if line.strip():
                lines.append(line.rstrip())
            continue
        number = match.group(2)
        if number not in numbers:
            numbers.append(number)
        chosen = _choice_for_line(line, assets)
        if chosen:
            line = _tag_with_id(match.group(1), match.group(3), chosen[0])
        lines.append(line.rstrip())
    return "\n".join(lines).strip(), numbers


def _rewrite_retention(body, numbers):
    kept = []
    for line in str(body or "").splitlines():
        if "<Picture" in line and "<Subject" not in line:
            continue
        if line.strip():
            kept.append(line.rstrip())
    if kept:
        return "\n".join(kept).strip()
    return "\n".join("<Subject %s>: fully_preserved" % number for number in numbers)


def _join_h3(fields):
    chunks = []
    preamble = fields.get("__preamble") or ""
    if preamble:
        chunks.append(preamble)
    for name in fields.get("__order") or []:
        body = fields.get(name) or ""
        chunks.append("%s:\n%s" % (name, body) if body else "%s:" % name)
    return "\n".join(chunks).strip()


def bind_feed_unit(unit, assets):
    """Attach asset ids to Subject lines and drop Picture lines that have no still."""
    text = str(unit or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    rows = [item for item in (assets or []) if isinstance(item, dict)]
    ids = images_for_feed_unit(text, rows)
    fields = _h3_fields(text)
    if not fields or "subject_definitions" not in fields:
        return text, ids
    keep_pictures = bool(_frame_ids(text, rows))
    body, numbers = _rewrite_subjects(fields.get("subject_definitions") or "", rows, keep_pictures)
    fields["subject_definitions"] = body
    if not keep_pictures:
        for name in fields.get("__order") or []:
            if name == "subject_definitions":
                continue
            cleaned = _strip_pictures(fields.get(name) or "")
            if name == "retention_analysis":
                cleaned = _rewrite_retention(cleaned, numbers)
            fields[name] = cleaned
    return _join_h3(fields), ids


def bind_feed_document(feed, assets):
    """Rewrite every delivery unit. Returns ``(feed, image id lists)``."""
    raw = str(feed or "")
    rows = [item for item in (assets or []) if isinstance(item, dict)]
    units = delivery_units(raw)
    if not raw.strip() or not rows or not units:
        return raw, []
    rewritten = []
    groups = []
    for unit in units:
        text, ids = bind_feed_unit(unit, rows)
        rewritten.append(text.strip())
        groups.append(ids)
    first = units[0].splitlines()[0] if units[0] else ""
    start = raw.find(first) if first else 0
    prefix = raw[:start].strip() if start > 0 else ""
    body = "\n\n".join(block for block in rewritten if block)
    if prefix:
        body = prefix + "\n\n" + body
    if raw.endswith("\n"):
        body += "\n"
    return body, groups


def shots_from_storyboard(value, assets=None):
    """One canvas shot per storyboard row. Empty when the body is not a shot list."""
    shots = []
    for index, row in enumerate(storyboard_rows(value)):
        prompt = _prompt(row)
        if not prompt:
            continue
        dialogue = _dialogue(row)
        duration_raw = _duration_value(row)
        blob = "\n".join(
            str(row.get(key) or "")
            for key in (
                "shot_id",
                "visual_content",
                "camera_spec",
                "blocking_note",
                "dialogue",
                "prompt",
                "description",
                "scene_ref",
            )
        )
        shots.append(
            {
                "id": _shot_id(row, index),
                "duration": _clamp_duration(duration_raw, dialogue),
                "prompt": prompt,
                "images": _images(row, assets, blob),
                "dialogue": dialogue,
                "narration": str(row.get("narration") or "").strip(),
            }
        )
    return shots


def _shots_for_units(current, units):
    """One shot per delivery unit when the storyboard never became rows."""
    rows = [dict(shot) for shot in current if isinstance(shot, dict)]
    made = []
    for index, text in enumerate(units):
        shot = dict(rows[index] if index < len(rows) else (rows[0] if rows else {}))
        shot["prompt"] = text
        if index >= len(rows) or not shot.get("id"):
            shot["id"] = "S%02d" % (index + 1)
        made.append(shot)
    return made


def apply_storyboard_shots(spec):
    """Fill shots from the storyboard when the canvas list is empty or a stand-in."""
    spec = spec if isinstance(spec, dict) else {}
    current = spec.get("shots") if isinstance(spec.get("shots"), list) else []
    derived = shots_from_storyboard(spec.get("storyboard"), spec.get("assets"))
    placeholder = shots_are_placeholder(current)
    wrote = bool(derived) and placeholder
    if not wrote:
        if placeholder and not derived:
            from end.prompt_gate import delivery_units

            units = delivery_units(spec.get("feed"))
            if units:
                updated = dict(spec)
                updated["shots"] = _shots_for_units(current, units)
                return updated
        return spec
    updated = dict(spec)
    updated["shots"] = derived
    return updated


def apply_feed_binding(spec):
    """Point each feed unit at the asset stills it actually names."""
    spec = dict(spec or {})
    assets = [item for item in (spec.get("assets") or []) if isinstance(item, dict)]
    feed, groups = bind_feed_document(spec.get("feed") or "", assets)
    if not groups:
        return spec
    spec["feed"] = feed
    units = delivery_units(feed)
    current = [shot for shot in (spec.get("shots") or []) if isinstance(shot, dict)]
    shots = []
    for index, ids in enumerate(groups):
        shot = dict(current[index]) if index < len(current) else {}
        if not shot.get("id"):
            shot["id"] = "S%02d" % (index + 1)
        if ids:
            shot["images"] = list(ids)
        if index < len(units) and units[index].strip():
            shot["prompt"] = units[index].strip()
        shots.append(shot)
    if len(current) > len(groups):
        shots.extend(dict(shot) for shot in current[len(groups) :])
    spec["shots"] = shots
    return spec
