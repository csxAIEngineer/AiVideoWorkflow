"""Template-driven request construction for stage workers.

Each worker task has its own skill folder. specialist, director, and
critic are different processes, written as different documents. Rule-based
code fills named slots, discloses only the reference files the current
branch needs, and sends the filled skill. The original manju-laoli packs
are not pasted into the request.

The shared generator still runs one specialist per node, then the
director merge. The critic still files a report and does not rewrite.
"""

from pathlib import Path

from Agents.templates.filler import apply_conditionals, fill, parse_messages, truthy
from Agents.templates.loader import (
    TASKS,
    TEMPLATES_ROOT,
    TURNS,
    assemble,
    expand_includes,
    list_templates,
    load_frontmatter,
    load_template,
    node_duty,
    reference_path,
    render,
    strip_frontmatter,
    template_path,
)
from Agents.templates.slots import extract_brief, merge_slots

_SHORT_ROLE = 80


def skill_contract(relative, repair=False):
    """Kept for callers that still look up an original. The send path does not paste it."""
    from pathlib import Path as _Path

    agent = _Path(__file__).resolve().parents[1] / "skills" / str(relative)
    if agent.is_file():
        body = agent.read_text(encoding="utf-8").strip()
        if repair:
            from AI.skills.registry import _strip_source_banner

            body = _strip_source_banner(body)
        return body
    from AI.skills.registry import suite_original

    return suite_original(relative, repair=repair)


def format_partials(partials):
    """Director user block: one section per specialist note."""
    blocks = ["各节点正文："]
    for item in partials or []:
        if not isinstance(item, dict):
            continue
        skill = str(item.get("skill") or "").strip()
        if not skill:
            continue
        blocks.append("## %s\n%s" % (skill, item.get("text") or ""))
    return "\n\n".join(blocks)


def specialist_messages(task, *, role="", skill_path="", brief="", repair_text="", contract=None, **extra):
    """Build the specialist request. ``contract`` is ignored so originals stay out."""
    from AI.skills.registry import _NODE_SHAPE

    extracted = extract_brief(brief)
    slots = merge_slots(
        extracted,
        {
            "role": short_role(task, "specialist", role),
            "skill_path": str(skill_path or "").strip(),
            "output_shape": _NODE_SHAPE,
            "repair_text": str(repair_text or "").strip(),
            "node_duty": node_duty(task, skill_path),
        },
        extra,
    )
    if not truthy(slots.get("brief")) and not _has_payload(slots):
        slots["brief"] = str(brief or "").strip()
    return render(task, "specialist", slots)


def director_messages(task, *, brief="", partials=None, repair_text="", merge_instruction="", **extra):
    extracted = extract_brief(brief)
    slots = merge_slots(
        extracted,
        {
            "role": short_role(task, "director", ""),
            "merge_instruction": str(merge_instruction or "").strip(),
            "partials": format_partials(partials),
            "repair_text": str(repair_text or "").strip(),
            "output_shape": _output_shape(task, brief, merge_slots(extracted, extra)),
        },
        extra,
    )
    if not truthy(slots.get("brief")) and not _has_payload(slots):
        slots["brief"] = str(brief or "").strip()
    return render(task, "director", slots)


def critic_messages(task, *, role="", user_body="", **extra):
    slots = merge_slots(
        extract_brief(user_body) if user_body and not extra.get("draft") else {},
        {
            "role": short_role(task, "critic", role),
            "user_body": str(user_body or "").strip(),
            "machine_check": _machine_check(task),
        },
        extra,
    )
    return render(task, "critic", slots)


def short_role(task, turn, role):
    text = str(role or "").strip()
    if text and "\n" not in text and len(text) <= _SHORT_ROLE:
        return text
    titles = {
        ("script", "specialist"): "你是剧本生成链路里的一个节点。",
        ("script", "director"): "你是汇总节点。",
        ("script", "critic"): "你是剧本审查员。",
        ("asset", "specialist"): "你是 P2 资产提示词链路里的一个节点。",
        ("asset", "director"): "你是汇总节点。",
        ("asset", "critic"): "你是 P2 资产提示词的审查员。",
        ("storyboard", "specialist"): "你是分镜生成链路里的一个节点。",
        ("storyboard", "director"): "你是汇总节点。",
        ("storyboard", "critic"): "你是分镜审查员。",
        ("seam", "specialist"): "你是分镜衔接生成员。",
        ("seam", "director"): "你是汇总节点。",
        ("seam", "critic"): "你是分镜衔接审查员。",
        ("feed", "specialist"): "你是提示词封装链路里的一个节点。",
        ("feed", "director"): "你是汇总节点。",
        ("feed", "critic"): "你是封装稿审查员。",
    }
    return titles.get((str(task or ""), str(turn or "")), "你是这一阶段的节点。")


_PAYLOAD_KEYS = (
    "source", "locks", "asset_list", "draft", "skeleton",
    "dialogue", "durations", "shot_skeleton", "style_lock",
    "critic_report", "machine_report", "route", "partials",
)


def _has_payload(slots):
    return any(truthy((slots or {}).get(name)) for name in _PAYLOAD_KEYS)


def _output_shape(task, brief, extra=None):
    extra = extra or {}
    if truthy(extra.get("output_shape")):
        return str(extra.get("output_shape"))
    from AI.skills.registry import _H3_FEED_CONTRACT, _MERGE_SHAPE, parse_route

    shape = _MERGE_SHAPE.get(task) or _MERGE_SHAPE["script"]
    model = str(extra.get("model") or extra.get("h3") or "").strip().lower()
    if task == "feed" and (model == "h3" or "h3" in model or _feed_is_h3(brief, extra)):
        shape = shape + "\n" + _H3_FEED_CONTRACT
    return shape


def _feed_is_h3(brief, extra):
    if truthy((extra or {}).get("h3")):
        return True
    skeleton = str((extra or {}).get("skeleton") or "")
    text = "%s\n%s" % (brief or "", skeleton)
    if "subject_definitions" in text:
        return True
    try:
        from AI.skills.registry import parse_route

        return parse_route(brief, "P4").get("model") == "h3"
    except Exception:
        return False


def _machine_check(task):
    if str(task or "") == "seam":
        return ""
    try:
        from Agents.workers.critic import machine_check_skill

        return machine_check_skill(task)
    except RuntimeError:
        return ""


__all__ = [
    "TASKS",
    "TEMPLATES_ROOT",
    "TURNS",
    "apply_conditionals",
    "assemble",
    "critic_messages",
    "director_messages",
    "expand_includes",
    "extract_brief",
    "fill",
    "format_partials",
    "list_templates",
    "load_frontmatter",
    "load_template",
    "merge_slots",
    "node_duty",
    "parse_messages",
    "reference_path",
    "render",
    "short_role",
    "skill_contract",
    "specialist_messages",
    "strip_frontmatter",
    "template_path",
    "truthy",
]
