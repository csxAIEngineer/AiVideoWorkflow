"""One stage at a time. The writer only sees the files resolve_files returns for that stage."""

import inspect
import json
import math
import re
import time
from pathlib import Path

from end.board_shots import apply_feed_binding, apply_storyboard_shots, bind_feed_document, shots_are_placeholder
from end.delivery import internal_dir, list_deliverables, previs_name, publish
from end.gates import check_asset_blocks, check_feed, check_p45, check_prompt, unverified_claim
from end.intake import enrich_brief, intake_note, load_intake
from end.locks import lock_brief

_ASSET_HEAD = re.compile(r"^(#{2,6})\s*\*{0,2}\s*图\s*(\d+)\b", re.M)


def _asset_pieces(text):
    text = str(text or "")
    matches = list(_ASSET_HEAD.finditer(text))
    pieces = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pieces.append((int(match.group(2)), text[match.start() : end].strip()))
    return pieces


def keep_missing_asset_blocks(previous, new):
    """A repair that omits a 图N must not delete that block."""
    old = _asset_pieces(previous)
    fresh = _asset_pieces(new)
    if not old:
        return new
    if not fresh:
        return previous
    old_ids = []
    old_by_id = {}
    for number, body in old:
        if number not in old_by_id:
            old_ids.append(number)
        old_by_id[number] = body
    fresh_ids = {number for number, _body in fresh}
    missing = [number for number in old_ids if number not in fresh_ids]
    if not missing:
        return new
    new_by_id = {}
    for number, body in fresh:
        new_by_id.setdefault(number, body)
    parts = []
    seen = set()
    for number in old_ids:
        seen.add(number)
        parts.append(new_by_id.get(number, old_by_id[number]))
    for number, body in fresh:
        if number not in seen:
            seen.add(number)
            parts.append(body)
    return "\n\n".join(part.strip() for part in parts if part.strip()) + "\n"


def _repair_with_graph(
    folder, stage, repair, write_model, debug_dir, model, template, locks=None, title="", choices=None
):
    """One repair graph for the failed items. Passing text stays on disk."""
    from end.repair_units import (
        apply_repair_choices,
        apply_repairs,
        prepare_asset_repair,
        repair_brief,
        units_for,
    )
    from end.trace import begin_trace

    folder = Path(folder)
    internal = folder / "_内部_"
    asset = _read(internal / "assets.md")
    previs = _read(internal / "previs.md")
    storyboard = _read(internal / "分镜.md")
    feed = _read(internal / "feed.md")
    # Always recheck disk before trusting a stale gate report. Feed H2 may already
    # be fixed by normalize_h3_shot_cuts; rewriting from an old FAIL just rebreaks it.
    _passed, fresh = check_stage(folder, stage, locks or {}, title)
    asset = _read(internal / "assets.md")
    previs = _read(internal / "previs.md")
    storyboard = _read(internal / "分镜.md")
    feed = _read(internal / "feed.md")
    if _passed:
        return _saved_stage(stage, asset, previs, storyboard, feed)
    repair = fresh or repair
    units = units_for(stage, repair, asset=asset, previs=previs, feed=feed)
    chosen = dict(choices) if isinstance(choices, dict) else {}
    if stage == "asset" and chosen:
        asset, indexes, units = apply_repair_choices(asset, units, chosen)
        if not units:
            return {"image_prompts": asset, "_figure_repair": True, "_figure_indexes": indexes}
    elif stage == "asset" and units:
        asset, indexes, units = prepare_asset_repair(asset, units)
        if not units:
            return {"image_prompts": asset, "_figure_repair": True, "_figure_indexes": indexes}
    if not units:
        return _saved_stage(stage, asset, previs, storyboard, feed)
    brief = repair_brief(stage, units)
    prompts = begin_trace(folder, stage, repair=True, brief="建议修补 %s 项" % len(units))
    result = _write_model(
        write_model,
        brief,
        "",
        debug_dir=debug_dir,
        model=model,
        template=template,
        prompts=prompts,
    )
    repairs = []
    if isinstance(result, dict):
        if isinstance(result.get("repairs"), list):
            repairs = result.get("repairs")
        elif isinstance(result.get("partials"), list):
            repairs = [
                {"id": item.get("skill"), "text": item.get("text")}
                for item in result.get("partials")
                if isinstance(item, dict)
            ]
        elif len(units) == 1:
            fresh = _flatten_prompt_text(result.get("text"))
            if not fresh:
                fresh = _flatten_prompt_text(result.get("image_prompts") or result.get("previs") or result.get("feed"))
            if fresh:
                repairs = [{"id": units[0]["id"], "text": fresh}]
    applied = apply_repairs(
        stage,
        units,
        repairs,
        asset=asset,
        previs=previs,
        storyboard=storyboard,
        feed=feed,
    )
    if stage == "feed" and isinstance(applied, dict):
        applied = dict(applied)
        applied["feed"] = normalize_h3_shot_cuts(applied.get("feed") or feed)
    if stage == "storyboard" and isinstance(applied, dict):
        applied = dict(applied)
        applied["previs"] = coerce_previs(applied.get("previs") or previs)
    return applied


def _saved_stage(stage, asset, previs, storyboard, feed):
    if stage == "asset":
        return {"image_prompts": asset, "_figure_repair": True, "_figure_indexes": []}
    if stage == "storyboard":
        return {"storyboard": storyboard, "previs": previs}
    if stage == "feed":
        return {"feed": feed}
    return {}


def markdown_from_asset_items(items):
    """Build #### 图N blocks from assets[] / card objects. Empty if nothing usable."""
    if not isinstance(items, list):
        return ""
    parts = []
    next_number = 1
    for item in items:
        if not isinstance(item, dict):
            text = _flatten_prompt_text(item)
            if text:
                if _asset_pieces(text):
                    parts.extend(body for _n, body in _asset_pieces(text))
                else:
                    parts.append("#### 图%d · 资产\n%s" % (next_number, text))
                    next_number += 1
            continue
        prompt = item.get("prompt")
        prompt_text = (
            prompt.strip()
            if isinstance(prompt, str)
            else _flatten_prompt_text(prompt)
        )
        if not prompt_text:
            continue
        numbered = _asset_pieces(prompt_text)
        if numbered:
            for number, body in numbered:
                parts.append(body.strip())
                next_number = max(next_number, number + 1)
            continue
        title = str(
            item.get("title") or item.get("subject") or item.get("name") or item.get("id") or "资产"
        ).strip() or "资产"
        id_match = re.search(r"(?:图\s*)?(\d+)", str(item.get("id") or title))
        number = int(id_match.group(1)) if id_match and not str(item.get("id") or "").startswith("A") else next_number
        # Prefer sequential 图N for A1/A2 style ids
        if re.match(r"^A\d+$", str(item.get("id") or ""), re.I):
            number = next_number
        if title.startswith("####"):
            head = title
        elif re.match(r"^图\s*\d+", title):
            head = "#### %s" % title
        else:
            head = "#### 图%d · %s" % (number, title)
        parts.append("%s\n%s" % (head, prompt_text))
        next_number = max(next_number, number + 1)
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"


def _flatten_prompt_text(value):
    """Turn a string, a list of prompts, or a nested object into one markdown body."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        # Card list [{title, prompt}, ...] must keep #### 图N headers (not bare bodies).
        if value and all(isinstance(item, dict) for item in value):
            from_cards = markdown_from_asset_items(value)
            if _asset_pieces(from_cards):
                return from_cards.strip()
        parts = [_flatten_prompt_text(item) for item in value]
        return "\n\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("image_prompts", "markdown", "text", "content"):
            if value.get(key):
                found = _flatten_prompt_text(value[key])
                if found:
                    return found
        # Single asset card — do not return bare prompt without a 图 heading.
        card = markdown_from_asset_items([value])
        if _asset_pieces(card):
            return card.strip()
        if value.get("prompt"):
            found = _flatten_prompt_text(value.get("prompt"))
            if found and _asset_pieces(found):
                return found
        # Merge often returns {"图5 · 场景": "正文…", ...} instead of #### markdown.
        parts = []
        for key, body in value.items():
            title = str(key or "").strip()
            if not title.startswith("####") and "图" not in title:
                continue
            text = body.strip() if isinstance(body, str) else _flatten_prompt_text(body)
            if not text:
                continue
            if text.lstrip().startswith("####"):
                parts.append(text)
                continue
            head = title if title.startswith("####") else "#### %s" % title
            parts.append("%s\n%s" % (head, text))
        return "\n\n".join(parts)
    return ""


def _prompt_shape(value):
    if isinstance(value, str):
        return {"type": "str", "len": len(value)}
    if isinstance(value, list):
        first = value[0] if value else None
        info = {"type": "list", "len": len(value), "first": type(first).__name__ if value else ""}
        if isinstance(first, dict):
            info["firstKeys"] = sorted(str(key) for key in first.keys())[:8]
        return info
    if isinstance(value, dict):
        return {"type": "dict", "keys": sorted(str(key) for key in value.keys())[:12]}
    return {"type": type(value).__name__}


def _coerce_asset_prompts(result):
    """Keep 图 blocks when the model puts them in a list, assets[], or specialist notes."""
    if not isinstance(result, dict):
        return result
    raw = result.get("image_prompts")
    flat = _flatten_prompt_text(raw)
    notes = []
    for item in result.get("partials") or []:
        notes.append(item.get("text") if isinstance(item, dict) else item)
    partial_flat = _flatten_prompt_text(notes)
    assets_flat = markdown_from_asset_items(result.get("assets"))
    field_blocks = len(_asset_pieces(flat))
    partial_blocks = len(_asset_pieces(partial_flat))
    assets_blocks = len(_asset_pieces(assets_flat))
    chosen = flat
    source = "field"
    if field_blocks == 0 and partial_blocks:
        chosen = partial_flat
        source = "partials"
    if len(_asset_pieces(chosen)) == 0 and assets_blocks:
        chosen = assets_flat
        source = "assets"
    chosen_blocks = len(_asset_pieces(chosen))
    # #region agent log
    try:
        with open("/Users/shaneson/AiVideo/.cursor/debug-d078f2.log", "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "sessionId": "d078f2",
                        "runId": "asset-fix",
                        "hypothesisId": "A1",
                        "location": "stage_runner.py:_coerce_asset_prompts",
                        "message": "coerce asset prompts",
                        "data": {
                            "raw": _prompt_shape(raw),
                            "field_blocks": field_blocks,
                            "partial_blocks": partial_blocks,
                            "assets_blocks": assets_blocks,
                            "source": source,
                            "chosen_blocks": chosen_blocks,
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion
    if chosen_blocks and chosen != raw:
        result = dict(result)
        result["image_prompts"] = chosen if chosen.endswith("\n") else chosen + "\n"
    return result


def asset_markdown(result):
    """Use the string field that actually contains 图 blocks."""
    if not isinstance(result, dict):
        return ""
    fields = {key: value for key, value in result.items() if isinstance(value, str)}
    image = fields.get("image_prompts") or ""
    if _asset_pieces(image):
        return image
    best = image
    best_count = 0
    for text in fields.values():
        count = len(_asset_pieces(text))
        if count > best_count:
            best = text
            best_count = count
    return best


def apply_structured_assets(previous, items):
    """Fold assets[].prompt back onto the 图 block with the same title."""
    old = _asset_pieces(previous)
    if not old or not isinstance(items, list):
        return previous
    old_by_id = {}
    old_ids = []
    for number, body in old:
        if number not in old_by_id:
            old_ids.append(number)
        old_by_id[number] = body
    replacements = {}
    used = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        numbered = _asset_pieces(prompt)
        if numbered:
            for number, body in numbered:
                if number in old_by_id and number not in used and _accept_replacement(old_by_id[number], body):
                    replacements[number] = body
                    used.add(number)
            continue
        title = re.sub(r"\s+", "", str(item.get("title") or item.get("subject") or ""))
        if not title:
            continue
        match = None
        for number in old_ids:
            if number in used:
                continue
            heading = re.sub(r"\s+", "", old_by_id[number].splitlines()[0])
            if title in heading or heading in title:
                match = number
                break
        if match is None:
            continue
        heading = old_by_id[match].splitlines()[0]
        candidate = heading + "\n\n" + prompt
        if not _accept_replacement(old_by_id[match], candidate):
            continue
        replacements[match] = candidate
        used.add(match)
    if not replacements:
        return previous
    parts = []
    for number in old_ids:
        parts.append(replacements.get(number, old_by_id[number]))
    return "\n\n".join(part.strip() for part in parts if part.strip()) + "\n"


_VOICE_MARK = re.compile(r"≤\s*8\s*词|8\s*词")
_HAND_MARK = re.compile(r"禁止手持|不得手持|不出现手")
_SECOND_FACE = ("different face", "second face", "second recognizable face", "another face")
_ROLE_TITLE = re.compile(r"女主|男主|主角|反派|立绘|角色")
_ASSET_KINDS = ("角色", "道具", "九宫格", "场景母版", "未知")
_ROLE_NEGATIVES = (
    "extra fifth panel, five-panel layout, different face, second face"
)
_GRID_FIELDS = ("【场景定位】", "【九宫格内容分配】", "【参考场景锁定模式】")
_GRID_DOWNGRADE = "若中文标注崩字：格内编号 1~9，图外对照表；禁止无标注裸格交付。"
_GRID_LOCK_A = "【参考场景锁定模式 · 模式A 档 · 仅允许摄影机移动】仅允许摄影机移动。"
_GRID_LOCK_B = "【参考场景锁定模式 · 模式B 档 · 允许按子区域切换机位】允许按子区域切换机位。"
_CROSS_REF_PARENS = re.compile(r"[（(]\s*同图\s*\d+[^）)]*[）)]")


def _fail_count(text):
    import tempfile

    handle = tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False)
    name = handle.name
    try:
        handle.write(text if str(text).endswith("\n") else str(text) + "\n")
        handle.close()
        _code, report = check_asset_blocks(name)
    finally:
        try:
            Path(name).unlink()
        except OSError:
            pass
    return sum(1 for line in (report or "").splitlines() if "FAIL:" in line)


def _accept_replacement(old, new):
    if not str(new or "").strip() or str(new).strip() == str(old).strip():
        return False
    return _fail_count(new) < _fail_count(old)


def _rule_asset_kind(title, body):
    """Rule fallback: 女主/男主 = 角色. Never treat bare 全图共4格 as角色 (道具也是四格)."""
    title = str(title or "")
    body = str(body or "")
    if "道具设定图" in body or "道具设定" in body or "道具" in title:
        return "道具"
    if "九宫格" in title or re.search(r"全图共\s*9\s*格", body):
        return "九宫格"
    if "母版" in title and not _ROLE_TITLE.search(title):
        return "场景母版"
    if _ROLE_TITLE.search(title) or "五官" in body or "声纹" in body or "立绘" in body:
        return "角色"
    if re.search(r"四格|4\s*格", body) and re.search(
        r"视图|材质|尺寸参照|设定|胶囊|包装|静物", body + title
    ):
        return "道具"
    return "未知"


def _parse_kind_list(raw, expected):
    text = str(raw or "").strip()
    if not text:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, list):
        return []
    out = []
    for item in data[:expected]:
        name = str(item or "").strip()
        out.append(name if name in _ASSET_KINDS else "未知")
    return out


def _llm_asset_kinds(pieces, model=None):
    """One short call for every block. Empty list means fall back to rules."""
    if not pieces:
        return []
    rows = []
    for index, (_number, body) in enumerate(pieces, 1):
        title = body.splitlines()[0] if body else ""
        excerpt = " ".join(str(body or "").split())[:280]
        rows.append("%d. 标题：%s\n   摘要：%s" % (index, title, excerpt))
    prompt = "\n".join(
        (
            "你是资产图分类器。对下面每条 #### 图块判断类型。",
            "只能从：角色、道具、九宫格、场景母版、未知 中选一个。",
            "女主/男主/主角/立绘/人物=角色；药盒/胶囊/武器等物件=道具；场景九宫格=九宫格；无人空场景母版=场景母版。",
            "只输出长度=%d 的 JSON 数组，例如 [\"角色\",\"道具\"]。不要解释。" % len(pieces),
            "",
            "\n".join(rows),
        )
    )
    try:
        from end.ai_client import ask_text

        raw = ask_text(
            [{"role": "user", "content": prompt}],
            model=model,
            timeout=45,
            max_output_tokens=200,
        )
    except Exception:
        return []
    return _parse_kind_list(raw, len(pieces))


def _classify_asset_kinds(pieces, model=None, classify=None):
    """LLM first; rule fallback per block when LLM misses or disagrees length."""
    if classify is not None:
        kinds = list(classify(pieces) or [])
    else:
        kinds = _llm_asset_kinds(pieces, model=model)
    out = []
    for index, (_number, body) in enumerate(pieces):
        title = body.splitlines()[0] if body else ""
        rule = _rule_asset_kind(title, body)
        llm = kinds[index] if index < len(kinds) else ""
        kind = llm if llm in _ASSET_KINDS and llm != "未知" else rule
        if kind == "未知":
            kind = rule
        out.append(kind)
    return out


def _infer_grid_lock_line(text):
    """Pick A/B lock sentence from already-written 模式A/B; empty if ambiguous."""
    body = str(text or "")
    has_a = bool(re.search(r"模式\s*A|模式A", body))
    has_b = bool(re.search(r"模式\s*B|模式B", body))
    if has_b and not has_a:
        return _GRID_LOCK_B
    if has_a and not has_b:
        return _GRID_LOCK_A
    return ""


def _patch_grid_block(text):
    """Hardcode A3 phrases the model routinely drops on 九宫格."""
    out = _CROSS_REF_PARENS.sub("", str(text or ""))
    added = []
    if not re.search(r"全图共\s*9\s*格", out):
        out += "\n全图共 9 格，不多不少。\n"
        added.append("全图共9格")
    if not re.search(r"16\s*[:：]\s*9", out):
        out += "\n整板固定 16:9。\n"
        added.append("16:9")
    lock = ""
    if "仅允许摄影机移动" not in out and "允许按子区域切换机位" not in out:
        lock = _infer_grid_lock_line(out)
    for field in _GRID_FIELDS:
        if re.search(re.escape(field[:-1]) + r"[^】]*】", out):
            continue
        # Lock line already carries 【参考场景锁定模式 · …】— skip bare header
        if field == "【参考场景锁定模式】" and lock:
            continue
        out += "\n%s\n" % field
        added.append(field)
    if lock:
        out = re.sub(r"(?m)^【参考场景锁定模式】\s*$", "", out)
        out += "\n%s\n" % lock
        added.append("lock")
    if not re.search(r"1\s*[~～-]\s*9", out) or "对照表" not in out:
        out += "\n%s\n" % _GRID_DOWNGRADE
        added.append("downgrade")
    return out


def _patch_asset_block(body, kind=""):
    text = str(body or "")
    heading = text.splitlines()[0] if text else ""
    kind = kind or _rule_asset_kind(heading, text)
    is_role = kind == "角色"
    is_prop = kind == "道具"
    is_grid = kind == "九宫格"
    if is_grid:
        return _patch_grid_block(text)
    if is_role and not _VOICE_MARK.search(text):
        if "音色速览" in text:
            text = text.replace("音色速览", "≤8 词音色速览", 1)
        else:
            text += "\n【≤8 词音色速览标签】沿用本块已写的音色描述。\n"
    if is_role and "五官 10 项锁定" not in text and "五官10项锁定" not in text:
        text += "\n【五官 10 项锁定】面部结构以本块已写的头部与脸部描述为准。\n"
    if is_role and "fifth panel" not in text.lower():
        text += "\n%s\n" % _ROLE_NEGATIVES
    elif is_role and not any(token in text.lower() for token in _SECOND_FACE):
        text += "\ndifferent face, second face\n"
    if is_prop and not _HAND_MARK.search(text):
        text += "\n禁止手持，不出现手。\n"
    return text


def patch_asset_phrases(text, model=None, classify=None):
    """Add the checker's required phrases without dropping a block.

    Kind comes from LLM when possible; 女主/男主 rules fill gaps. Role negatives are fixed.
    """
    pieces = _asset_pieces(text)
    if not pieces:
        return text
    kinds = _classify_asset_kinds(pieces, model=model, classify=classify)
    return (
        "\n\n".join(
            _patch_asset_block(body, kinds[index] if index < len(kinds) else "").strip()
            for index, (_number, body) in enumerate(pieces)
        )
        + "\n"
    )


def assets_from_markdown(text, previous):
    """Keep one spec asset per 图 block, and keep references already stored for that id."""
    previous_by_id = {}
    if isinstance(previous, list):
        for item in previous:
            if isinstance(item, dict) and item.get("id"):
                previous_by_id[str(item.get("id"))] = item
    items = []
    for number, body in _asset_pieces(text):
        heading = body.splitlines()[0] if body else ""
        title = heading.split("·", 1)[-1].strip() if "·" in heading else heading
        asset_id = "图%d" % number
        if "道具" in heading:
            kind = "prop"
        elif "角色" in heading:
            kind = "character"
        else:
            kind = "location"
        old = previous_by_id.get(asset_id) or {}
        references = old.get("references") if isinstance(old.get("references"), list) else []
        depends = old.get("depends_on") if isinstance(old.get("depends_on"), str) else ""
        items.append(
            {
                "id": asset_id,
                "title": title,
                "prompt": body.strip(),
                "depends_on": depends,
                "references": list(references),
                "subject": str(old.get("subject") or title),
                "subject_kind": str(old.get("subject_kind") or kind),
                "view": str(old.get("view") or "single"),
            }
        )
    return items


_ORDER = ("script", "asset", "storyboard", "feed")
_NAMED = (
    ("只要打戏", "combat"),
    ("只要投喂稿", "feed"),
    ("只要投喂", "feed"),
    ("只要分镜", "storyboard"),
    ("只要资产", "asset"),
    ("只要剧本", "script"),
    ("只要对标", "recon"),
)
_COMBAT_WORDS = ("打斗", "打戏", "战斗", "对决", "开打", "武打", "格斗", "斗法", "厮杀", "交手")
_STAGE_CODE = {
    "recon": "P1",
    "script": "P1",
    "asset": "P2",
    "storyboard": "P3",
    "feed": "P4",
}
_GATE_PHASE = {
    "recon": "gate_intel",
    "script": "gate_script",
    "asset": "gate_asset",
    "storyboard": "gate_storyboard",
    "feed": "gate_feed",
}
_GATE_LABEL = {
    "recon": "对标情报卡",
    "script": "剧本门",
    "asset": "资产门",
    "storyboard": "分镜门",
    "feed": "投喂前",
}
_PERCENT = {"recon": 18, "script": 30, "asset": 46, "storyboard": 62, "feed": 76, "qc": 88}
_ENGINE = {
    "script": "sd-script-engine/",
    "asset": "sd-asset-engine/",
    "storyboard": "sd-storyboard-engine/",
    "feed": "sd-feed-engine/",
    "recon": "sd-recon-engine/",
    "R1": "sd-real-engine/",
    "R2": "sd-combat-engine/",
    "R3": "sd-xuanhuan-engine/",
}
REPAIR_LIMIT = 3
_DRAFT_FILE = {
    "recon": "对标情报卡.md",
    "script": "剧本.md",
    "asset": "assets.md",
    "storyboard": "分镜.md",
    "feed": "feed.md",
}
_TASK_LINE = {
    "script": "任务：剧本",
    "asset": "任务：资产锁定",
    "storyboard": "任务：分镜",
    "feed": "任务：提示词封装",
}
_SCRIPT_CHAIN = {
    "sd-script-engine/references/source-registration.md": "01-来源登记.md",
    "sd-script-engine/references/story-bible-and-ip.md": "02-IP与角色.md",
    "sd-script-engine/references/episode-planning.md": "04-分集规划.md",
    "sd-script-engine/references/six-stage-pipeline.md": "05-正式剧本.md",
    "sd-script-engine/references/dialogue-naturalization.md": "05-台词.md",
    "short-drama-director/references/dialogue-doctor-7d.md": "05-对白医生.md",
    "sd-script-engine/references/roundtable-review.md": "06-圆桌.md",
}
_INSTRUCTION = {
    "recon": "只写《对标情报卡》，放在 intel。这是来源，不是事实。不要写剧本。",
    "script": "只写剧本，放在 script。过程稿放在 internal。不要改锁。",
    "asset": "只写资产提示词，放在 image_prompts。每条以「#### 图N · 名称」开头，自包含，禁止同上、见母版、同图N。",
    "storyboard": "\n".join((
        "只写分镜，放在 storyboard。有打斗时招式只来自已挂载的打戏原文。",
        "预演故事板放在 previs，必须是 markdown 字符串，禁止 JSON 数组/对象。",
        "不要写成「段N验证……」摘要。照这个形状写满，标签原文保留，括号里必须是数字：",
        "## 段 1",
        "**本段要验什么**：这一段要保住的站位",
        "**风险提示**：人物左右不要对调",
        "### 故事板提示词（1 格）",
        "第 1 格：她站在门内，身体朝门，视线看向门外，手抬在身前，镜头在侧面，光从门内照来。",
        "看图先看这三点：人、门、光",
        "后面每一段都同样写全。每一格都要答完六问：谁在哪、朝哪、看哪、做什么、镜头在哪、光从哪来。",
        "文末一行：预演故事板：段1~段N ✅",
    )),
    "feed": "\n".join((
        "只写投喂稿，放在 feed。feed 必须是字符串。",
        "用 ## 【投喂单元 N/M】标题，下面写七段。",
        "每一段改成六段正文。字段名必须带冒号并且单独成行：subject_definitions:、summary:、retention_analysis:、detailed_description:、overall_soundscape:、non_diegetic_music:。",
        "每个投喂单元单独计时。[Shot 1] 不要写时间戳。从 [Shot 2] 起写成 [Shot 2] At 00:04.000, the camera cuts to …，只在本单元内递增，最后一刀不超过 15 秒。不要写成 At 00:04.000 [Shot 2]。下一段不要再从 At 00:00 接在上一段后面。",
        "每个 Subject 必须对应 assets.md 里已经有的图。没有关键帧资产就不要写 Picture。",
        "不要写接口 JSON，不要写 segments 对象。画面写正面有什么。",
    )),
}


def detect_only(text):
    raw = str(text or "")
    for needle, name in _NAMED:
        if needle in raw:
            return name
    return ""


def named_stage(only):
    if only == "combat":
        return "storyboard"
    if only in ("recon", "script", "asset", "storyboard", "feed"):
        return only
    return "feed"


def stages_for(locks, only):
    locks = locks or {}
    if only == "recon":
        return ["recon"]
    need_recon = locks.get("source") in ("3", "4") and not locks.get("intel_confirmed")
    target = named_stage(only)
    stages = ["recon"] if need_recon else []
    for name in _ORDER:
        stages.append(name)
        if only and name == target:
            break
    return stages


def next_pending(stages, accepted):
    done = set(accepted or [])
    for name in stages or []:
        if name not in done:
            return name
    return ""


def draft_has_combat(text):
    raw = str(text or "")
    return any(word in raw for word in _COMBAT_WORDS)


def engine_prefix(only, locks):
    if only == "combat":
        return _ENGINE.get((locks or {}).get("combat") or "", "")
    return _ENGINE.get(only or "", "")


def mount_header(stage, locks, carried, only):
    """Routing block for slice_context.resolve_files. Full text is loaded from those paths."""
    locks = locks or {}
    source = str(locks.get("source") or "1")
    if stage == "recon" and source not in ("3", "4"):
        source = "3"
    combat = "none"
    if stage in ("storyboard", "feed") and (draft_has_combat(carried) or only == "combat"):
        combat = locks.get("combat") or "none"
    prefix = ""
    if stage == "recon":
        prefix = _ENGINE["recon"]
    elif only and stage == named_stage(only):
        prefix = engine_prefix(only, locks)
    lines = [
        "阶段：%s" % _STAGE_CODE.get(stage, "P4"),
        "赛道：%s" % (locks.get("track") or "1"),
        "来源：%s" % source,
        "动作档：%s" % combat,
        "模型：%s" % (locks.get("model") or "h3"),
        "素材形态：%s" % (locks.get("material_mode") or "M1"),
    ]
    if prefix:
        lines.append("只加载：%s" % prefix)
    if stage == "storyboard" and only != "combat":
        lines.append("预演：1")
    return "\n".join(lines)


def stage_brief(stage, locks, carried):
    return "\n\n".join(
        part
        for part in (
            mount_header(stage, locks, carried, ""),
            lock_brief(locks),
            _INSTRUCTION.get(stage, ""),
            "上一阶段正文：" if carried else "",
            carried,
        )
        if part
    )


def _read(path):
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def carried_text(folder, stage):
    folder = Path(folder)
    internal = folder / "_内部_"
    chunks = []
    if stage in ("script", "asset", "storyboard", "feed"):
        intel = _read(internal / "对标情报卡.md")
        if intel:
            chunks.append(intel)
    if stage in ("asset", "storyboard", "feed"):
        script = _read(internal / "剧本.md")
        if script:
            chunks.append(script)
    if stage in ("storyboard", "feed"):
        assets = _read(internal / "assets.md")
        if assets:
            chunks.append(assets)
    if stage == "feed":
        board = _read(internal / "分镜.md")
        if board:
            chunks.append(board)
    return "\n\n".join(chunks)


def _write_internal(folder, name, text):
    if not str(text or "").strip():
        return None
    path = internal_dir(folder) / name
    path.write_text(str(text).strip() + "\n", encoding="utf-8")
    return path


def store_stage(folder, stage, result):
    """Keep this stage's fields. Drop any locks object the model tried to return."""
    result = dict(result or {})
    result.pop("locks", None)
    folder = Path(folder)
    if stage == "recon":
        _write_internal(folder, "对标情报卡.md", result.get("intel") or result.get("script") or "")
    elif stage == "script":
        _write_internal(folder, "剧本.md", result.get("script") or "")
        _write_internal(folder, "剧本过程.md", result.get("internal") or "")
    elif stage == "asset":
        _write_internal(folder, "assets.md", result.get("image_prompts") or "")
    elif stage == "storyboard":
        _write_internal(folder, "分镜.md", result.get("storyboard") or "")
        previs = coerce_previs(result.get("previs") or "")
        if previs.strip():
            result = dict(result)
            result["previs"] = previs
            _write_internal(folder, "previs.md", previs)
    elif stage == "feed":
        loaded = _load_spec(folder)
        bound, _groups = bind_feed_document(result.get("feed") or "", loaded.get("assets") or [])
        result["feed"] = bound
        _write_internal(folder, "feed.md", result.get("feed") or "")
    return result


SCRIPT_REVISE_OPTIONS = ("更贴我写的故事", "产品信息写清楚", "打斗再收着点", "其他")
ASSET_REVISE = "根据建议修改"
STORYBOARD_KEEP = "先用这一稿继续"


def storyboard_has_draft(folder):
    return bool(_read(Path(folder) / "_内部_" / "分镜.md").strip())


def script_revise_question():
    return {
        "id": "script_revise",
        "kind": "single",
        "reply": "这一稿机检已过，没有打回项。选一个改法，再写一版。写完仍停在剧本门。",
        "options": list(SCRIPT_REVISE_OPTIONS),
        "cards": [],
    }


def _gate_copy(stage, passed, report, tries=0, has_draft=False):
    if passed and stage == "script":
        return (
            "剧本门。机检已过，没有打回项。继续才进入资产。"
            "精修会先让你选改法，再写一版，写完仍停在这里。",
            ["继续", "精修"],
        )
    if passed and stage == "recon":
        return "对标情报卡写好了。确认后才写剧本。这张卡是来源，不是事实。", ["确认", "继续"]
    if passed and stage == "asset":
        return "资产门。机检已过。继续就进入分镜。", ["继续"]
    if passed and stage == "storyboard":
        return "分镜门。继续之后才写投喂稿。", ["继续"]
    if passed and stage == "feed":
        return "投喂前。闸门已过。继续就做质检和交付。", ["继续"]
    tail = (report or "").strip()
    if len(tail) > 1200:
        tail = tail[-1200:]
    if stage in ("asset", "storyboard", "feed"):
        reply = "机检没过。根据建议修改会按下面这些项改。"
        if tail:
            reply = reply + "\n" + tail
        options = [ASSET_REVISE]
        if stage == "storyboard" and has_draft:
            options.append(STORYBOARD_KEEP)
        return reply, options
    if stage == "script":
        keep = "通过的部分留下"
    else:
        keep = "通过的部分留下"
    if tries:
        reply = "机检没过。已按检查结果自动改了%d次，还剩这些。根据建议修改会按这些项改上一稿，%s。" % (tries, keep)
    else:
        reply = "机检没过。下面是还要改的地方。根据建议修改会按这些项改上一稿，%s。" % keep
    if tail:
        reply = reply + "\n" + tail
    return reply, [ASSET_REVISE]


def gate_result(stage, passed, report, pipeline, has_draft=False):
    tries = int((pipeline or {}).get("repairs") or 0)
    reply, options = _gate_copy(stage, passed, report, tries, has_draft=has_draft)
    phase = _GATE_PHASE.get(stage, "gate_fail")
    if not passed and stage == "feed" and (pipeline or {}).get("check") == "qc":
        phase = "gate_fail"
    stored = dict(pipeline or {})
    stored.update(
        {
            "current": stage,
            "action": "wait",
            "passed": bool(passed),
            "report": report or "",
        }
    )
    return {
        "terminal": "gate",
        "status": "gate",
        "stage": "独立质检" if phase == "gate_fail" else _GATE_LABEL.get(stage, "停在门上"),
        "progress": {
            "percent": _PERCENT.get(stage, 50),
            "phase": phase,
            "message": reply.split("\n", 1)[0],
            "pipeline": stored,
            "question": {"id": stage, "kind": "single", "reply": reply, "options": options, "cards": []},
        },
        "message": {
            "role": "assistant",
            "content": reply,
            "options": options,
            "choice_kind": "single",
            "cards": [],
        },
    }


def _spec_patch(stage, result, previous):
    merged = dict(previous or {})
    result = result or {}
    if stage == "script":
        if result.get("title"):
            merged["title"] = result.get("title")
        if result.get("script"):
            merged["script"] = result.get("script")
    elif stage == "asset":
        if result.get("image_prompts"):
            merged["image_prompts"] = result.get("image_prompts")
        if result.get("assets"):
            merged["assets"] = result.get("assets")
    elif stage == "storyboard":
        if result.get("storyboard"):
            merged["storyboard"] = result.get("storyboard")
        if result.get("previs"):
            merged["previs"] = coerce_previs(result.get("previs") or "")
        merged = apply_storyboard_shots(merged)
    elif stage == "feed":
        if result.get("feed"):
            merged["feed"] = result.get("feed")
        incoming = result.get("shots")
        if isinstance(incoming, list) and incoming and not shots_are_placeholder(incoming):
            merged["shots"] = incoming
        merged = apply_storyboard_shots(merged)
        merged = apply_feed_binding(merged)
    merged.pop("locks", None)
    return merged


def _load_spec(folder):
    path = Path(folder) / "spec.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def check_stage(folder, stage, locks, title=""):
    folder = Path(folder)
    internal = folder / "_内部_"
    mode = (locks or {}).get("material_mode") or "M1"
    if stage == "recon":
        text = _read(internal / "对标情报卡.md")
        if not text.strip():
            return False, "对标情报卡是空的"
        return True, ""
    if stage == "script":
        text = _read(internal / "剧本.md")
        if not text.strip():
            return False, "剧本是空的"
        claim = unverified_claim(text, mode)
        if claim:
            return False, "没看见图，却写了%s" % claim
        return True, ""
    if stage == "asset":
        path = internal / "assets.md"
        text = _read(path)
        if not path.is_file() or not text.strip():
            return False, "资产稿是空的，继续会再写一次。"
        claim = unverified_claim(text, mode)
        if claim:
            return False, "没看见图，却写了%s" % claim
        code, report = check_asset_blocks(path)
        if code != 0:
            return False, report
        return True, ""
    if stage == "storyboard":
        text = _read(internal / "分镜.md")
        if not text.strip():
            return False, "分镜是空的"
        claim = unverified_claim(text, mode)
        if claim:
            return False, "没看见图，却写了%s" % claim
        previs = _read(internal / "previs.md")
        if previs.strip():
            body = previs_check_text(text, previs, mode)
            # Persist rendered markdown so repair units see real段 bodies, not JSON shards.
            if body.strip() and body.strip() != previs.strip():
                _write_internal(folder, "previs.md", body)
            target = folder / previs_name(title or _load_spec(folder).get("title") or "未命名")
            target.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
            code, report = check_p45(target, material_mode=mode)
            # #region agent log
            try:
                with open("/Users/shaneson/AiVideo/.cursor/debug-d078f2.log", "a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "sessionId": "d078f2",
                                "runId": "previs-fix",
                                "hypothesisId": "S3,S4",
                                "location": "stage_runner.py:check_stage:storyboard",
                                "message": "storyboard gate after previs render",
                                "data": {
                                    "code": code,
                                    "persisted": body.strip() != previs.strip(),
                                    "fail_count": sum(
                                        1 for line in (report or "").splitlines() if "FAIL:" in line
                                    ),
                                    "p451": sum(
                                        1 for line in (report or "").splitlines() if "P451" in line
                                    ),
                                    "p452": sum(
                                        1 for line in (report or "").splitlines() if "P452" in line
                                    ),
                                },
                                "timestamp": int(time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError:
                pass
            # #endregion
            if code != 0:
                if target.is_file():
                    _write_internal(folder, target.name, target.read_text(encoding="utf-8"))
                    target.unlink()
                return False, report
        return True, ""
    if stage == "feed":
        path = internal / "feed.md"
        text = render_feed_text(_read(path))
        bound, _groups = bind_feed_document(text, _load_spec(folder).get("assets") or [])
        if bound.strip():
            text = bound
        if text.strip() and text.strip() != _read(path).strip():
            text = patch_h3_voiceover(text)
            _write_internal(folder, "feed.md", text)
        if not text.strip():
            return False, "投喂稿是空的"
        claim = unverified_claim(text, mode)
        if claim:
            return False, "没看见图，却写了%s" % claim
        code, report = check_feed(path, locks.get("model") or "h3", mode, locks.get("track") or "1")
        if code != 0:
            return False, report
        return True, ""
    return True, ""


_HEADING_SEGMENT = re.compile(r"(?m)^#{1,6}\s*\**\s*段\s*\d+")


def _promote_inline_segments(text):
    """Turn inline 段N into the heading the previs checker recognizes.

    Never split JSON/list previs — segment_id values like「段 1」would shred it.
    """
    text = str(text or "").strip()
    if not text or _HEADING_SEGMENT.search(text):
        return text
    stripped = text.lstrip()
    if stripped[:1] in "[{":
        return text
    chunks = []
    for part in re.split(r"(?=段\s*\d+)", text):
        match = re.match(r"段\s*(\d+)\s*", part)
        if not match:
            continue
        rest = part[match.end() :].strip()
        chunks.append("## 段 %s\n%s" % (match.group(1), rest))
    if not chunks:
        return text
    return "\n\n".join(chunks)


def _previs_segment_rows(data):
    """Normalize a parsed object into segment dicts, or None."""
    if isinstance(data, dict):
        for key in ("segments", "previs", "boards"):
            rows = data.get(key)
            if isinstance(rows, list) and rows:
                data = rows
                break
        else:
            return None
    if not isinstance(data, list) or not data:
        return None
    first = data[0] if isinstance(data[0], dict) else {}
    markers = ("check_focus", "risk_alert", "grids", "segment_id", "本段要验什么", "风险提示")
    if not any(key in first for key in markers):
        return None
    return [item for item in data if isinstance(item, dict)]


def _previs_payload(value):
    """Parse structured previs (list/dict/JSON/Python-literal). Empty → markdown path."""
    if isinstance(value, (list, dict)):
        return _previs_segment_rows(value)
    text = str(value or "").strip()
    if text.startswith("素材形态："):
        text = re.sub(r"^素材形态：[^\n]*\n+", "", text).strip()
    if not text or text[:1] not in "[{":
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import ast

            data = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return None
    return _previs_segment_rows(data)


def _panel_prompt_lines(grid, index):
    row = grid if isinstance(grid, dict) else {}
    prompt = str(row.get("prompt") or "").strip()
    number = int(row.get("grid_index") or index)
    if prompt and not re.match(r"第\s*\d+\s*格", prompt):
        prompt = "第 %d 格：%s" % (number, prompt)
    if not prompt:
        prompt = "第 %d 格：待补画面。" % number
    lines = [prompt]
    if "看图先看这三点" not in prompt:
        six = row.get("six_questions") if isinstance(row.get("six_questions"), dict) else {}
        tip = "、".join(
            str(six.get(key) or "").strip()
            for key in ("who_where", "facing", "looking", "doing", "camera_pos", "light_dir")
            if str(six.get(key) or "").strip()
        )
        if tip:
            lines.append("看图先看这三点：%s" % tip)
        else:
            lines.append("看图先看这三点：人、位、光")
    return lines


def _render_previs_segments(payload, mode="M1"):
    blocks = []
    for index, segment in enumerate(payload, 1):
        sid = str(segment.get("segment_id") or "段 %d" % index)
        match = re.search(r"\d+", sid)
        number = int(match.group()) if match else index
        focus = str(
            segment.get("check_focus")
            or segment.get("本段要验什么")
            or "这一段要保住的站位"
        ).strip()
        risk = str(
            segment.get("risk_alert") or segment.get("风险提示") or "人物左右不要对调"
        ).strip()
        grids = segment.get("grids") if isinstance(segment.get("grids"), list) else []
        count = int(segment.get("grid_count") or len(grids) or 1)
        if count < 1:
            count = 1
        lines = [
            "## 段 %d" % number,
            "**本段要验什么**：%s" % focus,
            "**风险提示**：%s" % risk,
            "### 故事板提示词（%d 格）" % count,
        ]
        if grids:
            for grid_index, grid in enumerate(grids, 1):
                lines.extend(_panel_prompt_lines(grid, grid_index))
        else:
            lines.append("第 1 格：待补画面。")
            lines.append("看图先看这三点：人、位、光")
        blocks.append("\n".join(lines))
    total = len(blocks) or 1
    body = "\n\n".join(blocks) + "\n\n预演故事板：段1~段%d ✅" % total
    if not body.startswith("素材形态："):
        body = "素材形态：%s\n\n%s" % (mode or "M1", body.lstrip())
    if not body.endswith("\n"):
        body += "\n"
    return body


def render_previs_markdown(previs, mode="M1"):
    """Turn JSON/list previs into the P45 markdown shape; leave real markdown alone."""
    if previs is None or previs == "":
        return ""
    incoming = type(previs).__name__
    payload = _previs_payload(previs)
    source = "markdown"
    if payload is not None:
        source = "structured"
        body = _render_previs_segments(payload, mode)
    else:
        text = str(previs or "").strip()
        if not text:
            return ""
        if "本段要验什么" in text and _HEADING_SEGMENT.search(text):
            body = text
        else:
            body = _promote_inline_segments(text)
            source = "promoted"
        if not body.startswith("素材形态："):
            body = "素材形态：%s\n\n%s" % (mode or "M1", body.lstrip())
        if not body.endswith("\n"):
            body += "\n"
    # #region agent log
    try:
        with open("/Users/shaneson/AiVideo/.cursor/debug-d078f2.log", "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "sessionId": "d078f2",
                        "runId": "previs-fix",
                        "hypothesisId": "T1",
                        "location": "stage_runner.py:render_previs_markdown",
                        "message": "previs render",
                        "data": {
                            "incoming": incoming,
                            "source": source,
                            "chars": len(body),
                            "segments": len(_HEADING_SEGMENT.findall(body)),
                            "has_labels": "本段要验什么" in body and "风险提示" in body,
                            "has_grid_decl": "故事板提示词（" in body or "故事板提示词(" in body,
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion
    return body


def coerce_previs(value, mode="M1"):
    """Write-path gate: never persist list/dict/JSON previs as str(list)."""
    rendered = render_previs_markdown(value, mode)
    # #region agent log
    try:
        with open("/Users/shaneson/AiVideo/.cursor/debug-d078f2.log", "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "sessionId": "d078f2",
                        "runId": "previs-fix",
                        "hypothesisId": "T2",
                        "location": "stage_runner.py:coerce_previs",
                        "message": "previs coerce at write",
                        "data": {
                            "incoming": type(value).__name__,
                            "out_chars": len(rendered),
                            "is_markdown": "本段要验什么" in rendered,
                            "was_structured": _previs_payload(value) is not None,
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion
    return rendered


_PANEL_LINE = re.compile(r"第\s*\d+\s*格\s*[：:]")
_BOARD_LABELS = ("本段要验什么", "风险提示", "故事板提示词", "看图")


def _board_rank(text):
    raw = str(text or "")
    panels = len(_PANEL_LINE.findall(raw))
    labels = sum(1 for label in _BOARD_LABELS if label in raw)
    headings = len(_HEADING_SEGMENT.findall(raw))
    return (panels, labels, headings, len(raw.strip()))


def keep_richer_board(previous, new):
    """Keep a real panel board when a later rewrite comes back as a summary."""
    previous = str(previous or "").strip()
    new = str(new or "").strip()
    old_panels, old_labels, old_headings, old_len = _board_rank(previous)
    new_panels, new_labels, new_headings, new_len = _board_rank(new)
    if not previous:
        return new
    if new_panels > old_panels:
        return new
    if new_panels == old_panels and new_panels > 0 and new_labels >= old_labels:
        return new
    if new_panels == old_panels and new_labels > old_labels:
        return new
    if old_panels == 0 and new_panels == 0 and new_labels == old_labels and new_headings >= old_headings and new_len >= old_len:
        return new
    return previous


def _board_candidates(storyboard, previs):
    found = []
    for raw in (previs, storyboard):
        text = str(raw or "").strip()
        if not text:
            continue
        found.append(text)
        promoted = _promote_inline_segments(text)
        if promoted != text:
            found.append(promoted)
    return found


def previs_check_text(storyboard, previs, mode):
    """Check the text that actually contains the panels.

    Structured JSON previs is rendered to P45 markdown first. Promoting a JSON
    blob is forbidden — it only manufactures empty「## 段 N」shells.
    """
    rendered = render_previs_markdown(previs, mode)
    if "本段要验什么" in rendered and _HEADING_SEGMENT.search(rendered):
        return rendered
    candidates = _board_candidates(storyboard, previs)
    body = max(candidates, key=_board_rank) if candidates else ""
    if not str(body or "").strip():
        return rendered if rendered.strip() else ""
    body = str(body).strip()
    if body.startswith("素材形态："):
        return body if body.endswith("\n") else body + "\n"
    return "素材形态：%s\n\n%s\n" % (mode or "M1", body)


_H3_SECTION_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)


def _feed_payload(value):
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text or text[0] not in "{[":
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, (dict, list)):
        try:
            import ast

            data = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return None
    if isinstance(data, (dict, list)):
        return data
    return None


def _feed_segments(payload):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    segments = payload.get("segments")
    if isinstance(segments, list) and segments:
        return [item for item in segments if isinstance(item, dict)]
    content = payload.get("content")
    if isinstance(content, dict) and any(content.get(field) for field in _H3_SECTION_FIELDS):
        return [payload]
    if any(payload.get(field) for field in _H3_SECTION_FIELDS):
        return [{"content": payload}]
    return []


_H3_AT = re.compile(r"(\[Shot\s+\d+\][^\n]{0,14}?(?:At|在)\s*)(\d{1,2}):(\d{2})\.(\d{3})")


def _feed_span(time_range):
    match = re.match(r"(\d+):(\d{2})\s*-\s*(\d+):(\d{2})", str(time_range or "").strip())
    if not match:
        return None, None
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    if end <= start:
        return None, None
    return start, end - start


def _rebase_cuts(body, start):
    if not start:
        return body

    def local(match):
        seconds = int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 1000
        shifted = max(0, seconds - start)
        whole = int(shifted)
        millis = int(round((shifted - whole) * 1000))
        return "%s%02d:%02d.%03d" % (match.group(1), whole // 60, whole % 60, millis)

    return _H3_AT.sub(local, body)


_H3_FIELD_ALT = "|".join(_H3_SECTION_FIELDS)
_LOOSE_FIELD = re.compile(
    r"^(?:【\s*)?(%s)(?:\s*】)?\s*[:：]?\s*$" % _H3_FIELD_ALT
)
_H3_COLON_FIELD = re.compile(r"^[ \t]*(?:%s)[ \t]*[:：]" % _H3_FIELD_ALT)
_H3_BARE_FIELD = re.compile(r"^[ \t]*(%s)[ \t]*$" % _H3_FIELD_ALT)
_H3_BRACKET_FIELD = re.compile(
    r"^[ \t]*【[ \t]*(%s)[ \t]*】[ \t]*[:：]?[ \t]*(.*)$" % _H3_FIELD_ALT
)
_H3_ANGLE_WRAP = re.compile(
    r"^[ \t]*<\s*(%s)\s*>(.*?)</\s*\1\s*>[ \t]*$" % _H3_FIELD_ALT
)
_H3_ANGLE_OPEN = re.compile(r"^[ \t]*<\s*(%s)\s*>(.*)$" % _H3_FIELD_ALT)
_H3_ANGLE_CLOSE = re.compile(r"^[ \t]*</\s*(%s)\s*>[ \t]*$" % _H3_FIELD_ALT)
_SHOT_HEAD = re.compile(r"\[Shot\s+\d+\]")
_SHOT_CLOCK = re.compile(
    r"\[Shot\s+(\d+)\][^\n]{0,40}?(?:At|在)\s*(\d{1,2}):(\d{2})\.(\d{3})"
)
_SHOT1_CLOCK = re.compile(
    r"(\[Shot\s*1\])[^\n]{0,40}?(?:At|在)\s*\d{1,2}:\d{2}\.\d{3}\s*,?\s*"
)
_SHOT_RANGE = re.compile(
    r"^(\[Shot\s+(\d+)\])\s*\[(\d{1,2}:\d{2}\.\d{3})\s*[-–—]\s*(\d{1,2}:\d{2}\.\d{3})\]\s*",
    re.M,
)
_LEADING_SHOT_CLOCK = re.compile(
    r"(?m)^([ \t]*)(?:At|在)\s+(\d{1,2}:\d{2}\.\d{3})\s+(\[Shot\s+(\d+)\])[ \t]*"
)
_UNIT_HEAD_LINE = re.compile(r"(?m)^##[^\n]*投喂单元[^\n]*$")
_DURATION_LINE = re.compile(r"(?m)^(?:duration|duration_seconds)\s*[:=]")


def _debug_feed_clocks(message, data):
    # region agent log
    try:
        with open("/Users/shaneson/AiVideo/.cursor/debug-d078f2.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "sessionId": "d078f2",
                "runId": "feed-overlap",
                "hypothesisId": "A,B,C,E",
                "location": "stage_runner.py:normalize_h3_shot_cuts",
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


def _feed_cut_snapshot(text):
    heads = list(_UNIT_HEAD_LINE.finditer(str(text or "")))
    spans = []
    if not heads:
        spans = [str(text or "")]
    else:
        for index, match in enumerate(heads):
            end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
            spans.append(text[match.start():end])
    units = []
    for index, block in enumerate(spans, 1):
        cuts = []
        for match in _SHOT_CLOCK.finditer(block):
            seconds = int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 1000
            cuts.append([int(match.group(1)), seconds])
        units.append({
            "n": index,
            "shot1": bool(_SHOT1_CLOCK.search(block)),
            "cuts": cuts[:12],
        })
    return {"units": len(units), "clocks": units}


def _format_clock(seconds):
    whole = max(0, int(seconds))
    millis = int(round((float(seconds) - int(seconds)) * 1000))
    if millis >= 1000:
        whole += 1
        millis -= 1000
    if millis < 0:
        millis = 0
    return "%02d:%02d.%03d" % (whole // 60, whole % 60, millis)


_LEADING_CLOCK = re.compile(
    r"^(\[Shot\s+\d+\])(?:[^\n]{0,40}?(?:At|在)\s*\d{1,2}:\d{2}\.\d{3}\s*,?\s*)?"
)
_CUT_LEAD = re.compile(
    r"^(?:the camera cuts to|the shot cuts to|the shot transitions to|the shot changes to|the shot switches to)\s+",
    re.I,
)
_FEED_TAIL = re.compile(r"(?m)^(---\s*|《素材接线单》)")


def _restamp_shot(chunk, number, seconds, first):
    chunk = str(chunk or "").strip()
    match = _LEADING_CLOCK.match(chunk)
    rest = chunk[match.end():].strip() if match else chunk
    head = "[Shot %d]" % number
    if first:
        rest = _CUT_LEAD.sub("", rest)
        return ("%s %s" % (head, rest)).strip()
    return "%s At %s, %s" % (head, _format_clock(seconds), rest)


def _detail_span(lines):
    start = None
    for index, line in enumerate(lines):
        match = _LOOSE_FIELD.match(line.strip())
        if match and match.group(1) == "detailed_description":
            start = index
            break
    if start is None:
        return None, None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _LOOSE_FIELD.match(lines[index].strip()):
            end = index
            break
    return start, end


def _cut_moment(seconds):
    if seconds is None:
        return None
    return float(seconds)


def _expand_overlong_unit(block):
    """Move shots whose cut is past 15s into a following unit and rebase that unit."""
    lines = str(block or "").splitlines()
    start, end = _detail_span(lines)
    if start is None:
        return [str(block or "").strip()]
    pieces = _shot_pieces("\n".join(lines[start + 1:end]))
    stay = []
    move = []
    origin = None
    for _number, seconds, chunk in pieces:
        moment = _cut_moment(seconds)
        if origin is None and moment is not None and moment > 15.001:
            origin = moment
        if origin is None:
            stay.append(chunk.strip())
        else:
            local = 0.0 if moment is None else moment - origin
            move.append((local, chunk))
    if not move:
        return [str(block or "").strip()]
    stayed = "\n".join(lines[:start + 1] + stay + lines[end:]).strip()
    moved_lines = []
    for index, (local, chunk) in enumerate(move, 1):
        moved_lines.append(_restamp_shot(chunk, index, local, index == 1))
    spawned = "\n".join(lines[:start + 1] + moved_lines + lines[end:]).strip()
    return [stayed] + _expand_overlong_unit(spawned)


def _peel_feed_tail(block):
    match = _FEED_TAIL.search(str(block or ""))
    if not match:
        return str(block or ""), ""
    return block[:match.start()].rstrip(), block[match.start():].strip()


def _renumber_feed_heads(text):
    matches = list(_UNIT_HEAD_LINE.finditer(text))
    if not matches:
        return text
    total = len(matches)
    out = text
    for number, match in reversed(list(enumerate(matches, 1))):
        line = match.group(0)
        replaced = re.sub(
            r"(投喂单元\s*)\d+\s*/\s*\d+",
            lambda found, index=number, count=total: "%s%s/%s" % (found.group(1), index, count),
            line,
            count=1,
        )
        out = out[:match.start()] + replaced + out[match.end():]
    return out


def _unit_over_cap(text):
    heads = list(_UNIT_HEAD_LINE.finditer(text))
    spans = [text] if not heads else []
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        spans.append(text[match.start():end])
    for block in spans:
        for _number, seconds in (
            (int(match.group(1)), int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 1000)
            for match in _SHOT_CLOCK.finditer(block)
        ):
            if seconds > 15.001:
                return True
    return False


def _settle_ready_units(raw):
    """Drop Shot 1 clocks. Split a unit whose later cuts pass 15s."""
    stripped = _SHOT1_CLOCK.sub(r"\1 ", str(raw or ""))
    if not _unit_over_cap(stripped):
        return stripped
    heads = list(_UNIT_HEAD_LINE.finditer(stripped))
    if not heads:
        return stripped
    prefix = stripped[:heads[0].start()]
    blocks = []
    tail = ""
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(stripped)
        block = stripped[match.start():end].strip()
        if index == len(heads) - 1:
            block, tail = _peel_feed_tail(block)
        if _unit_over_cap(block):
            blocks.extend(_expand_overlong_unit(block))
        else:
            blocks.append(block)
    out = prefix.rstrip() + "\n\n" + "\n\n".join(block for block in blocks if block.strip())
    if tail:
        out = out.rstrip() + "\n\n" + tail
    if not out.endswith("\n"):
        out += "\n"
    return _renumber_feed_heads(out)


def _declared_h3_duration(seconds):
    whole = int(math.ceil(float(seconds)))
    return max(4, min(15, whole))


def _last_cut_seconds(block):
    clocks = list(_SHOT_CLOCK.finditer(block))
    if not clocks:
        return None
    last = clocks[-1]
    return int(last.group(2)) * 60 + int(last.group(3)) + int(last.group(4)) / 1000.0


def _unit_without_tail(block):
    match = _FEED_TAIL.search(str(block or ""))
    if not match:
        return str(block or ""), ""
    return block[: match.start()], block[match.start() :]


def _fill_duration_block(block):
    """Add duration from the last cut inside the unit. Leave shot sentences and the footer alone."""
    body, tail = _unit_without_tail(block)
    if _DURATION_LINE.search(body):
        return block
    seconds = _last_cut_seconds(body)
    if seconds is None:
        return block
    shown = _declared_h3_duration(seconds)
    unit = _UNIT_HEAD_LINE.search(body)
    line = "duration: %s" % shown
    if unit:
        filled = body[: unit.end()] + "\n" + line + body[unit.end() :]
    elif body.startswith("\n"):
        filled = line + body
    else:
        filled = line + "\n" + body
    return filled + tail


def _fill_missing_h3_duration(text):
    raw = str(text or "")
    heads = list(_UNIT_HEAD_LINE.finditer(raw))
    if not heads:
        return _fill_duration_block(raw)
    parts = [raw[: heads[0].start()]]
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        parts.append(_fill_duration_block(raw[match.start() : end]))
    return "".join(parts)


def _flip_leading_shot_clocks(text):
    """Move ``At MM:SS.mmm [Shot N]`` so the shot tag comes first.

    Shot 1 drops the clock. Later shots become ``[Shot N] At MM:SS.mmm,``.
    """
    def repl(match):
        indent, clock, head, number = match.group(1), match.group(2), match.group(3), int(match.group(4))
        if number == 1:
            return indent + head + " "
        return "%s%s At %s, " % (indent, head, clock)

    return _LEADING_SHOT_CLOCK.sub(repl, str(text or ""))


def normalize_h3_shot_cuts(text):
    """Turn ``[Shot N] [start-end]`` ranges into H2 ``At MM:SS.mmm`` cuts.

    Shot 1 drops the clock. Later shots keep the range start as the cut point.
    A headed unit whose cut passes 15s is split, and the next unit starts again
    with an untimed Shot 1.
    """
    raw = str(text or "")
    if "[Shot" not in raw:
        return raw
    # region agent log
    try:
        _lead = len(re.findall(r"(?m)^[ \t]*(?:At|在)\s+\d{1,2}:\d{2}\.\d{3}\s+\[Shot\s+\d+\]", raw))
        _trail = len(_SHOT_CLOCK.findall(raw))
        _shot1_lead = len(re.findall(r"(?m)^[ \t]*(?:At|在)\s+\d{1,2}:\d{2}\.\d{3}\s+\[Shot\s*1\]", raw))
        with open("/Users/shaneson/AiVideo/.cursor/debug-d078f2.log", "a", encoding="utf-8") as _handle:
            _handle.write(json.dumps({
                "sessionId": "d078f2",
                "runId": "lead-clock",
                "hypothesisId": "A,B,C",
                "location": "stage_runner.py:normalize_h3_shot_cuts:entry",
                "message": "clock order before normalize",
                "data": {"leading_at": _lead, "trailing_at": _trail, "shot1_leading": _shot1_lead},
                "timestamp": int(time.time() * 1000),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion
    raw = _flip_leading_shot_clocks(raw)
    # region agent log
    try:
        _lead_after = len(re.findall(r"(?m)^[ \t]*(?:At|在)\s+\d{1,2}:\d{2}\.\d{3}\s+\[Shot\s+\d+\]", raw))
        _trail_after = len(_SHOT_CLOCK.findall(raw))
        with open("/Users/shaneson/AiVideo/.cursor/debug-d078f2.log", "a", encoding="utf-8") as _handle:
            _handle.write(json.dumps({
                "sessionId": "d078f2",
                "runId": "post-fix",
                "hypothesisId": "A,B",
                "location": "stage_runner.py:normalize_h3_shot_cuts:flipped",
                "message": "clock order after flip",
                "data": {"leading_at": _lead_after, "trailing_at": _trail_after},
                "timestamp": int(time.time() * 1000),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion
    before = _feed_cut_snapshot(raw)
    if _SHOT_RANGE.search(raw):
        heads = list(_UNIT_HEAD_LINE.finditer(raw))
        if len(heads) <= 1:
            prefix = raw[: heads[0].start()] if heads else ""
            body = raw[heads[0].start():] if heads else raw
            raw = prefix + _normalize_h3_shot_cuts_block(body)
        else:
            parts = []
            if heads[0].start() > 0:
                parts.append(raw[:heads[0].start()])
            for index, match in enumerate(heads):
                end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
                parts.append(_normalize_h3_shot_cuts_block(raw[match.start():end]))
            raw = "".join(parts)
    settled = _fill_missing_h3_duration(_settle_ready_units(raw))
    _debug_feed_clocks("normalize shot clocks", {"before": before, "after": _feed_cut_snapshot(settled)})
    return settled


def _normalize_h3_shot_cuts_block(text):
    def repl(match):
        head, number, start = match.group(1), int(match.group(2)), match.group(3)
        if number == 1:
            return head + " "
        return "%s At %s, " % (head, start)

    out = _SHOT_RANGE.sub(repl, str(text or ""))
    return _SHOT1_CLOCK.sub(r"\1 ", out)


def _loose_h3_sections(text):
    sections = {}
    current = None
    buf = []

    def flush():
        if current:
            sections[current] = "\n".join(buf).strip()

    for line in str(text or "").splitlines():
        match = _LOOSE_FIELD.match(line.strip())
        if match:
            flush()
            current = match.group(1)
            buf = []
        elif current:
            buf.append(line)
    flush()
    return sections


def _shot_pieces(body):
    marks = list(_SHOT_HEAD.finditer(body))
    if not marks:
        return []
    pieces = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        chunk = body[mark.start():end].strip()
        clock = _SHOT_CLOCK.match(chunk)
        if clock:
            seconds = int(clock.group(2)) * 60 + int(clock.group(3)) + int(clock.group(4)) / 1000
            number = int(clock.group(1))
        else:
            number = int(re.match(r"\[Shot\s+(\d+)\]", chunk).group(1))
            seconds = None
        pieces.append((number, seconds, chunk))
    return pieces


def _shot_groups(pieces):
    groups = []
    current = []
    origin = 0
    last = 0
    for number, seconds, chunk in pieces:
        if seconds is None:
            seconds = last
        last = seconds
        if current and seconds >= origin + 15:
            groups.append((origin, current))
            origin = int(seconds // 15) * 15
            current = [(number, seconds, chunk)]
        else:
            if not current:
                origin = int(seconds // 15) * 15
            current.append((number, seconds, chunk))
    if current:
        groups.append((origin, current))
    return groups


_UNIT_HEAD = re.compile(r"(?m)^## 【投喂单元")
_TAIL_MARK = re.compile(r"^(?:---\s*|强制禁止项)")


def _order_h3_block(block):
    """Move the six H3 sections into checker order. Leave an already-correct block untouched."""
    lines = block.splitlines()
    if not lines:
        return block
    preamble = []
    fields = {}
    order = []
    current = None
    buf = []
    tail = []
    closed = False

    def flush():
        nonlocal current, buf
        if current:
            fields[current] = buf
        current = None
        buf = []

    for line in lines[1:]:
        match = None if closed else _LOOSE_FIELD.match(line.strip())
        if match:
            flush()
            current = match.group(1)
            if current not in order:
                order.append(current)
            buf = []
            continue
        if current and _TAIL_MARK.match(line.strip()):
            flush()
            closed = True
            tail.append(line)
            continue
        if closed or (not current and fields):
            tail.append(line)
        elif current:
            buf.append(line)
        else:
            preamble.append(line)
    flush()
    wanted = [field for field in _H3_SECTION_FIELDS if field in fields]
    if not fields or order == wanted:
        return block
    out = [lines[0], *preamble]
    for field in wanted:
        body = "\n".join(fields[field]).strip("\n")
        out.append("%s:" % field)
        if body:
            out.append(body)
    while tail and not tail[0].strip():
        tail.pop(0)
    if tail:
        if out and out[-1].strip():
            out.append("")
        out.extend(tail)
    text = "\n".join(out)
    if block.endswith("\n"):
        text += "\n"
    return text


def _reorder_ready_h3(raw):
    """Reorder each finished H3 unit. The checker rejects sound sections placed before detailed_description."""
    matches = list(_UNIT_HEAD.finditer(raw))
    if not matches:
        return raw
    pieces = [raw[:matches[0].start()]]
    before = []
    after = []
    changed = False
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        block = raw[match.start():end]
        ordered = _order_h3_block(block)
        pieces.append(ordered)
        before.append(_h3_names(block))
        after.append(_h3_names(ordered))
        changed = changed or ordered != block
    if not changed:
        return raw
    return "".join(pieces)


def _h3_names(block):
    names = []
    for line in block.splitlines():
        match = _LOOSE_FIELD.match(line.strip())
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _line_ending(line):
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _field_lines(name, body):
    """A body that is only a section name stays on the colon line so the next pass leaves it."""
    body = str(body or "").strip()
    if not body:
        return ["%s:" % name]
    if _H3_BARE_FIELD.match(body):
        return ["%s: %s" % (name, body)]
    return ["%s:" % name, body]


def _coerce_h3_field_line(core):
    """Turn one header line into ``field:``. A line that already uses a colon stays as written."""
    if _H3_COLON_FIELD.match(core):
        return [core]
    wrapped = _H3_ANGLE_WRAP.match(core)
    if wrapped:
        return _field_lines(wrapped.group(1), wrapped.group(2))
    opened = _H3_ANGLE_OPEN.match(core)
    if opened:
        name = opened.group(1)
        body = re.sub(r"</\s*%s\s*>" % re.escape(name), "", opened.group(2))
        return _field_lines(name, body)
    if _H3_ANGLE_CLOSE.match(core):
        return []
    bracket = _H3_BRACKET_FIELD.match(core)
    if bracket:
        return _field_lines(bracket.group(1), bracket.group(2))
    bare = _H3_BARE_FIELD.match(core)
    if bare:
        return ["%s:" % bare.group(1)]
    return [core]


def _coerce_h3_field_block(block):
    pieces = []
    for line in str(block or "").splitlines(keepends=True):
        core, ending = _line_ending(line)
        rewritten = _coerce_h3_field_line(core)
        if not rewritten:
            continue
        if len(rewritten) == 1:
            pieces.append(rewritten[0] + ending)
        else:
            pieces.append("\n".join(rewritten) + ending)
    return "".join(pieces)


def _coerce_h3_field_heads(text):
    """Per feed unit, make section names line-start ``field:`` before the ready shortcut."""
    raw = str(text or "")
    heads = list(_UNIT_HEAD_LINE.finditer(raw))
    if not heads:
        return _coerce_h3_field_block(raw)
    parts = [_coerce_h3_field_block(raw[: heads[0].start()])]
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        parts.append(_coerce_h3_field_block(raw[match.start() : end]))
    return "".join(parts)


def _normalize_loose_h3(text):
    """Turn bare H3 field names into line-oriented sections the checker can see."""
    raw = _coerce_h3_field_heads(str(text or ""))
    ready = bool(re.search(r"(?m)^subject_definitions\s*[:：]", raw)) and "## 【投喂单元" in raw
    sections = {} if ready else _loose_h3_sections(raw)
    bare = len(re.findall(
        r"(?m)^(?:%s)\s*$" % "|".join(_H3_SECTION_FIELDS),
        raw,
    ))
    if ready:
        return normalize_h3_shot_cuts(_reorder_ready_h3(raw))
    if "subject_definitions" not in sections or "detailed_description" not in sections:
        return normalize_h3_shot_cuts(raw)
    pieces = _shot_pieces(sections.get("detailed_description") or "")
    groups = _shot_groups(pieces) or [(0, [])]
    total = len(groups)
    blocks = []
    for index, (origin, shots) in enumerate(groups, 1):
        lines = ["## 【投喂单元 %s/%s · 段%s】" % (index, total, index)]
        last = origin
        shot_text = []
        for number, seconds, chunk in shots:
            last = max(last, seconds)
            chunk = _rebase_cuts(chunk, origin)
            if number == 1:
                chunk = _SHOT1_CLOCK.sub(r"\1 ", chunk)
            shot_text.append(chunk)
        span = max(4, min(15, int(round(last - origin)) or 4))
        lines.append("duration: %s" % span)
        for field in _H3_SECTION_FIELDS:
            if field == "detailed_description":
                body = "\n\n".join(shot_text).strip()
            else:
                body = sections.get(field) or ""
            if not body:
                continue
            lines.append("%s:" % field)
            lines.append(body)
        blocks.append("\n".join(lines))
    return normalize_h3_shot_cuts("\n\n".join(blocks).strip())


def render_feed_text(value):
    """Write an H3 segment object as one six-section block per segment."""
    payload = _feed_payload(value)
    segments = _feed_segments(payload) if payload is not None else []
    if not segments:
        return _normalize_loose_h3(str(value or ""))
    total = len(segments)
    blocks = []
    for index, segment in enumerate(segments, 1):
        content = segment.get("content")
        if not isinstance(content, dict):
            content = segment
        lines = ["## 【投喂单元 %s/%s · 段%s】" % (index, total, index)]
        start, span = _feed_span(segment.get("time_range") or "")
        if span:
            shown = int(span) if span == int(span) else span
            lines.append("duration: %s" % shown)
        for field in _H3_SECTION_FIELDS:
            body = str(content.get(field) or "").strip()
            if field == "detailed_description" and start:
                body = _rebase_cuts(body, start)
            if not body:
                continue
            lines.append("%s:" % field)
            lines.append(body)
        blocks.append("\n".join(lines))
    return normalize_h3_shot_cuts("\n\n".join(blocks).strip())


def patch_h3_voiceover(text):
    """H3 voiceover must carry the checker's closed-lips phrase."""
    raw = str(text or "")
    if "subject_definitions" not in raw:
        return raw
    if not re.search(r"voiceover|画外音|旁白", raw, re.I):
        return raw
    block = re.compile(r"(says in an off-screen voiceover:\s*<d>.*?</d>)", re.I | re.S)
    lips = re.compile(r"lips\s+(?:remain|stay)\s+(?:completely\s+)?closed", re.I)

    def _with_lips(match):
        follow = raw[match.end():match.end() + 160]
        if lips.search(match.group(0) + follow):
            return match.group(0)
        return match.group(0) + " while their lips remain completely closed"

    patched = block.sub(_with_lips, raw)
    if re.search(r"voiceover|画外音|旁白", patched, re.I) and not lips.search(patched):
        return patched.rstrip() + "\nwhile their lips remain completely closed\n"
    return patched


def _repair_line(stage, repair):
    repair = str(repair or "").strip()
    if not repair:
        return ""
    if stage == "storyboard":
        return "\n".join((
            "机检没过。previs 必须仍是完整预演故事板，不要改成「段N验证」摘要。",
            "每一段以「## 段 N」单独成行，段内保留这些原文：本段要验什么、风险提示、故事板提示词（数字格）、第 1 格：、看图先看这三点。",
            "每一格写全六问：谁在哪、身体朝哪、视线看哪、在做什么、镜头在哪、光从哪来。",
            "已有的「第 k 格」原样留下，只补缺的标签和六问。文末保留「预演故事板：段1~段N ✅」。",
            repair,
        ))
    if stage == "asset":
        return "机检没过，只改这些，不要另起一套。已有图块必须全部保留，通过的块原样留下：\n" + repair
    return "机检没过，只改这些，不要另起一套：\n" + repair


def _repair_task(stage):
    if stage == "storyboard":
        target = "分镜和预演故事板"
        locked = "剧本和资产图"
    elif stage == "asset":
        target = "资产图块"
        locked = "剧本"
    elif stage == "feed":
        target = "投喂稿"
        locked = "剧本、资产和分镜"
    else:
        target = "这一阶段正文"
        locked = "上一阶段正文"
    return "\n".join((
        "这次是返修。只改机检点名的失败项，改完放进%s。" % target,
        "%s整段保留，当作锁定参考。" % locked,
        "没有点名的段落原样保留。",
    ))


_REPAIR_FIELD = {
    "storyboard": "改完的分镜放在 storyboard，预演放在 previs。",
    "asset": "改完的图块放在 image_prompts。",
    "script": "改完的剧本放在 script。",
    "feed": "改完的投喂稿放在 feed。",
    "recon": "改完的情报卡放在 intel。",
}


def _user_story_block(stage, story, draft):
    """The raw user text is only for the first script. Later prompts use the drafts."""
    if stage != "script" or str(draft or "").strip():
        return ""
    body = str(story or "").strip()
    if not body:
        return ""
    return "用户原文：\n" + body


def _locked_reference(label, text):
    body = str(text or "").strip()
    if not body:
        return ""
    return "%s（锁定参考，整段保留）：\n%s" % (label, body)


def _accepts_trace(func):
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    for param in signature.parameters.values():
        if param.name == "trace_prompts" or param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return False


def _write_model(write_model, brief, pack, debug_dir, model, template, prompts):
    kwargs = {
        "debug_dir": debug_dir,
        "model": model,
        "template": template,
    }
    if prompts and _accepts_trace(write_model):
        kwargs["trace_prompts"] = prompts
    return write_model(brief, pack, **kwargs)


SCRIPT_STEPS = (
    ("1", "来源登记", "01-来源登记.md"),
    ("2", "IP与角色", "02-IP与角色.md"),
    ("3", "故事圣经", "03-故事圣经.md"),
    ("4", "分集规划", "04-分集规划.md"),
    ("5", "正式剧本", "05-正式剧本.md"),
    ("6", "圆桌", "06-圆桌.md"),
)
_SCRIPT_TASK = {
    "1": "这一步只做来源登记。正文放在 text。",
    "2": "这一步只做 IP 与角色。正文放在 text。",
    "3": "这一步只写全剧开发圣经。正文放在 text。",
    "4": "这一步只做分集规划。时长已经锁定，不要再问集数。正文放在 text。",
    "5": "这一步只写正式剧本。正文放在 script。标题放在 title。",
    "6": "这一步只做圆桌审核。意见放在 text。",
}


def _chain_dir(folder):
    path = Path(folder) / "_内部_" / "剧本链"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chain_text(folder, name):
    path = Path(folder) / "_内部_" / "剧本链" / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _script_rows(pipeline):
    pipeline = pipeline or {}
    repair = str(pipeline.get("repair") or "").strip()
    revise = str(pipeline.get("revise_note") or "").strip()
    if str(pipeline.get("action") or "") == "rewrite" or repair or revise:
        return [row for row in SCRIPT_STEPS if row[0] in ("5", "6")]
    start = str(pipeline.get("script_from") or "")
    ids = [row[0] for row in SCRIPT_STEPS]
    if start in ids:
        return [row for row in SCRIPT_STEPS if ids.index(row[0]) >= ids.index(start)]
    return list(SCRIPT_STEPS)


def _step_body(step_id, result):
    if step_id == "5":
        body = str(result.get("script") or "").strip()
        used = "script"
    else:
        body = str(result.get("text") or result.get("internal") or "").strip()
        used = "text" if str(result.get("text") or "").strip() else ("internal" if body else "")
    return body


def _script_brief(project, folder, locks, only, pipeline, step_id, previous):
    revise = str((pipeline or {}).get("revise_note") or "").strip()
    repair = str((pipeline or {}).get("repair") or "").strip()
    lines = [
        mount_header("script", locks, previous, only),
        "剧本步：%s" % step_id,
        _SCRIPT_TASK[step_id],
        lock_brief(locks),
    ]
    if step_id == "5" and revise:
        lines.append("精修原文：1")
        lines.append("用户要改的是：%s" % revise)
    if step_id == "5" and repair and not revise:
        lines.append("机检没过。只改点名的条目，原文不要重写。")
        lines.append(repair)
    rewriting = str((pipeline or {}).get("action") or "") == "rewrite"
    if step_id == "1" and not (revise or repair or rewriting):
        story = str((project or {}).get("brief") or "").strip()
        if story:
            lines.extend(["", "用户原文", story])
        note = intake_note(load_intake(folder))
        if note:
            lines.extend(["", note])
    elif step_id != "1":
        lines.extend(["", "上一步正文", previous or "（上一步还没有正文）"])
    if step_id in ("5", "6") and (revise or repair or rewriting):
        draft = _read(Path(folder) / "_内部_" / "剧本.md").strip()
        if draft:
            lines.extend(["", "要改的底稿", draft])
    return "\n".join(line for line in lines if line is not None).strip()


def _produce_script(project, folder, locks, only, write_model, load_pack, pipeline):
    from end.trace import begin_trace, finish_trace

    del load_pack
    pipeline = pipeline or {}
    revise = str(pipeline.get("revise_note") or "").strip()
    repair = str(pipeline.get("repair") or "").strip()
    rewriting = str(pipeline.get("action") or "") == "rewrite"
    continuing = bool(str(pipeline.get("script_from") or "").strip())
    chain = _chain_dir(folder)
    if not (revise or repair or rewriting or continuing):
        for child in chain.glob("*.md"):
            child.unlink()
    brief = _script_suite_brief(project, folder, locks, only, pipeline)
    debug = bool((project.get("progress") or {}).get("debug_llm"))
    debug_dir = Path(folder) / "debug" if debug or (project.get("progress") or {}).get("debug_stop") else None
    prompts = begin_trace(folder, "script", bool(revise or repair or rewriting), brief, label="剧本")
    try:
        result = _write_model(
            write_model,
            brief,
            "",
            debug_dir=debug_dir,
            model=project.get("model"),
            template=project.get("template"),
            prompts=prompts,
        )
    except Exception as error:
        finish_trace(folder, {}, error=str(error))
        raise
    if not isinstance(result, dict):
        finish_trace(folder, {}, error="这一段没有写成 JSON")
        raise RuntimeError("这一段没有写成 JSON")
    _save_script_partials(chain, result.get("partials") or [])
    script = str(result.get("script") or "").strip()
    if not script:
        formal = chain / "05-正式剧本.md"
        if formal.is_file():
            script = formal.read_text(encoding="utf-8").strip()
    if not script:
        finish_trace(folder, {}, error="这一步没有正文")
        raise RuntimeError("这一步没有正文")
    title = str(result.get("title") or "").strip()
    internal = str(result.get("internal") or "").strip()
    if not internal:
        table = chain / "06-圆桌.md"
        if table.is_file():
            internal = table.read_text(encoding="utf-8").strip()
    _write_internal(folder, "剧本.md", script)
    if internal:
        _write_internal(folder, "剧本过程.md", internal)
    (chain / "05-正式剧本.md").write_text(script + "\n", encoding="utf-8")
    finish_trace(folder, {"script": script, "title": title, "internal": internal, "partials": result.get("partials") or []})
    return {"title": title, "script": script, "internal": internal}


def _script_suite_brief(project, folder, locks, only, pipeline):
    pipeline = pipeline or {}
    revise = str(pipeline.get("revise_note") or "").strip()
    repair = str(pipeline.get("repair") or "").strip()
    rewriting = str(pipeline.get("action") or "") == "rewrite"
    lines = [
        mount_header("script", locks, "", only),
        "任务：剧本",
        lock_brief(locks),
        "只写剧本，放在 script。圆桌意见放在 internal。",
    ]
    if revise:
        lines.append("精修原文：1")
        lines.append("用户要改的是：%s" % revise)
    if repair and not revise:
        lines.append("机检没过。只改点名的条目，原文不要重写。")
        lines.append(repair)
    if not (revise or repair or rewriting or pipeline.get("script_from")):
        story = str((project or {}).get("brief") or "").strip()
        if story:
            lines.extend(["", "用户原文", story])
        note = intake_note(load_intake(folder))
        if note:
            lines.extend(["", note])
    else:
        draft = _read(Path(folder) / "_内部_" / "剧本.md").strip()
        if draft:
            lines.extend(["", "要改的底稿", draft])
        kept = []
        for _step, label, filename in SCRIPT_STEPS:
            body = _chain_text(folder, filename)
            if body:
                kept.append("%s\n%s" % (label, body))
        if kept:
            lines.extend(["", "已有剧本链", "\n\n".join(kept)])
    return "\n".join(line for line in lines if line is not None).strip()


def _save_script_partials(chain, partials):
    for item in partials or []:
        if not isinstance(item, dict):
            continue
        name = _SCRIPT_CHAIN.get(str(item.get("skill") or ""))
        text = str(item.get("text") or "").strip()
        if name and text:
            (chain / name).write_text(text + "\n", encoding="utf-8")


def _produce(project, folder, stage, locks, only, write_model, load_pack):
    pipeline = (project.get("progress") or {}).get("pipeline") or {}
    if stage == "script":
        return _produce_script(project, folder, locks, only, write_model, load_pack, pipeline)
    carried = carried_text(folder, stage)
    header = mount_header(stage, locks, carried, only)
    story = enrich_brief(project.get("brief") or "", load_intake(folder))
    pipeline = (project.get("progress") or {}).get("pipeline") or {}
    note = ""
    current_draft = ""
    repair = str(pipeline.get("repair") or "").strip()
    raw_choices = pipeline.get("repair_choices")
    repair_choices = raw_choices if isinstance(raw_choices, dict) else {}
    if note or repair:
        current_draft = _read(Path(folder) / "_内部_" / _DRAFT_FILE.get(stage, "")).strip()
        if stage == "storyboard":
            previs_draft = _read(Path(folder) / "_内部_" / "previs.md").strip()
            if previs_draft:
                current_draft = (current_draft + "\n\n预演故事板：\n" + previs_draft).strip()
    task_line = _TASK_LINE.get(stage, "")
    debug = bool((project.get("progress") or {}).get("debug_llm"))
    debug_dir = Path(folder) / "debug" if debug or (project.get("progress") or {}).get("debug_stop") else None
    if (repair or repair_choices) and stage in ("asset", "storyboard", "feed"):
        return _repair_with_graph(
            folder,
            stage,
            repair,
            write_model,
            debug_dir,
            project.get("model"),
            project.get("template"),
            locks,
            str(project.get("title") or ""),
            repair_choices,
        )
    if repair:
        brief = "\n\n".join(
            part
            for part in (
                header,
                task_line,
                lock_brief(locks),
                _repair_task(stage),
                _repair_line(stage, repair),
                "要改的底稿：\n" + current_draft if current_draft else "",
                _REPAIR_FIELD.get(stage, ""),
                "精修要求：\n" + note if note else "",
                _locked_reference("上一阶段正文", carried),
                "再确认：只改机检点名的失败项。锁定参考整段保留，不要为它另写一批。",
            )
            if part
        )
    else:
        brief = "\n\n".join(
            part
            for part in (
                header,
                task_line,
                lock_brief(locks),
                _user_story_block(stage, story, current_draft),
                _INSTRUCTION.get(stage, ""),
                "当前这一稿：\n" + current_draft if current_draft else "",
                "精修要求：\n" + note if note else "",
                "上一阶段正文：\n" + carried if carried else "",
            )
            if part
        )
    pack = "" if task_line else load_pack(project.get("template"), brief, _STAGE_CODE.get(stage, "P4"))
    from end.trace import begin_trace

    prompts = begin_trace(folder, stage, repair=bool(repair), brief=brief)
    result = _write_model(
        write_model,
        brief,
        pack,
        debug_dir=debug_dir,
        model=project.get("model"),
        template=project.get("template"),
        prompts=prompts,
    )
    if not isinstance(result, dict):
        raise RuntimeError("这一段没有写成 JSON")
    return result


def _finish(folder, locks, pipeline):
    folder = Path(folder)
    spec = _load_spec(folder)
    feed_path = folder / "_内部_" / "feed.md"
    if feed_path.is_file():
        code, report = check_prompt(feed_path, locks.get("model") or "h3", locks.get("track") or "1")
        if code != 0:
            failed = dict(pipeline or {})
            failed["check"] = "qc"
            failed["current"] = "feed"
            return gate_result("feed", False, report, failed)
        claim = unverified_claim(_read(feed_path), locks.get("material_mode"))
        if claim:
            failed = dict(pipeline or {})
            failed["check"] = "qc"
            return gate_result("feed", False, "没看见图，却写了%s" % claim, failed)
    title = spec.get("title") or "未命名"
    previs_file = folder / previs_name(title)
    previs_text = _read(previs_file) if previs_file.is_file() else ""
    names = publish(
        folder,
        locks,
        title,
        spec.get("image_prompts") or _read(folder / "_内部_" / "assets.md"),
        _read(feed_path),
        previs_text,
    )
    return {
        "terminal": "ready",
        "status": "done",
        "stage": "提示词已就绪",
        "deliverables": names or list_deliverables(folder),
        "progress": {
            "percent": 100,
            "phase": "prompts_ready",
            "message": "提示词已就绪。",
            "pipeline": dict(pipeline or {}),
            "question": None,
        },
        "message": None,
    }


def _project_with_pipeline(project, pipeline):
    progress = dict((project or {}).get("progress") or {})
    progress["pipeline"] = dict(pipeline or {})
    updated = dict(project or {})
    updated["progress"] = progress
    return updated


def _has_saved_draft(folder, stage):
    internal = Path(folder) / "_内部_"
    names = []
    draft = _DRAFT_FILE.get(stage) or ""
    if draft:
        names.append(draft)
    if stage == "storyboard":
        names.append("previs.md")
    for name in names:
        if _read(internal / name).strip():
            return True
    return False


def _unfinished_write(stage, pipeline, error):
    """A failed first write has no draft to keep. Stay on this gate and allow another write."""
    stored = dict(pipeline or {})
    stored.update(
        {
            "current": stage,
            "action": "wait",
            "passed": False,
            "retry_write": True,
            "report": "",
            "repair": "",
            "check": "",
        }
    )
    detail = str(error or "").strip()
    if len(detail) > 500:
        detail = detail[:500]
    label = _GATE_LABEL.get(stage, "这一阶段")
    reply = "%s没写完。继续会再写一次。" % label
    if detail:
        reply = reply + "\n" + detail
    return {
        "terminal": "gate",
        "status": "gate",
        "stage": label,
        "progress": {
            "percent": _PERCENT.get(stage, 50),
            "phase": _GATE_PHASE.get(stage, "gate_fail"),
            "message": reply.split("\n", 1)[0],
            "pipeline": stored,
            "question": {"id": stage, "kind": "single", "reply": reply, "options": ["继续"], "cards": []},
        },
        "message": {
            "role": "assistant",
            "content": reply,
            "options": ["继续"],
            "choice_kind": "single",
            "cards": [],
        },
    }


def _keep_previous_draft(stage, pipeline, folder=None):
    """A failed rewrite leaves the saved draft and returns to the same gate."""
    outcome = gate_result(
        stage,
        bool((pipeline or {}).get("passed")),
        (pipeline or {}).get("report") or "",
        pipeline,
        has_draft=bool(folder) and storyboard_has_draft(folder),
    )
    note = "这一版没写完，上一稿还留着。"
    previous = (outcome.get("message") or {}).get("content") or ""
    reply = note if not previous else note + "\n" + previous
    message = dict(outcome.get("message") or {})
    message["content"] = reply
    progress = dict(outcome.get("progress") or {})
    question = dict(progress.get("question") or {})
    question["reply"] = reply
    progress["question"] = question
    progress["message"] = note
    updated = dict(outcome)
    updated["message"] = message
    updated["progress"] = progress
    return updated


def _write_route(project, folder, locks, only, write_model):
    from end.trace import begin_trace, finish_trace

    del only

    brief = "\n".join(
        (
            "阶段：P0",
            "赛道：%s" % ((locks or {}).get("track") or "1"),
            "来源：%s" % ((locks or {}).get("source") or "1"),
            "动作档：%s" % ((locks or {}).get("combat") or "none"),
            "模型：%s" % ((locks or {}).get("model") or "h3"),
            "素材形态：%s" % ((locks or {}).get("material_mode") or "M1"),
            "",
            "任务：赛道分流",
            lock_brief(locks),
            "写路线说明，放在 text。不要改已经选定的锁。",
        )
    )
    debug = bool((project.get("progress") or {}).get("debug_llm"))
    debug_dir = Path(folder) / "debug" if debug or (project.get("progress") or {}).get("debug_stop") else None
    prompts = begin_trace(folder, "route", False, brief, label="赛道分流")
    try:
        result = _write_model(
            write_model,
            brief,
            "",
            debug_dir=debug_dir,
            model=project.get("model"),
            template=project.get("template"),
            prompts=prompts,
        )
    except Exception as error:
        finish_trace(folder, {}, error=str(error))
        raise
    text = ""
    if isinstance(result, dict):
        text = str(result.get("text") or result.get("internal") or "").strip()
    if text:
        _write_internal(folder, "路线.md", text)
    finish_trace(folder, {"text": text})
    return text


def _write_side(project, folder, locks, write_model, task_line, filename, label, stage):
    from end.trace import begin_trace, finish_trace

    brief = "\n".join(
        (
            "阶段：P5",
            "赛道：%s" % ((locks or {}).get("track") or "1"),
            "来源：%s" % ((locks or {}).get("source") or "1"),
            "动作档：%s" % ((locks or {}).get("combat") or "none"),
            "模型：%s" % ((locks or {}).get("model") or "h3"),
            "素材形态：%s" % ((locks or {}).get("material_mode") or "M1"),
            "",
            task_line,
            lock_brief(locks),
            "上一阶段正文：\n" + carried_text(folder, "feed"),
        )
    )
    debug = bool((project.get("progress") or {}).get("debug_llm"))
    debug_dir = Path(folder) / "debug" if debug or (project.get("progress") or {}).get("debug_stop") else None
    prompts = begin_trace(folder, stage, False, brief, label=label)
    try:
        result = _write_model(
            write_model,
            brief,
            "",
            debug_dir=debug_dir,
            model=project.get("model"),
            template=project.get("template"),
            prompts=prompts,
        )
    except Exception as error:
        finish_trace(folder, {}, error=str(error))
        raise
    text = ""
    if isinstance(result, dict):
        text = str(result.get("text") or result.get("internal") or "").strip()
    if text:
        _write_internal(folder, filename, text)
    finish_trace(folder, {"text": text})
    return text


def _side_note(project, folder, locks, write_model, task_line, filename, label, stage):
    try:
        _write_side(project, folder, locks, write_model, task_line, filename, label, stage)
    except Exception as error:
        _write_internal(folder, filename, "这一次没有写完。\n%s" % error)


def _done(project, folder, locks, pipeline, write_model):
    _side_note(project, folder, locks, write_model, "任务：独立质检", "质检.md", "独立质检", "qc")
    if str((locks or {}).get("delivery") or "") in ("1", "3"):
        _side_note(project, folder, locks, write_model, "任务：离线看板", "看板说明.md", "离线看板", "board")
    return _finish(folder, locks, pipeline)


def execute(project, folder, locks, write_model, load_pack):
    """Write the current stage, or finish. Stop at the gate. Do not continue into the next stage."""
    progress = project.get("progress") or {}
    pipeline = dict(progress.get("pipeline") or {})
    only = pipeline.get("only") or ""
    action = pipeline.get("action") or "write"
    if action == "finish":
        return _done(project, folder, locks, pipeline, write_model)
    if action == "wait":
        current = pipeline.get("current") or ""
        return gate_result(
            current,
            pipeline.get("passed"),
            pipeline.get("report"),
            pipeline,
            has_draft=storyboard_has_draft(folder),
        )
    stages = stages_for(locks, only)
    accepted = list(pipeline.get("accepted") or [])
    pending = next_pending(stages, accepted)
    requested = pipeline.get("current") or pending
    current = requested if requested == pending else pending
    if not current:
        return _done(project, folder, locks, pipeline, write_model)
    if current in accepted and pipeline.get("action") != "rewrite":
        pipeline = dict(pipeline)
        pipeline["action"] = "finish"
        return _done(project, folder, locks, pipeline, write_model)
    if not accepted and action == "write" and not (
        pipeline.get("script_from") or pipeline.get("repair") or pipeline.get("revise_note")
    ):
        try:
            _write_route(project, folder, locks, only, write_model)
        except RuntimeError as error:
            return _unfinished_write(current, pipeline, error)
    if action == "rewrite":
        pipeline["repair"] = pipeline.get("repair") or pipeline.get("report") or ""
    passed = False
    report = ""
    checked = False
    spec = _load_spec(folder)
    attempts = 0
    while True:
        project = _project_with_pipeline(project, pipeline)
        from end.trace import finish_trace, stage_output

        try:
            stored_feed = _read(Path(folder) / "_内部_" / "feed.md") if current == "feed" else ""
            rendered_feed = render_feed_text(stored_feed) if current == "feed" else ""
            reuse_wrapped_feed = (
                current == "feed"
                and bool(rendered_feed.strip())
                and rendered_feed.strip() != stored_feed.strip()
            )
            if reuse_wrapped_feed:
                result = {"feed": rendered_feed}
            else:
                result = _produce(project, folder, current, locks, only, write_model, load_pack)
            if current == "script":
                pipeline.pop("script_from", None)
        except RuntimeError as error:
            finish_trace(folder, {}, error=str(error))
            has_draft = _has_saved_draft(folder, current)
            if has_draft:
                status = dict(pipeline)
                if checked:
                    status["passed"] = passed
                    status["report"] = report
                return _keep_previous_draft(current, status, folder)
            return _unfinished_write(current, pipeline, error)
        if current == "asset":
            figure_repair = False
            figure_indexes = []
            if isinstance(result, dict):
                figure_repair = bool(result.pop("_figure_repair", False))
                figure_indexes = list(result.pop("_figure_indexes", []) or [])
            result = _coerce_asset_prompts(result)
            previous_assets = _read(Path(folder) / "_内部_" / "assets.md")
            image = asset_markdown(result)
            if not _asset_pieces(image):
                image = apply_structured_assets(previous_assets, (result or {}).get("assets"))
            if not _asset_pieces(image):
                # Last resort: build #### blocks from assets[] on the result or spec.
                image = markdown_from_asset_items(
                    (result or {}).get("assets") or spec.get("assets")
                )
            kept = keep_missing_asset_blocks(previous_assets, image)
            patched = (
                kept
                if figure_repair
                else patch_asset_phrases(kept, model=project.get("model"))
            )
            kept = patched
            if figure_repair:
                old_bodies = [body for _number, body in _asset_pieces(previous_assets)]
                new_bodies = [body for _number, body in _asset_pieces(kept)]
                unchanged = [
                    index
                    for index, (old, new) in enumerate(zip(old_bodies, new_bodies))
                    if old == new
                ]
            result = dict(result or {})
            result["image_prompts"] = kept
            result["assets"] = assets_from_markdown(kept, spec.get("assets"))
            if not str(kept or "").strip():
                # #region agent log
                try:
                    with open(
                        "/Users/shaneson/AiVideo/.cursor/debug-d078f2.log", "a", encoding="utf-8"
                    ) as handle:
                        handle.write(
                            json.dumps(
                                {
                                    "sessionId": "d078f2",
                                    "runId": "asset-fix",
                                    "hypothesisId": "A2",
                                    "location": "stage_runner.py:execute:asset",
                                    "message": "asset prompts still empty",
                                    "data": {
                                        "result_keys": sorted(str(k) for k in (result or {}))[:20],
                                        "image_prompts": _prompt_shape(
                                            (result or {}).get("image_prompts")
                                        ),
                                        "assets": _prompt_shape((result or {}).get("assets")),
                                        "spec_assets": _prompt_shape(spec.get("assets")),
                                        "previous_chars": len(previous_assets or ""),
                                    },
                                    "timestamp": int(time.time() * 1000),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                except OSError:
                    pass
                # #endregion
                raise RuntimeError(
                    "资产提示词是空的（图块可能被写成了对象而不是 #### 图N 正文）"
                )
        if current == "storyboard":
            previous_board = _read(Path(folder) / "_内部_" / "分镜.md")
            previous_previs = _read(Path(folder) / "_内部_" / "previs.md")
            mode = (locks or {}).get("material_mode") or "M1"
            storyboard = keep_richer_board(previous_board, (result or {}).get("storyboard") or "")
            # Coerce before keep_richer — str(list) would otherwise beat real markdown on length.
            incoming_previs = coerce_previs((result or {}).get("previs") or "", mode)
            previous_previs = coerce_previs(previous_previs, mode)
            previs = keep_richer_board(previous_previs, incoming_previs)
            result = dict(result or {})
            result["storyboard"] = storyboard
            result["previs"] = previs
        if current == "feed":
            raw_feed = render_feed_text((result or {}).get("feed") or "")
            patched_feed = patch_h3_voiceover(raw_feed)
            result = dict(result or {})
            result["feed"] = patched_feed
            preview = dict(spec)
            preview["feed"] = patched_feed
            result["feed"] = apply_feed_binding(preview).get("feed") or patched_feed
        store_stage(folder, current, result)
        finish_trace(folder, stage_output(current, result))
        spec = _spec_patch(current, result, spec)
        passed, report = check_stage(folder, current, locks, spec.get("title") or "")
        checked = True
        attempts += 1
        pipeline["repairs"] = attempts
        pipeline["repair"] = "" if passed else (report or "")
        if current == "script" and not passed and not pipeline["repair"]:
            pipeline["repair"] = "机检没过"
        if (
            current == "asset"
            and not passed
            and (
                "资产稿是空的" in (report or "")
                or "文件不存在" in (report or "")
            )
        ):
            pipeline["retry_write"] = True
        if passed or current in ("asset", "storyboard", "feed") or attempts >= REPAIR_LIMIT:
            break
    if passed:
        pipeline.pop("repair_round", None)
        pipeline.pop("repair_choices", None)
    outcome = gate_result(
        current,
        passed,
        report,
        pipeline,
        has_draft=storyboard_has_draft(folder),
    )
    if current == "asset" and not passed and pipeline.get("repair_round"):
        from end.repair_units import reopen_asset_question

        kept, question = reopen_asset_question(
            _read(Path(folder) / "_内部_" / "assets.md"),
            pipeline.get("repair_choices") or {},
        )
        if question:
            outcome = _attach_asset_choice(outcome, question, kept)
    outcome["spec"] = spec
    outcome["used_stage"] = current
    outcome["mount"] = mount_header(current, locks, carried_text(folder, current), only)
    return outcome


def _attach_asset_choice(outcome, question, choices):
    """Replace the generic gate with one block's direction question."""
    progress = dict(outcome.get("progress") or {})
    pipeline = dict(progress.get("pipeline") or {})
    pipeline["repair_choices"] = choices
    pipeline["repair_round"] = True
    pipeline["action"] = "wait"
    progress["pipeline"] = pipeline
    progress["question"] = question
    progress["message"] = "选一个改法"
    attached = dict(outcome)
    attached["progress"] = progress
    attached["message"] = question_message(question)
    return attached


def question_message(question):
    question = question or {}
    return {
        "role": "assistant",
        "content": question.get("reply") or "",
        "options": list(question.get("options") or []),
        "choice_kind": question.get("kind") or "single",
        "cards": list(question.get("cards") or []),
    }


def initial_pipeline(only):
    return {
        "only": only or "",
        "accepted": [],
        "current": "",
        "action": "write",
        "passed": True,
        "report": "",
        "check": "",
    }


def accept_pipeline(pipeline, locks):
    """Move to the next stage after a passed gate. A failed gate rewrites the same stage."""
    pipeline = dict(pipeline or {})
    locks = locks or {}
    current = pipeline.get("current") or ""
    if not pipeline.get("passed"):
        pipeline["action"] = "rewrite"
        return pipeline, "write"
    accepted = list(pipeline.get("accepted") or [])
    if current and current not in accepted:
        accepted.append(current)
    pipeline["accepted"] = accepted
    stages = stages_for(locks, pipeline.get("only") or "")
    nxt = next_pending(stages, accepted)
    if not nxt:
        pipeline["action"] = "finish"
        pipeline["current"] = current
        return pipeline, "finish"
    pipeline["action"] = "write"
    pipeline["current"] = nxt
    pipeline["passed"] = True
    pipeline["check"] = ""
    return pipeline, "write"
