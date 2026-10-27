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


def is_full_delivery(text):
    """A six-section H3 unit or a Seedance unit. Render must not add room-tone lines."""
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
        _log_feed_check(path, 1 if segments else 0, [code])
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
    _log_feed_check(path, len(segments), codes)
    if failures:
        return 1, "\n".join(failures)
    return 0, ""


def _log_feed_check(path, segments, codes):
    # #region agent log
    try:
        line = json.dumps(
            {
                "sessionId": "77976e",
                "runId": "post-fix",
                "hypothesisId": "F",
                "location": "end/prompt_gate.py:validate_feed",
                "message": "feed segments checked",
                "data": {
                    "file": Path(path).name,
                    "segments": segments,
                    "codes": codes,
                },
                "timestamp": int(time.time() * 1000),
            },
            ensure_ascii=False,
        )
        with open(
            "/Users/shaneson/AiVideo/.cursor/debug-77976e.log",
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(line + "\n")
    except Exception:
        pass
    # #endregion


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
