"""Images and narrations attached when a project is first started."""

import json
import re
from pathlib import Path

from end.board_shots import PLACEHOLDER_PROMPT, shots_are_placeholder
from end.workflow_from_spec import _clamp_duration, _is_upload_id, _reference_ids

UNREAD = "尚未读图"

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


def clean_reading(text):
    lines = []
    for raw in str(text or "").splitlines():
        line = " ".join(raw.split())
        if line:
            lines.append(line)
    return "\n".join(lines)[:4000]


def material_reading(item):
    return clean_reading((item or {}).get("reading")) or UNREAD


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
        entry = {
            "id": material_id,
            "title": str(item.get("title") or material_id),
            "narration": clean_narration(item.get("narration")),
        }
        reading = clean_reading(item.get("reading"))
        if reading:
            entry["reading"] = reading
        items.append(entry)
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
        line += "\n  素材读图：" + material_reading(item)
        lines.append(line)
    return "\n".join(lines)


KEEP_PERSON = "用户上传的素材，保持这张图里的人和场景。"
KEEP_PRODUCT = "用户上传的产品图。保持包装、外观和画面里的文字。不要把包装画成人物，也不要另编场景里的人。"
KEEP_PLACE = "用户上传的场景图。保持这张图里的地点和空间。不要把场景画成人物。"
_DEFAULT_PROMPTS = (KEEP_PERSON, KEEP_PRODUCT, KEEP_PLACE)


def upload_binding_note(items):
    """Tell the writer which real upload ids exist, and which still should reference them."""
    if not items:
        return ""
    ids = "、".join(str(item.get("id")) for item in items if item.get("id")) or "（无）"
    return (
        "上传图 id 只有：%s。references 只能填这些 id，剧本里也不要点名别的 U 编号。"
        "读图主体是产品、包装、药品、道具或载具时，把这个 id 写进对应道具资产的 references，不要写进角色图，不要另造一张和上传图同名的资产，提示词不要用「保持这张图里的人和场景」。"
        "读图主体是人物时，写进对应角色资产：女性对女主，男性对男主。"
        "没有上传的人物照片不要写成 U0。"
        % ids
    )


def writer_upload_block(items):
    parts = [part for part in (intake_note(items), upload_binding_note(items)) if part]
    return "\n\n".join(parts)


def enrich_brief(brief, items):
    """Tell the writer that uploads are references and narrations stay verbatim.

    Identity follows the still reading. Story follows the user's own words.
    """
    if not items:
        return brief
    lines = [
        brief,
        "",
        "用户已经上传这些图。上传图只是参考，不是资产，也不要写进镜头 images。",
        "每张上传图都要被一张要生成的资产引用：references 填上传图 id，例如 [\"U1\"]。",
        "depends_on 只填另一张要生成的资产 id。侧面和背面依赖生成好的正面，上传图只写在直接参考它的那张资产上。",
        "提示词和分镜只绑定生成出来的资产。每张图的旁白原句写进使用该资产的镜头 narration，不要改字。",
        "没有旁白的图，对应镜头 narration 为空字符串。",
        upload_binding_note(items),
        "",
        "文字决定要拍成什么样：时长、广告、感觉、情节和结尾。用户没写的部分，用读图补上身份和场景。",
        "读图已定的身份不要再问，也不要给出和读图相反的选项。用户原文里的感觉和故事照旧问。",
        "身份跟图。产品种类、外观、画面里的人和实际拍摄的地点，以素材读图为准。海边的汽车不能被说成两轮车，选项里不能出现骑行者、摘头盔这类画面里没有的事。",
        "故事跟用户自己写下的话。时长、广告、感觉、谁在做什么、结尾，以用户原文为准。用户原文里的感觉和故事照旧问。原文没写的，用读图补上身份和场景，不另编一套职业或城市。",
        "「基于这些素材做广告」只是把意图指到图上，不是授权改掉图里的东西。",
        "追问给出的选项是模型的话，不是用户意图。选项不得和读图冲突。用户点选这样的选项，不能当成他把汽车改成了摩托车。只有用户自己打出来的话，写明换掉载具或地点，才算改口。点选模型编的选项不算。",
        "用户明确改口之后，故事世界跟文字，产品外观仍跟图。例如用户写「不要海边，改到清晨的城市」，地点改成城市，车上路的仍是读图里的那辆 SUV，不是另一类车。上传图继续做 references。",
        "图上的旁白只做原句旁白，不负责给主体改名。",
        "读图里的「这张图在建议什么广告」只用来起草第一问，不单独锁死剧情。",
        "素材读图写着「尚未读图」时，不要假装看见了画面里的职业、载具或城市。",
    ]
    for item in items:
        lines.append("- %s %s。旁白：%s" % (item["id"], item["title"], item.get("narration") or "（无）"))
        lines.append("  素材读图：%s" % material_reading(item))
    return "\n".join(lines)


def _asset_prompt(item, dropped=None):
    written = str((dropped or {}).get("prompt") or "").strip()
    kind = _reading_kind(item)
    if written and written not in _DEFAULT_PROMPTS:
        return written
    if item.get("narration"):
        return item["narration"]
    if kind == "prop":
        return KEEP_PRODUCT
    if kind == "location":
        return KEEP_PLACE
    if written:
        return written
    return KEEP_PERSON


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


_PROP_MARKS = (
    "包装",
    "药品",
    "药盒",
    "产品",
    "商品",
    "道具",
    "胶囊",
    "盒子",
    "瓶装",
    "载具",
    "汽车",
    "轿车",
    "摩托",
    "SUV",
    "suv",
)
_PERSON_MARKS = (
    "人物",
    "人像",
    "肖像",
    "女性",
    "男性",
    "女人",
    "男人",
    "女孩",
    "男孩",
    "女生",
    "男生",
    "女主",
    "男主",
    "人脸",
    "半身照",
    "证件照",
)
_PLACE_MARKS = ("空镜", "街景", "风景", "室内", "室外", "客厅", "地点")
_GENERIC_TOKENS = {
    "设定",
    "道具",
    "角色",
    "场景",
    "母版",
    "九宫",
    "宫格",
    "图片",
    "素材",
    "上传",
    "主体",
    "种类",
    "外观",
    "这张",
    "一个",
    "不要",
    "保持",
    "画面",
    "纯净",
    "画幅",
    "参考",
    "资产",
    "生成",
}


def _negated(prefix):
    if re.search(r"(没有|无|不是|未见|不包含|并不).{0,4}$", prefix or ""):
        return True
    return str(prefix or "").endswith("非")


def _clause_has(text, marks):
    raw = str(text or "")
    folded = raw.lower()
    for mark in marks:
        haystack = folded if mark.isascii() else raw
        needle = mark.lower() if mark.isascii() else mark
        start = 0
        while True:
            index = haystack.find(needle, start)
            if index < 0:
                break
            if not _negated(raw[max(0, index - 8) : index]):
                return True
            start = index + len(needle)
    return False


def _subject_clause(reading):
    text = str(reading or "")
    labeled = re.search(r"主体种类[^\n：:]{0,8}[:：]\s*(.*)", text)
    if labeled:
        rest = labeled.group(1).strip().strip("*").strip()
        if not rest:
            for line in text[labeled.end() :].splitlines():
                line = line.strip().strip("*").strip()
                if line:
                    rest = line
                    break
        return re.split(r"\n\s*\n|外观\s*[:：]|场景\s*[:：]", rest)[0].strip()
    named = re.search(r"主体是\s*([^\n。]+)", text)
    if named:
        return named.group(1).strip()
    return ""


def _reading_kind(item):
    """Prop, character, or location from the intake still reading. Title alone is not enough."""
    reading = material_reading(item)
    if not reading or reading == UNREAD:
        return ""
    clause = _subject_clause(reading) or reading
    prop = _clause_has(clause, _PROP_MARKS)
    person = _clause_has(clause, _PERSON_MARKS)
    place = _clause_has(clause, _PLACE_MARKS)
    if prop and person:
        prop_at = min((clause.find(mark) for mark in _PROP_MARKS if mark in clause), default=10**9)
        person_at = min((clause.find(mark) for mark in _PERSON_MARKS if mark in clause), default=10**9)
        return "character" if person_at < prop_at else "prop"
    if prop:
        return "prop"
    if person:
        return "character"
    if place:
        return "location"
    return ""


def _person_side(text):
    raw = re.sub(
        r"(没有|无|不是|未见)(.{0,4})(人物|人像|女性|男性|女人|男人|女孩|男孩|女生|男生)",
        "",
        str(text or ""),
    )
    female = bool(re.search(r"女主|女性|女人|女孩|女生|女子", raw))
    male = bool(re.search(r"男主|男性|男人|男孩|男生|男子", raw))
    if female and not male:
        return "female"
    if male and not female:
        return "male"
    return ""


def _han_tokens(text):
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", str(text or "")))
    found = set()
    for size in (2, 3, 4):
        for index in range(0, len(chars) - size + 1):
            token = chars[index : index + size]
            if token not in _GENERIC_TOKENS:
                found.add(token)
    return found


def _overlap(asset, item):
    blob = "%s\n%s\n%s" % (
        asset.get("title") or "",
        asset.get("subject") or "",
        str(asset.get("prompt") or "")[:240],
    )
    tokens = _han_tokens(item.get("title")) | _han_tokens(_subject_clause(material_reading(item)))
    return len(tokens & _han_tokens(blob))


def _generated_kind(asset):
    kind = _asset_kind(asset.get("subject_kind"))
    if kind and kind != "other":
        return kind
    title = "%s %s" % (asset.get("title") or "", asset.get("subject") or "")
    if any(word in title for word in ("道具", "产品", "包装", "药盒")):
        return "prop"
    if any(word in title for word in ("角色", "女主", "男主", "人物")):
        return "character"
    if any(word in title for word in ("场景", "空镜", "九宫格", "母版")):
        return "location"
    return ""


def _is_upload_standin(asset, item):
    """An A* card minted only to hold an upload, using the default keep-prompt."""
    if not re.fullmatch(r"A\d+", str(asset.get("id") or "")):
        return False
    upload_id = str(item.get("id") or "")
    refs = [str(ref) for ref in (asset.get("references") or [])]
    if refs != [upload_id]:
        return False
    title = str(asset.get("title") or "").strip()
    upload_title = str(item.get("title") or item.get("id") or "").strip()
    if title and upload_title and title != upload_title:
        return False
    prompt = str(asset.get("prompt") or "").strip()
    return (not prompt) or prompt in _DEFAULT_PROMPTS


def _wrong_referencer(referencer, item, match):
    """A real still has the upload, but the reading belongs on a different still."""
    if match is None or match is referencer:
        return False
    kind = _reading_kind(item)
    if not kind:
        return False
    if _generated_kind(referencer) != kind:
        return True
    if kind != "character":
        return False
    side = _person_side(_subject_clause(material_reading(item)) or material_reading(item))
    ref_side = _person_side("%s %s" % (referencer.get("title") or "", referencer.get("subject") or ""))
    return bool(side and ref_side and side != ref_side)


def _match_upload_asset(assets, item):
    """The generated still that should reference this upload. Kind comes from the reading."""
    kind = _reading_kind(item)
    if not kind:
        return None
    candidates = []
    for asset in assets:
        if _is_upload_standin(asset, item):
            continue
        if _generated_kind(asset) != kind:
            continue
        candidates.append(asset)
    if not candidates:
        return None
    if kind == "character":
        side = _person_side(_subject_clause(material_reading(item)) or material_reading(item))
        if side:
            narrowed = [
                asset
                for asset in candidates
                if _person_side("%s %s" % (asset.get("title") or "", asset.get("subject") or "")) == side
            ]
            if len(narrowed) == 1:
                return narrowed[0]
            if not narrowed:
                return None
            candidates = narrowed
        elif len(candidates) == 1:
            return candidates[0]
        else:
            return None
    if len(candidates) == 1:
        if _overlap(candidates[0], item) > 0:
            return candidates[0]
        return None
    ranked = sorted(candidates, key=lambda asset: _overlap(asset, item), reverse=True)
    best = _overlap(ranked[0], item)
    second = _overlap(ranked[1], item)
    if best > second and best > 0:
        return ranked[0]
    return None


def _generated_from_upload(item, dropped, used):
    title = str(item.get("title") or dropped.get("title") or "素材").strip() or "素材"
    inferred = _reading_kind(item)
    kind = inferred or _asset_kind(dropped.get("subject_kind"))
    view = str(dropped.get("view") or "").strip().lower()
    if inferred in ("prop", "location"):
        view = "single"
    elif kind in _TURNAROUND:
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
        if referenced or not shots_are_placeholder(shots):
            continue
        narration = item.get("narration") or ""
        shots.append(
            {
                "id": _shot_id(shots),
                "duration": _clamp_duration(None, narration),
                "prompt": narration or PLACEHOLDER_PROMPT,
                "images": [asset_id],
                "dialogue": "",
                "narration": narration,
            }
        )


def _attach_upload(asset, upload_id):
    refs = []
    for ref in asset.get("references") or []:
        text = str(ref)
        if text and text != str(asset.get("id") or "") and text not in refs:
            refs.append(text)
    if upload_id not in refs:
        refs.append(upload_id)
    asset["references"] = refs


def _replace_name(names, old, new):
    result = []
    for name in names:
        text = new if str(name) == old else str(name)
        if text and text not in result:
            result.append(text)
    return result[:8]


def _drop_standin(kept, shots, standin, replacement_id):
    old = str(standin.get("id") or "")
    if standin in kept:
        kept.remove(standin)
    for asset in kept:
        if str(asset.get("depends_on") or "") == old:
            asset["depends_on"] = replacement_id if replacement_id != asset.get("id") else ""
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        images = shot.get("images")
        if isinstance(images, list):
            shot["images"] = _replace_name(images, old, replacement_id)
        if str(shot.get("end_image") or "") == old:
            shot["end_image"] = replacement_id


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
        upload_id = str(item["id"])
        referencer = _direct_referencer(kept, upload_id)
        match = _match_upload_asset(kept, item)
        if (
            referencer is not None
            and _is_upload_standin(referencer, item)
            and match is not None
            and match is not referencer
        ):
            _attach_upload(match, upload_id)
            _drop_standin(kept, shots, referencer, match["id"])
            continue
        if referencer is not None and _is_upload_standin(referencer, item):
            referencer["prompt"] = _asset_prompt(item, referencer)
            kind = _reading_kind(item)
            if kind:
                referencer["subject_kind"] = kind
                if kind in ("prop", "location"):
                    referencer["view"] = "single"
            continue
        if referencer is not None and _wrong_referencer(referencer, item, match):
            referencer["references"] = [
                str(ref) for ref in (referencer.get("references") or []) if str(ref) != upload_id
            ]
            _attach_upload(match, upload_id)
            for asset in list(kept):
                if asset is not match and _is_upload_standin(asset, item):
                    _drop_standin(kept, shots, asset, match["id"])
            continue
        if referencer is not None:
            for asset in list(kept):
                if asset is not referencer and _is_upload_standin(asset, item):
                    _drop_standin(kept, shots, asset, referencer["id"])
            continue
        if match is not None:
            _attach_upload(match, upload_id)
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
