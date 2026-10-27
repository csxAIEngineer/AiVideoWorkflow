"""One stage at a time. The writer only sees the files resolve_files returns for that stage."""

import inspect
import json
import re
import time
from pathlib import Path

from end.board_shots import apply_storyboard_shots, shots_are_placeholder
from end.delivery import internal_dir, list_deliverables, previs_name, publish
from end.gates import check_asset_blocks, check_feed, check_p45, check_prompt, unverified_claim
from end.intake import enrich_brief, intake_note, load_intake
from end.locks import lock_brief

_ASSET_HEAD = re.compile(r"^(#{2,6})\s*\*{0,2}\s*图\s*(\d+)\b", re.M)


def _agent_log(hypothesis, message, data):
    # region agent log
    try:
        payload = {
            "sessionId": "9920cc",
            "runId": "post-fix",
            "hypothesisId": hypothesis,
            "location": "end/stage_runner.py",
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-9920cc.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


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


def _figure_log(message, data):
    # region agent log
    try:
        payload = {
            "sessionId": "ee5fa7",
            "runId": "post-fix",
            "hypothesisId": "H-fig",
            "location": "end/stage_runner.py",
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


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
    units = units_for(stage, repair, asset=asset, previs=previs, feed=feed)
    if not units:
        _figure_log("repair skipped", {"stage": stage, "units": 0})
        _passed, fresh = check_stage(folder, stage, locks or {}, title)
        units = units_for(stage, fresh, asset=asset, previs=previs, feed=feed)
        # region agent log
        _figure_log(
            "repair recheck",
            {
                "stage": stage,
                "storedLen": len(repair or ""),
                "freshLen": len(fresh or ""),
                "summary": sum(
                    1 for line in (fresh or "").splitlines() if line.startswith("[x]") or line.startswith("[+]")
                ),
                "units": len(units),
            },
        )
        # endregion
        repair = fresh or repair
    chosen = dict(choices) if isinstance(choices, dict) else {}
    if stage == "asset" and chosen:
        _passed, fresh = check_stage(folder, stage, locks or {}, title)
        units = units_for(stage, fresh, asset=asset)
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
    _figure_log(
        "repair brief",
        {
            "stage": stage,
            "units": [unit.get("id") for unit in units],
            "briefLen": len(brief),
            "hasSuiteTask": "任务：资产锁定" in brief or "任务：分镜" in brief or "任务：提示词封装" in brief,
        },
    )
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
    _figure_log(
        "repair splice",
        {"stage": stage, "units": len(units), "replies": len(repairs)},
    )
    return applied


def _saved_stage(stage, asset, previs, storyboard, feed):
    if stage == "asset":
        return {"image_prompts": asset, "_figure_repair": True, "_figure_indexes": []}
    if stage == "storyboard":
        return {"storyboard": storyboard, "previs": previs}
    if stage == "feed":
        return {"feed": feed}
    return {}


def _flatten_prompt_text(value):
    """Turn a string, a list of prompts, or a nested object into one markdown body."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_flatten_prompt_text(item) for item in value]
        return "\n\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("image_prompts", "prompt", "text", "markdown", "content"):
            if value.get(key):
                found = _flatten_prompt_text(value[key])
                if found:
                    return found
        return ""
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
    """Keep 图 blocks when the model puts them in a list or in the specialist notes."""
    if not isinstance(result, dict):
        return result
    raw = result.get("image_prompts")
    flat = _flatten_prompt_text(raw)
    notes = []
    for item in result.get("partials") or []:
        notes.append(item.get("text") if isinstance(item, dict) else item)
    partial_flat = _flatten_prompt_text(notes)
    field_blocks = len(_asset_pieces(flat))
    partial_blocks = len(_asset_pieces(partial_flat))
    chosen = flat
    source = "field"
    if field_blocks == 0 and partial_blocks:
        chosen = partial_flat
        source = "partials"
    chosen_blocks = len(_asset_pieces(chosen))
    # region agent log
    try:
        payload = {
            "sessionId": "ee5fa7",
            "runId": "post-fix",
            "hypothesisId": "A",
            "location": "end/stage_runner.py:_coerce_asset_prompts",
            "message": "asset prompt shape",
            "data": {
                "shape": _prompt_shape(raw),
                "fieldBlocks": field_blocks,
                "partialBlocks": partial_blocks,
                "source": source,
                "chosenBlocks": chosen_blocks,
            },
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion
    if chosen_blocks and chosen != raw:
        result = dict(result)
        result["image_prompts"] = chosen
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


def _patch_asset_block(body):
    text = str(body or "")
    heading = text.splitlines()[0] if text else ""
    is_prop = "道具设定图" in text
    is_role = (not is_prop) and ("角色" in heading) and re.search(r"全图共\s*4\s*格", text)
    if is_role and not _VOICE_MARK.search(text):
        if "音色速览" in text:
            text = text.replace("音色速览", "≤8 词音色速览", 1)
        else:
            text += "\n【≤8 词音色速览标签】沿用本块已写的音色描述。\n"
    if is_role and "五官 10 项锁定" not in text and "五官10项锁定" not in text:
        text += "\n【五官 10 项锁定】面部结构以本块已写的头部与脸部描述为准。\n"
    if is_role and not any(token in text.lower() for token in _SECOND_FACE):
        text += "\ndifferent face, second face\n"
    if is_prop and not _HAND_MARK.search(text):
        text += "\n禁止手持，不出现手。\n"
    return text


def patch_asset_phrases(text):
    """Add the checker's required phrases without dropping a block."""
    pieces = _asset_pieces(text)
    if not pieces:
        return text
    return "\n\n".join(_patch_asset_block(body).strip() for _number, body in pieces) + "\n"


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


def _asset_item_log(result):
    # region agent log
    items = result.get("assets") if isinstance(result, dict) else None
    rows = []
    if isinstance(items, list):
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            prompt = str(item.get("prompt") or "")
            rows.append(
                {
                    "title": str(item.get("title") or "")[:40],
                    "kind": str(item.get("subject_kind") or ""),
                    "len": len(prompt),
                    "blocks": len(_asset_pieces(prompt)),
                    "head": prompt[:60],
                    "hands": ("禁止手持" in prompt or "不得手持" in prompt or "不出现手" in prompt),
                    "voice": ("8 词" in prompt or "8词" in prompt),
                    "face": ("五官 10 项锁定" in prompt or "五官10项锁定" in prompt),
                }
            )
    _agent_log("F", "asset items", {"rows": rows})
    # endregion


def _write_field_log(result):
    # region agent log
    if not isinstance(result, dict):
        _agent_log("E", "write fields", {"type": type(result).__name__})
        return
    fields = {key: value for key, value in result.items() if isinstance(value, str)}
    assets = result.get("assets")
    first = assets[0] if isinstance(assets, list) and assets and isinstance(assets[0], dict) else {}
    _agent_log(
        "E",
        "write fields",
        {
            "keys": sorted(str(key) for key in result.keys()),
            "strLens": {key: len(value) for key, value in fields.items()},
            "blockCounts": {key: len(_asset_pieces(value)) for key, value in fields.items()},
            "imageHead": (fields.get("image_prompts") or "")[:80],
            "assetsLen": len(assets) if isinstance(assets, list) else 0,
            "assetItemKeys": sorted(str(key) for key in first.keys()),
            "assetPromptLen": len(str(first.get("prompt") or "")),
        },
    )
    # endregion


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
        "预演故事板放在 previs，不要写成「段N验证……」摘要。照这个形状写满，标签原文保留，括号里必须是数字：",
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
        "Seedance 用 ## 【投喂单元 N/M】标题，下面写七段。",
        "模型是 h3 时，每一段改成六段正文。字段名必须带冒号并且单独成行：subject_definitions:、summary:、retention_analysis:、detailed_description:、overall_soundscape:、non_diegetic_music:。",
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
        previs = str(result.get("previs") or "").strip()
        if previs:
            _write_internal(folder, "previs.md", previs)
    elif stage == "feed":
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
            merged["previs"] = result.get("previs")
        merged = apply_storyboard_shots(merged)
    elif stage == "feed":
        if result.get("feed"):
            merged["feed"] = result.get("feed")
        incoming = result.get("shots")
        if isinstance(incoming, list) and incoming and not shots_are_placeholder(incoming):
            merged["shots"] = incoming
        merged = apply_storyboard_shots(merged)
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
            # region agent log
            _agent_log(
                "A",
                "storyboard check inputs",
                {
                    "previsLen": len(previs),
                    "storyboardLen": len(text),
                    "previsHeadings": len(re.findall(r"(?m)^#{1,6}\s*\**\s*段\s*\d+", previs)),
                    "storyboardHeadings": len(re.findall(r"(?m)^#{1,6}\s*\**\s*段\s*\d+", text)),
                    "previsInline": len(re.findall(r"段\s*\d+", previs)),
                    "storyboardInline": len(re.findall(r"段\s*\d+", text)),
                    "repairDraft": _DRAFT_FILE.get("storyboard"),
                    "instructionHasHeading": "## 段" in _INSTRUCTION.get("storyboard", ""),
                },
            )
            # endregion
            target = folder / previs_name(title or _load_spec(folder).get("title") or "未命名")
            body = previs_check_text(text, previs, mode)
            # region agent log
            _agent_log(
                "B",
                "storyboard check body",
                {
                    "headings": len(_HEADING_SEGMENT.findall(body)),
                    "usesStoryboard": "段 1" in body or "## 段 1" in body,
                },
            )
            # endregion
            target.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
            code, report = check_p45(target, material_mode=mode)
            # region agent log
            try:
                with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "sessionId": "ee5fa7",
                                "runId": "pre",
                                "hypothesisId": "D",
                                "location": "end/stage_runner.py:check_stage",
                                "message": "storyboard six-question check",
                                "data": {
                                    "passed": code == 0,
                                    "p453": len([line for line in (report or "").splitlines() if "P453" in line and "FAIL" in line]),
                                    "previsRank": list(_board_rank(previs)),
                                    "storyboardRank": list(_board_rank(text)),
                                    "selectedChars": len(body),
                                    "selectedIsPrevis": previs.strip() in body,
                                },
                                "timestamp": int(time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError:
                pass
            # endregion
            if code != 0:
                if target.is_file():
                    _write_internal(folder, target.name, target.read_text(encoding="utf-8"))
                    target.unlink()
                return False, report
        return True, ""
    if stage == "feed":
        path = internal / "feed.md"
        text = render_feed_text(_read(path))
        if text.strip() and text.strip() != _read(path).strip():
            text = patch_h3_voiceover(text)
            _write_internal(folder, "feed.md", text)
        if not text.strip():
            return False, "投喂稿是空的"
        claim = unverified_claim(text, mode)
        if claim:
            return False, "没看见图，却写了%s" % claim
        # region agent log
        _agent_log(
            "K",
            "feed check",
            {
                "units": len(re.findall(r"(?m)^##\s*【投喂单元", text)),
                "h3Fields": text.count("subject_definitions"),
                "sevenSection": "【画幅风格】" in text,
                "model": locks.get("model") or "h3",
            },
        )
        # endregion
        code, report = check_feed(path, locks.get("model") or "h3", mode, locks.get("track") or "1")
        # region agent log
        _agent_log(
            "L",
            "feed check result",
            {
                "code": code,
                "missedUnits": "未找到投喂单元" in (report or ""),
                "h9": "H9" in (report or ""),
                "fails": len([line for line in (report or "").splitlines() if "FAIL" in line]),
            },
        )
        # endregion
        if code != 0:
            return False, report
        return True, ""
    return True, ""


_HEADING_SEGMENT = re.compile(r"(?m)^#{1,6}\s*\**\s*段\s*\d+")


def _promote_inline_segments(text):
    """Turn inline 段N into the heading the previs checker recognizes."""
    text = str(text or "").strip()
    if not text or _HEADING_SEGMENT.search(text):
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
    """Check the text that actually contains the panels."""
    candidates = _board_candidates(storyboard, previs)
    body = max(candidates, key=_board_rank) if candidates else ""
    # region agent log
    _agent_log(
        "I",
        "previs candidate",
        {
            "candidates": len(candidates),
            "panels": _board_rank(body)[0],
            "labels": _board_rank(body)[1],
            "headings": _board_rank(body)[2],
            "chars": _board_rank(body)[3],
        },
    )
    # endregion
    if not str(body or "").strip():
        return ""
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


_LOOSE_FIELD = re.compile(
    r"^(?:【\s*)?(%s)(?:\s*】)?\s*[:：]?\s*$" % "|".join(_H3_SECTION_FIELDS)
)
_SHOT_HEAD = re.compile(r"\[Shot\s+\d+\]")
_SHOT_CLOCK = re.compile(
    r"\[Shot\s+(\d+)\][^\n]{0,40}?(?:At|在)\s*(\d{1,2}):(\d{2})\.(\d{3})"
)
_SHOT1_CLOCK = re.compile(
    r"(\[Shot\s*1\])[^\n]{0,40}?(?:At|在)\s*\d{1,2}:\d{2}\.\d{3}\s*,?\s*"
)


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


def _debug_baee6e_feed(message, data):
    # region agent log
    try:
        with open("/Users/shaneson/AiVideo/.cursor/debug-baee6e.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "sessionId": "baee6e",
                "runId": "post-fix",
                "hypothesisId": "A",
                "location": "stage_runner.py:_reorder_ready_h3",
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


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
    # region agent log
    _debug_baee6e_feed("h3 section order", {"changed": changed, "before": before, "after": after})
    # endregion
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


def _normalize_loose_h3(text):
    """Turn bare H3 field names into line-oriented sections the checker can see."""
    raw = str(text or "")
    ready = bool(re.search(r"(?m)^subject_definitions\s*[:：]", raw)) and "## 【投喂单元" in raw
    sections = {} if ready else _loose_h3_sections(raw)
    bare = len(re.findall(
        r"(?m)^(?:%s)\s*$" % "|".join(_H3_SECTION_FIELDS),
        raw,
    ))
    # #region agent log
    try:
        with open("/Users/shaneson/AiVideo/.cursor/debug-eab7ec.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "sessionId": "eab7ec",
                "runId": "post-fix",
                "hypothesisId": "C",
                "location": "stage_runner.py:_normalize_loose_h3",
                "message": "loose h3 feed",
                "data": {
                    "chars": len(raw),
                    "bareFields": bare,
                    "colonField": bool(re.search(r"(?m)^subject_definitions\s*[:：]", raw)),
                    "unitHeading": "## 【投喂单元" in raw,
                    "ready": ready,
                    "sections": sorted(sections),
                },
                "timestamp": int(time.time() * 1000),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion
    if ready:
        return _reorder_ready_h3(raw)
    if "subject_definitions" not in sections or "detailed_description" not in sections:
        return raw
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
    return "\n\n".join(blocks).strip()


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
    return "\n\n".join(blocks).strip()


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
    # region agent log
    try:
        import json
        import time

        payload = {
            "sessionId": "ee5fa7",
            "runId": "post-fix",
            "hypothesisId": "D",
            "location": "end/stage_runner.py:_step_body",
            "message": "script step body field",
            "data": {
                "step": step_id,
                "used": used,
                "bodyLen": len(body),
                "keys": [str(key) for key in list(result)[:12]] if isinstance(result, dict) else [],
            },
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion
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
    # region agent log
    _agent_log(
        "D",
        "produce brief",
        {
            "stage": stage,
            "repairLen": len(repair),
            "draftLen": len(current_draft),
            "draftBlocks": len(_asset_pieces(current_draft)),
            "hasKeepLine": "已有图块必须全部保留" in brief,
        },
    )
    # endregion
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


def _write_fail_log(stage, has_draft, error):
    # region agent log
    try:
        payload = {
            "sessionId": "ee5fa7",
            "hypothesisId": "H9",
            "location": "end/stage_runner.py:execute",
            "message": "write failed",
            "data": {
                "stage": stage,
                "hasDraft": bool(has_draft),
                "errorHead": str(error or "").splitlines()[0][:120],
            },
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


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
            _write_fail_log(current, has_draft, error)
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
            _write_field_log(result)
            _asset_item_log(result)
            result = _coerce_asset_prompts(result)
            previous_assets = _read(Path(folder) / "_内部_" / "assets.md")
            image = asset_markdown(result)
            if not _asset_pieces(image):
                image = apply_structured_assets(previous_assets, (result or {}).get("assets"))
            kept = keep_missing_asset_blocks(previous_assets, image)
            # region agent log
            _agent_log(
                "B",
                "asset rewrite blocks",
                {
                    "attempt": attempts + 1,
                    "previous": len(_asset_pieces(previous_assets)),
                    "returned": len(_asset_pieces(image)),
                    "kept": len(_asset_pieces(kept)),
                    "restored": kept != image,
                },
            )
            # endregion
            patched = kept if figure_repair else patch_asset_phrases(kept)
            # region agent log
            _agent_log(
                "G",
                "phrase patch",
                {"changed": patched != kept, "blocks": len(_asset_pieces(patched))},
            )
            # endregion
            kept = patched
            if figure_repair:
                old_bodies = [body for _number, body in _asset_pieces(previous_assets)]
                new_bodies = [body for _number, body in _asset_pieces(kept)]
                unchanged = [
                    index
                    for index, (old, new) in enumerate(zip(old_bodies, new_bodies))
                    if old == new
                ]
                _figure_log(
                    "figure kept",
                    {
                        "targets": figure_indexes,
                        "unchanged": unchanged,
                        "oldPieces": len(old_bodies),
                        "newPieces": len(new_bodies),
                    },
                )
            result = dict(result or {})
            result["image_prompts"] = kept
            result["assets"] = assets_from_markdown(kept, spec.get("assets"))
        if current == "storyboard":
            previous_board = _read(Path(folder) / "_内部_" / "分镜.md")
            previous_previs = _read(Path(folder) / "_内部_" / "previs.md")
            storyboard = keep_richer_board(previous_board, (result or {}).get("storyboard") or "")
            previs = keep_richer_board(previous_previs, (result or {}).get("previs") or "")
            # region agent log
            _agent_log(
                "H",
                "storyboard keep",
                {
                    "attempt": attempts + 1,
                    "previousPanels": _board_rank(previous_previs)[0],
                    "returnedPanels": _board_rank((result or {}).get("previs") or "")[0],
                    "keptPanels": _board_rank(previs)[0],
                    "keptPrevious": previs == str(previous_previs or "").strip() and bool(str(previous_previs or "").strip()),
                },
            )
            # endregion
            result = dict(result or {})
            result["storyboard"] = storyboard
            result["previs"] = previs
        if current == "feed":
            raw_feed = render_feed_text((result or {}).get("feed") or "")
            patched_feed = patch_h3_voiceover(raw_feed)
            # region agent log
            _agent_log(
                "J",
                "feed voiceover patch",
                {"changed": patched_feed != raw_feed, "chars": len(patched_feed)},
            )
            # endregion
            result = dict(result or {})
            result["feed"] = patched_feed
        store_stage(folder, current, result)
        finish_trace(folder, stage_output(current, result))
        spec = _spec_patch(current, result, spec)
        passed, report = check_stage(folder, current, locks, spec.get("title") or "")
        checked = True
        # region agent log
        _agent_log(
            "C",
            "stage check",
            {
                "stage": current,
                "attempt": attempts + 1,
                "passed": bool(passed),
                "storedBlocks": len(_asset_pieces(_read(Path(folder) / "_内部_" / "assets.md"))) if current == "asset" else 0,
                "failLines": len([line for line in (report or "").splitlines() if "FAIL" in line]),
            },
        )
        # endregion
        attempts += 1
        pipeline["repairs"] = attempts
        pipeline["repair"] = "" if passed else (report or "")
        if current == "script" and not passed and not pipeline["repair"]:
            pipeline["repair"] = "机检没过"
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
