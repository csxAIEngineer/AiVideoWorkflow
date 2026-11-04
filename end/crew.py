"""Who is working, derived from phase, stage, and pipeline. Not stored."""

CREW = (
    {"id": "script", "name": "编剧"},
    {"id": "asset", "name": "资产"},
    {"id": "storyboard", "name": "分镜"},
    {"id": "check", "name": "机检"},
    {"id": "feed", "name": "投喂"},
)

_PHASE_ROLE = {
    "gate_intel": "script",
    "gate_script": "script",
    "gate_asset": "asset",
    "gate_storyboard": "storyboard",
    "gate_feed": "feed",
    "gate_machine": "check",
    "feed": "feed",
    "qc": "check",
}

_CURRENT_ROLE = {
    "recon": "script",
    "script": "script",
    "asset": "asset",
    "storyboard": "storyboard",
    "feed": "feed",
    "qc": "check",
}

_QUIET_STATUS = {"done", "error", "paused", "queued", ""}
_QUIET_PHASE = {
    "prompts_ready",
    "done",
    "intent_debug",
    "prompt_debug",
    "llm_debug",
    "image_debug",
    "error",
    "paused",
    "images",
    "videos",
    "building",
    "retry",
    "reconnect",
    "aspect_wait",
}
_HOLD_PHASE = {"lock_wait", "drill_think", "drill_wait", "writing"}


def attach_crew(project):
    """Copy progress and add the tiny crew signal. Leaves the stored dict alone."""
    project = project or {}
    progress = dict(project.get("progress") or {})
    if not progress:
        progress = {"percent": 0, "phase": "", "message": str(project.get("stage") or "")}
    progress["crew"] = [dict(item) for item in CREW]
    progress["active_role"] = active_role(project)
    return progress


def active_role(project):
    project = project or {}
    status = str(project.get("status") or "")
    progress = project.get("progress") or {}
    phase = str(progress.get("phase") or "")
    pipeline = progress.get("pipeline") if isinstance(progress.get("pipeline"), dict) else {}
    message = str(progress.get("message") or project.get("stage") or "").strip()
    action = str(pipeline.get("action") or "")
    current = str(pipeline.get("current") or "")

    if status in _QUIET_STATUS or status in ("building", "rendering"):
        return ""
    if phase in _QUIET_PHASE or _is_board(message):
        return ""
    if phase == "gate_machine" or phase == "qc" or action == "finish":
        return "check"
    if _message_is_check(message) and not phase.startswith("gate_"):
        return "check"
    if phase in _PHASE_ROLE:
        return _PHASE_ROLE[phase]
    if phase == "gate_fail":
        return _CURRENT_ROLE.get(current) or _from_message(message) or ""
    if current in _CURRENT_ROLE:
        return _CURRENT_ROLE[current]
    found = _from_message(message)
    if found:
        return found
    if phase in _HOLD_PHASE or status in ("writing", "drilling", "gate"):
        return "script"
    return ""


def _is_board(message):
    return "看板" in message and "机检" not in message


def _message_is_check(message):
    text = str(message or "").strip()
    if not text:
        return False
    if text in ("机检", "投喂机检", "独立质检"):
        return True
    if "投喂机检" in text:
        return True
    if text.startswith("机检") and "已过" not in text:
        return True
    return False


def _from_message(message):
    text = str(message or "")
    if "机检" in text or "质检" in text:
        if "已过" not in text:
            return "check"
    if "投喂" in text or "封装" in text:
        return "feed"
    if "分镜" in text:
        return "storyboard"
    if "资产" in text:
        return "asset"
    if any(word in text for word in ("剧本", "分流", "对标", "立项")):
        return "script"
    return ""
