"""Image-Node owns every still in a video. Shots only reuse those stills."""

from end.intake import bind_upload_references
from end.soundtrack import soundtrack_enabled
from end.workflow_from_spec import _is_upload_id, _reference_ids, _safe_id, collect_steps

IMAGE_NODE_ID = "image"
VIEWS = ("front", "side", "back")
PRODUCT_VIEWS = ("hero", "three_quarter", "macro")
VEHICLE_TURNAROUND = "vehicle_turnaround"
PRODUCT_HERO = "product_hero"
DRAMA_STILL = "drama_still"
TURNAROUND = ("character", "prop")
VIEW_LABEL = {"front": "正面", "side": "侧面", "back": "背面"}
PRODUCT_LABEL = {"hero": "英雄图", "three_quarter": "四分之三", "macro": "微距"}

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
_VIEW_ALIASES = {
    "front": "front",
    "side": "side",
    "back": "back",
    "single": "single",
    "正面": "front",
    "侧面": "side",
    "背面": "back",
    "单张": "single",
    "hero": "hero",
    "three_quarter": "three_quarter",
    "three-quarter": "three_quarter",
    "macro": "macro",
    "英雄": "hero",
    "英雄图": "hero",
    "四分之三": "three_quarter",
    "微距": "macro",
}


def prepare_image_node(spec, locked_ids=None, items=None, policy=None):
    """Fill the stills this policy requires, then keep every generated still on this spec.

    Upload ids are references on a generated asset. They are not stills.
    No policy means vehicle_turnaround, the same front, side, and back fill as before.
    """
    chosen = policy or VEHICLE_TURNAROUND
    if chosen not in (VEHICLE_TURNAROUND, PRODUCT_HERO, DRAMA_STILL):
        chosen = VEHICLE_TURNAROUND
    spec = bind_upload_references(spec, items, extra_ids=locked_ids)
    locked = {str(item) for item in (locked_ids or []) if item}
    used = set()
    assets = []
    for index, item in enumerate(spec.get("assets") or []):
        if not isinstance(item, dict):
            continue
        raw_id = _safe_id(item.get("id"), "")
        if raw_id in locked or _is_upload_id(raw_id):
            continue
        assets.append(_normalize_asset(item, index, used))
    if chosen == PRODUCT_HERO:
        assets = _expand_product(assets)
    elif chosen == DRAMA_STILL:
        assets = _single_stills(assets)
    else:
        assets = _expand_turnarounds(assets)
    known = {asset["id"] for asset in assets}
    for asset in assets:
        if asset["depends_on"] not in known or asset["depends_on"] == asset["id"]:
            asset["depends_on"] = ""
    spec["assets"] = assets
    _keep_shot_images(spec, {asset["id"] for asset in assets if asset["prompt"]})
    return spec


def video_nodes(spec, soundtrack="off"):
    """One required Image-Node, then one node per shot, plus the other functions."""
    assets, shots = collect_steps(spec or {})
    by_id = {asset["id"]: asset for asset in assets}
    nodes = [
        _function("script", "提示词"),
        _function("storyboard", "分镜"),
        {
            "id": IMAGE_NODE_ID,
            "kind": "image",
            "role": "function",
            "title": "Image-Node",
            "required": True,
            "assets": assets,
        },
    ]
    for shot in shots:
        used = [by_id[image_id] for image_id in shot["images"] if image_id in by_id]
        nodes.append(
            {
                "id": shot["id"],
                "kind": "shot",
                "role": "shot",
                "title": shot["id"],
                "assets": used,
                "images": [asset["id"] for asset in used],
            }
        )
    tail = [_function("join", "拼接")]
    if soundtrack_enabled(soundtrack):
        tail.append(_function("audio", "声轨"))
    tail.extend([_function("subtitles", "字幕"), _function("film", "成片")])
    nodes.extend(tail)
    return nodes


def _function(node_id, title):
    return {
        "id": node_id,
        "kind": node_id,
        "role": "function",
        "title": title,
        "assets": [],
    }


def _normalize_asset(item, index, used):
    asset_id = _take_id(item.get("id"), index, used)
    title = str(item.get("title") or "资产").strip() or "资产"
    prompt = str(item.get("prompt") or "").strip()
    depends_on = _safe_id(item.get("depends_on"), "") if item.get("depends_on") else ""
    references = [ref for ref in _reference_ids(item.get("references")) if ref != asset_id]
    kind = _kind(item, title, prompt)
    view = _view(item.get("view"))
    subject = _subject(item.get("subject"), title)
    if kind not in TURNAROUND:
        view = "single"
    elif view not in VIEWS and view not in PRODUCT_VIEWS:
        view = "single" if depends_on or not prompt else ""
    return {
        "id": asset_id,
        "title": title,
        "prompt": prompt,
        "depends_on": depends_on,
        "references": references,
        "subject": subject,
        "subject_kind": kind,
        "view": view,
    }


def _single_stills(assets):
    """One written prompt stays one still. No hero set and no vehicle views."""
    return [dict(asset, view="single") for asset in assets]


def _expand_turnarounds(assets):
    used = {asset["id"] for asset in assets}
    groups = {}
    for asset in assets:
        if asset["subject_kind"] not in TURNAROUND or asset["view"] == "single" or not asset["prompt"]:
            continue
        groups.setdefault((asset["subject_kind"], asset["subject"]), []).append(asset)
    for (kind, subject), group in groups.items():
        front = next((asset for asset in group if asset["view"] == "front"), None)
        if front is None:
            front = next((asset for asset in group if not asset["depends_on"]), group[0])
            if front["view"] in ("side", "back"):
                front = _add_view(assets, front, "front", used)
            else:
                front["view"] = "front"
        present = {
            asset["view"]
            for asset in assets
            if asset["subject_kind"] == kind and asset["subject"] == subject and asset["view"] in VIEWS
        }
        for view in VIEWS:
            if view not in present:
                _add_view(assets, front, view, used)
        for asset in assets:
            if (
                asset["subject_kind"] == kind
                and asset["subject"] == subject
                and asset["view"] in ("side", "back")
                and not asset["depends_on"]
            ):
                asset["depends_on"] = front["id"]
        _keep_references_on_anchor(assets, front, kind, subject, ("side", "back"))
    return assets


def _expand_product(assets):
    """One product gets hero, three-quarter, and macro. Other props stay as written."""
    picked = _pick_product(assets)
    if picked is None:
        _fill_blank_views(assets)
        return assets
    kind, subject = picked
    used = {asset["id"] for asset in assets}
    slots = _assign_product_views(assets, kind, subject)
    hero = slots.get("hero")
    if hero is None:
        base = slots.get("three_quarter") or slots.get("macro")
        if base is None:
            base = next(
                (
                    asset
                    for asset in assets
                    if asset["subject_kind"] == kind and asset["subject"] == subject and asset["prompt"]
                ),
                None,
            )
        if base is None:
            _fill_blank_views(assets)
            return assets
        hero = _add_product_view(assets, base, "hero", used)
        slots["hero"] = hero
    hero["depends_on"] = ""
    for view in PRODUCT_VIEWS:
        if slots.get(view) is None:
            slots[view] = _add_product_view(assets, hero, view, used)
    macro = slots.get("macro")
    if macro is not None and macro is not hero:
        macro["depends_on"] = hero["id"]
    _share_product_references(assets, kind, subject)
    _fill_blank_views(assets)
    return assets


def _pick_product(assets):
    props = [
        asset
        for asset in assets
        if asset["subject_kind"] == "prop" and asset["prompt"] and not _is_variant(asset)
    ]
    for asset in props:
        if asset["view"] in PRODUCT_VIEWS:
            return asset["subject_kind"], asset["subject"]
    if not props:
        return None
    return props[0]["subject_kind"], props[0]["subject"]


def _is_variant(asset):
    return asset["view"] == "single" and bool(asset["depends_on"])


def _assign_product_views(assets, kind, subject):
    slots = {view: None for view in PRODUCT_VIEWS}
    members = [
        asset
        for asset in assets
        if asset["subject_kind"] == kind
        and asset["subject"] == subject
        and asset["prompt"]
        and not _is_variant(asset)
    ]
    for asset in members:
        if asset["view"] in PRODUCT_VIEWS and slots[asset["view"]] is None:
            slots[asset["view"]] = asset
    remap = {"front": "hero", "side": "three_quarter", "back": "macro", "": "hero", "single": "hero"}
    for asset in members:
        if asset["view"] in PRODUCT_VIEWS:
            continue
        target = remap.get(asset["view"])
        if target and slots[target] is None:
            asset["view"] = target
            slots[target] = asset
            if target == "hero":
                asset["depends_on"] = ""
        elif asset["view"] in ("front", "side", "back", ""):
            asset["view"] = "single"
    return slots


def _fill_blank_views(assets):
    for asset in assets:
        if asset["view"] == "":
            asset["view"] = "single"


def _share_product_references(assets, kind, subject):
    """Hero, three-quarter, and macro are all generated from the same uploads."""
    members = [
        asset
        for asset in assets
        if asset["subject_kind"] == kind
        and asset["subject"] == subject
        and asset["view"] in PRODUCT_VIEWS
    ]
    held = []
    for asset in members:
        for ref in asset.get("references") or []:
            if ref not in held:
                held.append(ref)
    if not held:
        return
    for asset in members:
        asset["references"] = list(held)


def _keep_references_on_anchor(assets, anchor, kind, subject, dependent_views):
    """Dependent views lock to the anchor. The upload feeds the anchor only."""
    held = list(anchor.get("references") or [])
    for asset in assets:
        if asset["subject_kind"] != kind or asset["subject"] != subject:
            continue
        if asset["view"] not in dependent_views:
            continue
        for ref in asset.get("references") or []:
            if ref not in held:
                held.append(ref)
        asset["references"] = []
    anchor["references"] = held


def _add_view(assets, front, view, used):
    created = {
        "id": _take_id("%s-%s" % (front["id"], view), len(assets), used),
        "title": _base_title(front["title"]) + " · " + VIEW_LABEL[view],
        "prompt": _turnaround_prompt(front["subject_kind"], view, front["prompt"]),
        "depends_on": "" if view == "front" else front["id"],
        "references": [],
        "subject": front["subject"],
        "subject_kind": front["subject_kind"],
        "view": view,
    }
    assets.append(created)
    return created


def _add_product_view(assets, hero, view, used):
    created = {
        "id": _take_id("%s-%s" % (hero["id"], view), len(assets), used),
        "title": _base_title(hero["title"]) + " · " + PRODUCT_LABEL[view],
        "prompt": _product_prompt(view, hero["prompt"]),
        "depends_on": hero["id"] if view == "macro" else "",
        "references": [],
        "subject": hero["subject"],
        "subject_kind": hero["subject_kind"],
        "view": view,
    }
    assets.append(created)
    return created


def _product_prompt(view, identity):
    lines = {
        "hero": "单张英雄图，产品居中，看清完整轮廓。不要在同一张图里拼四分之三或微距。",
        "three_quarter": "单张四分之三照片，看清体积和一条侧棱，锁定参考图。不要在同一张图里拼英雄图或微距。",
        "macro": "单张微距，只放大一处要看清的细节，锁定英雄图。不要在同一张图里拼整件产品。",
    }
    return lines[view] + "\n" + identity


def _turnaround_prompt(kind, view, identity):
    who = "同一件道具" if kind == "prop" else "同一个人"
    lines = {
        "front": "单张正面照片，平视，%s。不要在同一张图里拼侧面或背面。" % who,
        "side": "单张严格左侧面照片，%s，锁定参考图。不要在同一张图里拼正面或背面。" % who,
        "back": "单张背面照片，完全背对镜头，%s，锁定参考图。不要在同一张图里拼正面或侧面。" % who,
    }
    return lines[view] + "\n" + identity


def _keep_shot_images(spec, known):
    for shot in spec.get("shots") or []:
        if not isinstance(shot, dict) or shot.get("images") is None:
            continue
        shot["images"] = [
            str(name)
            for name in (shot.get("images") or [])
            if str(name) in known or _safe_id(name, "") in known
        ]


def _kind(item, title, prompt):
    raw = str(item.get("subject_kind") or "").strip().lower()
    if raw in _KIND_ALIASES:
        return _KIND_ALIASES[raw]
    text = "%s\n%s" % (title, prompt)
    if any(word in title for word in ("空镜",)) or any(word in text for word in ("场景概念", "无人物")):
        return "location"
    if any(word in title for word in ("道具", "静物")):
        return "prop"
    if any(word in title for word in ("定妆", "角色", "主角", "男主", "女主")):
        return "character"
    return "other"


def _view(value):
    return _VIEW_ALIASES.get(str(value or "").strip().lower(), "")


def _title_labels():
    return list(VIEW_LABEL.values()) + list(PRODUCT_LABEL.values())


def _subject(value, title):
    text = str(value or "").strip()
    if text:
        return text
    cleaned = title
    for label in _title_labels():
        cleaned = cleaned.replace(" · " + label, "").replace("·" + label, "")
    return cleaned.strip() or title


def _base_title(title):
    cleaned = str(title or "资产")
    for label in _title_labels():
        cleaned = cleaned.replace(" · " + label, "").replace("·" + label, "")
    return cleaned.strip() or "资产"


def _take_id(raw, index, used):
    base = _safe_id(raw, "A%s" % (index + 1))
    if base not in used:
        used.add(base)
        return base
    number = 2
    while True:
        candidate = _safe_id("%s_%s" % (base, number), "A%s" % (index + 1))
        if candidate not in used:
            used.add(candidate)
            return candidate
        number += 1
