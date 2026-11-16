"""Pull named payload slots out of a worker brief.

The program already owns structured fields: source, locks, skeleton,
durations, dialogue, drafts, and repair notes. The filler should receive
those as named slots rather than one undifferentiated brief dump.
Unrecognized text stays in ``brief``.
"""

import re

from Agents.templates.filler import truthy

_ROUTE_LINE = re.compile(
    r"^(阶段|赛道|来源|动作档|模型|素材形态|只加载|预演)\s*[:：]"
)

# Longer aliases first so "待审分镜衔接" is not cut as "待审分镜".
_HEADINGS = (
    ("skeleton", re.compile(r"^(?:当前骨架如下|当前段缝骨架如下|H3 的 Ref2VA 骨架)")),
    ("dialogue", re.compile(r"^对白和旁白必须逐字出现")),
    ("durations", re.compile(r"^分镜时长合计")),
    ("shot_skeleton", re.compile(r"^骨架（")),
    ("source", re.compile(r"^(?:用户原文|剧本：\s*$|剧本\s*$)")),
    ("locks", re.compile(r"^锁：")),
    ("asset_list", re.compile(r"^(?:要写的资产|资产清单)：")),
    ("draft", re.compile(
        r"^(?:上一稿|已有草稿|待审稿：|待审分镜：|待审分镜衔接：|待审封装稿：)"
    )),
    ("critic_report", re.compile(r"^(?:审查报告|语义审查报告)")),
    ("machine_report", re.compile(r"^机检失败")),
    ("style_lock", re.compile(
        r"^(?:每一段 subject_definitions 的第一行必须|画风锁没过)"
    )),
)

_STYLE_LINE = re.compile(
    r"第一行必须(?:原样是|是)这一句[：:]\s*\n([^\n]+)",
    re.S,
)
_H3 = re.compile(r"(?:模型\s*[:：]\s*.*h3|MiniMax H3|subject_definitions\s*:)", re.I)


def extract_brief(brief):
    """Return a slot dict. ``brief`` is the residual text."""
    text = str(brief or "")
    if not text.strip():
        return {}
    lines = text.splitlines()
    route, rest_start = _take_route(lines)
    remainder = lines[rest_start:]
    sections = {}
    current = "brief"
    buckets = {"brief": []}
    for line in remainder:
        heading = _heading_for(line)
        if heading:
            current = heading
            buckets.setdefault(current, [])
            # Keep the heading line with the section so the model still
            # sees the original label, except when the line is only a label.
            if re.match(r"^(?:用户原文|剧本|锁：|要写的资产：|资产清单：)\s*$", line.strip()):
                continue
            buckets[current].append(line)
            continue
        buckets.setdefault(current, []).append(line)
    for name, rows in buckets.items():
        value = "\n".join(rows).strip()
        if value:
            sections[name] = value
    if route:
        sections["route"] = "\n".join(route).strip()
        if any("h3" in line.lower() or "MiniMax H3" in line for line in route):
            sections["h3"] = "h3"
    combined = "\n".join(part for part in (sections.get("brief"), sections.get("skeleton"), text) if part)
    if _H3.search(combined):
        sections["h3"] = "h3"
    style = _style_lock_from(text, sections)
    if style:
        sections.setdefault("style_lock", style)
    return {key: value for key, value in sections.items() if truthy(value)}


def merge_slots(*parts):
    """Later dictionaries override earlier ones when their value is truthy."""
    merged = {}
    for part in parts:
        for key, value in dict(part or {}).items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip() and key in merged:
                continue
            merged[key] = value
    return merged


def _take_route(lines):
    route = []
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    while index < len(lines):
        raw = lines[index]
        if not raw.strip():
            # Allow a blank inside a route block only if the next line is still a route line.
            nxt = index + 1
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            if nxt < len(lines) and _ROUTE_LINE.match(lines[nxt].strip()):
                index += 1
                continue
            break
        if _ROUTE_LINE.match(raw.strip()):
            route.append(raw)
            index += 1
            continue
        break
    return route, index


def _heading_for(line):
    text = line.strip()
    if not text:
        return ""
    for name, pattern in _HEADINGS:
        if pattern.match(text):
            return name
    return ""


def _style_lock_from(text, sections):
    match = _STYLE_LINE.search(str(text or ""))
    if match:
        return match.group(1).strip()
    skeleton = sections.get("skeleton") or ""
    found = re.search(r"(?m)^subject_definitions:\s*\n([^\n]+)", skeleton)
    if found:
        line = found.group(1).strip()
        if line and not line.endswith(":") and not line.startswith("<"):
            return line
    return ""
