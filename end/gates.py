"""Run the skill's own checkers. A non-zero exit stops the pipeline and does not rewrite."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "AI" / "skills" / "manju-laoli-skill"
ASSET_CHECK = SUITE / "short-drama-director" / "scripts" / "check_asset_blocks.py"
GATE_CHECK = SUITE / "sd-feed-engine" / "scripts" / "gate_check.py"
VALIDATOR = SUITE / "short-drama-director" / "scripts" / "validate_prompt.py"
P45_CHECK = SUITE / "sd-storyboard-engine" / "scripts" / "check_p45.py"
BOARD = SUITE / "short-drama-director" / "scripts" / "build_board_lite.py"

_SEGMENT = re.compile(r"段\s*(\d+)")
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


def feed_uses_h3(text, model):
    """H3 Ref2VA is six English sections. The seven-section gate cannot see those units."""
    return (model or "h3") == "h3" and bool(_H3_FEED.search(str(text or "")))


def _prepare_h3_feed(path, text):
    """A bare subject_definitions line is still an H3 feed. Give it a colon before checking."""
    if "subject_definitions" not in str(text or ""):
        return text
    from end.stage_runner import render_feed_text

    rendered = render_feed_text(text)
    bare = bool(re.search(r"(?m)^[ \t]*subject_definitions[ \t]*$", str(text or "")))
    # region agent log
    try:
        import json as _json
        import time as _time

        with open("/Users/shaneson/AiVideo/.cursor/debug-eab7ec.log", "a", encoding="utf-8") as handle:
            handle.write(
                _json.dumps(
                    {
                        "sessionId": "eab7ec",
                        "runId": "post-fix",
                        "hypothesisId": "C",
                        "location": "end/gates.py:_prepare_h3_feed",
                        "message": "subject_definitions shape",
                        "data": {
                            "bare": bare,
                            "colon": "subject_definitions:" in str(text or ""),
                            "changed": rendered.strip() != str(text or "").strip(),
                        },
                        "timestamp": int(_time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # endregion
    if rendered.strip() and rendered.strip() != str(text or "").strip():
        Path(path).write_text(rendered.strip() + "\n", encoding="utf-8")
        return rendered
    return text


def check_feed(path, model, mode, track):
    text = ""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        text = ""
    if (model or "h3") == "h3":
        text = _prepare_h3_feed(path, text)
    routed = feed_uses_h3(text, model)
    # region agent log
    try:
        import json as _json
        import time as _time

        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
            handle.write(
                _json.dumps(
                    {
                        "sessionId": "ee5fa7",
                        "hypothesisId": "H10",
                        "location": "end/gates.py:check_feed",
                        "message": "feed check route",
                        "data": {
                            "model": model or "",
                            "h3": bool(routed),
                            "bracket": "【subject_definitions】" in text,
                            "colon": "subject_definitions:" in text,
                        },
                        "timestamp": int(_time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # endregion
    if routed:
        code, report = check_prompt(path, model or "h3", track or "1")
        kept = [line for line in (report or "").splitlines() if "C31 " not in line]
        return code, "\n".join(kept).strip()
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
    return run_script(VALIDATOR, args)


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
