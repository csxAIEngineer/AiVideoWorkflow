"""Run the skill's own checkers. A non-zero exit stops the pipeline and does not rewrite."""

import re
import subprocess
import sys
from pathlib import Path

from end.product_names import UNIT_TOKEN

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "AI" / "skills" / "manju-laoli-skill"
ASSET_CHECK = SUITE / "short-drama-director" / "scripts" / "check_asset_blocks.py"
GATE_CHECK = SUITE / "sd-feed-engine" / "scripts" / "gate_check.py"
VALIDATOR = SUITE / "short-drama-director" / "scripts" / "validate_prompt.py"
P45_CHECK = SUITE / "sd-storyboard-engine" / "scripts" / "check_p45.py"
BOARD = SUITE / "short-drama-director" / "scripts" / "build_board_lite.py"

_SEGMENT = re.compile(r"(?:%s|段)\s*(\d+)" % UNIT_TOKEN)
_VERIFY = ("已核验", "与资产图册一致")


def run_script(script, args, timeout=90):
    script = Path(script)
    if not script.is_file():
        return 2, "缺少机检脚本 %s" % script
    try:
        proc = subprocess.run(
            [sys.executable, str(script), *list(args)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return proc.returncode, proc.stdout or ""


def segment_count(text):
    return len(set(_SEGMENT.findall(str(text or ""))))


def check_asset_blocks(path):
    return run_script(ASSET_CHECK, [str(path), "--min-blocks", "1"])


def check_gate(path, model, mode):
    return run_script(
        GATE_CHECK,
        [str(path), "--model", model or "h3", "--mode", mode or "M1"],
    )


_H3_FEED = re.compile(
    r"(?m)^(?:\s*subject_definitions\s*(?:[:：]|$)|【\s*subject_definitions\s*】)"
)
_H3_SECTIONS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
# Stable sentence the repair brief matches. Do not reuse the Seedance missing-unit line.
H3_STRUCTURE_MARK = "稿子不是 H3 结构"


def feed_uses_h3(text, model):
    """The model lock chooses this checker. Draft section markers do not.

    H3 always stays on the H3 path, including a prose unit that already has
    ``## 【封装单元 N/M】`` but no Ref2VA fields. ``text`` is unused so older
    callers can still pass the draft.
    """
    del text
    return (model or "h3") == "h3"


def _ref2va_heading(text):
    return bool(_H3_FEED.search(str(text or "")))


def _h3_structure_line():
    return (
        "[-] FAIL: %s。模型已锁定 H3，封装稿必须写成 Ref2VA 六段式（%s）。"
        "字段名单独成行并带冒号，顺序固定。不能用自由正文或七段式顶替。"
        % (H3_STRUCTURE_MARK, "、".join(_H3_SECTIONS))
    )


def _h3_unit_structure_line(number):
    return (
        "[-] FAIL: [段%s] 这一段不是 H3 结构，缺 subject_definitions 及其余五段。"
        "改成 Ref2VA 六段式（%s），不要补【画幅风格】或【时间轴分镜】。"
        % (number, "、".join(_H3_SECTIONS))
    )


def h3_structure_report(text):
    """FAIL text when an H3-locked draft is not Ref2VA. Empty when it can be checked.

    A ``subject_definitions`` heading is enough to enter ``validate_prompt``,
    which still reports a missing sibling section as H1. A unit with no such
    heading is not H3 structure, even when the packing-unit title is already right.
    """
    from end.product_names import UNIT_CLAIM, UNIT_HEAD_ANY

    raw = str(text or "")
    heads = list(UNIT_HEAD_ANY.finditer(raw))
    missing = []
    if heads:
        for index, head in enumerate(heads):
            end = heads[index + 1].start() if index + 1 < len(heads) else len(raw)
            chunk = raw[head.start():end]
            if _ref2va_heading(chunk):
                continue
            claim = UNIT_CLAIM.match(head.group(0))
            number = int(claim.group(2)) if claim else index + 1
            missing.append(number)
        if not missing:
            return ""
    elif _ref2va_heading(raw):
        return ""
    lines = [_h3_structure_line()]
    for number in missing:
        lines.append(_h3_unit_structure_line(number))
    return "\n".join(lines)


def _prepare_h3_feed(path, text):
    """A bare subject_definitions line is still an H3 feed. Give it a colon before checking."""
    if "subject_definitions" not in str(text or ""):
        return text
    from end.stage_runner import normalize_h3_shot_cuts, render_feed_text

    rendered = normalize_h3_shot_cuts(render_feed_text(text))
    bare = bool(re.search(r"(?m)^[ \t]*subject_definitions[ \t]*$", str(text or "")))
    if rendered.strip() and rendered.strip() != str(text or "").strip():
        Path(path).write_text(rendered.strip() + "\n", encoding="utf-8")
        return rendered
    return text


def _drop_hidden_c31(code, report):
    """C31 lines are not shown. They must not keep a failing exit by themselves.

    Repair builds units from FAIL lines. Stripping C31 and still returning 1
    leaves a gate with no row to edit, so the same failure comes back.
    """
    lines = [line for line in str(report or "").splitlines() if "C31 " not in line]
    kept = "\n".join(lines).strip()
    if code != 1:
        return code, kept
    if any("FAIL" in line for line in lines):
        return 1, kept
    return 0, kept


def check_feed(path, model, mode, track):
    text = ""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        text = ""
    if feed_uses_h3(text, model):
        text = _prepare_h3_feed(path, text)
        structure = h3_structure_report(text)
        if structure:
            return 1, structure
        from end.prompt_gate import feed_unit_mismatch

        mismatch = feed_unit_mismatch(text)
        if mismatch:
            return 1, mismatch
        code, report = check_prompt(path, model or "h3", track or "1")
        return _drop_hidden_c31(code, report)
    return check_gate(path, model, mode)


def check_prompt(path, model, track):
    text = ""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        text = ""
    if (model or "h3") == "h3" and "subject_definitions" in text:
        text = _prepare_h3_feed(path, text)
    args = [str(path), "--model", model or "h3", "--track", str(track or "1")]
    if segment_count(text) >= 2:
        args.append("--by-segment")
    return _drop_hidden_c31(*run_script(VALIDATOR, args))


def check_p45(path, segments=None, material_mode="M1"):
    args = [str(path)]
    if segments:
        args.extend(["--segments", str(segments)])
    if material_mode == "M2":
        args.append("--m2")
    return run_script(P45_CHECK, args)


def unverified_claim(text, mode):
    if mode == "M2":
        return ""
    raw = str(text or "")
    for phrase in _VERIFY:
        if phrase in raw:
            return phrase
    return ""


_UPLOAD_TOKEN = re.compile(r"\bU\d+\b")
_UPLOAD_CITE = ("照片", "上传", "素材", "产品图", "已入库", "肖像", "参考图", "人像")


def invented_upload_ids(text, items):
    """Upload ids cited in prose that are not on the intake list.

    Real intake ids are U1, U2, … U0 is never assigned. Longer tokens such as
    U01 are only flagged when the nearby words treat them as an uploaded still.
    """
    known = set()
    for item in items or []:
        if isinstance(item, dict) and item.get("id"):
            known.add(str(item.get("id")))
    found = []
    raw = str(text or "")
    for match in _UPLOAD_TOKEN.finditer(raw):
        token = match.group(0)
        if token in known or token in found:
            continue
        if not re.fullmatch(r"U\d{1,2}", token):
            continue
        window = raw[max(0, match.start() - 16) : match.end() + 8]
        cited = any(word in window for word in _UPLOAD_CITE)
        if cited or re.fullmatch(r"U\d", token):
            found.append(token)
    return found


def invented_upload_claim(text, items):
    bad = invented_upload_ids(text, items)
    if not bad:
        return ""
    listed = "、".join(
        "%s %s" % (item.get("id"), item.get("title") or "")
        for item in (items or [])
        if isinstance(item, dict) and item.get("id")
    ) or "（没有上传图）"
    return (
        "写了没有上传的图 %s。已上传只有：%s。"
        "不要编造上传编号，读图是产品或包装时不要写成人物照片。"
        % ("、".join(bad), listed)
    )
