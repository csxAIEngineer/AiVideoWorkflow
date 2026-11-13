"""Stage machine checks. These call the existing checkers. They do not reimplement them.

Asset drafts stay on ``check_asset_blocks``. ``validate_prompt`` runs only for
an encapsulated feed. Warnings alone do not fail a gate that has no FAIL.
"""

import importlib
import re


def _stage_runner():
    return importlib.import_module("end.stage_runner")


def _gates():
    return importlib.import_module("end.gates")


def _contract():
    return importlib.import_module("end.storyboard_contract")


def report(errors, warnings=None, *, fatal="", needs_human=False):
    errors = [str(item) for item in (errors or []) if str(item).strip()]
    warnings = [str(item) for item in (warnings or []) if str(item).strip()]
    return {
        "ok": not errors and not fatal,
        "fatal": fatal,
        "exit_code": 0 if not errors and not fatal else (2 if fatal else 1),
        "errors": errors,
        "warnings": warnings,
        "stats": {},
        "blocks": 0,
        "report": "\n".join(errors),
        "needs_human": bool(needs_human) and bool(errors or fatal),
    }


def check_script_draft(text, state=None):
    """Empty draft, unverified image claims, invented upload ids. No style review."""
    state = state or {}
    gates = _gates()
    raw = str(text or "")
    if not raw.strip():
        return report(["剧本是空的"])
    errors = []
    mode = str((state.get("locks") or {}).get("material_mode") or "M1")
    claim = gates.unverified_claim(raw, mode)
    if claim:
        errors.append("没看见图，却写了%s" % claim)
    invented = gates.invented_upload_claim(raw, state.get("intake") or [])
    if invented:
        errors.append(invented)
    return report(errors)


def storyboard_stops_for_person(text):
    """Empty boards and conflicts wait for a person.

    A named field gap (``S01 缺时长``) is repaired by the generator. It is not
    the full-board 「补全」 rewrite.
    """
    raw = str(text or "").strip()
    if not raw:
        return False
    contract = _contract()
    if not contract.needs_human(raw):
        return False
    if raw.startswith("分镜未完成") and re.search(r"S\d+\s*缺", raw):
        return False
    return True


def check_storyboard_draft(text, state=None):
    """Empty board and project_shots / known-id failures. The generator fixes named fields."""
    state = state or {}
    gates = _gates()
    contract = _contract()
    raw = str(text or "")
    if not raw.strip():
        return report(["分镜是空的"], needs_human=True)
    errors = []
    mode = str((state.get("locks") or {}).get("material_mode") or "M1")
    claim = gates.unverified_claim(raw, mode)
    if claim:
        errors.append("没看见图，却写了%s" % claim)
    invented = gates.invented_upload_claim(raw, state.get("intake") or [])
    if invented:
        errors.append(invented)
    known = state.get("known_ids")
    if known is None:
        known = contract.known_ids(state.get("assets") or [])
    _shots, field_report = contract.project_shots(raw, known)
    if field_report:
        errors.append(field_report)
    elif state.get("shots"):
        conflict = contract.shots_conflict(_shots, state.get("shots"))
        if conflict:
            errors.append(conflict)
    hold = str(state.get("hold") or "").strip()
    if hold:
        errors.append(hold)
    joined = "\n".join(errors)
    return report(errors, needs_human=storyboard_stops_for_person(joined))


def check_feed_draft(text, state=None):
    """H3 ``check_feed`` / ``validate_prompt``, plus the duration hard gate.

    A warnings-only validator exit still passes. Duration shrink, a clamped
    over-15s span, and deleted dialogue are FAILs even when the validator
    only printed an info line.
    """
    state = state or {}
    runner = _stage_runner()
    gates = _gates()
    raw = str(text or "")
    if not raw.strip():
        return report(["投喂稿是空的"])
    errors = []
    warnings = []
    mode = str((state.get("locks") or {}).get("material_mode") or "M1")
    claim = gates.unverified_claim(raw, mode)
    if claim:
        errors.append("没看见图，却写了%s" % claim)
    import tempfile
    from pathlib import Path

    handle = tempfile.NamedTemporaryFile(
        "w",
        suffix=".md",
        prefix="feed-loop-",
        encoding="utf-8",
        delete=False,
    )
    path = Path(handle.name)
    try:
        handle.write(raw if raw.endswith("\n") else raw + "\n")
        handle.close()
        code, feed_report = gates.check_feed(
            path,
            (state.get("locks") or {}).get("model") or "h3",
            mode,
            (state.get("locks") or {}).get("track") or "1",
        )
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    if code != 0:
        errors.append(feed_report or "投喂机检没过")
    else:
        for line in str(feed_report or "").splitlines():
            if "WARN" in line:
                warnings.append(line.strip())
    errors.extend(
        runner.feed_duration_failures(
            raw,
            state.get("storyboard") or "",
            state.get("shots") or [],
        )
    )
    return report(errors, warnings)
