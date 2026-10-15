"""Images and narrations attached when a project is first started."""

import json
import re
from pathlib import Path

from end.workflow_from_spec import _clamp_duration, _is_upload_id, _reference_ids

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
    """Tell the writer that uploads are references and narrations stay verbatim."""
    if not items:
        return brief
    lines = [
        brief,
        "",
        "用户已经上传这些图。上传图只是参考，不是资产，也不要写进镜头 images。",
        "每张上传图都要被一张要生成的资产引用：references 填上传图 id，例如 [\"U1\"]。",
        "depends_on 只填另一张要生成的资产 id。侧面和背面依赖生成好的正面，上传图只写在直接参考它的那张资产上。",
        "剧本和分镜只绑定生成出来的资产。每张图的旁白原句写进使用该资产的镜头 narration，不要改字。",
        "没有旁白的图，对应镜头 narration 为空字符串。",
    ]
    for item in items:
        lines.append("- %s %s。旁白：%s" % (item["id"], item["title"], item.get("narration") or "（无）"))
    return "\n".join(lines)


def _asset_prompt(item, dropped=None):
    written = str((dropped or {}).get("prompt") or "").strip()
    if written:
        return written
    if item.get("narration"):
        return item["narration"]
    return "用户上传的素材，保持这张图里的人和场景。"


_KIND_ALIASES = {
    "character": "character",
    "person": "character",
    "人物": "character",
    "角色": "character",
    "prop": "prop",
    "道具": "prop",
    "location": "location",
    "地点": "location",
    "场景": "location",
    "other": "other",
}
_TURNAROUND = ("character", "prop")


def _shot_id(shots):
    used = set()
    for shot in shots:
        if isinstance(shot, dict) and shot.get("id"):
            used.add(str(shot.get("id")))
    number = 1
    while "S%02d" % number in used:
        number += 1
    return "S%02d" % number


def _upload_sort(value):
    match = re.fullmatch(r"U(\d+)", str(value or ""))
    if match:
        return (0, int(match.group(1)), str(value))
    return (1, 0, str(value or ""))


def _fresh_asset_id(used):
    number = 1
    while True:
        candidate = "A%s" % number
        if candidate not in used and not _is_upload_id(candidate):
            used.add(candidate)
            return candidate
        number += 1


def _asset_kind(value):
    return _KIND_ALIASES.get(str(value or "").strip().lower(), "")


def _shot_names(shot, key):
    if not isinstance(shot, dict) or shot.get(key) is None:
        return None
    return [str(name) for name in (shot.get(key) or [])]


def _collect_upload_ids(assets, shots, items, extra_ids):
    found = set()
    for item in items or []:
        if isinstance(item, dict) and item.get("id"):
            found.add(str(item["id"]))
    for item in extra_ids or []:
        if item:
            found.add(str(item))
    for asset in assets:
        if _is_upload_id(asset.get("id")):
            found.add(str(asset["id"]))
        if _is_upload_id(asset.get("depends_on")):
            found.add(str(asset["depends_on"]))
        for ref in _reference_ids(asset.get("references")):
            if _is_upload_id(ref) or ref in found:
                found.add(ref)
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        for name in shot.get("images") or []:
            if _is_upload_id(name):
                found.add(str(name))
        if _is_upload_id(shot.get("end_image")):
            found.add(str(shot.get("end_image")))
    return found


def _upload_records(upload_ids, items, dropped):
    provided = {}
    for item in items or []:
        if isinstance(item, dict) and item.get("id"):
            provided.setdefault(str(item["id"]), item)
    records = []
    for upload_id in sorted(upload_ids, key=_upload_sort):
        gone = dropped.get(upload_id) or {}
        item = provided.get(upload_id)
        if item is None:
            item = {
                "id": upload_id,
                "title": str(gone.get("title") or upload_id),
                "narration": "",
            }
        records.append((item, gone))
    return records


def _generated_from_upload(item, dropped, used):
    title = str(item.get("title") or dropped.get("title") or "素材").strip() or "素材"
    kind = _asset_kind(dropped.get("subject_kind"))
    view = str(dropped.get("view") or "").strip().lower()
    if kind in _TURNAROUND:
        if view not in ("front", "side", "back"):
            view = ""
    elif kind:
        view = "single"
    else:
        view = ""
    subject = str(dropped.get("subject") or "").strip() or title
    return {
        "id": _fresh_asset_id(used),
        "title": title,
        "prompt": _asset_prompt(item, dropped),
        "depends_on": "",
        "references": [str(item["id"])],
        "subject": subject,
        "subject_kind": kind,
        "view": view,
    }


def _direct_referencer(assets, upload_id):
    matches = []
    for asset in assets:
        if upload_id in [str(ref) for ref in (asset.get("references") or [])]:
            matches.append(asset)
    if not matches:
        return None
    for asset in matches:
        if not str(asset.get("depends_on") or ""):
            return asset
    return matches[0]


def _retarget_names(names, upload_ids, owners):
    result = []
    for name in names:
        if name in upload_ids:
            target = owners.get(name)
            if target and target not in result:
                result.append(target)
            continue
        if name not in result:
            result.append(name)
    return result[:8]


def _place_narration(shots, records, owners):
    used = set()
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        images = [str(name) for name in (shot.get("images") or [])]
        for item, _dropped in records:
            upload_id = str(item["id"])
            if upload_id in used or not item.get("narration"):
                continue
            asset_id = owners.get(upload_id)
            if asset_id not in images and upload_id not in images:
                continue
            if not str(shot.get("narration") or "").strip():
                shot["narration"] = item["narration"]
            used.add(upload_id)
    for item, _dropped in records:
        upload_id = str(item["id"])
        if upload_id in used or not item.get("narration"):
            continue
        asset_id = owners.get(upload_id)
        if not asset_id:
            continue
        for shot in shots:
            if not isinstance(shot, dict) or str(shot.get("narration") or "").strip():
                continue
            images = [str(name) for name in (shot.get("images") or [])]
            if asset_id not in images:
                images.insert(0, asset_id)
                shot["images"] = images[:8]
            shot["narration"] = item["narration"]
            used.add(upload_id)
            break
    for item, _dropped in records:
        asset_id = owners.get(str(item["id"]))
        if not asset_id:
            continue
        referenced = False
        for shot in shots:
            if isinstance(shot, dict) and asset_id in [str(name) for name in (shot.get("images") or [])]:
                referenced = True
                break
        if referenced:
            continue
        narration = item.get("narration") or ""
        shots.append(
            {
                "id": _shot_id(shots),
                "duration": _clamp_duration(None, narration),
                "prompt": narration or "按这张资产图演这一镜",
                "images": [asset_id],
                "dialogue": "",
                "narration": narration,
            }
        )


def bind_upload_references(spec, items=None, extra_ids=None):
    """Uploads stay out of the asset list. A generated still references each one."""
    spec = spec if isinstance(spec, dict) else {}
    raw_assets = spec.get("assets")
    assets = [item for item in (raw_assets or []) if isinstance(item, dict)] if isinstance(raw_assets, list) else []
    shots = spec.get("shots")
    if not isinstance(shots, list):
        shots = []
    upload_ids = _collect_upload_ids(assets, shots, items, extra_ids)
    if not upload_ids:
        spec["assets"] = assets
        spec["shots"] = shots
        return spec

    dropped = {}
    kept = []
    for asset in assets:
        asset_id = str(asset.get("id") or "")
        if asset_id in upload_ids:
            dropped[asset_id] = asset
            continue
        kept.append(asset)
    for asset in kept:
        refs = []
        for ref in _reference_ids(asset.get("references")):
            if ref in upload_ids and ref not in refs:
                refs.append(ref)
        parent = str(asset.get("depends_on") or "").strip()
        if parent in upload_ids:
            if parent not in refs:
                refs.append(parent)
            asset["depends_on"] = ""
        asset["references"] = refs

    used = {str(asset.get("id")) for asset in kept}
    used.update(upload_ids)
    for item, gone in _upload_records(upload_ids, items, dropped):
        if _direct_referencer(kept, str(item["id"])):
            continue
        kept.append(_generated_from_upload(item, gone, used))

    owners = {}
    for upload_id in upload_ids:
        asset = _direct_referencer(kept, upload_id)
        if asset:
            owners[upload_id] = asset["id"]
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        images = _shot_names(shot, "images")
        if images is not None:
            shot["images"] = _retarget_names(images, upload_ids, owners)
        end_image = str(shot.get("end_image") or "")
        if end_image in upload_ids:
            target = owners.get(end_image)
            if target:
                shot["end_image"] = target
            else:
                shot.pop("end_image", None)
    _place_narration(shots, _upload_records(upload_ids, items, dropped), owners)
    spec["assets"] = kept
    spec["shots"] = shots
    return spec


def merge_intake(spec, items):
    """Point every upload at a generated asset, and keep its narration on that shot."""
    return bind_upload_references(spec, items)
