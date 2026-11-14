"""Packed-unit seam ledger and the official H3 submit fields it writes.

One ledger row per packed feed unit. The row id is that unit's id.
The official id is ``封装单元 N/M``, the same form as the packing heading
``【封装单元 N/M】``. An older stored row that still says ``封装单元 1`` is
the same unit when the heading already has the total.
End submits video_mode, end_image, first-frame source, and reference images
from the unit records. D-lock is the judgment. Official parameters are mapped
from it. Packing does not read this ledger.
"""

import re

from end.product_names import UNIT_NAME, UNIT_TOKEN

VIDEO_MODES = ("text", "first_frame", "first_last_frame", "reference")
H3_MAX_REFERENCE_IMAGES = 9
D_LOCKS = ("none", "1", "2", "3", "4")
FRAME_MODES = ("first_frame", "first_last_frame")
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


def _empty_use(value):
    text = _text(value)
    if text in EMPTY_USES:
        return text
    return _EMPTY_ALIAS.get(text, text)


def _reference(ids):
    images = []
    for item in ids or []:
        text = _text(item)
        if text and text not in images:
            images.append(text)
        if len(images) >= H3_MAX_REFERENCE_IMAGES:
            break
    if images:
        return {
            "video_mode": "reference",
            "images": images,
            "end_image": "",
            "first_frame_source": "none",
        }
    return {
        "video_mode": "text",
        "images": [],
        "end_image": "",
        "first_frame_source": "none",
    }


def _frames(source, end_image=""):
    end_image = _text(end_image)
    return {
        "video_mode": "first_last_frame" if end_image else "first_frame",
        "images": [],
        "end_image": end_image,
        "first_frame_source": _text(source) or "none",
    }


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


def packed_units(spec):
    """Packed feed units, with stills and any video fields already stored on them.

    The id is the heading form ``封装单元 N/M``. A stored row whose id is that
    unit without the total is read as the heading id. D-lock and the other
    submit fields on that row are copied, not rewritten. Storyboard shot ids
    are not units.
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
    try:
        from end.board_shots import images_for_feed_unit
    except ImportError:
        images_for_feed_unit = None
    assets = spec.get("assets") or []
    rows = []
    for index, unit in enumerate(derived):
        row = dict(unit)
        saved = _stored_row(stored, by_id, row["id"])
        if saved is None and index < len(stored) and not by_id:
            saved = stored[index]
        images = []
        if images_for_feed_unit is not None:
            images = list(images_for_feed_unit(row.get("prompt") or "", assets) or [])
        if not images and isinstance(saved, dict):
            images = list(saved.get("images") or [])
        row["images"] = images
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


def map_submit_params(entry, shot=None):
    """Official H3 fields for one inbound seam. D-lock decides. End does not."""
    entry = entry or {}
    lock = _lock(entry.get("d_lock"))
    bound = _bound_images(shot)
    designed = _text(entry.get("designed_still"))
    end_image = _text(entry.get("end_image"))
    anchor = bool(entry.get("anchor_end")) and bool(end_image)
    scene_images = [_text(item) for item in (entry.get("scene_images") or []) if _text(item)]
    if lock in ("", "none"):
        if entry.get("lock_t0") and designed:
            return _frames(designed)
        return _reference(bound)
    if lock == "1":
        if entry.get("redraw_still") and designed:
            source = designed
        else:
            source = "previous_tail"
        return _frames(source, end_image if anchor else "")
    if lock == "2":
        return _reference(bound)
    if lock == "3":
        return _reference(scene_images)
    if lock == "4":
        chosen = _text(entry.get("video_mode"))
        if chosen == "text":
            return _reference([])
        if chosen in FRAME_MODES:
            source = _text(entry.get("first_frame_source"))
            if source in ("", "none"):
                source = designed or "previous_tail"
            end = end_image if chosen == "first_last_frame" and end_image else ""
            return _frames(source, end)
        if chosen == "reference":
            listed = entry.get("images")
            return _reference(listed if isinstance(listed, list) and listed else bound)
        if entry.get("lock_t0") and designed:
            return _frames(designed)
        return _reference(bound)
    return _reference(bound)


def apply_submit_params(entries, shots):
    """Copy official fields onto each entry. Bound stills stay on bound_images."""
    by_id = {}
    for shot in shots or []:
        if isinstance(shot, dict) and shot.get("id"):
            by_id[str(shot.get("id"))] = shot
    applied = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row["d_lock"] = _lock(row.get("d_lock"))
        row["shot_id"] = _text(row.get("shot_id"))
        shot = by_id.get(row["shot_id"]) or {}
        row["bound_images"] = _bound_images(shot)
        row["empty_use"] = _empty_use(row.get("empty_use"))
        official = map_submit_params(row, shot)
        row.update(official)
        applied.append(row)
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
        relation = _text(entry.get("scene_relation"))
        if relation and relation not in SCENE_RELATIONS:
            errors.append("%s 的场景关系无效" % shot_id)
        writing = _text(entry.get("writing_mode"))
        if writing and writing not in WRITING_MODES:
            errors.append("%s 的写法建议无效" % shot_id)
        if writing == "视频延长强连续" and lock != "1":
            errors.append("%s 的视频延长只能配 ①" % shot_id)
        mode = _text(entry.get("video_mode"))
        if mode not in VIDEO_MODES:
            errors.append("%s 的 video_mode 必须是官方四种之一" % shot_id)
        elif mode in FRAME_MODES and list(entry.get("images") or []):
            errors.append("%s 的首帧或首尾帧不能再带参考图" % shot_id)
        official = map_submit_params(entry, shot)
        if mode in VIDEO_MODES and (
            mode != official["video_mode"]
            or list(entry.get("images") or []) != official["images"]
            or _text(entry.get("end_image")) != official["end_image"]
            or _text(entry.get("first_frame_source") or "none") != official["first_frame_source"]
        ):
            errors.append("%s 的提交参数和 D 锁不一致" % shot_id)
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
            if _text(entry.get("end_image")) or _text(entry.get("first_frame_source")) not in ("", "none"):
                errors.append("%s 的 ② 不能带上一段尾巴或结束帧" % shot_id)
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
    """Stamp official submit fields onto packed feed units. Asset bindings stay."""
    by_id = {}
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("shot_id"):
            by_id[str(entry.get("shot_id"))] = entry
    updated = []
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        row = dict(unit)
        entry = by_id.get(str(row.get("id") or ""))
        if entry is None:
            for key in SHOT_VIDEO_FIELDS:
                row.pop(key, None)
            updated.append(row)
            continue
        row["video_mode"] = entry.get("video_mode") or ""
        row["first_frame_source"] = entry.get("first_frame_source") or "none"
        row["seam_d_lock"] = entry.get("d_lock") or "none"
        row["video_images"] = list(entry.get("images") or [])
        if _text(entry.get("writing_mode")):
            row["writing_mode"] = _text(entry.get("writing_mode"))
        if _text(entry.get("end_image")):
            row["end_image"] = _text(entry.get("end_image"))
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
    """Images a render shot may send. Frame and text calls send none."""
    mode = _text((shot or {}).get("video_mode"))
    if mode in FRAME_MODES or mode == "text":
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
