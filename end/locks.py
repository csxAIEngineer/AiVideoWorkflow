"""Project locks live in locks.json. The model does not choose them and cannot rewrite them."""

import json
import re
from pathlib import Path

ASPECTS = ("16:9", "9:16", "21:9")
RENDER_COMMANDS = ("出图出片", "生成图片", "跑图", "出片", "出图")
_BLOCKED = ("不出图", "不要出图", "别出图", "先不出图", "不出片", "不要出片", "别出片")

STYLE_RECOMMEND = "按你的推荐走"
SEGMENT_LIMITS = {"2.5": 30, "2.0": 15, "h3": 15}

TRACK_OPTIONS = ("① 国内剧", "② 海外剧", "③ 解说叙事", "④ 其他")
SOURCE_OPTIONS = ("① 原创", "② 改编", "③ 对标爆款", "④ 热点速成")
NARRATION_OPTIONS = ("历史人物志", "历史事件战役", "科普知识解说")
REFERENCE_OPTIONS = ("① 全能参考", "② 首尾帧")
DELIVERY_OPTIONS = ("① 只要离线看板", "② 只要 markdown", "③ 两者都要")
SEAM_OPTIONS = ("① 末帧定格承接", "② 换机位硬切", "③ 空镜过渡", "④ 技巧型转场")
COMBAT_OPTIONS = ("R1 写实克制", "R2 商业高燃", "R3 玄幻大招")
LANDED_OPTIONS = ("① 原创", "③ 对标爆款")

_TRACK_VALUE = {"① 国内剧": "1", "② 海外剧": "2", "③ 解说叙事": "3", "④ 其他": "4"}
_SOURCE_VALUE = {"① 原创": "1", "② 改编": "2", "③ 对标爆款": "3", "④ 热点速成": "4"}
_MODEL_VALUE = {"Seedance 2.5": "2.5", "Seedance 2.0": "2.0", "MiniMax H3": "h3"}
_DELIVERY_VALUE = {"① 只要离线看板": "1", "② 只要 markdown": "2", "③ 两者都要": "3"}
_COMBAT_VALUE = {"R1 写实克制": "R1", "R2 商业高燃": "R2", "R3 玄幻大招": "R3"}
_LANDED_VALUE = {"① 原创": "1", "③ 对标爆款": "3"}

_LOCK_KEYS = (
    "track",
    "narration_kind",
    "source",
    "source_was",
    "source_landed",
    "model",
    "reference_mode",
    "aspect",
    "delivery",
    "duration_seconds",
    "seams",
    "style",
    "style_look",
    "style_source",
    "style_choice",
    "combat",
    "material_mode",
    "intel_confirmed",
)


def locked_aspect(text):
    match = re.search(r"(?:画幅|aspect_ratio)\s*[:：=]\s*(21:9|16:9|9:16)", str(text or ""))
    if not match:
        return ""
    return match.group(1)


def locks_ready(text):
    """Legacy brief scan. New projects use locks.json and do not infer these fields."""
    raw = str(text or "")
    style = re.search(r"画风\s*[:：]\s*(\S+)", raw)
    combat = re.search(r"动作强度\s*[:：]\s*(R[1-3])", raw, re.I)
    model = re.search(r"模型\s*[:：]\s*(Seedance 2\.5|Seedance 2\.0|MiniMax H3)", raw)
    delivery = re.search(r"交付\s*[:：]\s*[①②③]", raw)
    seam = re.search(r"段缝\s*[:：]\s*(?:[①②③④]|单段)", raw)
    return bool(
        re.search(r"赛道\s*[:：]\s*[1-4]", raw)
        and re.search(r"素材来源\s*[:：]\s*[1-4]", raw)
        and locked_aspect(raw)
        and style
        and combat
        and model
        and delivery
        and seam
    )


def wants_render(text):
    body = "".join(str(text or "").split())
    if not body or any(token in body for token in _BLOCKED):
        return False
    return any(token in body for token in RENDER_COMMANDS)


def blank_locks():
    return {
        "track": "",
        "narration_kind": "",
        "source": "",
        "source_was": "",
        "source_landed": "",
        "model": "",
        "reference_mode": "",
        "aspect": "",
        "delivery": "",
        "duration_seconds": None,
        "seams": None,
        "style": "",
        "style_look": "",
        "style_source": "",
        "style_choice": "",
        "combat": "",
        "material_mode": "M1",
        "intel_confirmed": False,
    }


def copy_locks(locks):
    copied = blank_locks()
    source = locks or {}
    for key in _LOCK_KEYS:
        if key in source:
            copied[key] = source[key]
    if isinstance(copied["seams"], list):
        copied["seams"] = list(copied["seams"])
    return copied


def locks_path(folder):
    return Path(folder) / "locks.json"


def read_locks(folder):
    path = locks_path(folder)
    if not path.is_file():
        return blank_locks()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return blank_locks()
    if not isinstance(data, dict):
        return blank_locks()
    return copy_locks(data)


def write_locks(folder, locks):
    """Persist locks chosen by the user. Callers must not pass model output here."""
    path = locks_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = copy_locks(locks)
    path.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return read_locks(folder)


def absorb_model_output(folder, _payload):
    """A model payload never replaces locks.json."""
    return read_locks(folder)


def segment_limit(model):
    return SEGMENT_LIMITS.get(str(model or ""), 0)


def seam_needed(locks):
    seconds = locks.get("duration_seconds")
    limit = segment_limit(locks.get("model"))
    if not seconds or not limit:
        return False
    return (float(seconds) / float(limit)) > 1


def judge_material_mode(text, has_images=False):
    raw = str(text or "")
    if any(token in raw for token in ("不要图号", "不写图号", "无图号")):
        return "M0"
    if has_images or any(token in raw for token in ("交回图", "已回传图", "图已交回")):
        return "M2"
    return "M1"


def _as_seconds(number, unit):
    value = float(number)
    if value <= 0:
        return None
    if unit in ("分钟", "分"):
        value *= 60
    if value == int(value):
        return int(value)
    return value


def explicit_duration_seconds(text):
    """Read a duration the user actually wrote. Genre words are not a duration."""
    raw = str(text or "")
    labeled = re.search(r"时长\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(分钟|分|秒|s)?", raw, re.I)
    if labeled:
        unit = labeled.group(2) or "秒"
        if str(unit).lower() == "s":
            unit = "秒"
        return _as_seconds(labeled.group(1), unit)
    found = re.search(r"(?<![\d:.])(\d+(?:\.\d+)?)\s*(分钟|秒)", raw)
    if not found:
        return None
    return _as_seconds(found.group(1), found.group(2))


def parse_duration_answer(text):
    seconds = explicit_duration_seconds(text)
    if seconds:
        return seconds
    bare = re.fullmatch(r"\d+(?:\.\d+)?", str(text or "").strip())
    if not bare:
        return None
    return _as_seconds(bare.group(0), "秒")


def _ready_for_duration(locks):
    if not locks.get("track") or not locks.get("source") or not locks.get("model"):
        return False
    if locks.get("track") == "3" and not locks.get("narration_kind"):
        return False
    if locks.get("model") == "2.5" and not locks.get("reference_mode"):
        return False
    return bool(locks.get("aspect") and locks.get("delivery"))


def prepare_locks(locks, demand="", material_text="", has_images=False):
    """Fill a duration the user already wrote, and whether seam applies.

    An empty video model becomes the fixed Wan lock. Other locks are never inferred.
    """
    updated = copy_locks(locks)
    if not updated.get("model"):
        updated["model"] = "h3"
    updated["material_mode"] = judge_material_mode(material_text or demand, has_images)
    if _ready_for_duration(updated) and not updated.get("duration_seconds"):
        seconds = explicit_duration_seconds(demand)
        if seconds:
            updated["duration_seconds"] = seconds
    if (
        updated.get("duration_seconds")
        and updated.get("model")
        and updated.get("seams") is None
        and not seam_needed(updated)
    ):
        updated["seams"] = []
    return updated


def _question(name, kind, reply, options, cards=None):
    return {
        "id": name,
        "kind": kind,
        "reply": reply,
        "options": list(options),
        "cards": list(cards or []),
    }


def style_question(cards):
    cards = [card for card in (cards or []) if card.get("title")]
    options = [card["title"] for card in cards]
    if STYLE_RECOMMEND not in options:
        options.append(STYLE_RECOMMEND)
    return _question(
        "style",
        "cards",
        "导演风格卡锁定，选定即冻结。卡里可以选择导演名。交给视频模型的正文不写人名、工作室名、影片名。",
        options,
        cards,
    )


def pending_question(locks, cards=None):
    """The next lock still missing. Options are the lock's own choices, with no inferred default."""
    locks = locks or blank_locks()
    if not locks.get("track"):
        return _question(
            "track",
            "single",
            "这是第零道锁，先于后面所有问题。选②走海外剧规则。选③走解说规则，并再选子类型。",
            TRACK_OPTIONS,
        )
    if locks.get("track") == "3" and locks.get("narration_kind") not in NARRATION_OPTIONS:
        return _question(
            "narration_kind",
            "single",
            "解说叙事要再选子类型。历史人物志、历史事件战役、科普知识解说，三选一。",
            NARRATION_OPTIONS,
        )
    if not locks.get("source"):
        return _question(
            "source",
            "single",
            "来源和赛道分开存。选③或④，先出对标情报卡，确认后才写剧本。",
            SOURCE_OPTIONS,
        )
    if locks.get("model") == "2.5" and locks.get("reference_mode") not in REFERENCE_OPTIONS:
        return _question(
            "reference_mode",
            "single",
            "Seedance 2.5 的参考模式。①全能参考，②首尾帧。不默认。",
            REFERENCE_OPTIONS,
        )
    if locks.get("aspect") not in ASPECTS:
        return _question(
            "aspect",
            "single",
            "只锁成片。角色、场景、道具、九宫格、故事板整板写 16:9。不要默认竖屏。",
            ASPECTS,
        )
    if locks.get("delivery") not in ("1", "2", "3"):
        return _question(
            "delivery",
            "single",
            "必须由用户选定。markdown 是正文，看板由它编译。",
            DELIVERY_OPTIONS,
        )
    if not locks.get("duration_seconds"):
        return _question(
            "duration",
            "number",
            "先写这支片子的成片时长，单位用秒。没写时长，不能判断要不要段缝。",
            [],
        )
    if seam_needed(locks) and not locks.get("seams"):
        return _question(
            "seam",
            "multi",
            "成片要比单段上限长。四项分开勾，各自独立，不要并成一项。",
            SEAM_OPTIONS,
        )
    if not locks.get("style_look"):
        return style_question(cards)
    if locks.get("combat") not in ("R1", "R2", "R3"):
        return _question(
            "combat",
            "single",
            "R1 写实克制，R2 商业高燃，R3 玄幻大招。有打斗时按这个档读对应引擎，总控不编招式。",
            COMBAT_OPTIONS,
        )
    if locks.get("source") == "4" and locks.get("intel_confirmed") and not locks.get("source_landed"):
        return _question(
            "source_landed",
            "single",
            "热点选题已经选定。落到①原创，或③对标爆款。不从题材推断。",
            LANDED_OPTIONS,
        )
    return None


def ready_to_write(locks):
    question = pending_question(locks)
    if question is None:
        return True
    return question["id"] == "source_landed" and not locks.get("intel_confirmed")


def _exact(text, options):
    body = " ".join(str(text or "").split())
    for option in options:
        if body == " ".join(str(option).split()):
            return option
    return ""


def _visual_phrase(card):
    look = " ".join(str((card or {}).get("look") or "").split())
    title = " ".join(str((card or {}).get("title") or "").split())
    if title and title in look:
        look = " ".join(part.strip() for part in look.replace(title, "").split("，") if part.strip())
    return look


def apply_selection(locks, question, text, cards=None):
    """Accept only an option that was shown. Anything else stays on the same question."""
    updated = copy_locks(locks)
    question = question or {}
    name = question.get("id") or ""
    options = list(question.get("options") or [])
    if name == "style":
        options = list(style_question(cards if cards is not None else question.get("cards")).get("options") or [])
    picked = _exact(text, options) if name != "seam" else ""
    if name == "duration":
        seconds = parse_duration_answer(text)
        if not seconds:
            return updated, "时长要写成秒，例如 45秒。"
        updated["duration_seconds"] = seconds
        return updated, ""
    if name == "seam":
        parts = [part for part in re.split(r"[、,，;；\n]+", str(text or "")) if part.strip()]
        chosen = []
        for part in parts:
            option = _exact(part, SEAM_OPTIONS)
            if not option or option in chosen:
                return updated, "段缝要按给出的四项分开选。"
            chosen.append(option)
        if not chosen:
            return updated, "段缝至少勾一项。"
        updated["seams"] = [_seam_code(option) for option in chosen]
        return updated, ""
    if not picked:
        return updated, "这一问要选给出的选项。"
    if name == "track":
        updated["track"] = _TRACK_VALUE[picked]
    elif name == "narration_kind":
        updated["narration_kind"] = picked
    elif name == "source":
        updated["source"] = _SOURCE_VALUE[picked]
    elif name == "model":
        updated["model"] = _MODEL_VALUE[picked]
        if updated["model"] != "2.5":
            updated["reference_mode"] = ""
    elif name == "reference_mode":
        updated["reference_mode"] = picked
    elif name == "aspect":
        updated["aspect"] = picked
    elif name == "delivery":
        updated["delivery"] = _DELIVERY_VALUE[picked]
    elif name == "combat":
        updated["combat"] = _COMBAT_VALUE[picked]
    elif name == "source_landed":
        updated["source_was"] = "4"
        updated["source_landed"] = _LANDED_VALUE[picked]
        updated["source"] = updated["source_landed"]
    elif name == "style":
        _apply_style(updated, picked, cards if cards is not None else question.get("cards"))
    else:
        return updated, "这一问要选给出的选项。"
    return updated, ""


def _seam_code(option):
    return {"① 末帧定格承接": "1", "② 换机位硬切": "2", "③ 空镜过渡": "3", "④ 技巧型转场": "4"}[option]


def _apply_style(updated, picked, cards):
    cards = list(cards or [])
    if picked == STYLE_RECOMMEND:
        card = cards[0] if cards else None
        look = _visual_phrase(card) if card else ""
        if not look:
            look = "自然光，写实肤色，浅景深"
        updated["style_choice"] = STYLE_RECOMMEND
        updated["style_source"] = card.get("title") if card else ""
        updated["style_look"] = look
        updated["style"] = look
        return
    card = next((item for item in cards if item.get("title") == picked), None)
    look = _visual_phrase(card) if card else picked
    updated["style_choice"] = picked
    updated["style_source"] = picked
    updated["style_look"] = look
    updated["style"] = look


def lock_brief(locks):
    """Text the writer may read. Director names stay out of this brief."""
    locks = locks or {}
    model = str(locks.get("model") or "")
    if model in ("", "h3"):
        model_line = "出镜：万相 3.0"
    else:
        model_line = "模型：%s" % model
    lines = [
        "以下锁已由用户选定，只读。不要改写，也不要写进正文里的人名、工作室名、影片名。",
        "赛道：%s" % (locks.get("track") or ""),
        "来源：%s" % (locks.get("source") or ""),
        model_line,
        "成片画幅：%s" % (locks.get("aspect") or ""),
        "交付：%s" % (locks.get("delivery") or ""),
        "成片时长秒：%s" % (locks.get("duration_seconds") or ""),
        "段缝：%s" % ("、".join(locks.get("seams") or []) or "单段"),
        "画风正文：%s" % (locks.get("style_look") or ""),
        "动作强度：%s" % (locks.get("combat") or ""),
        "素材形态：%s" % (locks.get("material_mode") or "M1"),
        "角色、场景、道具、九宫格、故事板整板写 16:9。",
    ]
    if locks.get("narration_kind"):
        lines.append("解说子类型：%s" % locks["narration_kind"])
    if locks.get("reference_mode"):
        lines.append("参考模式：%s" % locks["reference_mode"])
    if locks.get("model") == "2.0":
        lines.append("Seedance 2.0 不写首尾帧。")
    if locks.get("material_mode") != "M2":
        lines.append("没有看见图。正文不要写已核验，也不要写与资产图册一致。")
    return "\n".join(lines)


def corpus(brief, messages):
    parts = [str(brief or "")]
    for item in messages or []:
        if isinstance(item, dict) and item.get("role") == "user":
            parts.append(str(item.get("content") or ""))
    return "\n".join(part for part in parts if part)


def load_style_cards(text, limit=4):
    """Candidate cards from the skill recommender. The user still has to pick one."""
    root = Path(__file__).resolve().parents[1]
    path = (
        root
        / "AI"
        / "skills"
        / "manju-laoli-skill"
        / "short-drama-director"
        / "scripts"
        / "style-lock-recommend.py"
    )
    if not path.is_file():
        return []
    import importlib.util

    spec = importlib.util.spec_from_file_location("style_lock_recommend", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _combat, _why, _vfx, pool, _excluded, _route = module.recommend(str(text or ""))
    cards = []
    for num, name, _fight, _fx, native, look, warn in pool[:limit]:
        cards.append(
            {
                "id": str(num),
                "title": str(name),
                "look": str(look),
                "warn": str(warn),
                "native": str(native),
            }
        )
    return cards
