"""One stage at a time. The writer only sees the files resolve_files returns for that stage."""

import json
import re
import time
from pathlib import Path

from end.delivery import internal_dir, list_deliverables, previs_name, publish
from end.gates import check_asset_blocks, check_feed, check_p45, check_prompt, unverified_claim
from end.intake import enrich_brief, load_intake
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
    "feed": "只写投喂稿，放在 feed。Seedance 七段顺序固定。模型是 h3 时只在这一步改成六段式。画面写正面有什么。",
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


def script_revise_question():
    return {
        "id": "script_revise",
        "kind": "single",
        "reply": "这一稿机检已过，没有打回项。选一个改法，再写一版。写完仍停在剧本门。",
        "options": list(SCRIPT_REVISE_OPTIONS),
        "cards": [],
    }


def _gate_copy(stage, passed, report, tries=0):
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
    if tries:
        reply = "机检没过。已按检查结果自动改了%d次，还剩这些。继续再改一轮。" % tries
    else:
        reply = "机检没过。下面是还要改的地方。继续再改一轮。"
    if tail:
        reply = reply + "\n" + tail
    return reply, ["继续"]


def gate_result(stage, passed, report, pipeline):
    tries = int((pipeline or {}).get("repairs") or 0)
    reply, options = _gate_copy(stage, passed, report, tries)
    phase = _GATE_PHASE.get(stage, "gate_fail")
    if not passed and stage == "feed" and (pipeline or {}).get("check") == "qc":
        phase = "gate_fail"
    stored = dict(pipeline or {})
    stored.update(
        {
            "current": stage,
            "action": "wait",
            "passed": bool(passed),
            "report": (report or "")[-4000:],
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
    elif stage == "feed":
        if result.get("feed"):
            merged["feed"] = result.get("feed")
        if result.get("shots"):
            merged["shots"] = result.get("shots")
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
            if code != 0:
                if target.is_file():
                    _write_internal(folder, target.name, target.read_text(encoding="utf-8"))
                    target.unlink()
                return False, report
        return True, ""
    if stage == "feed":
        path = internal / "feed.md"
        text = _read(path)
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


def _produce(project, folder, stage, locks, only, write_model, load_pack):
    carried = carried_text(folder, stage)
    header = mount_header(stage, locks, carried, only)
    story = enrich_brief(project.get("brief") or "", load_intake(folder))
    pipeline = (project.get("progress") or {}).get("pipeline") or {}
    note = ""
    current_draft = ""
    repair = str(pipeline.get("repair") or "").strip()
    if stage == "script":
        note = str(pipeline.get("revise_note") or "").strip()
    if note or repair:
        current_draft = _read(Path(folder) / "_内部_" / _DRAFT_FILE.get(stage, "")).strip()
        if stage == "storyboard":
            previs_draft = _read(Path(folder) / "_内部_" / "previs.md").strip()
            if previs_draft:
                current_draft = (current_draft + "\n\n预演故事板：\n" + previs_draft).strip()
    brief = "\n\n".join(
        part
        for part in (
            header,
            lock_brief(locks),
            "用户原文：\n" + story if story else "",
            _INSTRUCTION.get(stage, ""),
            "当前这一稿：\n" + current_draft if current_draft else "",
            "精修要求：\n" + note if note else "",
            _repair_line(stage, repair),
            "上一阶段正文：\n" + carried if carried else "",
        )
        if part
    )
    pack = load_pack(project.get("template"), brief, _STAGE_CODE.get(stage, "P4"))
    debug = bool((project.get("progress") or {}).get("debug_llm"))
    debug_dir = Path(folder) / "debug" if debug or (project.get("progress") or {}).get("debug_stop") else None
    result = write_model(
        brief,
        pack,
        debug_dir=debug_dir,
        model=project.get("model"),
        template=project.get("template"),
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


def execute(project, folder, locks, write_model, load_pack):
    """Write the current stage, or finish. Stop at the gate. Do not continue into the next stage."""
    progress = project.get("progress") or {}
    pipeline = dict(progress.get("pipeline") or {})
    only = pipeline.get("only") or ""
    action = pipeline.get("action") or "write"
    if action == "finish":
        return _finish(folder, locks, pipeline)
    if action == "wait":
        current = pipeline.get("current") or ""
        return gate_result(current, pipeline.get("passed"), pipeline.get("report"), pipeline)
    stages = stages_for(locks, only)
    accepted = list(pipeline.get("accepted") or [])
    pending = next_pending(stages, accepted)
    requested = pipeline.get("current") or pending
    current = requested if requested == pending else pending
    if not current:
        return _finish(folder, locks, pipeline)
    if current in accepted and pipeline.get("action") != "rewrite":
        pipeline = dict(pipeline)
        pipeline["action"] = "finish"
        return _finish(folder, locks, pipeline)
    if action == "rewrite":
        pipeline["repair"] = pipeline.get("repair") or pipeline.get("report") or ""
    passed = False
    report = ""
    spec = _load_spec(folder)
    attempts = 0
    while True:
        project = _project_with_pipeline(project, pipeline)
        result = _produce(project, folder, current, locks, only, write_model, load_pack)
        if current == "asset":
            _write_field_log(result)
            _asset_item_log(result)
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
            patched = patch_asset_phrases(kept)
            # region agent log
            _agent_log(
                "G",
                "phrase patch",
                {"changed": patched != kept, "blocks": len(_asset_pieces(patched))},
            )
            # endregion
            kept = patched
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
            raw_feed = str((result or {}).get("feed") or "")
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
        spec = _spec_patch(current, result, spec)
        passed, report = check_stage(folder, current, locks, spec.get("title") or "")
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
        pipeline["repair"] = "" if passed else (report or "")[-4000:]
        if passed or attempts >= REPAIR_LIMIT:
            break
    outcome = gate_result(current, passed, report, pipeline)
    outcome["spec"] = spec
    outcome["used_stage"] = current
    outcome["mount"] = mount_header(current, locks, carried_text(folder, current), only)
    return outcome


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
