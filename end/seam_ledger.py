"""Storyboard seam ledger and the official H3 submit fields it writes.

The ledger is the source of video_mode, end_image, first-frame source, and
reference images. End does not invent those from whichever stills happen to
be nearby. D-lock is the judgment. Official parameters are mapped from it.
"""

VIDEO_MODES = ("text", "first_frame", "first_last_frame", "reference")
H3_MAX_REFERENCE_IMAGES = 9
D_LOCKS = ("none", "1", "2", "3", "4")
HARD_SPLIT = ("2", "3", "4")
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


def default_writing_mode(lock, scene_relation=""):
    lock = _lock(lock)
    if lock == "1" and _text(scene_relation) == "same_scene":
        return "视频延长强连续"
    if lock == "1":
        return "首帧增强"
    return "普通衔接"


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
        if not _text(row.get("writing_mode")):
            row["writing_mode"] = default_writing_mode(row["d_lock"], row.get("scene_relation"))
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


def check_seam_ledger(ledger, shots):
    """Hard gate. One D-lock per adjacent pair, then the official parameter rules."""
    errors = []
    rows = _shot_rows(shots)
    ledger = ledger if isinstance(ledger, dict) else {}
    raw_entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    entries = [item for item in raw_entries if isinstance(item, dict)]
    if len(entries) != len(raw_entries):
        errors.append("分镜衔接里有无法读取的条目")
    by_id = {}
    for entry in entries:
        shot_id = _text(entry.get("shot_id"))
        if not shot_id:
            errors.append("有一条段缝没有镜头号")
            continue
        if shot_id in by_id:
            errors.append("%s 有多于一条段缝" % shot_id)
        by_id[shot_id] = entry
    if not rows:
        errors.append("没有镜头，不能做分镜衔接")
    ordered = []
    for index, shot in enumerate(rows):
        shot_id = str(shot.get("id"))
        entry = by_id.get(shot_id)
        if entry is None:
            errors.append("%s 没有段缝" % shot_id)
            continue
        ordered.append((index, shot, entry))
    for shot_id in by_id:
        if shot_id not in {str(shot.get("id")) for shot in rows}:
            errors.append("段缝 %s 对不上分镜" % shot_id)
    locks = []
    for index, shot, entry in ordered:
        shot_id = str(shot.get("id"))
        lock = _lock(entry.get("d_lock"))
        if lock not in D_LOCKS:
            errors.append("%s 的 D 锁无效" % shot_id)
            continue
        locks.append(lock)
        if index == 0 and lock != "none":
            errors.append("第一镜没有入缝，D 锁要写 none")
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


def write_shot_fields(shots, entries):
    """Stamp official submit fields onto storyboard shots. Asset bindings stay."""
    by_id = {}
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("shot_id"):
            by_id[str(entry.get("shot_id"))] = entry
    updated = []
    for shot in shots or []:
        if not isinstance(shot, dict):
            continue
        row = dict(shot)
        entry = by_id.get(str(row.get("id") or ""))
        if entry is None:
            updated.append(row)
            continue
        row["video_mode"] = entry.get("video_mode") or ""
        row["first_frame_source"] = entry.get("first_frame_source") or "none"
        row["seam_d_lock"] = entry.get("d_lock") or "none"
        row["writing_mode"] = entry.get("writing_mode") or ""
        row["video_images"] = list(entry.get("images") or [])
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


def strip_seam(spec):
    """Drop the ledger and every submit field a storyboard rewrite invalidates."""
    spec = dict(spec or {})
    spec.pop("seam_ledger", None)
    shots = []
    for shot in spec.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        row = dict(shot)
        for key in SHOT_VIDEO_FIELDS:
            row.pop(key, None)
        shots.append(row)
    if "shots" in spec:
        spec["shots"] = shots
    return spec


def group_indexes(durations, locks=None, limit=15):
    """Pack shot indexes. ②③④ always start a new unit. ① may merge."""
    locks = list(locks or [])
    groups = []
    bucket = []
    total = 0.0
    for index, raw in enumerate(durations or []):
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            continue
        if seconds <= 0:
            continue
        lock = _lock(locks[index]) if index < len(locks) and _text(locks[index]) else "none"
        if lock not in D_LOCKS:
            lock = "none"
        if seconds > limit:
            if bucket:
                groups.append(bucket)
                bucket = []
                total = 0.0
            groups.append([index])
            continue
        hard = lock in HARD_SPLIT and bool(bucket)
        if bucket and (hard or total + seconds > limit + 1e-6):
            groups.append(bucket)
            bucket = [index]
            total = seconds
        else:
            bucket.append(index)
            total += seconds
    if bucket:
        groups.append(bucket)
    return groups


def group_durations(durations, locks=None, limit=15):
    """Same groups as ``group_indexes``, as duration lists for the feed scaffold."""
    kept = []
    for raw in durations or []:
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            kept.append(seconds)
    return [
        [kept[index] for index in group]
        for group in group_indexes(kept, locks, limit)
    ]


def _entry_map(entries):
    found = {}
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("shot_id"):
            found[str(entry.get("shot_id"))] = entry
    return found


def _timed(shots, entries):
    by_id = _entry_map(entries)
    kept = []
    durations = []
    locks = []
    for shot in _shot_rows(shots):
        duration = _duration(shot)
        if duration is None or duration <= 0:
            continue
        entry = by_id.get(str(shot.get("id"))) or {}
        lock = _text(shot.get("seam_d_lock")) or _lock(entry.get("d_lock")) or "none"
        kept.append(shot)
        durations.append(duration)
        locks.append(lock)
    return kept, durations, locks


def remap_units(shots, entries, limit=15):
    """One submit parameter set per packed feed unit.

    A ① seam that was merged inside a unit is no longer its own H3 call.
    The unit keeps the inbound seam of its first shot. ②③④ are never inside
    a unit, because grouping already split them.
    """
    kept, durations, locks = _timed(shots, entries)
    by_id = _entry_map(entries)
    units = []
    for group in group_indexes(durations, locks, limit):
        first = kept[group[0]]
        entry = by_id.get(str(first.get("id"))) or {}
        mode = _text(entry.get("video_mode"))
        params = {
            "shot_ids": [str(kept[index].get("id")) for index in group],
            "video_mode": mode,
            "end_image": _text(entry.get("end_image")),
            "first_frame_source": _text(entry.get("first_frame_source")) or "none",
            "images": list(entry.get("images") or []),
            "writing_mode": _text(entry.get("writing_mode")),
            "d_lock": _lock(entry.get("d_lock")) or "none",
        }
        if mode == "reference":
            images = []
            for index in group:
                shot = kept[index]
                source = by_id.get(str(shot.get("id"))) or {}
                bound = source.get("bound_images")
                if not isinstance(bound, list) or not bound:
                    bound = shot.get("images") or []
                for asset in bound:
                    text = _text(asset)
                    if text and text not in images:
                        images.append(text)
            params["images"] = images[:H3_MAX_REFERENCE_IMAGES]
            params["end_image"] = ""
            params["first_frame_source"] = "none"
        elif mode in FRAME_MODES:
            params["images"] = []
        elif mode == "text":
            params["images"] = []
            params["end_image"] = ""
            params["first_frame_source"] = "none"
        units.append(params)
    return units


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
    """Locked storyboards cannot 出图 or 出片 until the seam ledger passes."""
    spec = spec or {}
    if spec.get("storyboard_contract") != CONTRACT:
        return ""
    if not _text(spec.get("storyboard")):
        return ""
    ledger = spec.get("seam_ledger") if isinstance(spec.get("seam_ledger"), dict) else {}
    if not ledger.get("entries"):
        return "分镜已锁定，还没有分镜衔接，不能出图或出片。"
    report = check_seam_ledger(ledger, spec.get("shots") or [])
    if not report.get("ok"):
        return "分镜已锁定，分镜衔接还没通过，不能出图或出片。"
    rows = shots if shots is not None else (spec.get("shots") or [])
    missing = []
    for shot in rows or []:
        if not isinstance(shot, dict) or not shot.get("id"):
            continue
        if _text(shot.get("video_mode")) not in VIDEO_MODES:
            missing.append(str(shot.get("id")))
    if missing:
        return "分镜已锁定，这些镜头还没有 video_mode：%s。不能出图或出片。" % "、".join(missing)
    return ""
