"""Images and narrations attached when a project is first started."""

import json
from pathlib import Path

from web.workflow_from_spec import _clamp_duration

MAX_MATERIALS = 8
INTAKE_NAME = "intake.json"


def material_title(filename, index):
    stem = Path(str(filename or "")).name
    stem = Path(stem).stem
    kept = []
    for char in stem:
        if char.isalnum() or char in " _-—" or "\u4e00" <= char <= "\u9fff":
            kept.append(char)
    text = " ".join("".join(kept).split())[:24].strip()
    return text or ("素材%s" % index)


def clean_narration(text):
    return " ".join(str(text or "").split())[:800]


def new_material(index, narration, filename):
    return {
        "id": "U%s" % index,
        "title": material_title(filename, index),
        "narration": clean_narration(narration),
    }


def load_intake(folder):
    path = Path(folder) / INTAKE_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    items = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        material_id = str(item.get("id") or "").strip() or ("U%s" % index)
        items.append(
            {
                "id": material_id,
                "title": str(item.get("title") or material_id),
                "narration": clean_narration(item.get("narration")),
            }
        )
    return items


def save_intake(folder, items):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / INTAKE_NAME
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def intake_note(items):
    if not items:
        return ""
    lines = ["已上传图素材："]
    for item in items:
        line = "- %s %s" % (item["id"], item["title"])
        if item.get("narration"):
            line += "。旁白：" + item["narration"]
        lines.append(line)
    return "\n".join(lines)


def enrich_brief(brief, items):
    """Tell the writer to keep the uploaded stills and the narration wording."""
    if not items:
        return brief
    lines = [
        brief,
        "",
        "用户已经上传这些图素材。出片直接使用这些图，不要再写成会换掉原图的定妆。",
        "剧本和分镜必须用到这些资产 id。每张图的旁白原句写进使用该图的镜头 narration，不要改字。",
        "没有旁白的图，对应镜头 narration 为空字符串。",
    ]
    for item in items:
        lines.append("- %s %s。旁白：%s" % (item["id"], item["title"], item.get("narration") or "（无）"))
    return "\n".join(lines)


def _asset_prompt(item):
    if item.get("narration"):
        return item["narration"]
    return "用户上传的素材，保持这张图里的人和场景。"


def _shot_id(shots):
    used = set()
    for shot in shots:
        if isinstance(shot, dict) and shot.get("id"):
            used.add(str(shot.get("id")))
    number = 1
    while "S%02d" % number in used:
        number += 1
    return "S%02d" % number


def merge_intake(spec, items):
    """Add uploaded stills and put each narration on a shot that uses that still."""
    if not items:
        return spec if isinstance(spec, dict) else {}
    spec = spec if isinstance(spec, dict) else {}
    assets = spec.get("assets")
    if not isinstance(assets, list):
        assets = []
        spec["assets"] = assets
    by_id = {}
    for asset in assets:
        if isinstance(asset, dict) and asset.get("id"):
            by_id[str(asset.get("id"))] = asset
    for item in items:
        asset = by_id.get(item["id"])
        if asset is None:
            asset = {
                "id": item["id"],
                "title": item["title"],
                "prompt": _asset_prompt(item),
                "depends_on": "",
            }
            assets.append(asset)
            by_id[item["id"]] = asset
        elif not str(asset.get("prompt") or "").strip():
            asset["prompt"] = _asset_prompt(item)
    shots = spec.get("shots")
    if not isinstance(shots, list):
        shots = []
        spec["shots"] = shots
    used = set()
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        images = [str(name) for name in (shot.get("images") or [])]
        for image_id in images:
            match = next((item for item in items if item["id"] == image_id and item["id"] not in used), None)
            if match is None or not match.get("narration"):
                continue
            if not str(shot.get("narration") or "").strip():
                shot["narration"] = match["narration"]
            used.add(match["id"])
    for item in items:
        if item["id"] in used or not item.get("narration"):
            continue
        for shot in shots:
            if not isinstance(shot, dict) or str(shot.get("narration") or "").strip():
                continue
            images = [str(name) for name in (shot.get("images") or [])]
            if item["id"] not in images:
                images.insert(0, item["id"])
                shot["images"] = images[:8]
            shot["narration"] = item["narration"]
            used.add(item["id"])
            break
    for item in items:
        referenced = False
        for shot in shots:
            if isinstance(shot, dict) and item["id"] in [str(name) for name in (shot.get("images") or [])]:
                referenced = True
                break
        if referenced:
            continue
        narration = item.get("narration") or ""
        shots.append(
            {
                "id": _shot_id(shots),
                "duration": _clamp_duration(None, narration),
                "prompt": narration or "按用户上传的图演这一镜",
                "images": [item["id"]],
                "dialogue": "",
                "narration": narration,
            }
        )
    return spec
