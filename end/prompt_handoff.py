"""One handoff from prepared delivery text to image, video, and speech.

Image text comes from ``#### 图N`` blocks in ``image_prompts``.
Video text comes from ``feed`` delivery units (H3 six sections or Seedance
seven sections). Storyboard columns and the placeholder line are only the
fallback when no delivery unit exists. A prepared unit is sent as written.
"""

import re

from end.repair_units import asset_pieces

_UNIT_HEAD = re.compile(r"(?m)^#{1,6}[^\n]*投喂单元[^\n]*$")
_INNER_SHOT = re.compile(r"\[Shot\s+\d+\]|(?:^|\n)\s*镜\s*\d+")
_DURATION = re.compile(r"(?m)^duration:\s*(\d+(?:\.\d+)?)\s*$")
_CAPTION = re.compile(r"<d>\s*(?:\[[^\]]+\]\s*)?(.*?)\s*</d>", re.I | re.S)
_FIGURE = re.compile(r"图\s*(\d+)")
_PLACEHOLDER = "按这张资产图演这一镜"
# A shorter asset line is the collapsed form of its 图 block, not a later edit.
_SHORT_ASSET = 80


def _render_feed(feed):
    from end.stage_runner import render_feed_text

    return render_feed_text(feed).strip()


def looks_like_feed(text):
    """True when the body is a prepared H3 or Seedance delivery, not a note."""
    raw = str(text or "")
    if "subject_definitions" in raw or "投喂单元" in raw:
        return True
    if "【画幅风格】" in raw or "【时间轴分镜】" in raw:
        return True
    if "integrated_multimodal_description" in raw:
        return True
    if re.search(r"(?m)^(I2VA|FL2VA|L2VA)\s*:", raw):
        return True
    return False


def is_prepared_delivery(text):
    """A video prompt that must be sent unchanged."""
    return looks_like_feed(text)


def feed_units(feed):
    """Delivery units in run order. Empty when ``feed`` is not a delivery document."""
    rendered = _render_feed(feed)
    if not rendered or not looks_like_feed(rendered):
        return []
    units = []
    for index, text in enumerate(_split_units(rendered), 1):
        units.append({
            "index": index,
            "text": text,
            "duration": _unit_duration(text),
        })
    return units


def feed_has_units(feed):
    return bool(feed_units(feed))


def _split_units(text):
    marks = list(_UNIT_HEAD.finditer(text))
    if not marks:
        return [text.strip()] if text.strip() else []
    parts = []
    if marks[0].start() > 0:
        lead = text[:marks[0].start()].strip()
        if lead and looks_like_feed(lead):
            parts.append(lead)
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        chunk = text[mark.start():end].strip()
        if chunk:
            parts.append(chunk)
    return parts


def _unit_duration(text):
    match = _DURATION.search(text or "")
    if not match:
        return None
    try:
        seconds = int(float(match.group(1)))
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return seconds


def caption_lines(text):
    """Spoken lines already wrapped in ``<d>`` by the feed."""
    found = []
    for match in _CAPTION.finditer(text or ""):
        line = re.sub(r"\s+", " ", match.group(1)).strip()
        if line and line not in found:
            found.append(line)
    return found


def _figure_number(asset):
    raw = "%s %s" % (asset.get("id") or "", asset.get("title") or "")
    match = _FIGURE.search(raw)
    if not match:
        return None
    return int(match.group(1))


def _match_block(asset, blocks, used):
    number = _figure_number(asset)
    if number is not None:
        for block_number, body in blocks:
            if block_number == number and block_number not in used:
                return block_number, body
    title = re.sub(r"\s+", "", str(asset.get("title") or ""))
    if len(title) < 2:
        return None, ""
    for block_number, body in blocks:
        if block_number in used:
            continue
        heading = re.sub(r"\s+", "", (body.splitlines() or [""])[0])
        if title in heading or (heading and heading in title):
            return block_number, body
    return None, ""


def choose_image_text(prompt, block):
    """Prefer the 图 block unless the asset line is a longer separate edit."""
    prompt = str(prompt or "").strip()
    block = str(block or "").strip()
    if not block:
        return prompt
    if not prompt or prompt == _PLACEHOLDER or prompt in block:
        return block
    if block in prompt:
        return prompt
    if len(prompt) < _SHORT_ASSET:
        return block
    return prompt


def reconcile_assets(spec):
    """Fill asset prompts from ``image_prompts`` and keep blocks that have no row."""
    spec = spec or {}
    assets = [dict(item) for item in (spec.get("assets") or []) if isinstance(item, dict)]
    blocks = asset_pieces(spec.get("image_prompts") or "")
    if not blocks:
        return assets
    used = set()
    for asset in assets:
        number, body = _match_block(asset, blocks, used)
        if not body:
            continue
        used.add(number)
        asset["prompt"] = choose_image_text(asset.get("prompt"), body)
    for number, body in blocks:
        if number in used:
            continue
        stripped = body.strip()
        if any(str(asset.get("prompt") or "").strip() == stripped for asset in assets):
            continue
        heading = stripped.splitlines()[0] if stripped else ""
        title = heading.split("·", 1)[-1].strip() if "·" in heading else heading
        assets.append({
            "id": "图%d" % number,
            "title": title or ("图%d" % number),
            "prompt": stripped,
            "view": "single",
        })
    return assets


def _inner_counts(units):
    return [len(_INNER_SHOT.findall(unit["text"])) for unit in units]


def _even_groups(shots, count):
    if count <= 0:
        return []
    groups = []
    cursor = 0
    base, extra = divmod(len(shots), count)
    for index in range(count):
        take = base + (1 if index < extra else 0)
        groups.append(list(shots[cursor:cursor + take]))
        cursor += take
    return groups


def _groups_for(units, shots):
    shots = [shot for shot in (shots or []) if isinstance(shot, dict)]
    if not units:
        return []
    if not shots or len(units) == len(shots):
        if len(units) == len(shots):
            return [[shot] for shot in shots]
        return [[] for _ in units]
    counts = _inner_counts(units)
    if not any(counts):
        return _even_groups(shots, len(units))
    groups = []
    cursor = 0
    for count in counts:
        take = count or 0
        groups.append(list(shots[cursor:cursor + take]))
        cursor += take
    if cursor < len(shots) and groups:
        groups[-1].extend(shots[cursor:])
    return groups


def _mentioned_images(text, known):
    found = []
    for match in _FIGURE.finditer(text or ""):
        asset_id = "图%s" % match.group(1)
        if asset_id in known and asset_id not in found:
            found.append(asset_id)
    return found


def _group_images(group):
    found = []
    for shot in group or []:
        for name in shot.get("images") or []:
            text = str(name or "").strip()
            if text and text not in found:
                found.append(text)
    return found


def video_mode_for(text, images):
    """H3 six sections are reference. I2VA / FL2VA / L2VA pick a frame role."""
    raw = str(text or "")
    pictures = list(images or [])
    six = "subject_definitions" in raw
    if re.search(r"FL2VA|mode\s*=\s*first_last_frame", raw, re.I) and len(pictures) >= 2 and not six:
        return "first_last_frame"
    if re.search(r"(?m)^L2VA\s*:", raw) and pictures and not six:
        return "last_frame"
    if re.search(r"(?m)^I2VA\s*:", raw) and pictures and not six:
        return "first_frame"
    if six or "【时间轴分镜】" in raw or "【画幅风格】" in raw:
        return "reference" if pictures else "text"
    return ""


def _caption_and_speech(unit_text, group):
    """Subtitles keep every prepared line. Speech is only text the video prompt lacks."""
    lines = caption_lines(unit_text)
    for shot in group or []:
        extra = str(shot.get("narration") or "").strip()
        if extra and extra not in unit_text and extra not in lines:
            lines.append(extra)
    speech = [line for line in lines if line not in unit_text]
    return "\n".join(lines), "\n".join(speech)


def _duration_for(unit, group):
    if unit.get("duration"):
        return unit["duration"]
    total = 0
    for shot in group or []:
        try:
            total += int(shot.get("duration") or 0)
        except (TypeError, ValueError):
            continue
    return total or None


def shots_from_units(units, shots, assets):
    """One render shot per delivery unit. The prompt is that unit, verbatim."""
    known = {str(asset.get("id") or "") for asset in assets or [] if isinstance(asset, dict)}
    groups = _groups_for(units, shots)
    rendered = []
    same_count = len(units) == len([shot for shot in (shots or []) if isinstance(shot, dict)])
    for index, unit in enumerate(units):
        group = groups[index] if index < len(groups) else []
        images = _mentioned_images(unit["text"], known)
        for name in _group_images(group):
            if name not in images:
                images.append(name)
        images = images[:8]
        mode = video_mode_for(unit["text"], images)
        if not mode and len(group) == 1:
            mode = str(group[0].get("video_mode") or "").strip()
        if mode == "reference" and not images:
            mode = "text"
        if mode in ("first_frame", "last_frame") and not images:
            mode = "text"
        if mode == "first_last_frame" and len(images) < 2:
            mode = "reference" if images else "text"
        if same_count and group and group[0].get("id"):
            shot_id = group[0].get("id")
        else:
            shot_id = "V%02d" % (index + 1)
        narration, speech = _caption_and_speech(unit["text"], group)
        shot = {
            "id": shot_id,
            "duration": _duration_for(unit, group),
            "prompt": unit["text"],
            "dialogue": "",
            "narration": narration,
            "images": images,
            "tts_text": speech,
        }
        if mode == "first_last_frame" and len(images) >= 2:
            shot["images"] = [images[0]]
            shot["end_image"] = images[-1]
        if mode:
            shot["video_mode"] = mode
        rendered.append(shot)
    return rendered


def prepare_delivery_spec(spec):
    """Copy a spec whose assets and shots are what render should read.

    The caller's spec is left as stored. ``feed`` stays the video source.
    ``image_prompts`` stays the image source.
    """
    spec = dict(spec or {})
    assets = reconcile_assets(spec)
    spec["assets"] = assets
    units = feed_units(spec.get("feed"))
    if units:
        spec["shots"] = shots_from_units(units, spec.get("shots"), assets)
    return spec
