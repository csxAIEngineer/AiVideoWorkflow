"""One generator layout for every stage worker.

First draft: one specialist per skill original, then the director merge.
Repair: only the nodes named by the critic or the machine check run again.
The other nodes keep their previous text. The director assembles. It receives
only the short merge instruction and the specialist notes, not the director
skill, not any other skill original, and not a rewritten draft from the critic.
Requests are filled from pre-designed task skills. Specialists receive a
path pointer and a distilled node duty, not the raw skill original.
"""

import json
import re

from Agents.templates import director_messages, specialist_messages

_STAGE = {
    "script": "P1",
    "asset": "P2",
    "storyboard": "P3",
    "seam": "P3",
    "feed": "P4",
}
_STAGE_LINE = re.compile(r"(?m)^阶段：(P[0-5])\s*$")


def run_generator(
    task,
    brief,
    *,
    role,
    complete,
    partials=None,
    repair_text="",
    files=None,
):
    """Run specialists, then the director. Return ``{"raw", "partials"}``.

    ``files`` overrides ``suite_files`` so tests can name two originals.
    ``complete(role, messages)`` is the same callback the worker already uses.
    ``role`` is ``specialist`` or ``director``.
    """
    from AI.skills.registry import merge_prompt, suite_files

    chosen = list(files) if files is not None else list(
        suite_files(task, routing_context(task, brief))
    )
    if not chosen:
        raise RuntimeError("这个任务没有原文")
    previous = _notes(partials)
    selected = select_nodes(chosen, repair_text, previous)
    notes = dict(previous)
    repairing = bool(str(repair_text or "").strip()) and bool(previous)
    for relative in selected:
        raw = complete("specialist", specialist_messages(
            task,
            role=role,
            skill_path=relative,
            brief=brief,
            repair_text=repair_text if repairing else "",
        ))
        notes[relative] = _node_text(raw)
    ordered = [{"skill": relative, "text": notes.get(relative) or ""} for relative in chosen]
    raw = complete("director", director_messages(
        task,
        brief=brief,
        partials=ordered,
        repair_text=repair_text if repairing else "",
        merge_instruction=merge_prompt(task, routing_context(task, brief)),
    ))
    return {"raw": raw or "", "partials": ordered}




def select_nodes(files, repair_text, previous):
    """All nodes on the first draft. Only named nodes on a repair."""
    if not previous or not str(repair_text or "").strip():
        return list(files)
    text = str(repair_text)
    exact = [relative for relative in files if relative in text]
    if exact:
        return exact
    hit = []
    for relative in files:
        name = relative.rsplit("/", 1)[-1]
        stem = name[:-3] if name.endswith(".md") else name
        engine = relative.split("/", 1)[0]
        by_name = name in text and name != "SKILL.md"
        by_stem = bool(stem) and len(stem) >= 8 and stem != "SKILL" and stem in text
        by_engine = bool(engine) and (engine + "/") in text
        if by_name or by_stem or by_engine:
            hit.append(relative)
    return hit




def _notes(partials):
    found = {}
    for item in partials or []:
        if not isinstance(item, dict):
            continue
        skill = str(item.get("skill") or "").strip()
        if skill:
            found[skill] = str(item.get("text") or "")
    return found




def routing_context(task, brief):
    stage = _STAGE.get(task, "P4")
    text = str(brief or "")
    if _STAGE_LINE.search(text):
        return text
    return "阶段：%s\n赛道：1\n来源：1\n动作档：none\n模型：h3\n素材形态：M1\n%s" % (stage, text)




def _node_text(raw):
    text = str(raw or "").strip()
    if not text:
        return ""
    data = _json_object(text)
    if isinstance(data, dict):
        for key in ("text", "image_prompts", "script", "storyboard", "feed"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return text




def _json_object(raw):
    start = str(raw or "").find("{")
    if start < 0:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(str(raw)[start:])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
