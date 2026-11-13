"""Names for the packing stage and the machine gate that follows it.

The stage id stays ``feed``. The hard gate stays 「投喂机检」.
UI copy, traces, generator instructions, rendered unit headings, repair
rules, and the parsers that split units all read these strings. Parsers
also accept the previous unit token so an older draft still checks.
"""

import re

STAGE_ID = "feed"
STAGE_NAME = "提示词封装"
DRAFT_NAME = "封装稿"
WRITE_LABEL = "写封装稿"
REWRITE_LABEL = "重写封装稿"
UNIT_NAME = "封装单元"
LEGACY_UNIT_NAME = "投喂单元"
MACHINE_GATE = "投喂机检"

EMPTY_DRAFT = "封装稿是空的"
LEGACY_EMPTY_DRAFT = "投喂稿是空的"
EMPTY_DRAFT_REPLY = "封装稿没写出来。根据建议修改会按分镜和资产再写一版。"

STORYBOARD_PASS = "分镜门。继续之后才写提示词封装。"
FEED_PASS = "提示词封装。闸门已过。继续就做投喂机检并交付。"
MACHINE_FAIL = "投喂机检没过。根据建议修改会按下面这些项改。"
MACHINE_BLOCKED = "投喂机检未通过"

HEADING_RULE = "用 ## 【%s N/M】标题" % UNIT_NAME
# H3 packing only. Seedance does not use this sentence.
H3_SUBJECT_SOUND_RULE = (
    "主体标签只写官方 <Subject N>，不要写 <s1>。"
    "[Shot 1] 必须引用本单元 subject_definitions 里已经定义的 <Subject N>。"
    "overall_soundscape 只写环境音和动作音，不要写 <d>，也不要写对白或旁白原文。"
)
MISSING_UNITS = "未找到封装单元（## 【%s N/M】格式）" % UNIT_NAME
LEGACY_MISSING_UNITS = "未找到投喂单元"

# Either packing-unit token. New drafts use UNIT_NAME. Old drafts keep the other.
UNIT_TOKEN = "(?:%s|%s)" % (UNIT_NAME, LEGACY_UNIT_NAME)

UNIT_HEAD_LINE = re.compile(r"(?m)^##[^\n]*%s[^\n]*$" % UNIT_TOKEN)
UNIT_HEAD_ANY = re.compile(r"(?m)^#{1,6}[^\n]*%s[^\n]*$" % UNIT_TOKEN)
UNIT_CLAIM = re.compile(
    r"(?m)^(#{1,6}[^\n]*%s\s*)(\d+)\s*/\s*(\d+)([^\n]*)$" % UNIT_TOKEN
)
GATE_UNIT_HEAD = re.compile(r"(?m)^##[ \t]*【%s" % UNIT_TOKEN)
UNIT_RENUMBER = re.compile(r"(%s\s*)\d+\s*/\s*\d+" % UNIT_TOKEN)

CANONICAL_HEAD = "## 【%s" % UNIT_NAME
LEGACY_HEAD = "## 【%s" % LEGACY_UNIT_NAME


def unit_heading(index, total, segment=None):
    """The heading new drafts and repairs emit."""
    if segment is None or segment == "":
        return "## 【%s %s/%s】" % (UNIT_NAME, index, total)
    return "## 【%s %s/%s · 段%s】" % (UNIT_NAME, index, total, segment)


def has_unit_heading(text):
    """True when the draft already names a packing unit with either token."""
    raw = str(text or "")
    return CANONICAL_HEAD in raw or LEGACY_HEAD in raw


def mentions_unit(text):
    raw = str(text or "")
    return UNIT_NAME in raw or LEGACY_UNIT_NAME in raw


def is_empty_draft(report):
    """The whole report is the empty-packing-draft sentinel, new or old."""
    return str(report or "").strip() in (EMPTY_DRAFT, LEGACY_EMPTY_DRAFT)


def report_says_empty_draft(report):
    raw = str(report or "")
    return EMPTY_DRAFT in raw or LEGACY_EMPTY_DRAFT in raw


def report_says_missing_units(report):
    raw = str(report or "")
    return MISSING_UNITS in raw or LEGACY_MISSING_UNITS in raw
