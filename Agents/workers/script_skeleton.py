"""Program-owned script skeleton extracted before any model write."""

import re

from end.locks import lock_brief

_SCENE_HEAD = re.compile(r"^#{2,6}\s+(.+?)\s*$")
_SCENE_ID = re.compile(r"(EP\d+[-_]?SC\d+|SC\d+|第\d+场|场\s*\d+)", re.I)
_TIME_RANGE = re.compile(
    r"[（(]\s*(\d+(?:\.\d+)?)\s*s?\s*[-–—~至到]\s*(\d+(?:\.\d+)?)\s*s?\s*[)）]",
    re.I,
)
_TIME_SPAN = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*s\s*[)）]", re.I)
_DURATION_LINE = re.compile(
    r"(?m)^([ \t]*[-*]?[ \t]*(?:DURATION_TARGET|成片时长秒|成片时长)[ \t]*[:=：][ \t]*).+$"
)
_ASPECT_LINE = re.compile(
    r"(?m)^([ \t]*[-*]?[ \t]*(?:ASPECT_RATIO|成片画幅|画幅)[ \t]*[:=：][ \t]*).+$"
)
_STYLE_LINE = re.compile(
    r"(?m)^([ \t]*[-*]?[ \t]*(?:画风正文|STYLE_LOOK|画面风格)[ \t]*[:=：][ \t]*).+$"
)
_END_MARK = re.compile(r"(?m)^#{2,6}\s*本集结束\s*$")
_CONFORMING_HEADING = re.compile(
    r"^#{2,6}\s+(EP\d+-SC\d+)\s*｜\s*.+?"
    r"[（(]\s*\d+(?:\.\d+)?\s*s\s*[-–—~至到]\s*\d+(?:\.\d+)?\s*s\s*[)）]\s*$",
    re.I,
)
_CLOCK_RANGE = re.compile(
    r"[（(]\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*[-–—~至到]\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*[)）]"
)
_SEGMENT_HEAD = re.compile(r"Segment\s+\d+", re.I)


def script_skeleton(state):
    """User locks plus already confirmed scene structure. No invented scenes."""
    state = state or {}
    locks = dict(state.get("locks") or {})
    prior = str(state.get("draft") or state.get("prior_draft") or "").strip()
    scenes = parse_script_scenes(prior) if prior else []
    return {
        "locks": locks,
        "scenes": scenes,
        "fill": ("情节", "对白", "情绪", "出场"),
    }


def format_script_skeleton(skeleton):
    """Read-only contract text prepended to first-write and repair prompts."""
    skeleton = skeleton or {}
    locks = skeleton.get("locks") or {}
    brief = lock_brief(locks)
    lines = [
        "当前剧本骨架如下。锁定槽只读，不要改写。",
        "不要在骨架外另起一篇完整剧本。",
        "只填填写槽：情节、对白、情绪、出场。",
        brief,
    ]
    scenes = list(skeleton.get("scenes") or [])
    if scenes:
        lines.append("已确认结构（只读，不得增删场次、改场号或改已锁定的时段）：")
        for scene in scenes:
            heading = str(scene.get("heading") or "").strip()
            if heading:
                lines.append("- %s" % heading)
                cast = str(scene.get("cast") or "").strip()
                if cast and cast != "【填写】":
                    lines.append("  出场：%s" % cast)
                else:
                    lines.append("  出场：【填写】")
    else:
        lines.append("场次标题必须用锁定格式：## EP001-SC01｜内·地点·日（0s - 15s）。用 EP/SC 场号、中文内外与地点时分、秒为单位的时段。不要写 Segment 1，不要写 (00:00 - 00:15) 这种时钟时段。")
        lines.append("还没有已确认的场次结构。不要虚构一场列表再把它当成锁定项。")
        lines.append("每个场次必须有填写槽「出场：角色、道具」，列出该场可见的角色和关键道具。不要由程序代写姓名。")
    lines.append("填写槽：情节、对白、情绪。每个场次还要填出场：角色、道具。写在已确认的场次里；若还没有场次，可在正文里写出结构，但场次标题必须用上面的锁定格式，且不得改写上面的用户锁。不要虚构出场姓名。")
    return "\n".join(line for line in lines if line)


_CAST_LINE = re.compile(r"^出场\s*[:：]\s*(.*)$")
_SPEAKER_LINE = re.compile(r"^(.+?)\s*[：:]\s*\S")
_NARRATION_SPEAKER = re.compile(r"^(旁白|VO|V\.?O\.?|字幕)$", re.I)
_FIELD_LABELS = re.compile(r"^(情节|对白|情绪|出场|道具|角色|场景|音效|动作|画面|时间|地点|备注)$")


def parse_script_scenes(text, include_legacy=False):
    """Scene headings already present in an accepted script. Empty if none.

    include_legacy reads Segment / clock-range headings so storyboard can
    allocate shots from an older official script. Script machine-check still
    rejects those headings as non-conforming.
    """
    found = []
    for heading, body in _scene_blocks(text, include_legacy=include_legacy):
        scene_id = _scene_id(heading)
        duration = _scene_duration(heading)
        if include_legacy:
            if not scene_id:
                scene_id = _legacy_scene_id(heading)
            if duration is None:
                duration = _clock_duration(heading)
        names, cast = _cast_from_body(body)
        found.append({
            "id": scene_id or heading,
            "heading": heading,
            "duration": duration,
            "body": body,
            "cast": cast,
            "cast_names": names,
        })
    return found


def _scene_blocks(text, include_legacy=False):
    found = []
    current = None
    buf = []
    for line in str(text or "").splitlines():
        match = _SCENE_HEAD.match(line.strip())
        heading = match.group(1).strip() if match else ""
        if heading and _is_scene_heading(heading, include_legacy=include_legacy):
            if current is not None:
                found.append((current, "\n".join(buf)))
            current = heading
            buf = []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        found.append((current, "\n".join(buf)))
    return found


def _is_scene_heading(heading, include_legacy=False):
    scene_id = _scene_id(heading)
    if include_legacy and not scene_id:
        scene_id = _legacy_scene_id(heading)
    is_scene = bool(scene_id or _TIME_RANGE.search(heading))
    if include_legacy and (_SEGMENT_HEAD.search(heading) or _CLOCK_RANGE.search(heading)):
        is_scene = True
    return is_scene


def _cast_from_body(body):
    for line in str(body or "").splitlines():
        match = _CAST_LINE.match(line.strip())
        if not match:
            continue
        raw = match.group(1).strip()
        names = []
        for name in re.split(r"[、,，;；/]+", raw):
            name = name.strip()
            if not name or name == "【填写】":
                continue
            if name not in names:
                names.append(name)
        return names, raw
    return [], ""


def _scene_speakers(body):
    found = []
    for line in str(body or "").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or raw.startswith("▲") or raw.startswith("-"):
            continue
        match = _SPEAKER_LINE.match(raw)
        if not match:
            continue
        label = re.sub(r"\*+", "", match.group(1)).strip()
        if label.startswith("出场") or _FIELD_LABELS.match(label):
            continue
        speaker = re.sub(r"[（(][^）)]*[）)]", "", label).strip()
        if not speaker or _NARRATION_SPEAKER.match(speaker) or _FIELD_LABELS.match(speaker):
            continue
        found.append(speaker)
    return found


def _speaker_in_cast(speaker, names):
    return speaker in names


def restore_script_locks(text, skeleton):
    """Write user locks and confirmed scene headings back if the model tampered."""
    skeleton = skeleton or {}
    locks = skeleton.get("locks") or {}
    raw = str(text or "")
    raw = _restore_lock_lines(raw, locks)
    return _restore_scene_headings(raw, skeleton.get("scenes") or [])


def script_skeleton_failures(text, skeleton):
    """Slot-fidelity failures after a write. Empty when locks still match."""
    skeleton = skeleton or {}
    locks = skeleton.get("locks") or {}
    raw = str(text or "")
    errors = []
    seconds = locks.get("duration_seconds")
    if seconds not in (None, ""):
        found = _header_value(_DURATION_LINE, raw)
        if found is not None and not _same_duration(found, seconds):
            errors.append("时长锁被改写：正文是 %s，锁定为 %s 秒" % (found, seconds))
    aspect = str(locks.get("aspect") or "").strip()
    if aspect:
        found = _header_value(_ASPECT_LINE, raw)
        if found is not None and _normalize_aspect(found) != _normalize_aspect(aspect):
            errors.append("画幅锁被改写：正文是 %s，锁定为 %s" % (found, aspect))
    style = str(locks.get("style_look") or locks.get("style") or "").strip()
    if style:
        found = _header_value(_STYLE_LINE, raw)
        if found is not None and found != style:
            errors.append("画风锁被改写：正文不是锁定的画风正文")
    written = {scene["id"]: scene for scene in parse_script_scenes(raw) if scene.get("id")}
    for scene in skeleton.get("scenes") or []:
        scene_id = scene.get("id")
        if not scene_id:
            continue
        current = written.get(scene_id)
        if current is None:
            errors.append("锁定场次缺失：%s" % scene_id)
            continue
        if scene.get("heading") and current.get("heading") != scene.get("heading"):
            errors.append("锁定场次被改写：%s" % scene_id)
    for heading in _nonconforming_scene_headings(raw):
        errors.append(
            "场次标题不是锁定格式：%s。须为 ## EP001-SC01｜内·地点·日（0s - 15s）" % heading
        )
    if seconds not in (None, ""):
        durations = [
            scene.get("duration")
            for scene in parse_script_scenes(raw)
            if _CONFORMING_HEADING.match("## %s" % (scene.get("heading") or ""))
        ]
        if durations and all(item is not None for item in durations):
            total = sum(float(item) for item in durations)
            target = float(seconds)
            if total != target:
                display = int(total) if float(total).is_integer() else total
                errors.append("场次时段合计是 %s 秒，锁定为 %s 秒" % (display, seconds))
    for scene in parse_script_scenes(raw):
        if not _CONFORMING_HEADING.match("## %s" % (scene.get("heading") or "")):
            continue
        names = list(scene.get("cast_names") or [])
        scene_id = scene.get("id") or scene.get("heading")
        if not names:
            errors.append("场次缺出场：%s 必须有「出场：」并列出至少一个角色或道具" % scene_id)
            continue
        for speaker in _scene_speakers(scene.get("body") or ""):
            if not _speaker_in_cast(speaker, names):
                errors.append("对白说话人「%s」未出现在 %s 的出场名单里" % (speaker, scene_id))
    return errors



def _nonconforming_scene_headings(text):
    found = []
    for line in str(text or "").splitlines():
        raw = line.strip()
        match = _SCENE_HEAD.match(raw)
        if not match:
            continue
        heading = match.group(1).strip()
        if not _is_scene_like_heading(heading):
            continue
        if _CONFORMING_HEADING.match(raw):
            continue
        found.append(heading)
    return found


def _is_scene_like_heading(heading):
    if _SEGMENT_HEAD.search(heading) or _CLOCK_RANGE.search(heading):
        return True
    if _TIME_RANGE.search(heading) or _TIME_SPAN.search(heading):
        return True
    return bool(_scene_id(heading))


def _header_value(pattern, text):
    match = pattern.search(str(text or ""))
    if not match:
        return None
    line = match.group(0)
    parts = re.split(r"[:=：]", line, maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def _same_duration(found, seconds):
    match = re.search(r"(\d+(?:\.\d+)?)", str(found or ""))
    if not match:
        return False
    value = float(match.group(1))
    target = float(seconds)
    return value == target


def _normalize_aspect(value):
    return re.sub(r"\s+", "", str(value or "")).replace("：", ":")


def _scene_id(heading):
    match = _SCENE_ID.search(heading)
    if not match:
        return ""
    raw = re.sub(r"\s+", "", match.group(1))
    raw = raw.replace("_", "-")
    if raw.upper().startswith("EP") or raw.upper().startswith("SC"):
        return raw.upper()
    return raw


def _legacy_scene_id(heading):
    match = _SEGMENT_HEAD.search(heading)
    if not match:
        return ""
    number = re.search(r"\d+", match.group(0))
    if not number:
        return ""
    return "SEGMENT-%s" % number.group(0)


def _clock_seconds(hours_or_minutes, minutes_or_seconds, maybe_seconds):
    if maybe_seconds is None:
        return int(hours_or_minutes) * 60 + int(minutes_or_seconds)
    return int(hours_or_minutes) * 3600 + int(minutes_or_seconds) * 60 + int(maybe_seconds)


def _clock_duration(heading):
    match = _CLOCK_RANGE.search(heading)
    if not match:
        return None
    start = _clock_seconds(match.group(1), match.group(2), match.group(3))
    end = _clock_seconds(match.group(4), match.group(5), match.group(6))
    seconds = end - start
    if seconds <= 0:
        return None
    return int(seconds)


def _scene_duration(heading):
    span = _TIME_RANGE.search(heading)
    if span:
        start = float(span.group(1))
        end = float(span.group(2))
        seconds = end - start
        if seconds <= 0:
            return None
        return int(seconds) if seconds.is_integer() else seconds
    single = _TIME_SPAN.search(heading)
    if not single:
        return None
    seconds = float(single.group(1))
    if seconds <= 0:
        return None
    return int(seconds) if seconds.is_integer() else seconds


def _restore_lock_lines(text, locks):
    seconds = locks.get("duration_seconds")
    aspect = str(locks.get("aspect") or "").strip()
    style = str(locks.get("style_look") or locks.get("style") or "").strip()
    raw = str(text or "")
    # Replacement functions keep user text out of the re.sub replacement template,
    # so a backslash in aspect/style is literal, not a bad escape.
    if seconds not in (None, "") and _DURATION_LINE.search(raw):
        raw = _DURATION_LINE.sub(lambda m: m.group(1) + "%s 秒" % seconds, raw, count=1)
    if aspect and _ASPECT_LINE.search(raw):
        raw = _ASPECT_LINE.sub(lambda m: m.group(1) + aspect, raw, count=1)
    if style and _STYLE_LINE.search(raw):
        raw = _STYLE_LINE.sub(lambda m: m.group(1) + style, raw, count=1)
    return raw


def _restore_scene_headings(text, scenes):
    if not scenes:
        return str(text or "")
    lines = str(text or "").splitlines()
    seen = set()
    for index, line in enumerate(lines):
        match = _SCENE_HEAD.match(line.strip())
        if not match:
            continue
        scene_id = _scene_id(match.group(1).strip())
        for scene in scenes:
            if scene_id and scene_id == scene.get("id"):
                prefix = line[: len(line) - len(line.lstrip())]
                hashes = re.match(r"#+", line.strip())
                mark = hashes.group(0) if hashes else "##"
                lines[index] = "%s%s %s" % (prefix, mark, scene.get("heading") or match.group(1).strip())
                seen.add(scene["id"])
                break
    missing = [scene for scene in scenes if scene.get("id") and scene["id"] not in seen]
    if missing:
        insert_at = len(lines)
        for index, line in enumerate(lines):
            if _END_MARK.match(line.strip()):
                insert_at = index
                break
        block = []
        for scene in missing:
            if block:
                block.append("")
            block.append("## %s" % (scene.get("heading") or scene["id"]))
        lines[insert_at:insert_at] = block if insert_at == len(lines) else block + [""]
    return "\n".join(lines)

