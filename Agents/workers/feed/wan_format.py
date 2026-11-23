"""Wan 3.0 packing shell: official section names and H3-to-Wan mapping."""

import re

WAN_SECTIONS = (
    "总体描述",
    "参考素材引用",
    "分镜",
    "台词",
    "音效 / BGM",
    "风格 / 情绪",
    "负向清单",
)

WAN_NEGATIVE_LINE = "不要字幕、不要水印、不要复杂文字"

_WAN_NAME_ALT = "|".join(
    re.escape(name) for name in (
        "总体描述",
        "参考素材引用",
        "分镜",
        "台词",
        "音效 / BGM",
        "音效/BGM",
        "风格 / 情绪",
        "风格/情绪",
        "负向清单",
    )
)

WAN_FIELD_LINE = re.compile(
    r"^[ \t]*(?:【[ \t]*)?(?P<name>%s)(?:[ \t]*】)?[ \t]*[:：][ \t]*(?P<rest>.*)$" % _WAN_NAME_ALT
)

WAN_SHOT_HEADING = re.compile(
    r"^[ \t]*分镜\s*(?P<num>\d+)\s*（\s*(?P<start>\d{1,2}:\d{2}(?:\.\d{3})?)\s*-\s*(?P<end>\d{1,2}:\d{2}(?:\.\d{3})?)\s*）\s*[:：]?(?P<tail>.*)$"
)

H3_SHOT_HEADING = re.compile(r"^[ \t]*\[Shot\s+(\d+)\]([^\n]*)$")

_D_TAG = re.compile(r"<d>(.*?)</d>", re.S | re.I)
_CHINESE_TAG = re.compile(r"\[Chinese\]\s*", re.I)

_CANON = {
    "总体描述": "总体描述",
    "参考素材引用": "参考素材引用",
    "分镜": "分镜",
    "台词": "台词",
    "音效 / BGM": "音效 / BGM",
    "音效/BGM": "音效 / BGM",
    "风格 / 情绪": "风格 / 情绪",
    "风格/情绪": "风格 / 情绪",
    "负向清单": "负向清单",
}


def canonical_wan_name(name):
    return _CANON.get(str(name or "").strip(), "")


def is_wan_shaped(text):
    """True when a packed unit already uses the official Wan section names."""
    found = set()
    for line in str(text or "").splitlines():
        match = WAN_FIELD_LINE.match(line.strip())
        if not match:
            continue
        name = canonical_wan_name(match.group("name"))
        if name:
            found.add(name)
    return bool(found)


def is_wan_shell_line(line):
    """A section header or timed 分镜 line that submit-time reshape must not steal."""
    raw = str(line or "").strip()
    if WAN_FIELD_LINE.match(raw):
        return True
    if WAN_SHOT_HEADING.match(raw):
        return True
    return False


def format_wan_timecode(seconds):
    total_ms = int(round(float(seconds) * 1000.0))
    if total_ms < 0:
        total_ms = 0
    minutes, remainder = divmod(total_ms, 60000)
    secs, millis = divmod(remainder, 1000)
    return "%02d:%02d.%03d" % (minutes, secs, millis)


def wan_shot_heading(local, start, end):
    return "分镜%s（%s-%s）：" % (
        int(local),
        format_wan_timecode(start),
        format_wan_timecode(end),
    )


def _join(parts):
    return "\n".join(part for part in parts if str(part or "").strip()).strip()


def _strip_d(text):
    body = _D_TAG.sub(lambda match: match.group(1).strip(), str(text or ""))
    return _CHINESE_TAG.sub("", body).strip()


def _spoken_from_prose(text):
    lines = []
    rest = []
    for raw in str(text or "").splitlines():
        tags = list(_D_TAG.finditer(raw))
        if not tags:
            cleaned = _CHINESE_TAG.sub("", raw)
            rest.append(cleaned)
            continue
        kept = raw
        for match in tags:
            spoken = _CHINESE_TAG.sub("", match.group(1)).strip()
            if spoken:
                lines.append(spoken)
            kept = kept.replace(match.group(0), "").strip()
        if kept:
            rest.append(_CHINESE_TAG.sub("", kept))
    return _join(rest), _join(lines)


def _shots_from_detailed(text, shots=None):
    """Turn ``[Shot N]`` / At-cut lines into official 分镜 start-end headings."""
    raw = str(text or "").strip()
    if not raw:
        if shots:
            return "\n".join(shot["heading"] for shot in shots)
        return ""
    planned = {int(shot["local"]): shot for shot in (shots or []) if shot.get("local")}
    lines = []
    pending = None
    bodies = []

    def flush():
        if pending is None:
            return
        body = _join(bodies)
        head = pending.rstrip("：:") + "："
        lines.append(head + body if body else head)
        bodies.clear()

    for raw_line in raw.splitlines():
        core = raw_line.strip()
        match = WAN_SHOT_HEADING.match(core)
        if match:
            flush()
            pending = "分镜%s（%s-%s）：" % (
                match.group("num"),
                match.group("start"),
                match.group("end"),
            )
            tail = (match.group("tail") or "").strip()
            bodies[:] = [tail] if tail else []
            continue
        match = H3_SHOT_HEADING.match(core)
        if match:
            flush()
            number = int(match.group(1))
            tail = (match.group(2) or "").strip()
            tail = re.sub(
                r"^(?:At|在)[ \t]*\d{1,2}:\d{2}(?:\.\d{3})?(?:[ \t]*处)?[ \t]*[,，]?[ \t]*",
                "",
                tail,
                flags=re.I,
            ).strip()
            shot = planned.get(number)
            if shot:
                pending = shot["heading"]
            else:
                pending = "分镜%s：" % number
            bodies[:] = [tail] if tail else []
            continue
        if pending is None:
            # Prose before the first 分镜 line stays loose; do not invent a bare "：" head.
            if core:
                lines.append(core)
            continue
        bodies.append(core)
    flush()
    if not lines and bodies:
        return _join(bodies)
    return "\n".join(lines).strip()


def map_h3_sections_to_wan(sections, style_line="", shots=None):
    """Map four Ref2VA fields (or already-Wan fields) onto the official Wan shell."""
    sections = dict(sections or {})
    wan = {name: "" for name in WAN_SECTIONS}
    for name in WAN_SECTIONS:
        aliases = [name]
        if name == "音效 / BGM":
            aliases = ["音效 / BGM", "音效/BGM"]
        if name == "风格 / 情绪":
            aliases = ["风格 / 情绪", "风格/情绪"]
        for alias in aliases:
            if sections.get(alias):
                wan[name] = str(sections.get(alias) or "").strip()
                break
    subject = str(sections.get("subject_definitions") or "").strip()
    detailed = str(sections.get("detailed_description") or "").strip()
    sound = _join((
        sections.get("overall_soundscape"),
        sections.get("non_diegetic_music"),
    ))
    if subject:
        first, *rest = subject.splitlines()
        first = first.strip()
        if first.startswith("画面风格：") or first.startswith("画面风格:"):
            if not wan["风格 / 情绪"]:
                wan["风格 / 情绪"] = first
            subject_rest = _join(rest)
        else:
            subject_rest = subject
        if subject_rest and not wan["总体描述"]:
            if "<Subject" in subject_rest or "<Picture" in subject_rest or "图" in subject_rest[:40]:
                wan["参考素材引用"] = _join((wan["参考素材引用"], subject_rest))
            else:
                wan["总体描述"] = subject_rest
    visual, spoken = _spoken_from_prose(detailed)
    wan["分镜"] = wan["分镜"] or _shots_from_detailed(visual, shots)
    if spoken and not wan["台词"]:
        # Keep a leftover spoken string only when the exact line is already inside the tag.
        wan["台词"] = spoken
    if sound and not wan["音效 / BGM"]:
        wan["音效 / BGM"] = sound
    if style_line and not wan["风格 / 情绪"]:
        wan["风格 / 情绪"] = style_line
    if not wan["负向清单"]:
        wan["负向清单"] = WAN_NEGATIVE_LINE
    return wan


def render_wan_unit(header, duration, sections, style_line="", shots=None):
    wan = map_h3_sections_to_wan(sections, style_line=style_line, shots=shots)
    lines = [str(header or "").strip()]
    if duration not in (None, ""):
        lines.append("duration: %s" % duration)
    for name in WAN_SECTIONS:
        body = str(wan.get(name) or "").strip()
        if name == "分镜" and not body and shots:
            body = "\n".join(shot["heading"] for shot in shots)
        if name == "风格 / 情绪" and style_line and not body:
            body = style_line
        if name == "负向清单" and not body:
            body = WAN_NEGATIVE_LINE
        lines.append("%s:" % name)
        if body:
            lines.append(body)
    return "\n".join(lines).strip() + "\n"


def section_lines(style_line="", shots=None):
    """Empty Wan scaffold for one packed unit, with lock and timed 分镜 already filled."""
    rows = []
    for name in WAN_SECTIONS:
        if name == "风格 / 情绪" and style_line:
            rows.append("风格 / 情绪:\n%s" % style_line)
        elif name == "分镜" and shots:
            rows.append("分镜:\n%s" % "\n".join(shot["heading"] for shot in shots))
        elif name == "负向清单":
            rows.append("负向清单:\n%s" % WAN_NEGATIVE_LINE)
        else:
            rows.append("%s:" % name)
    return "\n".join(rows)
