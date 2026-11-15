"""Packed-unit seam ledger and the official H3 submit fields it writes.

One ledger row per packed feed unit. The row id is that unit's id.
The official id is ``封装单元 N/M``, the same form as the packing heading
``【封装单元 N/M】``. An older stored row that still says ``封装单元 1`` is
the same unit when the heading already has the total.
End submits video_mode, first-frame source, and reference images from the
unit records. D-lock stays on the ledger. Official H3 video generation uses
only ``first_frame`` and ``reference``. Continuity chooses between them.
Packing does not read this ledger.
"""

import logging
import re

from end.product_names import UNIT_NAME, UNIT_TOKEN

_LOG = logging.getLogger(__name__)

# Official H3 submit modes. ``text`` and ``first_last_frame`` are not used.
VIDEO_MODES = ("first_frame", "reference")
H3_MAX_REFERENCE_IMAGES = 9
D_LOCKS = ("none", "1", "2", "3", "4")
FRAME_MODES = ("first_frame",)
# Retired modes still drop reference stills if an older row carries them.
_RETIRED_FRAME_MODES = ("text", "first_last_frame")
# Plot still running across the cut. ``same_scene`` is not in this set:
# 同场景，事件断开 uses that same token. A plot group, a new scene, a time
# or place jump, and an emotion landing are cuts, not continuations.
PLOT_CONTINUOUS_RELATIONS = ("dialogue_split", "oner")
WRITING_MODES = (
    "普通衔接",
    "独立批量强连续",
    "首帧增强",
    "调度增强",
    "视频延长强连续",
)
SIZE_LADDER = (
    "大远景",
    "远景",
    "全景",
    "中全景",
    "中景",
    "中近景",
    "近景",
    "特写",
    "大特写",
)
SCENE_RELATIONS = (
    "opening",
    "same_scene",
    "new_scene",
    "time_jump",
    "place_jump",
    "plot_group",
    "dialogue_split",
    "oner",
    "emotion_landing",
)
# First packed unit has no inbound seam. Later units stay in the same place
# unless the ledger names another relation. 剧情段 default is ②, and most
# cuts in this product do not change place.
FIRST_UNIT_SCENE_RELATION = "opening"
DEFAULT_SCENE_RELATION = "same_scene"
# Longer phrases are listed first. The lookup also sorts by length so a
# short alias cannot claim a more specific phrase.
_SCENE_RELATION_PHRASES = (
    ("同场景，连续", "same_scene"),
    ("同场景，事件断开", "same_scene"),
    ("同场景", "same_scene"),
    ("同场", "same_scene"),
    ("same_scene", "same_scene"),
    ("新场景", "new_scene"),
    ("换场", "new_scene"),
    ("new_scene", "new_scene"),
    ("时间跳跃", "time_jump"),
    ("时间跳", "time_jump"),
    ("换时间", "time_jump"),
    ("time_jump", "time_jump"),
    ("地点跳", "place_jump"),
    ("换地点", "place_jump"),
    ("place_jump", "place_jump"),
    ("剧情段", "plot_group"),
    ("剧情组", "plot_group"),
    ("plot_group", "plot_group"),
    ("一句话被拆开", "dialogue_split"),
    ("对白拆分", "dialogue_split"),
    ("dialogue_split", "dialogue_split"),
    ("一镜到底", "oner"),
    ("oner", "oner"),
    ("情绪落地", "emotion_landing"),
    ("情绪收尾", "emotion_landing"),
    ("emotion_landing", "emotion_landing"),
    ("无入缝", "opening"),
    ("开场", "opening"),
    ("首段", "opening"),
    ("opening", "opening"),
)
CONTINUE_RELATIONS = ("same_scene", "dialogue_split", "oner")
EMPTY_USES = ("情绪空镜", "音效桥", "时空转场")
_EMPTY_ALIAS = {
    "emotion": "情绪空镜",
    "sound_bridge": "音效桥",
    "time_place": "时空转场",
    "情绪": "情绪空镜",
    "音效桥空镜": "音效桥",
    "时空": "时空转场",
}
FREEZE_WORDS = ("定格", "完全静止", "硬定格", "定格尾帧")
SHOT_VIDEO_FIELDS = (
    "video_mode",
    "end_image",
    "first_frame_source",
    "video_images",
    "seam_d_lock",
    "writing_mode",
    "redraw_still",
)
CONTRACT = "shot-prose-v1"


def _text(value):
    return str(value or "").strip()


def _is_extracted_tail(image_id):
    """True for a finished clip's real last frame, such as ``S03_tail``.

    That still is not a designed end frame. Official submit params stay
    ``first_frame`` or ``reference`` with an empty end frame. The render
    loop writes the extracted id onto the unit after the clip is on disk.
    """
    from end.render_loop import is_extracted_tail_id

    return is_extracted_tail_id(image_id)


def _fold_scene_relation(value):
    """Strip, casefold, and read fullwidth punctuation as ASCII."""
    chars = []
    for ch in str(value or ""):
        code = ord(ch)
        if code == 0x3000:
            chars.append(" ")
        elif code == 0x3001:
            chars.append(",")
        elif code == 0x3002:
            chars.append(".")
        elif 0xFF01 <= code <= 0xFF5E:
            chars.append(chr(code - 0xFEE0))
        else:
            chars.append(ch)
    return "".join(chars).strip().casefold()


def _compile_scene_relation_aliases():
    folded = []
    for phrase, token in _SCENE_RELATION_PHRASES:
        key = _fold_scene_relation(phrase)
        if key and token in SCENE_RELATIONS:
            folded.append((key, token))
    folded.sort(key=lambda item: len(item[0]), reverse=True)
    return tuple(folded)


_SCENE_RELATION_ALIASES = _compile_scene_relation_aliases()


def _relation_alias_at(text, alias):
    if text == alias:
        return True
    if not alias or not text.startswith(alias):
        return False
    nxt = text[len(alias):len(alias) + 1]
    if not nxt:
        return True
    ch = nxt[0]
    if ch == "_" or ch.isalnum():
        return False
    return True


def normalize_scene_relation(value, *, first=False):
    """Return the SCENE_RELATIONS token to store.

    A value already in the closed set is kept. Aliases match after strip,
    casefold, and fullwidth punctuation folded to ASCII. A longer phrase
    wins. Missing or unrecognized text uses FIRST_UNIT_SCENE_RELATION on
    the first packed unit and DEFAULT_SCENE_RELATION on every later unit.
    """
    text = _fold_scene_relation(value)
    if text:
        for token in SCENE_RELATIONS:
            if text == token:
                return token
        for alias, token in _SCENE_RELATION_ALIASES:
            if _relation_alias_at(text, alias):
                return token
    if first:
        return FIRST_UNIT_SCENE_RELATION
    return DEFAULT_SCENE_RELATION


def invalid_scene_relation_error(shot_id):
    return "%s 的场景关系无效。scene_relation 只能是：%s。" % (
        shot_id,
        "、".join(SCENE_RELATIONS),
    )


def _lock(value):
    text = _text(value)
    aliases = {
        "①": "1",
        "②": "2",
        "③": "3",
        "④": "4",
        "none": "none",
        "0": "none",
    }
    if text in aliases:
        return aliases[text]
    lowered = text.lower()
    if lowered in aliases:
        return aliases[lowered]
    if text in ("1", "2", "3", "4"):
        return text
    return text


def _bound_images(shot):
    found = []
    for item in (shot or {}).get("images") or []:
        text = _text(item)
        if text and text not in found:
            found.append(text)
    return found[:H3_MAX_REFERENCE_IMAGES]


def _size_class(value):
    text = _text(value)
    if text in SIZE_LADDER:
        return text
    return ""


def normalize_shot_size(value):
    """A 景别阶梯 term, or the original text when it is not one.

    ``中景镜头`` is a ladder word plus a suffix. A made-up size is left as
    written so the machine check still fails it. This does not pick a
    different size.
    """
    text = _text(value)
    if not text:
        return ""
    if text in SIZE_LADDER:
        return text
    for suffix in ("镜头", "景别"):
        if text.endswith(suffix):
            stem = text[: -len(suffix)].strip(" ：:")
            if stem in SIZE_LADDER:
                return stem
    return text


def _empty_use(value):
    text = _text(value)
    if text in EMPTY_USES:
        return text
    return _EMPTY_ALIAS.get(text, text)


def _flag(value):
    """True for a real continuity mark. Missing and the string false stay false."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    return _text(value).lower() in ("1", "true", "yes", "y", "是")


def _listed_ids(ids):
    images = []
    for item in ids or []:
        text = _text(item)
        if text and text not in images:
            images.append(text)
        if len(images) >= H3_MAX_REFERENCE_IMAGES:
            break
    return images


def reference_id_cap():
    """Official still cap. Asset refs stop at ``MAX_SHOT_REFERENCES``; H3 itself allows 9."""
    from end.workflow_from_spec import MAX_SHOT_REFERENCES

    return min(int(MAX_SHOT_REFERENCES), int(H3_MAX_REFERENCE_IMAGES))


def cap_reference_ids(ids, label=""):
    """Keep storyboard order. Drop the tail past the official cap and say which ids went."""
    limit = reference_id_cap()
    found = []
    overflow = []
    for item in ids or []:
        text = _text(item)
        if not text or text in found or text in overflow:
            continue
        if len(found) >= limit:
            overflow.append(text)
        else:
            found.append(text)
    if overflow:
        prefix = ("%s 的" % label) if label else ""
        _LOG.warning(
            "%s参考图超过提交上限 %s 张，按分镜顺序保留 %s，去掉 %s",
            prefix,
            limit,
            "、".join(found),
            "、".join(overflow),
        )
    return found


def _reference(ids):
    """Reference mode even when no still is bound yet. Never falls back to text."""
    return {
        "video_mode": "reference",
        "images": _listed_ids(ids),
        "end_image": "",
        "first_frame_source": "none",
    }


def _first_frame():
    """Previous tail only. No asset refs and no end frame in the same call."""
    return {
        "video_mode": "first_frame",
        "images": [],
        "end_image": "",
        "first_frame_source": "previous_tail",
    }


def continues_from_previous(entry, *, first=False):
    """Whether this packed unit continues the previous unit's action or plot.

    The first packed unit has no previous tail, so it is never continuous.

    Action-continuous: ``action_continuous`` is true. The action, camera
    direction, light, or prop state does not reset across the cut (同场景顺延,
    连贯动作被拆开). That is the continuity ``segment-splicing.md`` treats as
    the cut still being the same action.

    Plot-continuous: ``plot_continuous`` is true, or ``scene_relation`` is
    ``dialogue_split`` (一句话被拆开) or ``oner`` (一镜到底). ``same_scene``
    alone is not plot-continuous, because 同场景，事件断开 stores that same
    token. ``plot_group``, ``new_scene``, ``time_jump``, ``place_jump``, and
    ``emotion_landing`` are cuts.

    D-lock is recorded separately. It does not choose the video mode.
    """
    if first:
        return False
    entry = entry or {}
    if _flag(entry.get("action_continuous")) or _flag(entry.get("plot_continuous")):
        return True
    relation = normalize_scene_relation(entry.get("scene_relation"), first=False)
    return relation in PLOT_CONTINUOUS_RELATIONS


# Name, index, and the optional total. ``封装单元 1/3`` and ``投喂单元 2/4``.
_UNIT_ID = re.compile(
    r"(%s)\s*(\d+)(?:\s*[/／]\s*(\d+))?" % UNIT_TOKEN
)
_DURATION_LINE = re.compile(
    r"(?m)^(?:duration|duration_seconds)\s*[:=]\s*(\d+(?:\.\d+)?)"
)
_BARE_INDEX = re.compile(r"0*(\d+)$")


def _format_unit_id(name, number, total=None):
    if total:
        return "%s %s/%s" % (name, int(number), int(total))
    return "%s %s" % (name, int(number))


def _parsed_unit_id(value):
    """``(name, index, total or None)`` for an id string. Headings do not match."""
    match = _UNIT_ID.fullmatch(_text(value))
    if not match:
        return None
    total = int(match.group(3)) if match.group(3) else None
    return (match.group(1), int(match.group(2)), total)


def unit_id_from_text(text, index):
    """Official id from the unit heading. ``封装单元 1/3``.

    A heading that already says N/M keeps that total, including ``· 段K``.
    A heading with only the index, and a block with no unit heading, stay
    ``封装单元 1``. New packing always writes the N/M heading.
    """
    heading = ""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped:
            heading = stripped
            break
    match = _UNIT_ID.search(heading)
    if match:
        total = int(match.group(3)) if match.group(3) else None
        return _format_unit_id(match.group(1), match.group(2), total)
    return "%s %s" % (UNIT_NAME, index)


def _unit_duration(text):
    match = _DURATION_LINE.search(str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def units_from_feed(feed):
    """One record per packed feed unit, in heading order. No video fields yet."""
    from end.prompt_gate import delivery_units

    rows = []
    for index, text in enumerate(delivery_units(feed), 1):
        rows.append({
            "id": unit_id_from_text(text, index),
            "duration": _unit_duration(text),
            "prompt": text,
            "images": [],
        })
    return rows


def _stored_row(stored, by_id, unit_id):
    """Stored submit fields for this official id.

    An older row may still say ``封装单元 1`` while the heading says
    ``封装单元 1/3``. That row is the same unit. Its video fields stay.
    A different token, or two rows for the same index, is not guessed.
    """
    saved = by_id.get(unit_id)
    if isinstance(saved, dict):
        return saved
    parsed = _parsed_unit_id(unit_id)
    if parsed is None:
        return None
    found = []
    for item in stored:
        other = _parsed_unit_id(item.get("id"))
        if other is not None and other[0] == parsed[0] and other[1] == parsed[1]:
            found.append(item)
    if len(found) == 1:
        return found[0]
    return None


def _board_rows(spec):
    """Storyboard shots packed into feed units. Unit ids are not shots."""
    rows = []
    for shot in (spec or {}).get("shots") or []:
        if not isinstance(shot, dict) or not _text(shot.get("id")):
            continue
        if _parsed_unit_id(shot.get("id")) is not None:
            continue
        rows.append(shot)
    if any(_bound_images(shot) or shot.get("duration") not in (None, "") for shot in rows):
        return rows
    text = (spec or {}).get("storyboard") or ""
    if not _text(text):
        return rows
    try:
        from end.storyboard_contract import known_ids, project_shots
    except ImportError:
        return rows
    projected, report = project_shots(text, known_ids((spec or {}).get("assets")))
    if projected and not report:
        return projected
    return rows


def _shot_slices(board, unit_count):
    """Shots packed into each unit, using the same 15s groups as feed packing."""
    empty = [[] for _ in range(unit_count)]
    if not board or unit_count <= 0:
        return empty
    durations = []
    usable = True
    for shot in board:
        try:
            seconds = float(shot.get("duration"))
        except (TypeError, ValueError):
            usable = False
            break
        if seconds <= 0:
            usable = False
            break
        durations.append(seconds)
    if not usable:
        if len(board) == unit_count:
            return [[shot] for shot in board]
        return empty
    from end.stage_runner import group_shot_durations

    groups = group_shot_durations(durations)
    if len(groups) != unit_count:
        if len(board) == unit_count:
            return [[shot] for shot in board]
        return empty
    slices = []
    cursor = 0
    for group in groups:
        count = len(group)
        slices.append(board[cursor:cursor + count])
        cursor += count
    return slices


def _ids_for_board_shot(shot, text_ids):
    """Storyboard 资产栏 for one shot. A stored ``images`` list wins over the manuscript."""
    if isinstance(shot, dict) and isinstance(shot.get("images"), list):
        ordered = []
        for item in shot.get("images") or []:
            text = _text(item)
            if text and text not in ordered:
                ordered.append(text)
        return ordered
    return list((text_ids or {}).get(_text((shot or {}).get("id"))) or [])


def _union_shot_images(shots, text_ids, label=""):
    """Union of 资产栏, storyboard order, first appearance wins. No extra ids."""
    ordered = []
    for shot in shots or []:
        for asset_id in _ids_for_board_shot(shot, text_ids):
            if asset_id not in ordered:
                ordered.append(asset_id)
    return cap_reference_ids(ordered, label)


def unit_storyboard_images(spec, unit_count):
    """One id list per packed unit, or ``None`` when shots cannot be placed on those units.

    The list is the storyboard 资产栏 of the shots that unit covers. Prompt
    aliases are not consulted.
    """
    board = _board_rows(spec)
    slices = _shot_slices(board, unit_count)
    if not any(slices):
        return None
    from end.storyboard_contract import storyboard_asset_ids

    text_ids = storyboard_asset_ids((spec or {}).get("storyboard") or "")
    rows = []
    for shots in slices:
        label = "、".join(
            _text(shot.get("id")) for shot in shots if isinstance(shot, dict) and _text(shot.get("id"))
        )
        rows.append(_union_shot_images(shots, text_ids, label))
    return rows


def packed_units(spec):
    """Packed feed units, with stills and any video fields already stored on them.

    The id is the heading form ``封装单元 N/M``. A stored row whose id is that
    unit without the total is read as the heading id. D-lock and the other
    submit fields on that row are copied, not rewritten. Storyboard shot ids
    are not units.

    Reference stills are the storyboard 资产栏 of the shots this unit covers,
    in storyboard order. A ``资产：`` line on the unit is that same list. Prompt
    aliases do not replace it. A stored image list is used only when those
    shots cannot be placed on the units.
    """
    spec = spec or {}
    derived = units_from_feed(spec.get("feed") or "")
    stored = [
        item for item in (spec.get("feed_units") or [])
        if isinstance(item, dict) and _text(item.get("id"))
    ]
    by_id = {str(item.get("id")): item for item in stored}
    if not derived:
        return [dict(item) for item in stored]
    unions = unit_storyboard_images(spec, len(derived))
    from end.storyboard_contract import asset_ids_from_text

    rows = []
    for index, unit in enumerate(derived):
        row = dict(unit)
        saved = _stored_row(stored, by_id, row["id"])
        if saved is None and index < len(stored) and not by_id:
            saved = stored[index]
        if unions is not None and index < len(unions):
            images = list(unions[index])
        else:
            listed = asset_ids_from_text(row.get("prompt") or "")
            if listed is not None:
                images = cap_reference_ids(listed, row["id"])
            elif isinstance(saved, dict):
                images = list(saved.get("images") or [])
            else:
                images = []
        row["images"] = _listed_ids(images)
        if isinstance(saved, dict):
            for key in SHOT_VIDEO_FIELDS:
                if key in saved and saved.get(key) not in (None, ""):
                    row[key] = saved.get(key)
            if isinstance(saved.get("video_images"), list):
                row["video_images"] = list(saved.get("video_images") or [])
        rows.append(row)
    return rows


def _bare_index(value):
    text = _text(value)
    match = _BARE_INDEX.fullmatch(text)
    if not match:
        return None
    number = int(match.group(1))
    if number < 1:
        return None
    return number


def normalize_entry_ids(entries, units):
    """Map ``1``..``N`` onto unit ids only when that mapping is one-to-one.

    Anything else, including ``S01``, is left for the exact-id check to reject.
    """
    rows = [unit for unit in units or [] if isinstance(unit, dict) and _text(unit.get("id"))]
    items = [item for item in entries or [] if isinstance(item, dict)]
    if not rows or len(items) != len(entries or []) or len(items) != len(rows):
        return list(entries or [])
    ids = [str(unit.get("id")) for unit in rows]
    raw = [_text(item.get("shot_id")) for item in items]
    if raw == ids:
        return items
    numbers = [_bare_index(item) for item in raw]
    if any(number is None for number in numbers):
        return items
    if numbers != list(range(1, len(ids) + 1)):
        return items
    aligned = []
    for item, unit_id in zip(items, ids):
        row = dict(item)
        row["shot_id"] = unit_id
        aligned.append(row)
    return aligned


def units_for_submit(spec):
    """Unit records End submits. A ledger that does not match is not copied on."""
    units = packed_units(spec)
    ledger = spec.get("seam_ledger") if isinstance(spec.get("seam_ledger"), dict) else {}
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    if not units or not entries:
        return units
    aligned = normalize_entry_ids(entries, units)
    report = check_seam_ledger({"entries": aligned}, units)
    if not report.get("ok"):
        return units
    return write_unit_fields(units, apply_submit_params(aligned, units))


def _reference_ids(entry, shot):
    """Stills for reference mode. An empty unit list does not become text.

    Prompt tags and storyboard bindings are already on the packed unit.
    ``scene_images`` fills a unit that is still empty, such as an empty shot
    that named its scene still on the ledger.
    """
    bound = _bound_images(shot)
    if bound:
        return bound
    return _listed_ids((entry or {}).get("scene_images"))


def map_submit_params(entry, shot=None, *, first=False):
    """Official H3 fields for one packed unit.

    The first unit is always ``reference``. A later unit that is
    action-continuous or plot-continuous with the previous unit is
    ``first_frame`` from that unit's tail, with no asset refs and no end
    frame. Every other unit is ``reference``. ``text`` and
    ``first_last_frame`` are never chosen. D-lock is not the selector.
    """
    entry = entry or {}
    if first or not continues_from_previous(entry, first=False):
        return _reference(_reference_ids(entry, shot))
    return _first_frame()


def apply_submit_params(entries, shots):
    """Copy official fields onto each entry. Bound stills stay on bound_images.

    scene_relation is stored as a SCENE_RELATIONS token. The first packed
    unit defaults to FIRST_UNIT_SCENE_RELATION. Every later unit defaults
    to DEFAULT_SCENE_RELATION. A recognized alias or enum token is kept.
    """
    by_id = {}
    first_id = ""
    for shot in shots or []:
        if isinstance(shot, dict) and shot.get("id"):
            by_id[str(shot.get("id"))] = shot
            if not first_id:
                first_id = _text(shot.get("id"))
    applied = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row["d_lock"] = _lock(row.get("d_lock"))
        row["shot_id"] = _text(row.get("shot_id"))
        if first_id:
            is_first = row["shot_id"] == first_id
        else:
            is_first = not applied
        row["scene_relation"] = normalize_scene_relation(
            row.get("scene_relation"),
            first=is_first,
        )
        shot = by_id.get(row["shot_id"]) or {}
        row["bound_images"] = _bound_images(shot)
        row["empty_use"] = _empty_use(row.get("empty_use"))
        official = map_submit_params(row, shot, first=is_first)
        row.update(official)
        applied.append(row)
    return applied


# Slots the seam machine check reads. Every packed unit keeps these names
# after a model write. Empty strings are not invented prose.
SEAM_SLOT_KEYS = (
    "shot_id",
    "prev_id",
    "d_lock",
    "reason",
    "scene_relation",
    "action_continuous",
    "plot_continuous",
    "video_mode",
    "first_frame_source",
    "images",
    "end_image",
    "start_size",
    "end_size",
    "camera_from",
    "camera_to",
    "camera_change",
    "tail_croppable",
    "crop_plan",
    "transition",
    "enter_exit",
    "empty_use",
)
_SEAM_FLAGS = ("action_continuous", "plot_continuous", "camera_change", "tail_croppable")
_SEAM_TEXT = (
    "reason",
    "crop_plan",
    "transition",
    "enter_exit",
    "camera_from",
    "camera_to",
    "empty_use",
)


def _align_seam_entries(entries, rows):
    """One model dict per packed unit. A matching id wins, else order.

    An entry that names unit 2 stays on unit 2. A single object with no id
    fills unit 1. Units the model skipped stay None so the skeleton can
    open their slots without copying another unit's prose.
    """
    items = [item for item in entries or [] if isinstance(item, dict)]
    if items and len(items) == len(rows):
        items = normalize_entry_ids(items, rows)
    by_id = {}
    for item in items:
        shot_id = _text(item.get("shot_id"))
        if shot_id and shot_id not in by_id:
            by_id[shot_id] = item
    ids = [str(unit.get("id")) for unit in rows]
    matched = [None] * len(rows)
    used = set()
    if any(unit_id in by_id for unit_id in ids):
        for index, unit_id in enumerate(ids):
            item = by_id.get(unit_id)
            if item is None:
                continue
            matched[index] = item
            used.add(id(item))
        leftovers = [item for item in items if id(item) not in used]
        for index, item in enumerate(matched):
            if item is None and leftovers:
                matched[index] = leftovers.pop(0)
        return matched
    for index in range(len(rows)):
        if index < len(items):
            matched[index] = items[index]
    return matched


def fold_seam_entries(entries, units):
    """One skeleton row per packed unit, with every required slot name.

    ``shot_id``, ``prev_id``, ``scene_relation``, and the official submit
    fields are program-owned. The first unit's ``d_lock`` is ``none``.
    A later ``d_lock`` is kept only when it is 1–4. 景别 values that sit on
    the ladder (or a ladder word plus 镜头/景别) are kept; any other size is
    left so the machine check can still fail it. Reason text and other
    prose are not invented.
    """
    rows = [unit for unit in units or [] if isinstance(unit, dict) and _text(unit.get("id"))]
    if not rows:
        return apply_submit_params(
            [item for item in entries or [] if isinstance(item, dict)],
            units,
        )
    aligned = _align_seam_entries(entries, rows)
    built = []
    for index, unit in enumerate(rows):
        model = aligned[index] or {}
        row = {}
        for key in SEAM_SLOT_KEYS:
            if key in model:
                row[key] = model.get(key)
        for key, value in model.items():
            if key not in row:
                row[key] = value
        row["shot_id"] = str(unit.get("id"))
        row["prev_id"] = "" if index == 0 else str(rows[index - 1].get("id"))
        lock = _lock(row.get("d_lock"))
        if index == 0 or lock not in ("1", "2", "3", "4"):
            row["d_lock"] = "none" if index == 0 else ""
        else:
            row["d_lock"] = lock
        row["start_size"] = normalize_shot_size(row.get("start_size"))
        row["end_size"] = normalize_shot_size(row.get("end_size"))
        for flag in _SEAM_FLAGS:
            if flag not in model:
                row[flag] = False
            else:
                row[flag] = _flag(model.get(flag))
        for key in _SEAM_TEXT:
            if key not in model or model.get(key) is None:
                row[key] = ""
            elif isinstance(model.get(key), str):
                row[key] = model.get(key)
            else:
                row[key] = _text(model.get(key))
        built.append(row)
    applied = apply_submit_params(built, rows)
    for row in applied:
        for key in SEAM_SLOT_KEYS:
            if key not in row:
                row[key] = [] if key == "images" else ""
        if not isinstance(row.get("images"), list):
            row["images"] = _listed_ids(row.get("images"))
    return applied


def _shot_rows(shots):
    return [shot for shot in shots or [] if isinstance(shot, dict) and _text(shot.get("id"))]


def _duration(shot):
    try:
        return float(shot.get("duration"))
    except (TypeError, ValueError):
        return None


def check_seam_ledger(ledger, units):
    """Hard gate. One row per packed unit, then the official parameter rules.

    ``units`` are the packed feed units. Storyboard shot ids do not satisfy it.
    """
    errors = []
    rows = _shot_rows(units)
    ledger = ledger if isinstance(ledger, dict) else {}
    raw_entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    entries = [item for item in raw_entries if isinstance(item, dict)]
    if len(entries) != len(raw_entries):
        errors.append("分镜衔接里有无法读取的条目")
    entries = normalize_entry_ids(entries, rows)
    expected = "、".join(str(unit.get("id")) for unit in rows)
    if len(entries) != len(rows):
        errors.append(
            "分镜衔接有 %s 条，封装单元有 %s 个。一条封装单元必须恰好一条段缝，不能把多个单元收成更少的段。封装单元 id：%s。"
            % (len(entries), len(rows), expected or "（没有）")
        )
    by_id = {}
    for entry in entries:
        shot_id = _text(entry.get("shot_id"))
        if not shot_id:
            errors.append("有一条段缝没有封装单元 id")
            continue
        if shot_id in by_id:
            errors.append("%s 有多于一条段缝" % shot_id)
        by_id[shot_id] = entry
    if not rows:
        errors.append("没有封装单元，不能做分镜衔接")
    ordered = []
    known = {str(unit.get("id")) for unit in rows}
    for index, unit in enumerate(rows):
        shot_id = str(unit.get("id"))
        entry = by_id.get(shot_id)
        if entry is None:
            errors.append("%s 没有段缝" % shot_id)
            continue
        ordered.append((index, unit, entry))
    for shot_id in by_id:
        if shot_id not in known:
            errors.append("段缝 %s 对不上封装单元。要原样照抄这些 id：%s。" % (shot_id, expected or "（没有）"))
    locks = []
    for index, shot, entry in ordered:
        shot_id = str(shot.get("id"))
        lock = _lock(entry.get("d_lock"))
        if lock not in D_LOCKS:
            errors.append("%s 的 D 锁无效" % shot_id)
            continue
        locks.append(lock)
        if index == 0 and lock != "none":
            errors.append("第一个封装单元没有入缝，D 锁要写 none")
        if index > 0 and lock == "none":
            errors.append("%s 缺少 D 锁" % shot_id)
        if index > 0 and lock not in ("1", "2", "3", "4"):
            errors.append("%s 的相邻段缝必须恰好是 ①②③④ 之一" % shot_id)
        relation = normalize_scene_relation(
            entry.get("scene_relation"),
            first=(index == 0),
        )
        if relation not in SCENE_RELATIONS:
            errors.append(invalid_scene_relation_error(shot_id))
        writing = _text(entry.get("writing_mode"))
        if writing and writing not in WRITING_MODES:
            errors.append("%s 的写法建议无效" % shot_id)
        if writing == "视频延长强连续" and lock != "1":
            errors.append("%s 的视频延长只能配 ①" % shot_id)
        mode = _text(entry.get("video_mode"))
        if mode not in VIDEO_MODES:
            errors.append("%s 的 video_mode 只能是 first_frame 或 reference" % shot_id)
        elif mode in FRAME_MODES and list(entry.get("images") or []):
            errors.append("%s 的首帧不能再带参考图" % shot_id)
        official = map_submit_params(entry, shot, first=(index == 0))
        stored_end = _text(entry.get("end_image"))
        official_end = official["end_image"]
        # The extracted tail is stored on the finished unit. It is not the
        # designed end frame the official params leave empty.
        if _is_extracted_tail(stored_end) and not official_end:
            stored_end = ""
        if mode in VIDEO_MODES and (
            mode != official["video_mode"]
            or list(entry.get("images") or []) != official["images"]
            or stored_end != official_end
            or _text(entry.get("first_frame_source") or "none") != official["first_frame_source"]
        ):
            errors.append("%s 的提交参数和连续关系不一致" % shot_id)
        end_size = _size_class(entry.get("end_size"))
        start_size = _size_class(entry.get("start_size"))
        if _text(entry.get("end_size")) and not end_size:
            errors.append("%s 的末镜景别不在景别阶梯上" % shot_id)
        if _text(entry.get("start_size")) and not start_size:
            errors.append("%s 的起镜景别不在景别阶梯上" % shot_id)
        camera_from = _text(entry.get("camera_from"))
        camera_to = _text(entry.get("camera_to"))
        same_camera = bool(camera_from and camera_to and camera_from == camera_to)
        same_size = bool(end_size and start_size and end_size == start_size)
        if index > 0 and same_camera and same_size:
            errors.append("%s 段缝两侧同机位同景别" % shot_id)
        if lock == "1":
            if not entry.get("action_continuous") or relation not in CONTINUE_RELATIONS:
                errors.append("%s 标了 ①，但动作、对白或一镜到底并没有接上" % shot_id)
            if not entry.get("tail_croppable") or not _text(entry.get("crop_plan")):
                errors.append("%s 的 ① 缺少可截计划" % shot_id)
            if not (end_size and start_size and end_size == start_size):
                errors.append("%s 的 ① 两侧景别必须同档" % shot_id)
            if not entry.get("camera_change") or same_camera:
                errors.append("%s 的 ① 必须换机位" % shot_id)
            if entry.get("redraw_still") and not (
                _text(entry.get("designed_still")) and _text(entry.get("still_prompt"))
            ):
                errors.append("%s 要重绘尾帧，但没有静帧和提示词" % shot_id)
        if lock == "2":
            blob = " ".join((
                _text(entry.get("reason")),
                _text(entry.get("crop_plan")),
                _text(entry.get("enter_exit")),
            ))
            if any(word in blob for word in FREEZE_WORDS):
                errors.append("%s 的 ② 不能写定格" % shot_id)
            if not entry.get("camera_change") or same_camera:
                errors.append("%s 的 ② 必须换机位" % shot_id)
            if _text(entry.get("end_image")) and not _is_extracted_tail(entry.get("end_image")):
                errors.append("%s 的 ② 不能带结束帧" % shot_id)
            if (
                not continues_from_previous(entry, first=(index == 0))
                and _text(entry.get("first_frame_source")) not in ("", "none")
            ):
                errors.append("%s 的 ② 不能带上一段尾巴" % shot_id)
        if lock == "3":
            if _empty_use(entry.get("empty_use")) not in EMPTY_USES:
                errors.append("%s 的空镜要标明情绪、音效桥或时空转场" % shot_id)
            duration = _duration(shot)
            if duration is None or duration < 1 or duration > 2:
                errors.append("%s 的空镜时长要在 1 到 2 秒" % shot_id)
        if lock == "4" and not _text(entry.get("transition")):
            errors.append("%s 的 ④ 要写明转场手法" % shot_id)
        if entry.get("lock_t0") and not (
            _text(entry.get("designed_still")) and _text(entry.get("still_prompt"))
        ):
            errors.append("%s 锁了 t=0，但没有对应静帧" % shot_id)
    if locks.count("3") > 3:
        errors.append("空镜一集不能超过 3 处")
    if locks.count("4") > 3:
        errors.append("技巧型转场一集不能超过 3 处")
    report = "\n".join(errors)
    return {
        "ok": not errors,
        "fatal": "",
        "exit_code": 0 if not errors else 1,
        "errors": errors,
        "warnings": [],
        "stats": {"seams": len(ordered), "locks": locks},
        "blocks": len(ordered),
        "report": report,
        "needs_human": False,
    }


def write_unit_fields(units, entries):
    """Stamp official submit fields onto packed feed units.

    Reference mode keeps the bound stills on ``images`` and ``video_images``.
    A first-frame unit keeps neither, so the call does not also send asset refs.
    """
    by_id = {}
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("shot_id"):
            by_id[str(entry.get("shot_id"))] = entry
    updated = []
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        row = dict(unit)
        kept_tail = _text(row.get("end_image"))
        entry = by_id.get(str(row.get("id") or ""))
        if entry is None:
            for key in SHOT_VIDEO_FIELDS:
                row.pop(key, None)
            if _is_extracted_tail(kept_tail):
                row["end_image"] = kept_tail
            updated.append(row)
            continue
        row["video_mode"] = entry.get("video_mode") or ""
        row["first_frame_source"] = entry.get("first_frame_source") or "none"
        row["seam_d_lock"] = entry.get("d_lock") or "none"
        row["video_images"] = list(entry.get("images") or [])
        # A first-frame call cannot also carry asset refs.
        if row["video_mode"] == "reference":
            row["images"] = list(row["video_images"])
        else:
            row["images"] = []
        if _text(entry.get("writing_mode")):
            row["writing_mode"] = _text(entry.get("writing_mode"))
        entry_end = _text(entry.get("end_image"))
        if entry_end and not _is_extracted_tail(entry_end):
            row["end_image"] = entry_end
        elif _is_extracted_tail(kept_tail):
            row["end_image"] = kept_tail
        elif _is_extracted_tail(entry_end):
            row["end_image"] = entry_end
        else:
            row.pop("end_image", None)
        if entry.get("redraw_still"):
            row["redraw_still"] = True
        else:
            row.pop("redraw_still", None)
        updated.append(row)
    return updated


def write_shot_fields(shots, entries):
    """Stamp official submit fields onto records keyed by id. Units use this too."""
    return write_unit_fields(shots, entries)


def _strip_video_rows(rows):
    cleaned = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        for key in SHOT_VIDEO_FIELDS:
            row.pop(key, None)
        cleaned.append(row)
    return cleaned


def strip_seam(spec):
    """Drop the ledger and the video fields written onto shots and packed units."""
    spec = dict(spec or {})
    spec.pop("seam_ledger", None)
    if "shots" in spec:
        spec["shots"] = _strip_video_rows(spec.get("shots"))
    if "feed_units" in spec:
        spec["feed_units"] = _strip_video_rows(spec.get("feed_units"))
    return spec


def drop_packing(spec):
    """A new storyboard invalidates packing and the unit ledger."""
    spec = strip_seam(spec)
    spec.pop("feed", None)
    spec.pop("feed_units", None)
    return spec


def render_images_for(shot):
    """Images a render shot may send. Frame calls, and retired modes, send none."""
    mode = _text((shot or {}).get("video_mode"))
    if mode in FRAME_MODES or mode in _RETIRED_FRAME_MODES:
        return []
    if mode == "reference" and isinstance((shot or {}).get("video_images"), list):
        return _bound_images({"images": shot.get("video_images")})
    return None


def designed_still_assets(spec):
    """Stills that 出图 should actually make: a redraw, or an explicit t=0 lock."""
    spec = spec or {}
    ledger = spec.get("seam_ledger") if isinstance(spec.get("seam_ledger"), dict) else {}
    known = set()
    for asset in spec.get("assets") or []:
        if isinstance(asset, dict) and asset.get("id"):
            known.add(str(asset.get("id")))
    extra = []
    for entry in ledger.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if not (entry.get("redraw_still") or entry.get("lock_t0")):
            continue
        asset_id = _text(entry.get("designed_still"))
        prompt = _text(entry.get("still_prompt"))
        if not asset_id or not prompt or asset_id in known:
            continue
        known.add(asset_id)
        extra.append({"id": asset_id, "title": "衔接静帧", "prompt": prompt})
    return extra


def seam_blocks_render(spec, shots=None):
    """Locked storyboards cannot 出图 or 出片 until a unit-level ledger passes.

    Packing may already exist. A shot-level ledger, or a ledger shorter than
    the packed units, does not open stills or video.
    """
    spec = spec or {}
    if spec.get("storyboard_contract") != CONTRACT:
        return ""
    if not _text(spec.get("storyboard")):
        return ""
    units = packed_units(spec)
    if not units:
        return "分镜已锁定，还没有提示词封装，不能出图或出片。"
    ledger = spec.get("seam_ledger") if isinstance(spec.get("seam_ledger"), dict) else {}
    if not ledger.get("entries"):
        return "提示词封装已锁定，还没有分镜衔接，不能出图或出片。"
    report = check_seam_ledger(ledger, units)
    if not report.get("ok"):
        detail = str(report.get("report") or "").strip()
        if detail:
            return "分镜衔接还没通过，不能出图或出片。\n%s" % detail
        return "分镜衔接还没通过，不能出图或出片。"
    rows = shots if shots is not None else units
    missing = []
    for shot in rows or []:
        if not isinstance(shot, dict) or not shot.get("id"):
            continue
        if _text(shot.get("video_mode")) not in VIDEO_MODES:
            missing.append(str(shot.get("id")))
    if missing:
        return "这些封装单元还没有 video_mode：%s。不能出图或出片。" % "、".join(missing)
    return ""
