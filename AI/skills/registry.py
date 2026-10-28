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


# Outer reply is always one JSON object. These fields are markdown strings, not nested JSON.
_MARKDOWN_FIELDS = (
    "title、script、storyboard、image_prompts、feed、previs、intel、internal 是字符串。"
    "字符串里是 markdown 正文，禁止再套 JSON 数组或对象。"
    "image_prompts 用「#### 图N · 名称」；previs 用「## 段 N」；feed 用带冒号的分段正文。"
)
_ARRAY_FIELDS = (
    "只有 assets、shots 是数组。"
    "assets 每项含 id、title、prompt、depends_on、references、subject、subject_kind、view。"
    "shots 每项含 id、duration、prompt、images、dialogue、narration。"
)


def json_shape(template=None, brief=""):
    step = script_step_id(brief)
    if step:
        return script_json_shape(step)
    aspect = locked_aspect(brief)
    if aspect:
        head = "本片画幅以简报为准：%s。角色、场景、道具、九宫格、故事板整板写 16:9。" % aspect
    else:
        head = "不要默认竖屏。画幅未锁定时不要写成固定比例。"
    body = "\n".join(
        [
            "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
            _MARKDOWN_FIELDS,
            _ARRAY_FIELDS,
            "subject_kind 只能是 character、prop、location、other。view 写 single。",
            "全部图片都属于必须的 Image-Node。上传图只是参考，不是资产。",
            "要参考上传图时，references 填上传图 id，不要把上传图 id 写进 depends_on 或镜头 images。",
            "角色是一张脸加三格无脸全身。场景用九宫格。道具四格。",
            "duration 是整数秒。images 只列已经写在 Image-Node 里的生成资产 id。",
            "没有对白则 dialogue 为空字符串。没有旁白则 narration 为空字符串。",
            "提示词里禁止字幕、水印和乱码文字。正文不写人名、工作室名、影片名。",
            "这一阶段用不到的字段用空字符串或空数组。不要在 JSON 里放 locks。",
        ]
    )
    return (head + "\n" + body).strip()


def build_system_prompt(skill_pack, template=None, brief=""):
    shape = json_shape(template, brief)
    pack = (skill_pack or "").strip()
    tail = "再次确认：回复从 { 开始，到 } 结束。"
    if script_step_id(brief):
        return "\n\n".join(part for part in (shape, pack, tail) if part)
    return "\n\n".join(part for part in (drill_system_prompt(template), shape, pack, tail) if part)


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


def script_step_id(brief):
    return _search(r"剧本步\s*[:：]\s*([1-6])", brief or "")


def script_json_shape(step):
    if str(step) == "5":
        lines = [
            "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
            "正式剧本放在 script。标题放在 title。",
            "这一步用不到的字段不要写。不要在 JSON 里放 locks。",
        ]
    else:
        lines = [
            "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
            "这一步的正文放在 text。",
            "这一步用不到的字段不要写。不要在 JSON 里放 locks。",
        ]
    return "\n".join(lines)


def _read_original(path):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError("缺少原文 %s" % path.name)
    return path.read_text(encoding="utf-8").strip()


def _between(text, start, end):
    lines = str(text or "").splitlines()
    start_at = None
    end_at = len(lines)
    for index, line in enumerate(lines):
        if start_at is None:
            if line.strip() == start:
                start_at = index
            continue
        if end and line.strip() == end:
            end_at = index
            break
    if start_at is None:
        raise RuntimeError("缺少原文标题：%s" % start)
    return "\n".join(lines[start_at:end_at]).strip()


_SOURCE_BANNER = "> 精编自《灵境AI剧本改编 skill》（6阶段编剧链）"


def _strip_source_banner(text):
    kept = [line for line in str(text or "").splitlines() if line.strip() != _SOURCE_BANNER]
    return "\n".join(kept).strip()


def _is_repair_context(context):
    text = str(context or "")
    return any(mark in text for mark in ("机检没过", "这次是返修", "精修原文", "要改的底稿"))


def script_step_pack(step, refine=False, repair=False):
    """Original text for one screenwriter step. No summary and no other step."""
    step = str(step or "")
    engine = SUITE / "sd-script-engine" / "references"
    doctor = SUITE / "short-drama-director" / "references" / "dialogue-doctor-7d.md"
    pack = _script_step_body(step, engine, doctor, refine)
    if refine or repair:
        return _strip_source_banner(pack)
    return pack


def _script_step_body(step, engine, doctor, refine):
    if step == "1":
        return _read_original(engine / "source-registration.md")
    if step == "2":
        return _between(
            _read_original(engine / "story-bible-and-ip.md"),
            "# 上篇 · 阶段② IP 与角色开发",
            "# 下篇 · 阶段③全剧开发圣经",
        )
    if step == "3":
        return _between(
            _read_original(engine / "story-bible-and-ip.md"),
            "# 下篇 · 阶段③全剧开发圣经",
            "",
        )
    if step == "4":
        return _read_original(engine / "episode-planning.md")
    if step == "5":
        pipeline = _read_original(engine / "six-stage-pipeline.md")
        parts = [
            _between(
                pipeline,
                "## 六、阶段⑤正式剧本：原作忠实剧本主编",
                "## 七、阶段⑥五席圆桌审核（摘要）",
            ),
            _read_original(engine / "dialogue-naturalization.md"),
            _read_original(doctor),
        ]
        if refine:
            parts.append(
                _between(
                    pipeline,
                    "## 八、授权后精修或重写（04F）",
                    "## 九、全链禁止越界",
                )
            )
        return "\n\n".join(parts)
    if step == "6":
        return _read_original(engine / "roundtable-review.md")
    raise RuntimeError("没有这一步剧本")


def load_skill_pack(template=None, context="", stage="P4"):
    step = script_step_id(context)
    repair = _is_repair_context(context)
    if step:
        refine = bool(_search(r"精修原文\s*[:：]\s*(1)", context))
        return script_step_pack(step, refine=refine, repair=repair)
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
    pack = "\n\n".join(chunks)
    if repair:
        return _strip_source_banner(pack)
    return pack


_DIRECTOR_FILE = "short-drama-director/SKILL.md"
_DIRECTOR_TEXT = ""
LOCK_FILES = (
    "host/意图分析.md",
    "host/意图补充.md",
    "sd-decision-engine/SKILL.md",
    "sd-decision-engine/references/context-mapping.md",
    "sd-decision-engine/references/emotion-anchor-table.md",
    "sd-decision-engine/references/forbidden-zone-whitelist.md",
)
SCRIPT_FILES = (
    "sd-script-engine/references/source-registration.md",
    "sd-script-engine/references/story-bible-and-ip.md",
    "sd-script-engine/references/episode-planning.md",
    "sd-script-engine/references/six-stage-pipeline.md",
    "sd-script-engine/references/dialogue-naturalization.md",
    "short-drama-director/references/dialogue-doctor-7d.md",
    "sd-script-engine/references/roundtable-review.md",
)
BOARD_FILES = ("short-drama-director/references/storyboard-board-lite.md",)
_TASK_MARKERS = (
    ("任务：建议修补", "repair"),
    ("任务：立项锁定", "lock"),
    ("任务：赛道分流", "route"),
    ("任务：剧本", "script"),
    ("任务：资产锁定", "asset"),
    ("任务：分镜", "storyboard"),
    ("任务：提示词封装", "feed"),
    ("任务：独立质检", "qc"),
    ("任务：离线看板", "board"),
)
_MERGE_SHAPE = {
    "lock": "\n".join((
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
        "只交下一问。phase 是 ask 或 ready。reply 是给用户看的话。",
        "options 是这一问的全部选项。ready 时 options 为空数组，brief 写确认后的简报。",
        "不要替用户选定锁。",
    )),
    "route": "\n".join((
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
        "路线说明放在 text。不要改已经选定的赛道、来源、模型、画幅。",
    )),
    "script": "\n".join((
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
        _MARKDOWN_FIELDS,
        "正式剧本放在 script。标题放在 title。圆桌意见放在 internal。",
        "这一阶段用不到的字段不要写。",
    )),
    "asset": "\n".join((
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
        _MARKDOWN_FIELDS,
        _ARRAY_FIELDS,
        "资产提示词放在 image_prompts（字符串）。不要把图块写成数组。",
        "这一阶段用不到的字段不要写。",
    )),
    "storyboard": "\n".join((
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
        _MARKDOWN_FIELDS,
        _ARRAY_FIELDS,
        "分镜放在 storyboard（字符串）。预演故事板放在 previs（字符串，禁止 JSON 数组）。",
        "镜头结构若要交，放在 shots 数组，不要塞进 storyboard 或 previs。",
        "这一阶段用不到的字段不要写。",
    )),
    "feed": "\n".join((
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
        _MARKDOWN_FIELDS,
        "投喂稿放在 feed（字符串）。不要写 segments 对象，不要写接口 JSON。",
        "每个投喂单元单独计时。[Shot 1] 不要写时间戳。从 [Shot 2] 起写成 [Shot 2] At 00:04.000, the camera cuts to …，只在本单元内递增，最后一刀不超过 15 秒。不要写成 At 00:04.000 [Shot 2]。",
        "这一阶段用不到的字段不要写。",
    )),
    "qc": "\n".join((
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
        "质检意见放在 text。不要改写剧本、资产、分镜或投喂稿。",
    )),
    "board": "\n".join((
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
        "看板说明放在 text。不要写 HTML。",
    )),
}
_NODE_SHAPE = "\n".join((
    "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。",
    "只写这些原文要求落到本阶段产物里的内容，放在 text。",
    "有多份原文时，text 里用「## 」加文件路径分节，路径必须与上面的原文路径一致。",
    "不要复述简报，不要照抄原文，不要写这一节点没有的规格。",
))
# One cohort stays within the size of a single large original we already send whole.
COHORT_CHAR_BUDGET = 28000
# A smaller file would cost another full copy of the brief if it opened its own call.
COHORT_TINY_CHARS = 4500
_ORIGINAL_CACHE = {}
_MERGE_DIRECTOR = ""


def suite_task_for(brief):
    """Which collaborative task this brief belongs to. Empty when it stays a single call."""
    text = str(brief or "")
    found = []
    for marker, task in _TASK_MARKERS:
        index = text.find(marker)
        if index >= 0:
            found.append((index, task))
    if not found:
        return ""
    found.sort()
    return found[0][1]


def suite_files(task, brief=""):
    """Originals this task mounts. The director file is not in this list."""
    task = str(task or "")
    if task == "lock":
        return list(LOCK_FILES)
    if task == "script":
        return list(SCRIPT_FILES)
    if task == "board":
        return list(BOARD_FILES)
    stage = {"route": "P0", "asset": "P2", "storyboard": "P3", "feed": "P4", "qc": "P5"}.get(task)
    if not stage:
        raise RuntimeError("没有这个任务")
    route = parse_route(brief, stage)
    files = _slice_module().resolve_files(
        stage,
        track=str(route["track"]),
        combat=str(route["combat"]),
        model=str(route["model"]),
        source=str(route["source"]),
        previs=bool(route["previs"]),
    )
    only = str(route["only"] or "")
    if only:
        files = [item for item in files if item.startswith(only)]
    if not files:
        raise RuntimeError("这个任务没有原文")
    return files


def director_text():
    global _DIRECTOR_TEXT
    if not _DIRECTOR_TEXT:
        _DIRECTOR_TEXT = _read_original(SUITE / _DIRECTOR_FILE)
    return _DIRECTOR_TEXT


def _director_body_lines():
    text = director_text()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            text = text[end + 4 :]
    return text.strip().splitlines()


def director_for_merge():
    """Verbatim director chapters the merge node applies.

    The stage originals already sit on the specialist nodes. The merge keeps
    the lock rules, the budget law, and the industrial laws. Index tables that
    only say which file to open are left out.
    """
    global _MERGE_DIRECTOR
    if _MERGE_DIRECTOR:
        return _MERGE_DIRECTOR
    kept = []
    mode = "preamble"
    for line in _director_body_lines():
        if line.startswith("## "):
            if (
                line.startswith("## ★ 使用入口")
                or line.startswith("## 〇、")
                or line.startswith("## 三、")
            ):
                mode = "keep"
            else:
                mode = "skip"
        if mode == "keep" and line.startswith("### 机检维度分组"):
            mode = "table"
            continue
        if mode == "table":
            if line.startswith("**分级处置"):
                mode = "keep"
            else:
                continue
        if mode == "skip":
            continue
        kept.append(line)
    _MERGE_DIRECTOR = "\n".join(kept).strip()
    return _MERGE_DIRECTOR


def _cohort_engine(relative):
    text = str(relative)
    if text.startswith("host/"):
        return "host"
    head, sep, _tail = text.partition("/")
    return head if sep else text


def pack_cohorts(files, budget=COHORT_CHAR_BUDGET):
    """Group originals that share one model call.

    File order stays put. A call breaks when the next original would pass the
    character budget, or when a large original belongs to another engine.
    A short original rides along, because its own call would repeat the brief.
    """
    cohorts = []
    current = []
    size = 0
    engine = ""
    for relative in files:
        body_len = len(suite_original(relative))
        this_engine = _cohort_engine(relative)
        crosses = bool(current) and this_engine != engine and body_len >= COHORT_TINY_CHARS
        overflows = bool(current) and size + body_len > budget
        if crosses or overflows:
            cohorts.append(list(current))
            current = []
            size = 0
        current.append(relative)
        size += body_len
        engine = this_engine
        if size >= budget:
            cohorts.append(list(current))
            current = []
            size = 0
            engine = ""
    if current:
        cohorts.append(list(current))
    return cohorts


def suite_prompt_chars(task, brief):
    """Input characters before and after cohort packing. Partials are not included."""
    files = suite_files(task, brief)
    cohorts = pack_cohorts(files)
    skill = sum(len(suite_original(relative)) for relative in files)
    brief_chars = len(str(brief or ""))
    full_director = len(director_text())
    merge_director = len(director_for_merge())
    unpacked = skill + brief_chars * (len(files) + 1) + full_director
    packed = skill + brief_chars * (len(cohorts) + 1) + merge_director
    return {
        "files": len(files),
        "cohorts": len(cohorts),
        "calls_before": len(files) + 1,
        "calls_after": len(cohorts) + 1,
        "skill_chars": skill,
        "brief_chars": brief_chars,
        "director_chars": full_director,
        "merge_director_chars": merge_director,
        "unpacked_chars": unpacked,
        "packed_chars": packed,
    }


def _suite_path(relative):
    if str(relative).startswith("host/"):
        return HOST / str(relative)[len("host/") :]
    return SUITE / relative


def suite_original(relative, repair=False):
    key = (str(relative), bool(repair))
    cached = _ORIGINAL_CACHE.get(key)
    if cached is not None:
        return cached
    body = _read_original(_suite_path(relative))
    if repair:
        body = _strip_source_banner(body)
    _ORIGINAL_CACHE[key] = body
    return body


def cohort_prompt(relatives, repair=False):
    """Full originals for one specialist call. No director and no other cohort."""
    parts = []
    for relative in relatives:
        parts.append("# 本节点原文 %s" % relative)
        parts.append(suite_original(relative, repair=repair))
    parts.append(_NODE_SHAPE)
    return "\n\n".join(parts)


def skill_node_prompt(relative, repair=False):
    """Exactly one original. The director stays on the merge node."""
    return cohort_prompt([relative], repair=repair)


def merge_prompt(task):
    """Director chapters plus the task's output shape. No specialist originals."""
    shape = _MERGE_SHAPE.get(task) or _MERGE_SHAPE["script"]
    return "\n\n".join((
        "# 总控 %s" % _DIRECTOR_FILE,
        director_for_merge(),
        "你是汇总节点。各节点已经读过本阶段原文。只根据各节点正文和简报交出这一任务的结果。不要再展开其他原文。",
        shape,
    ))
