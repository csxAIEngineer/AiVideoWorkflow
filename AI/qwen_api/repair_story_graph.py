"""Repair a saved script candidate into a new manuscript version.

The worker starts from 剧本候选.json ``rejected_text`` or ``candidate``, plus
its ``issues``. It does not start from the official script and does not write
that file.
"""

from AI.qwen_api.script_repair_graph import build_script_repair_graph


def build_repair_story_graph(complete):
    """Uncompiled graph. ``complete(node, messages)`` is one model call."""
    return build_script_repair_graph(complete)


def compile_repair_story_graph(complete):
    return build_repair_story_graph(complete).compile()
