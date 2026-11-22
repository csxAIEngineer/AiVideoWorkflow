"""Fold specialist slot fills into a stage skeleton. The merger does not choose a draft."""

import json
import re

_MARKERS = {
    "asset": "当前资产骨架如下。",
    "script": "当前剧本骨架如下。",
    "storyboard": "当前分镜骨架如下。",
}
_FIELDS = {
    "asset": "image_prompts",
    "script": "script",
    "storyboard": "storyboard",
}
_SLOT_LINE = re.compile(r"^(格\d+|整板)\s*[:：]\s*(.*)$")
_SCRIPT_LIST_HEAD = re.compile(r"^-\s+(?:#{2,6}\s+)?(.+?)$")
_SCRIPT_CAST = re.compile(r"^出场\s*[:：]\s*(.*)$")
_STORY_HEAD = re.compile(r"^#{2,6}\s+(\S+)\s*$")
_STORY_FILL = re.compile(r"^(画面层|旁白|声景)\s*[:：]\s*(.*)$")
_SCENE_ID = re.compile(r"(EP\d+[-_]?SC\d+|SC\d+)", re.I)


def fold_stage_draft(task, brief, partials):
    """Return the official draft assembled from skeleton slots plus specialist fills.

    Empty when this task has no skeleton in the brief. Unfilled slots stay 【填写】.
    Skill-document notes that do not carry slots are ignored.
    """
    task = str(task or "").strip()
    mark = _MARKERS.get(task)
    if not mark:
        return ""
    skeleton = _skeleton_from_brief(brief, mark)
    if not skeleton.strip():
        return ""
    notes = [_note_as_markdown(item) for item in (partials or [])]
    if task == "asset":
        return _fold_asset(skeleton, notes)
    if task == "script":
        return _fold_script(skeleton, notes)
    return _fold_storyboard(skeleton, notes)


def folded_merge_output(task, draft):
    """JSON object the suite merge step already returns."""
    field = _FIELDS.get(str(task or "").strip())
    if not field:
        return ""
    return json.dumps({field: draft}, ensure_ascii=False)


def _skeleton_from_brief(brief, mark):
    text = str(brief or "")
    start = text.find(mark)
    if start < 0:
        return ""
    rest = text[start:]
    cut = rest.find("\n各节点正文：")
    if cut >= 0:
        rest = rest[:cut]
    return rest.strip() + "\n"


def _note_as_markdown(item):
    if isinstance(item, dict):
        raw = item.get("text")
        if raw is None:
            raw = item.get("body") or ""
    else:
        raw = item
    text = str(raw or "").strip()
    if not text:
        return ""
    data = _json_object(text)
    if not isinstance(data, dict):
        return text
    prompts = data.get("image_prompts")
    if isinstance(prompts, list):
        parts = []
        for card in prompts:
            if isinstance(card, str) and card.strip():
                parts.append(card.strip())
            elif isinstance(card, dict):
                block = str(card.get("prompt") or card.get("text") or "").strip()
                if block:
                    parts.append(block)
        if parts:
            return "\n\n".join(parts)
    for key in ("image_prompts", "script", "storyboard", "text", "feed"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return text


def _json_object(raw):
    start = str(raw or "").find("{")
    if start < 0:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(str(raw)[start:])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _filled(value):
    text = str(value or "").strip()
    if not text:
        return False
    if text == "【填写】" or text.startswith("【填写"):
        return False
    return True


def _fold_asset(skeleton, notes):
    from Agents.workers.asset_skeleton import _asset_pieces

    fills = {}
    for note in notes:
        for asset_id, body in _asset_pieces(note):
            for line in str(body or "").splitlines():
                match = _SLOT_LINE.match(line.strip())
                if not match or not _filled(match.group(2)):
                    continue
                fills[(asset_id, match.group(1))] = match.group(2).strip()
    pieces = _asset_pieces(skeleton)
    if not pieces:
        return ""
    blocks = []
    for asset_id, body in pieces:
        lines = []
        for line in str(body or "").splitlines():
            match = _SLOT_LINE.match(line.strip())
            key = (asset_id, match.group(1)) if match else None
            if key in fills:
                lines.append("%s：%s" % (match.group(1), fills[key]))
            else:
                lines.append(line)
        blocks.append("\n".join(lines).strip())
    return "\n\n".join(block for block in blocks if block) + "\n"


def _fold_script(skeleton, notes):
    from Agents.workers.script_skeleton import parse_script_scenes

    locked = _locked_script_scenes(skeleton)
    if not locked:
        return ""
    written = {}
    for note in notes:
        for scene in parse_script_scenes(note):
            scene_id = scene.get("id")
            if scene_id:
                written[scene_id] = scene
    blocks = []
    header = _script_lock_header(skeleton)
    if header:
        blocks.append(header)
    for scene in locked:
        heading = str(scene.get("heading") or "").strip()
        if not heading.startswith("#"):
            heading = "## %s" % heading
        body = _script_body(scene, written.get(scene.get("id")))
        blocks.append("%s\n%s" % (heading, body) if body else heading)
    return "\n\n".join(blocks).strip() + "\n"


def _locked_script_scenes(skeleton):
    from Agents.workers.script_skeleton import parse_script_scenes, _scene_id

    found = []
    pending = None
    for line in str(skeleton or "").splitlines():
        raw = line.strip()
        match = _SCRIPT_LIST_HEAD.match(raw)
        if match and ("｜" in match.group(1) or _SCENE_ID.search(match.group(1))):
            if pending:
                found.append(pending)
            heading = match.group(1).strip()
            heading = re.sub(r"^#{2,6}\s+", "", heading)
            pending = {
                "id": _scene_id(heading) or heading,
                "heading": heading,
                "cast": "",
            }
            continue
        if pending:
            cast = _SCRIPT_CAST.match(raw.lstrip())
            if cast:
                pending["cast"] = cast.group(1).strip()
    if pending:
        found.append(pending)
    if found:
        return found
    return [
        {"id": scene.get("id"), "heading": scene.get("heading"), "cast": scene.get("cast") or ""}
        for scene in parse_script_scenes(skeleton)
        if scene.get("heading")
    ]


def _script_lock_header(skeleton):
    lines = []
    started = False
    for line in str(skeleton or "").splitlines():
        stripped = line.strip()
        if re.match(r"^[-*]?\s*(DURATION_TARGET|成片时长秒|成片时长|ASPECT_RATIO|成片画幅|画幅)\s*[:：=]", stripped):
            started = True
            lines.append(line.rstrip())
            continue
        if started and not stripped:
            break
        if started:
            if stripped.startswith("已确认结构") or stripped.startswith("- ") or stripped.startswith("填写槽"):
                break
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _script_body(locked, written):
    locked_cast = str((locked or {}).get("cast") or "").strip()
    if locked_cast == "【填写】":
        locked_cast = ""
    if not written:
        return "出场：%s" % (locked_cast or "【填写】")
    body = str(written.get("body") or "").strip()
    names = list(written.get("cast_names") or [])
    raw_cast = str(written.get("cast") or "").strip()
    if names or _filled(raw_cast):
        return body if body else "出场：%s" % (raw_cast or "、".join(names))
    if not body:
        return "出场：%s" % (locked_cast or "【填写】")
    if _SCRIPT_CAST.search(body):
        return body
    return "出场：%s\n%s" % (locked_cast or "【填写】", body)


def _fold_storyboard(skeleton, notes):
    fills = {}
    for note in notes:
        current = None
        for line in str(note or "").splitlines():
            heading = _STORY_HEAD.match(line.strip())
            if heading:
                current = heading.group(1).strip()
                continue
            if not current:
                continue
            field = _STORY_FILL.match(line.strip())
            if field and _filled(field.group(2)):
                fills[(current, field.group(1))] = field.group(2).strip()
    sections = _storyboard_sections(skeleton)
    if not sections:
        return ""
    blocks = []
    for shot_id, body in sections:
        lines = []
        for line in str(body or "").splitlines():
            field = _STORY_FILL.match(line.strip())
            key = (shot_id, field.group(1)) if field else None
            if key in fills:
                lines.append("%s：%s" % (field.group(1), fills[key]))
            else:
                lines.append(line)
        blocks.append("\n".join(lines).strip())
    return "\n\n".join(block for block in blocks if block) + "\n"


def _storyboard_sections(skeleton):
    found = []
    current = None
    buf = []
    started = False
    for line in str(skeleton or "").splitlines():
        heading = _STORY_HEAD.match(line.strip())
        if heading:
            started = True
            if current is not None:
                found.append((current, "\n".join(buf)))
            current = heading.group(1).strip()
            buf = [line]
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        found.append((current, "\n".join(buf)))
    return found if started else []
