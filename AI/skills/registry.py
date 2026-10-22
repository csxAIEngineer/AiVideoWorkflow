"""One template. Each write loads the current stage from manju-laoli-skill via slice_context."""

import importlib.util
import re
from pathlib import Path

SKILLS = Path(__file__).resolve().parent
SUITE = SKILLS / "manju-laoli-skill"
HOST = SKILLS / "short-drama-director" / "host"
TEMPLATE_ID = "short-drama-director"
_SLICE = None

_HEADER_KEYS = ("阶段", "赛道", "来源", "动作档", "模型", "素材形态", "只加载", "预演")


def _skill_body(path):
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            text = text[end + 4 :]
    return text.strip()


def _host(name):
    path = HOST / name
    if not path.is_file():
        raise RuntimeError("缺少 skills/short-drama-director/host/%s" % name)
    return _skill_body(path)


def normalize_template(template=None):
    return TEMPLATE_ID


def template_choices():
    return [{"id": TEMPLATE_ID, "label": "漫剧", "policy": "drama_still"}]


def template_label(template=None):
    return "漫剧"


def template_policy(template=None):
    return "drama_still"


def template_files(template=None):
    return [
        "short-drama-director/host/意图分析.md",
        "short-drama-director/host/意图补充.md",
        "short-drama-director/host/成稿.md",
    ]


def template_render(template=None):
    return {
        "image": "",
        "image_from": "",
        "video_lock": "",
        "video_next": "",
        "video_tail": "",
        "video_from": "",
    }


def skill_pack_extra(template=None, context=""):
    return []


def brief_lead(template=None):
    return "漫剧立项"


def repair_lead(template=None):
    return "把下面的正文收成一个 JSON 对象。不要改画幅，不要改锁。"


def drill_system_prompt(template=None, context=""):
    return "\n\n".join([_host("意图分析.md"), _host("意图补充.md")]).strip()


def locked_aspect(text):
    match = re.search(r"(?:画幅|aspect_ratio)\s*[:：=]\s*(21:9|16:9|9:16)", str(text or ""))
    if not match:
        return ""
    return match.group(1)


def locks_ready(text):
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


def json_shape(template=None, brief=""):
    aspect = locked_aspect(brief)
    if aspect:
        head = "本片画幅以简报为准：%s。角色、场景、道具、九宫格、故事板整板写 16:9。" % aspect
    else:
        head = "不要默认竖屏。画幅未锁定时不要写成固定比例。"
    body = "\n".join(
        [
            "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
            "title、script、storyboard、image_prompts、feed、previs、intel、internal 是字符串。",
            "assets 数组，每项含 id、title、prompt、depends_on、references、subject、subject_kind、view。",
            "subject_kind 只能是 character、prop、location、other。view 写 single。",
            "全部图片都属于必须的 Image-Node。上传图只是参考，不是资产。",
            "要参考上传图时，references 填上传图 id，不要把上传图 id 写进 depends_on 或镜头 images。",
            "角色是一张脸加三格无脸全身。场景用九宫格。道具四格。",
            "shots 数组，每项含 id、duration、prompt、images、dialogue、narration。",
            "duration 是整数秒。images 只列已经写在 Image-Node 里的生成资产 id。",
            "没有对白则 dialogue 为空字符串。没有旁白则 narration 为空字符串。",
            "提示词里禁止字幕、水印和乱码文字。正文不写人名、工作室名、影片名。",
            "这一阶段用不到的字段用空字符串或空数组。不要在 JSON 里放 locks。",
        ]
    )
    return (head + "\n" + body).strip()


def build_system_prompt(skill_pack, template=None, brief=""):
    return (
        drill_system_prompt(template)
        + "\n\n"
        + json_shape(template, brief)
        + "\n\n"
        + (skill_pack or "").strip()
        + "\n再次确认：回复从 { 开始，到 } 结束。"
    )


def _search(pattern, text):
    match = re.search(pattern, str(text or ""))
    if not match:
        return ""
    return match.group(1)


def _model_code(value):
    text = str(value or "")
    folded = text.lower()
    if "h3" in folded or "minimax" in folded:
        return "h3"
    if "2.5" in text:
        return "2.5"
    if "2.0" in text:
        return "2.0"
    return ""


def _header(context):
    found = {}
    started = False
    for line in str(context or "").splitlines():
        if not line.strip():
            if started:
                break
            continue
        if "：" not in line:
            if started:
                break
            continue
        key, _, value = line.partition("：")
        key = key.strip()
        if key not in _HEADER_KEYS:
            if started:
                break
            continue
        started = True
        found[key] = value.strip()
    return found


def parse_route(context, stage="P4"):
    header = _header(context)
    model = _model_code(header.get("模型")) or _model_code(
        _search(r"模型\s*[:：]\s*(Seedance 2\.5|Seedance 2\.0|MiniMax H3)", context)
    )
    combat = header.get("动作档") or _search(r"动作强度\s*[:：]\s*(R[1-3])", context) or "none"
    return {
        "stage": (header.get("阶段") or stage or "P4").upper(),
        "track": header.get("赛道") or _search(r"赛道\s*[:：]\s*([1-4])", context) or "1",
        "source": header.get("来源") or _search(r"素材来源\s*[:：]\s*([1-4])", context) or "1",
        "combat": combat or "none",
        "model": model or "2.5",
        "mode": header.get("素材形态") or "M1",
        "only": header.get("只加载") or "",
        "previs": header.get("预演") in ("1", "true", "是"),
    }


def _slice_module():
    global _SLICE
    if _SLICE is not None:
        return _SLICE
    path = SUITE / "short-drama-director" / "scripts" / "slice_context.py"
    spec = importlib.util.spec_from_file_location("slice_context", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("缺少子技能 slice_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _SLICE = module
    return module


def load_skill_pack(template=None, context="", stage="P4"):
    route = parse_route(context, stage)
    files = _slice_module().resolve_files(
        route["stage"],
        track=str(route["track"]),
        combat=str(route["combat"]),
        model=str(route["model"]),
        source=str(route["source"]),
        previs=bool(route["previs"]),
    )
    only = str(route["only"] or "")
    if only:
        files = [item for item in files if item.startswith(only)]
        adapter = "short-drama-director/references/h3-adapter.md"
        if only.startswith("sd-feed-engine/") and route["model"] == "h3" and adapter not in files:
            files.append(adapter)
        if not files:
            raise RuntimeError("缺少子技能 %s" % only)
    chunks = []
    for relative in files:
        path = SUITE / relative
        if not path.is_file():
            raise RuntimeError("缺少子技能 %s" % relative)
        chunks.append("# %s\n\n%s" % (relative, path.read_text(encoding="utf-8").strip()))
    if not chunks:
        raise RuntimeError("缺少子技能 %s" % route["stage"])
    return "\n\n".join(chunks)
