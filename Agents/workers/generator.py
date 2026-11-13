"""One generator layout for every stage worker.

First draft: one specialist per skill original, then the director merge.
Repair: only the nodes named by the critic or the machine check run again.
The other nodes keep their previous text. The director assembles. It receives
the director skill and the specialist notes, not every specialist original
and not a rewritten draft from the critic.
"""

import json
import re

_STAGE = {
    "script": "P1",
    "asset": "P2",
    "storyboard": "P3",
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
    from AI.skills.registry import merge_prompt, skill_node_prompt, suite_files

    chosen = list(files) if files is not None else list(
        suite_files(task, _routing_context(task, brief))
    )
    if not chosen:
        raise RuntimeError("这个任务没有原文")
    previous = _notes(partials)
    selected = select_nodes(chosen, repair_text, previous)
    notes = dict(previous)
    repairing = bool(str(repair_text or "").strip()) and bool(previous)
    for relative in selected:
        system = _specialist_system(role, skill_node_prompt(relative, repair=repairing))
        raw = complete("specialist", [
            {"role": "system", "content": system},
            {"role": "user", "content": _specialist_user(brief, repair_text if repairing else "")},
        ])
        notes[relative] = _node_text(raw)
    ordered = [{"skill": relative, "text": notes.get(relative) or ""} for relative in chosen]
    raw = complete("director", [
        {"role": "system", "content": _director_system(role, merge_prompt(task, _routing_context(task, brief)))},
        {"role": "user", "content": _director_user(brief, ordered, repair_text if repairing else "")},
    ])
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


def _routing_context(task, brief):
    stage = _STAGE.get(task, "P4")
    text = str(brief or "")
    if _STAGE_LINE.search(text):
        return text
    return "阶段：%s\n赛道：1\n来源：1\n动作档：none\n模型：h3\n素材形态：M1\n%s" % (stage, text)


def _specialist_system(role, node_prompt):
    return "%s\n\n%s" % (str(role or "").strip(), node_prompt)


def _director_system(role, merge):
    return "%s\n\n%s" % (str(role or "").strip(), merge)


def _specialist_user(brief, repair_text):
    parts = [str(brief or "").strip()]
    if str(repair_text or "").strip():
        parts.append(
            "只改点到本节点原文的失败项。不要重写其他节点负责的内容。\n%s" % str(repair_text).strip()
        )
    return "\n\n".join(part for part in parts if part)


def _director_user(brief, partials, repair_text):
    blocks = ["各节点正文："]
    for item in partials:
        blocks.append("## %s\n%s" % (item["skill"], item.get("text") or ""))
    blocks.append("简报：\n%s" % str(brief or "").strip())
    if str(repair_text or "").strip():
        blocks.append(
            "审查和机检只点了这些问题。没有点名的节点正文保持原样。"
            "不要采用审查员夹带的整稿。\n%s" % str(repair_text).strip()
        )
    blocks.append("请汇总成这一阶段的一份稿。")
    return "\n\n".join(blocks)


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
