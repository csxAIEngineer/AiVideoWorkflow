"""Shared feed constants and the 15-second packing groups."""

import importlib
import re
from typing import TypedDict
from end.product_names import (
    DRAFT_NAME,
    H3_SUBJECT_SOUND_RULE,
    HEADING_RULE,
    UNIT_HEAD_LINE,
    UNIT_NAME,
    UNIT_TOKEN,
)


STAGE = "feed"


TASK = "feed"


GENERATOR_SYSTEM = "\n".join((
    "你是提示词封装的生成员。你只写%s，放在 feed 字符串。" % DRAFT_NAME,
    "投喂机检是这一环的硬门。审查员只交报告，不会替你改稿。",
    "H3 怎么写，以技能原文为准。不要跑在还没封装的剧本或分镜上。",
    "%s。" % HEADING_RULE,
    "可以把分镜合并进同一个%s，但各单元 duration 相加不得短于分镜总时长。" % UNIT_NAME,
    "简报里已经写好每个%s的 duration，和对白旁白的原句。duration 用那些和，对白逐字保留。" % UNIT_NAME,
    "对白和旁白必须原样留下。禁止为了压进 15 秒删情节或对白。",
    "一个单元若按分镜时长会超过 15 秒，拆成下一个单元，禁止把声明时长钳成 15。",
    "画外音的闭口证据必须紧跟这一单元里的画外音，不要接到出声对白后面，也不要接到文末。",
    "审查报告里夹带的稿作废。",
    "正文只写画面。不要写末态继承、拼接节点、换机位、可变数据。",
))


# Ref2VA order the packing scaffold writes. summary and retention_analysis are
# recognized only so their bodies can be discarded instead of leaking sideways.
H3_SECTIONS = (
    "subject_definitions",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)


H3_OMITTED = (
    "summary",
    "retention_analysis",
)


H3_FILL_ONLY = (
    (
        "简报里的 ## 【%s N/M】、duration 和四段字段名是锁定骨架。"
        "只填或修改各段正文，不要自己发明 Ref2VA 版式，也不要输出 JSON。"
        "不要把四段写成 JSON 字段，不要另包一层。"
        "不要改字段名，不要用 Subject、Soundscape、Music 代替这四行。"
        "不要丢掉单元标题、duration、画面风格锁定句或镜号。"
        "不要只写第一个单元。已经填好的单元保留，空着的槽只补正文。"
        "不要写 summary 或 retention_analysis。"
    )
    % UNIT_NAME
    + H3_SUBJECT_SOUND_RULE
    + "若简报里有锁定句，subject_definitions 的第一行只能是那一句。"
    "不要自己写画风，不要翻译，不要另写一种风格。"
    "detailed_description 里的 [Shot N] 和 At 时间也是锁定骨架。"
    "每一段从 [Shot 1] 重新编号，第一镜不写时间。"
    "后面每一镜紧跟简报里的 At MM:SS.mmm，这是这一段前面各镜的时长和，不是全集时间。"
    "不要增删、合并或调换镜头，不要改镜号，不要改这些时间码，也不要自己发明切点。"
    "只在这些标题下写画面和对白。"
    "若这一段已经写了资产：，那一行由程序按分镜资产栏写成，格式与分镜相同。"
    "不要改这行，不要另写一份资产列表，也不要靠角色名或英文单词补图号。"
)


_H3_FIELD_NAMES = H3_SECTIONS + H3_OMITTED


_H3_NAME_ALT = "|".join(_H3_FIELD_NAMES)


_H3_FIELD_LINE = re.compile(
    r"^[ \t]*(?:【[ \t]*)?(%s)(?:[ \t]*】)?[ \t]*[:：][ \t]*(.*)$" % _H3_NAME_ALT
)


_H3_SUBJECT_LINE = re.compile(r"(?m)^[ \t]*subject_definitions[ \t]*[:：]")


_LEADING_DURATION = re.compile(
    r"^(?:[ \t]*(?:duration|duration_seconds)[ \t]*[:=：][^\n]*(?:\n|$))+",
    re.I,
)


_UNIT_DURATION_LINE = re.compile(
    r"^[ \t]*(duration_seconds|duration)[ \t]*[:=：][ \t]*(.*)$",
    re.I,
)


_LOOSE_UNIT_LINE = re.compile(
    r"(?m)^[ \t]*【[ \t]*%s[ \t]+\d+[ \t]*/[ \t]*\d+[^】\n]*】[ \t]*$" % UNIT_TOKEN
)


CRITIC_SYSTEM = "\n".join((
    "你是%s审查员。你只审查，只交报告。" % DRAFT_NAME,
    "禁止改写%s，禁止输出 feed、draft、rewrite、text。" % DRAFT_NAME,
    "整段回复只能是一个 JSON 对象：",
    '{"verdict":"accept 或 revise","issues":[{"target":"%s","severity":"blocking 或 note","detail":"一句话"}]}' % UNIT_NAME,
    "没有要改的地方时 verdict 为 accept，issues 为空数组。",
    "detail 只指出问题，不写替换后的单元。",
    "评判标准只是机检技能：validate_prompt 的 H1–H12，加上时长不得缩短、画外音闭口必须紧跟画外音、画风锁、镜号锁。",
    "有锁定画风时，每一段 subject_definitions 的第一行必须是同一句。缺这句、写成别的句子、或多写一种风格，都是 blocking。只出现在房间或道具主体上不算。",
    "镜号和切点已经按分镜算好。不从 [Shot 1] 起、首镜带时间、切点不是预算的 At 时间、或多了少了镜头，都是 blocking。空着的 [Shot N]、subject_definitions 里没有 <Subject N>、或 overall_soundscape 是空的，也是 blocking。不要要求另写切点。",
    "summary 和 retention_analysis 不是封装字段。缺了不算问题，不要要求补上。",
    "不要用写作技能审查。机检会在你之后再跑。单独的 WARN 不算失败。",
))


class FeedState(TypedDict, total=False):
    stage: str
    task: str
    source: str
    opening: str
    draft: str
    locks: dict
    storyboard: str
    script: str
    shots: list
    skill_pack: str
    partials: list
    attempt: int
    max_attempts: int
    model: str
    critic_report: dict
    machine_report: dict
    history: list
    status: str
    stop_reason: str
    passed: bool
    generator_note: str
    error: str
    project_id: str
    project_folder: str


def _style_units(text):
    raw = str(text or "")
    heads = list(UNIT_HEAD_LINE.finditer(raw))
    if not heads:
        if "subject_definitions" in raw and raw.strip():
            return [raw]
        return []
    units = []
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
        units.append(raw[match.start():end])
    return units


def _unit_label(block, index, total):
    match = re.search(r"(封装单元|投喂单元)\s*(\d+)\s*/\s*(\d+)", str(block or ""))
    if match:
        return "%s %s/%s" % (match.group(1), match.group(2), match.group(3))
    return "%s %s/%s" % (UNIT_NAME, index, total)


def _storyboard_text(state):
    state = state or {}
    board = state.get("storyboard")
    if str(board or "").strip():
        return str(board)
    return str(state.get("source") or "")


def shot_duration_list(state):
    """Shot lengths the feed gate uses: canvas shots, otherwise the storyboard."""
    runner = importlib.import_module("end.stage_runner")
    durations = []
    for shot in (state or {}).get("shots") or []:
        if isinstance(shot, dict) and shot.get("duration") not in (None, ""):
            try:
                durations.append(float(shot.get("duration")))
            except (TypeError, ValueError):
                continue
    if not durations:
        durations = runner.storyboard_duration_list(_storyboard_text(state))
    return durations


def packing_groups(state, limit=15):
    """The same 15s packing the machine check uses. The model does not regroup.

    Merge is allowed. D-lock marks do not exist yet, so they do not split a unit.
    A span over 15 seconds is split, not clamped.
    """
    runner = importlib.import_module("end.stage_runner")
    durations = shot_duration_list(state)
    return runner.group_shot_durations(durations, limit)


_BOARD_HEAD = re.compile(r"^##\s*(?:镜号\s*)?(S\d+)\b", re.I)


_BOARD_SPOKEN = re.compile(r"^(?:对白|旁白)\s*[:：]\s*(.+)$")


_BOARD_DURATION = re.compile(r"^时长\s*[:：]\s*(\d+(?:\.\d+)?)")


_H3_FIELD_MARK = re.compile(
    r"(?m)^(?P<name>%s):[ \t]*(?P<rest>.*)$" % "|".join(H3_SECTIONS)
)


def _append_to_field(body, name, snippet):
    matches = list(_H3_FIELD_MARK.finditer(body))
    target = None
    for index, match in enumerate(matches):
        if match.group("name") == name:
            target = index
            break
    if target is None:
        return None
    if target + 1 < len(matches):
        at = matches[target + 1].start()
        return body[:at].rstrip() + "\n" + snippet + "\n" + body[at:]
    return body.rstrip() + "\n" + snippet


def _insert_field(body, name, snippet):
    section = "%s:\n%s" % (name, snippet)
    matches = list(_H3_FIELD_MARK.finditer(body))
    for match in matches:
        if match.group("name") == "overall_soundscape":
            return body[: match.start()] + section + "\n" + body[match.start() :]
    return body.rstrip() + "\n" + section
