"""Build the canvas shot list from a storyboard body."""

import ast
import json
import re
import time

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


def _log(message, data):
    # region agent log
    try:
        payload = {
            "sessionId": "eab7ec",
            "runId": "post-fix",
            "hypothesisId": "S",
            "location": "end/board_shots.py:apply_storyboard_shots",
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-eab7ec.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


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


def _debug_baee6e(message, data):
    # region agent log
    try:
        payload = {
            "sessionId": "baee6e",
            "runId": "post-fix",
            "hypothesisId": "A",
            "location": "end/board_shots.py:shots_from_storyboard",
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-baee6e.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


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
    # region agent log
    _debug_baee6e(
        "storyboard rows to canvas",
        {
            "rows": len(storyboard_rows(value)),
            "derived": len(shots),
            "withDescription": sum(1 for row in storyboard_rows(value) if row.get("description")),
        },
    )
    # endregion
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
    _log(
        "storyboard shots",
        {
            "boardRows": len(storyboard_rows(spec.get("storyboard"))),
            "derived": len(derived),
            "before": len(current),
            "placeholder": placeholder,
            "wrote": wrote,
            "ids": [shot["id"] for shot in (derived if wrote else current)],
        },
    )
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
