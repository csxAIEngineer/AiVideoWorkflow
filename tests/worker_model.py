"""Fixture answers for worker nodes after End stopped calling the write suite.

Production ``_worker_complete`` sends each specialist and the director through
``complete_text``. These tests still pass a brief-shaped ``write_model``.
The bridge turns that one node's system and user messages back into the brief
the fixture already understands. It does not call AI ``write``.
"""

import json
from pathlib import Path


def message_brief(messages):
    return "\n\n".join(
        str((item or {}).get("content") or "").strip()
        for item in messages or []
        if str((item or {}).get("content") or "").strip()
    )


def bridged_worker_complete(project, folder, write_model, prompts):
    """Test stand-in: critic uses ``complete_text``; other roles use the fixture."""
    from end.stage_runner import _accepts_trace

    def complete(role, messages):
        if role == "critic":
            from Agents.text import complete_text

            return complete_text(role, messages, model=project.get("model"))
        brief = message_brief(messages)
        debug = bool((project.get("progress") or {}).get("debug_llm"))
        debug_dir = Path(folder) / "debug" if debug or (project.get("progress") or {}).get("debug_stop") else None
        kwargs = {
            "debug_dir": debug_dir,
            "model": project.get("model"),
            "template": project.get("template"),
        }
        if prompts and _accepts_trace(write_model):
            kwargs["trace_prompts"] = prompts
        result = write_model(brief, "", **kwargs)
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result or "")

    return complete


_DEPTH = 0
_ORIGINAL = None


def install_worker_bridge():
    """Answer worker nodes from the ``write_model`` fixture for this process."""
    global _DEPTH, _ORIGINAL
    import end.stage_runner as runner

    if _DEPTH == 0:
        _ORIGINAL = runner._worker_complete
        runner._worker_complete = bridged_worker_complete
    _DEPTH += 1


def uninstall_worker_bridge():
    global _DEPTH, _ORIGINAL
    import end.stage_runner as runner

    if _DEPTH == 0:
        return
    _DEPTH -= 1
    if _DEPTH == 0 and _ORIGINAL is not None:
        runner._worker_complete = _ORIGINAL
        _ORIGINAL = None
