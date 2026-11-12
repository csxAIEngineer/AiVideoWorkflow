"""Run the existing asset-block checker as a script.

Pass and fail come from ``check_asset_blocks.py`` (A1–A8). This module does
not reimplement those rules and does not call ``validate_prompt.py``. Asset
drafts are not encapsulated feed prompts, so the feed validator does not apply.

The script's own exit code is the gate. ERROR fails the process (exit 1).
WARN, including A8 when the block count is under ``--min-blocks`` and A7 when
the block has no grid language, does not. Callers must not promote WARN to a
failure.
"""

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET_BLOCK_SCRIPT = (
    ROOT
    / "AI"
    / "skills"
    / "manju-laoli-skill"
    / "short-drama-director"
    / "scripts"
    / "check_asset_blocks.py"
)
CHECK_TIMEOUT = 90
_FAIL_LINE = re.compile(r"\[-\]\s*FAIL:\s*(.*)")
_WARN_LINE = re.compile(r"\[!\]\s*(?:WARN:\s*)?(.*)")


def empty_machine_report():
    return {
        "ok": False,
        "fatal": "",
        "exit_code": None,
        "errors": [],
        "warnings": [],
        "stats": {},
        "blocks": 0,
        "report": "",
    }


@lru_cache(maxsize=1)
def load_checker():
    """Load the script module for block splitting. The gate still runs ``main``."""
    spec = importlib.util.spec_from_file_location(
        "aivideo_check_asset_blocks",
        str(ASSET_BLOCK_SCRIPT),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载资产机检脚本")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blocks_of(text):
    """Figure headings in document order. Empty when the script cannot be loaded."""
    if not ASSET_BLOCK_SCRIPT.is_file():
        return []
    try:
        rows = load_checker().split_blocks(str(text or ""))
    except (OSError, RuntimeError, AttributeError):
        return []
    return [{"number": number, "title": title} for number, title, _body in rows]


def check_asset_draft(text, min_blocks=1, script=None):
    """Write ``text`` to a temp file and run the asset-block script.

    ``min_blocks`` is passed through. The product asset gate uses 1. The
    script default of 3 only adds an A8 warning.
    """
    path = Path(script) if script else ASSET_BLOCK_SCRIPT
    if not path.is_file():
        report = empty_machine_report()
        report["fatal"] = "checker_unavailable"
        report["errors"] = ["缺少机检脚本 %s" % path]
        report["report"] = report["errors"][0]
        return report
    handle = tempfile.NamedTemporaryFile(
        "w",
        suffix=".md",
        prefix="p2-asset-",
        encoding="utf-8",
        delete=False,
    )
    draft_path = Path(handle.name)
    try:
        handle.write(str(text or ""))
        if text and not str(text).endswith("\n"):
            handle.write("\n")
        handle.close()
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(path),
                    str(draft_path),
                    "--min-blocks",
                    str(int(min_blocks)),
                    "--json",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                timeout=CHECK_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            report = empty_machine_report()
            report["errors"] = ["机检超时"]
            report["report"] = str(exc)
            return report
        except OSError as exc:
            report = empty_machine_report()
            report["fatal"] = "checker_unavailable"
            report["errors"] = ["机检脚本没有跑起来：%s" % exc]
            report["report"] = report["errors"][0]
            return report
    finally:
        try:
            draft_path.unlink()
        except OSError:
            pass
    return _report_from_process(proc.returncode, proc.stdout or "", proc.stderr or "")


def _report_from_process(exit_code, stdout, stderr):
    payload = _json_payload(stdout)
    errors = []
    warnings = []
    stats = {}
    blocks = 0
    if isinstance(payload, dict):
        errors = [str(item) for item in (payload.get("errors") or [])]
        warnings = [str(item) for item in (payload.get("warnings") or [])]
        stats = dict(payload.get("stats") or {})
        try:
            blocks = int(payload.get("blocks") or 0)
        except (TypeError, ValueError):
            blocks = 0
    if not errors:
        errors = [match.group(1).strip() for match in _FAIL_LINE.finditer(stdout)]
    if not warnings:
        warnings = [
            match.group(1).strip()
            for match in _WARN_LINE.finditer(stdout)
            if "WARN" in match.group(0)
        ]
    if exit_code == 1 and not errors:
        notice = next(
            (line.strip() for line in stdout.splitlines() if "未识别到任何" in line),
            "",
        )
        errors = [notice or "资产机检没有通过"]
    report_text = stdout.strip()
    if stderr.strip():
        report_text = (report_text + "\n" + stderr.strip()).strip()
    fatal = "checker_unavailable" if exit_code == 2 else ""
    if exit_code == 2 and not errors:
        errors = [report_text or "资产机检无法读取稿件"]
    return {
        "ok": exit_code == 0,
        "fatal": fatal,
        "exit_code": exit_code,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
        "blocks": blocks,
        "report": report_text,
    }


def _json_payload(stdout):
    start = str(stdout or "").find("{")
    if start < 0:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(stdout[start:])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
