"""Write the H3 feed and the offline board, then run the vendored checker."""

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from end.locks import locked_aspect

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "AI" / "skills" / "manju-laoli-skill"
VALIDATOR = SUITE / "short-drama-director" / "scripts" / "validate_prompt.py"
BOARD = SUITE / "short-drama-director" / "scripts" / "build_board_lite.py"


_UNIT_HEAD = re.compile(r"(?m)^#{1,6}[^\n]*投喂单元[^\n]*$")
_UNIT_CLAIM = re.compile(r"(?m)^(#{1,6}[^\n]*投喂单元\s*)(\d+)\s*/\s*(\d+)([^\n]*)$")
_SUBJECT = re.compile(r"(?m)^subject_definitions\s*[:：]")


def delivery_units(feed):
    """H3 or Seedance blocks in order. Empty when ``feed`` is not a delivery."""
    raw = str(feed or "").strip()
    if not raw:
        return []
    heads = list(_UNIT_HEAD.finditer(raw))
    if heads:
        units = []
        for index, head in enumerate(heads):
            end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
            chunk = raw[head.start():end].strip()
            if chunk:
                units.append(chunk)
        return units
    subjects = list(_SUBJECT.finditer(raw))
    if len(subjects) > 1:
        units = []
        for index, mark in enumerate(subjects):
            start = 0 if index == 0 else mark.start()
            end = subjects[index + 1].start() if index + 1 < len(subjects) else len(raw)
            chunk = raw[start:end].strip()
            if chunk:
                units.append(chunk)
        return units
    if subjects or "【画幅风格】" in raw or "【时间轴分镜】" in raw:
        return [raw]
    return []


def feed_unit_claim(feed):
    """``(actual_units, max_claimed_total)`` from ``## 投喂单元 N/M`` heads."""
    units = delivery_units(feed)
    claimed = 0
    for match in _UNIT_CLAIM.finditer(str(feed or "")):
        claimed = max(claimed, int(match.group(3)))
    return len(units), claimed


def feed_unit_mismatch(feed):
    """Fail text when the heading promises more units than the body has."""
    actual, claimed = feed_unit_claim(feed)
    if claimed > actual >= 1:
        return (
            "[-] FAIL: 投喂单元标题写成 */%s，但正文只有 %s 个单元——"
            "缺的段不要用分镜短句顶替；请补全六个字段的 H3 单元，"
            "或把标题改成 %s/%s 后再出片"
            % (claimed, actual, actual, actual)
        )
    return ""


# One H3 call is 4–15s. A shorter storyboard row stays its own unit and is raised.
UNIT_MIN_SECONDS = 4
UNIT_MAX_SECONDS = 15
_LATER_SHOT = re.compile(r"\[Shot\s*(\d+)\b", re.I)
ONE_SHOT_FEED_RULE = "\n".join((
    "分镜有几镜就写几个投喂单元，按镜号顺序一一对应：第 1 镜是【投喂单元 1/N】。不要把多个分镜合进同一单元，也不要把一镜拆成多个单元。",
    "每个单元只写这一镜的 [Shot 1]，不要写 [Shot 2] 或更后的切镜。",
    "duration 写这一镜的秒数，并夹在 4–15 秒。短于 4 秒的镜仍单独成单元，duration 写成 4，不要为了凑时长并进别的镜。长于 15 秒的镜仍单独成单元，duration 写成 15。",
))


def unit_duration(seconds):
    """Storyboard seconds clamped to one generator call."""
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return max(UNIT_MIN_SECONDS, min(UNIT_MAX_SECONDS, value))


def _declared_duration(unit):
    match = re.search(r"(?m)^duration\s*:\s*(\d+)\s*$", str(unit or ""))
    if not match:
        return None
    return int(match.group(1))


def feed_storyboard_gap(feed, storyboard):
    """Fail when feed units are not one-to-one with the storyboard shots.

    Empty when there is no shot-prose board or no feed yet. A 3-second shot
    must be its own unit declared as 4 seconds, not merged into a neighbour.
    """
    from end.storyboard_contract import shot_durations

    if not str(feed or "").strip():
        return ""
    rows = shot_durations(storyboard)
    if not rows:
        return ""
    units = delivery_units(feed)
    if len(units) != len(rows):
        return (
            "[-] FAIL: 分镜有 %s 镜，投喂只有 %s 个单元。"
            "一个分镜对应一个投喂单元，不要把多镜合进同一单元。"
            % (len(rows), len(units))
        )
    problems = []
    for (shot_id, raw), unit in zip(rows, units):
        expected = unit_duration(raw)
        actual = _declared_duration(unit)
        if actual != expected:
            shown = "没有 duration" if actual is None else "%s 秒" % actual
            problems.append(
                "%s 分镜 %s 秒，单元 duration 应为 %s 秒，写成了 %s"
                % (shot_id, raw, expected, shown)
            )
        later = [int(number) for number in _LATER_SHOT.findall(unit) if int(number) >= 2]
        if later:
            problems.append(
                "%s 的单元里写了 [Shot %s]。一个分镜只写 [Shot 1]"
                % (shot_id, later[0])
            )
    if not problems:
        return ""
    return (
        "[-] FAIL: 投喂没有按分镜一镜一单元对齐。"
        "短于 4 秒的镜单独抬到 4 秒，不要并进别的镜；长于 15 秒的镜单独收到 15 秒。"
        + "".join(problems)
    )


def align_feed_unit_headers(feed):
    """Rewrite each unit head so N/M matches the blocks that are actually present."""
    raw = str(feed or "")
    units = delivery_units(raw)
    if not units:
        return raw
    total = len(units)
    blocks = []
    for index, unit in enumerate(units, 1):
        lines = unit.splitlines() or [""]
        if _UNIT_HEAD.match(lines[0]):
            match = _UNIT_CLAIM.match(lines[0])
            if match:
                lines[0] = "%s%s/%s%s" % (match.group(1), index, total, match.group(4))
            else:
                lines[0] = "## 【投喂单元 %s/%s】" % (index, total)
        else:
            lines.insert(0, "## 【投喂单元 %s/%s】" % (index, total))
        blocks.append("\n".join(lines).strip())
    out = "\n\n".join(blocks)
    if raw.endswith("\n"):
        out += "\n"
    return out


def is_full_delivery(text):
    """A six-section H3 unit or a Seedance unit. Render must not add room-tone lines.

    ``delivery_prompt`` still appends a caption lock. ``<d>`` stays spoken audio.
    """
    raw = str(text or "")
    if "subject_definitions" in raw and "detailed_description" in raw:
        return True
    return "【画幅风格】" in raw or "【时间轴分镜】" in raw


def feed_text(spec):
    """Prepared ``feed`` wins so a short shot list cannot replace feed.md."""
    spec = spec or {}
    raw = spec.get("feed")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    parts = []
    for shot in spec.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        prompt = str(shot.get("prompt") or "").strip()
        if prompt:
            parts.append(prompt)
    return "\n\n".join(parts).strip()


def project_markdown(spec, brief=""):
    spec = spec or {}
    aspect = locked_aspect(brief) or locked_aspect(str(spec.get("aspect_ratio") or "")) or "未锁定"
    title = str(spec.get("title") or "未命名").strip() or "未命名"
    return "\n".join(
        [
            "# %s" % title,
            "【画幅】：%s        【模型版本】：h3" % aspect,
            "",
            "## P1 · 剧本",
            str(spec.get("script") or ""),
            "",
            "## P2.5 · 资产出图提示词",
            str(spec.get("image_prompts") or ""),
            "",
            "## P3 · 分镜",
            str(spec.get("storyboard") or ""),
            "",
            "## P4 · 投喂提示词",
            "### 段1",
            "```text",
            feed_text(spec),
            "```",
            "",
        ]
    )


def _feed_segments(text):
    lines = text.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("subject_definitions:")
    ]
    if len(starts) <= 1:
        body = text.strip()
        return [body] if body else []
    segments = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        segment = "\n".join(lines[start:end]).strip()
        if segment:
            segments.append(segment)
    return segments


def _run_validator(path, aspect):
    command = [sys.executable, str(VALIDATOR), str(path), "--model", "h3"]
    if aspect in ("16:9", "9:16", "21:9"):
        command.extend(["--aspect", aspect])
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return proc.returncode, proc.stdout or ""


def validate_feed(path, aspect=""):
    text = Path(path).read_text(encoding="utf-8")
    segments = _feed_segments(text)
    if len(segments) <= 1:
        code, report = _run_validator(path, aspect)
        return code, report
    folder = Path(tempfile.mkdtemp(prefix="h3-feed-"))
    codes = []
    failures = []
    try:
        for index, segment in enumerate(segments, 1):
            target = folder / ("segment-%02d.md" % index)
            target.write_text(segment + "\n", encoding="utf-8")
            code, report = _run_validator(target, aspect)
            codes.append(code)
            if code != 0:
                failures.append("段 %s\n%s" % (index, report))
    finally:
        for target in folder.glob("*.md"):
            target.unlink()
        folder.rmdir()
    if failures:
        return 1, "\n".join(failures)
    return 0, ""




def build_board(markdown_path, html_path):
    try:
        proc = subprocess.run(
            [sys.executable, str(BOARD), str(markdown_path), "--out", str(html_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("看板没有生成：%s" % error)
    if proc.returncode != 0 or not Path(html_path).is_file():
        raise RuntimeError("看板没有生成：%s" % ((proc.stdout or "")[-800:]))
    return proc.stdout or ""
