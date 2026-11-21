"""Program-owned asset skeleton extracted from the official script."""

import re

from Agents.workers.script_skeleton import parse_script_scenes

_CAST_LINE = re.compile(r"^出场\s*[:：]\s*(.*)$")
_PROP_LINE = re.compile(r"^道具\s*[:：]\s*(.*)$")
_SKIP_PROP = re.compile(r"^(无|没有|无道具)(?:[（(][^）)]*[）)])?$")
_PROP_MARK = re.compile(
    r"(凝胶|药膏|药盒|药品|药管|支装|包装|瓶子?|药|盒|管|产品|枪)"
)
_INT_EXT = re.compile(r"^(?:内|外)[·・.]")
_PLACE_TAIL = re.compile(r"(?:[·・.]?(?:日|夜|晨|晚|黄昏|白天|晚上|无背景))+$")
_ASSET_HEAD = re.compile(r"^(#{2,6})\s*\*{0,2}\s*(图\s*\d+)\s*(?:·|•|-|—)?\s*(.*)$")
_SCENE_NAME_LINE = re.compile(r"场景名称\s*[:：]\s*(.+)$")
_SCENE_NAME_VALUE = re.compile(r"^(.+?)（资产图编号：(图\s*\d+)）\s*$")
_KIND_SUFFIX = {
    "角色": "角色",
    "道具": "道具",
    "场景母版": "母版",
    "九宫格": "九宫格",
}
_SCENE_KINDS = ("场景母版", "九宫格")


def asset_skeleton(state):
    """Global 图N identifiers and grid slots, before the model writes."""
    state = state or {}
    script = str(state.get("script") or state.get("source") or "").strip()
    assets = extract_asset_catalog(script)
    return {"assets": assets, "script": script}


def format_asset_skeleton(skeleton):
    """Read-only contract text prepended to first-write and repair prompts."""
    skeleton = skeleton or {}
    assets = list(skeleton.get("assets") or [])
    script = str(skeleton.get("script") or "")
    if not assets:
        if not _has_cast(script):
            return "剧本缺出场，不能生成资产骨架。不要按场次地点另建一份只有场景的资产板。\n"
        return ""
    lines = [
        "当前资产骨架如下。图号和格位是锁定槽，只读。",
        "只填各格的外形、服装或场景描述。不要自增图号，不要改格位，不要另开一份完整资产板再合并。",
        "主体编号和图号全局唯一，不要按单元或场次重新编号。",
        "标题、种类、格位、场景名称和参考场景锁定模式是只读锁定项。",
    ]
    for item in assets:
        lines.append("")
        lines.append(_locked_heading(item))
        lines.extend(_locked_lines(item))
        for slot in item.get("slots") or []:
            lines.append("%s：【填写】" % slot)
    return "\n".join(lines).strip() + "\n"


def extract_asset_catalog(script):
    """Characters, props, and scenes from 出场 and confirmed headings, numbered globally."""
    if not _has_cast(script):
        return []
    characters, props = _cast_items(script)
    scenes = _scene_places(script)
    assets = []
    index = 1
    for name in characters:
        assets.append(_card(index, name, "角色", "四格", 4))
        index += 1
    for name in props:
        assets.append(_card(index, name, "道具", "四格", 4))
        index += 1
    for place in scenes:
        assets.append(_card(index, place, "场景母版", "整板", 0))
        index += 1
        assets.append(_card(index, place, "九宫格", "九宫格", 9))
        index += 1
    return assets


def _card(index, title, kind, grid, slot_count):
    if slot_count <= 0:
        slots = ["整板"]
    else:
        slots = ["格%s" % number for number in range(1, slot_count + 1)]
    return {
        "id": "图%s" % index,
        "title": title,
        "kind": kind,
        "grid": grid,
        "slots": slots,
    }


def _has_cast(script):
    characters, props = _cast_items(script)
    return bool(characters or props)


def _cast_items(script):
    characters = []
    props = []
    seen = set()
    for line in str(script or "").splitlines():
        stripped = line.strip()
        match = _CAST_LINE.match(stripped)
        prop_only = _PROP_LINE.match(stripped) if not match else None
        if not match and not prop_only:
            continue
        raw = (match or prop_only).group(1)
        for name in re.split(r"[、,，;；/]+", raw):
            name = name.strip()
            if not name or name == "【填写】" or _SKIP_PROP.match(name) or name in seen:
                continue
            seen.add(name)
            if prop_only or _PROP_MARK.search(name):
                props.append(name)
            else:
                characters.append(name)
    return characters, props


def _scene_places(script):
    found = []
    seen = set()
    for scene in parse_script_scenes(script):
        place = _place_name(scene.get("heading") or "")
        if not place or place in seen:
            continue
        seen.add(place)
        found.append(place)
    return found


def _place_name(heading):
    text = str(heading or "")
    if "｜" in text:
        text = text.split("｜", 1)[1]
    elif "|" in text:
        text = text.split("|", 1)[1]
    text = re.sub(r"[（(][^）)]*[)）]", "", text).strip()
    text = _INT_EXT.sub("", text).strip()
    text = _PLACE_TAIL.sub("", text).strip(" ·・.")
    return text


def _locked_heading(item):
    suffix = _KIND_SUFFIX.get(item["kind"], item["kind"])
    return "#### %s · %s（%s）" % (item["id"], item["title"], suffix)


def _locked_lines(item):
    lines = ["种类：%s" % item["kind"], "格位：%s" % item["grid"]]
    if item["kind"] in _SCENE_KINDS:
        lines.append("场景名称：%s（资产图编号：%s）" % (item["title"], item["id"]))
        lines.append("整板固定 16:9。")
    if item["kind"] == "九宫格":
        lines.append("全图共 9 格，不多不少。")
        lines.append("【参考场景锁定模式】仅允许摄影机移动。")
    return lines


def _is_identifier_line(line):
    stripped = line.strip()
    if stripped.startswith("种类：") or stripped.startswith("格位："):
        return True
    if _SCENE_NAME_LINE.match(stripped):
        return True
    if stripped.startswith("整板固定 16:9"):
        return True
    if re.search(r"全图共\s*9\s*格", stripped) and "格1" not in stripped:
        return True
    if stripped.startswith("【参考场景锁定模式】"):
        return True
    return False


def restore_asset_locks(text, skeleton):
    """Put locked 图N headings and program-owned lines back. Keep existing fill."""
    assets = list((skeleton or {}).get("assets") or [])
    pieces = _asset_pieces(text)
    by_id = {asset_id: body for asset_id, body in pieces}
    blocks = []
    seen = set()
    for item in assets:
        asset_id = item["id"]
        seen.add(asset_id)
        blocks.append(_restore_block(item, by_id.get(asset_id, "")))
    for asset_id, body in pieces:
        if asset_id in seen:
            continue
        blocks.append(body.strip() or "#### %s" % asset_id)
    return "\n\n".join(block.strip() for block in blocks if block.strip()) + ("\n" if blocks else "")


def asset_skeleton_failures(text, skeleton):
    """Fail when 图 numbers, 场景名称, or leftover fill diverge from the lock."""
    skeleton = skeleton or {}
    script = str(skeleton.get("script") or "")
    assets = list(skeleton.get("assets") or [])
    errors = []
    if not _has_cast(script):
        errors.append("剧本缺出场，不能生成资产骨架")
        return errors
    expected = [item["id"] for item in assets]
    written = [asset_id for asset_id, _body in _asset_pieces(text)]
    by_id = {item["id"]: item for item in assets}
    for asset_id, body in _asset_pieces(text):
        item = by_id.get(asset_id)
        heading = body.splitlines()[0] if body else ""
        if item:
            locked = _locked_heading(item)
            if _ASSET_HEAD.match(heading.strip()):
                if heading.strip().lstrip("#").strip() != locked.lstrip("#").strip():
                    got = heading.split("·", 1)[1].strip() if "·" in heading else heading
                    title = "%s（%s）" % (item["title"], _KIND_SUFFIX.get(item["kind"], item["kind"]))
                    if got != title and not got.startswith(item["title"]):
                        errors.append("图号锁被改写：%s 应是 %s" % (asset_id, item["title"]))
            mismatch = _scene_name_mismatch(body, item)
            if mismatch:
                errors.append(mismatch)
    for asset_id in expected:
        if asset_id not in written:
            errors.append("锁定图号缺失：%s" % asset_id)
    for asset_id in written:
        if asset_id not in expected:
            errors.append("自增图号：%s 不在骨架里" % asset_id)
    if "【填写】" in str(text or ""):
        errors.append("填写槽仍是【填写】，资产稿未填完")
    return errors


def _scene_name_mismatch(body, item):
    if item.get("kind") not in _SCENE_KINDS:
        return None
    for line in str(body or "").splitlines():
        match = _SCENE_NAME_LINE.match(line.strip())
        if not match:
            continue
        raw = match.group(1).strip()
        parsed = _SCENE_NAME_VALUE.match(raw)
        if not parsed:
            return "场景名称或资产图编号与锁定不一致：%s 正文是 %s，锁定为 %s（资产图编号：%s）" % (
                item["id"],
                raw,
                item["title"],
                item["id"],
            )
        place = parsed.group(1).strip()
        figure = _canonical_id(parsed.group(2))
        if place != item["title"] or figure != item["id"]:
            return "场景名称或资产图编号与锁定不一致：%s 正文是 %s，锁定为 %s（资产图编号：%s）" % (
                item["id"],
                raw,
                item["title"],
                item["id"],
            )
        return None
    return None


def _canonical_id(raw):
    match = re.search(r"(\d+)", str(raw or ""))
    if not match:
        return str(raw or "").strip()
    return "图%s" % int(match.group(1))


def _asset_pieces(text):
    found = []
    current = None
    buf = []
    for line in str(text or "").splitlines():
        match = _ASSET_HEAD.match(line.strip())
        if match:
            if current is not None:
                found.append((current, "\n".join(buf).strip()))
            current = _canonical_id(match.group(2))
            buf = [line]
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        found.append((current, "\n".join(buf).strip()))
    return found


def _restore_block(item, body):
    heading = _locked_heading(item)
    lines = str(body or "").splitlines()
    if lines and _ASSET_HEAD.match(lines[0].strip()):
        lines = lines[1:]
    drop_fill = bool(_scene_name_mismatch("\n".join(lines), item))
    body_lines = []
    for line in lines:
        if _is_identifier_line(line):
            continue
        body_lines.append(line)
    has_fill = any(line.strip() for line in body_lines)
    prefix = _locked_lines(item)
    if drop_fill or not has_fill:
        for slot in item.get("slots") or []:
            prefix.append("%s：【填写】" % slot)
        return "\n".join([heading] + prefix).strip()
    return "\n".join([heading] + prefix + body_lines).strip()

_SKELETON_MARK = "当前资产骨架如下。"
_KIND_LINE = re.compile(r"^种类：(.+)$")
_FILL_KINDS_BY_FILE = {
    "character-portrait-templates.md": ("角色",),
    "prop-template.md": ("道具",),
    "scene-grid-template.md": ("场景母版", "九宫格"),
}


def slice_asset_brief(brief, relatives):
    """Keep only the skeleton slots this specialist cohort should fill."""
    brief = str(brief or "")
    mark = brief.find(_SKELETON_MARK)
    if mark < 0:
        return brief
    mode, kinds = _slice_mode(relatives)
    prefix = brief[:mark].rstrip()
    skeleton = brief[mark:]
    if mode == "unchanged":
        filtered = skeleton
    else:
        filtered = _filter_skeleton(skeleton, kinds, strip_fill=(mode != "fill"), mode=mode)
    if not prefix:
        return filtered
    return "%s\n\n%s" % (prefix, filtered)


def _fill_kinds_for(relatives):
    kinds = []
    seen = set()
    for relative in relatives or []:
        name = str(relative).rsplit("/", 1)[-1]
        for kind in _FILL_KINDS_BY_FILE.get(name, ()):
            if kind not in seen:
                seen.add(kind)
                kinds.append(kind)
    return kinds


def _slice_mode(relatives):
    kinds = _fill_kinds_for(relatives)
    if kinds:
        return "fill", kinds
    names = []
    for relative in relatives or []:
        path = str(relative)
        name = path.rsplit("/", 1)[-1]
        names.append((path, name))
    if any(
        name == "three-list-extraction.md"
        or (name == "SKILL.md" and "sd-asset-engine" in path)
        for path, name in names
    ):
        return "catalog", None
    if any(name in ("asset-spatial-ledger.md", "asset-delivery-modes.md") for _path, name in names):
        return "spatial", ("场景母版", "九宫格")
    return "unchanged", None


def _filter_skeleton(skeleton, kinds, strip_fill=False, mode="fill"):
    header, blocks = _split_skeleton(skeleton)
    if kinds is not None:
        blocks = [block for block in blocks if _block_kind(block) in kinds]
    if strip_fill:
        blocks = [_strip_fill_lines(block) for block in blocks]
        header = _review_header(header, mode)
    elif mode == "fill":
        header = _fill_header(header)
    text = header.rstrip()
    if blocks:
        text = "%s\n\n%s" % (text, "\n\n".join(blocks))
    return text.strip() + "\n"


def _strip_fill_lines(block):
    lines = [line for line in str(block or "").splitlines() if "【填写】" not in line]
    return "\n".join(lines).strip()


def _review_header(header, mode):
    lines = []
    for line in str(header or "").splitlines():
        if line.startswith("只填各格"):
            if mode == "catalog":
                lines.append("只对照图号抽出角色、道具、场景名单。不要填写各格外形，不要另开一份完整资产板。")
            else:
                lines.append("只写场景连续性和交付形态。不要填写各格外形，不要另开一份完整资产板。")
            continue
        lines.append(line)
    return "\n".join(lines).strip()



def _fill_header(header):
    lines = list(str(header or "").splitlines())
    out = []
    added = False
    for line in lines:
        out.append(line)
        if line.startswith("只填各格") and not added:
            out.append("本路只填下列图，不要写未列出的图。")
            added = True
    if not added:
        out.append("本路只填下列图，不要写未列出的图。")
    return "\n".join(out).strip()

def _split_skeleton(skeleton):
    header = []
    blocks = []
    current = []
    started = False
    for line in str(skeleton or "").splitlines():
        if _ASSET_HEAD.match(line.strip()):
            if current:
                blocks.append("\n".join(current).strip())
            current = [line]
            started = True
            continue
        if started:
            current.append(line)
        else:
            header.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return "\n".join(header).strip(), blocks


def _block_kind(block):
    for line in str(block or "").splitlines():
        match = _KIND_LINE.match(line.strip())
        if match:
            return match.group(1).strip()
    first = str(block or "").splitlines()[0] if block else ""
    heading = first.strip()
    for kind, suffix in _KIND_SUFFIX.items():
        if heading.endswith("（%s）" % suffix):
            return kind
    return ""
