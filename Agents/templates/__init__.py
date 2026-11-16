"""Template-driven request construction for stage workers.

Templates live in this package, one directory per worker task, plus a
``default`` fallback. Each turn (specialist, director, critic) has a
pre-designed ``.md`` file. The files were written from the existing skill
packs and stage rules: they fix the request shape, the output contract,
and the hard product constraints. Rule-based code fills the slots from the
task payload, skill contract, packing skeleton, locks, and repair notes.
Workers send the filled template; they do not concatenate raw skill
markdown with loose instructions at send time.

The shared generator still runs one specialist per skill original, then
the director merge. Specialists still receive that node's skill contract.
The director still receives only the short merge instruction and the
specialist notes. The critic still reviews and does not rewrite.
"""

from pathlib import Path

from Agents.templates.filler import fill, parse_messages, truthy

TEMPLATES_ROOT = Path(__file__).resolve().parent
TURNS = ("specialist", "director", "critic")
TASKS = ("script", "asset", "storyboard", "seam", "feed")


def template_path(task, turn):
    """Return the template file for this task and turn."""
    turn = str(turn or "").strip()
    if turn not in TURNS:
        raise RuntimeError("没有这个模板回合 %s" % turn)
    name = "%s.md" % turn
    specific = TEMPLATES_ROOT / str(task or "").strip() / name
    if specific.is_file():
        return specific
    fallback = TEMPLATES_ROOT / "default" / name
    if fallback.is_file():
        return fallback
    raise RuntimeError("缺少模板 %s/%s" % (task, turn))


def load_template(task, turn):
    path = template_path(task, turn)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise RuntimeError("模板是空的：%s" % path.relative_to(TEMPLATES_ROOT))
    return text


def render(task, turn, slots=None):
    """Load, fill, and split a template into chat messages."""
    return parse_messages(fill(load_template(task, turn), slots))


def list_templates():
    """Known template files, including the default fallback."""
    found = []
    for task in list(TASKS) + ["default"]:
        folder = TEMPLATES_ROOT / task
        if not folder.is_dir():
            continue
        for turn in TURNS:
            path = folder / ("%s.md" % turn)
            if path.is_file():
                found.append("%s/%s.md" % (task, turn))
    return found


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


def skill_contract(relative, repair=False):
    """The skill original for one specialist node, without the send wrapper."""
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


def specialist_messages(task, *, role, skill_path, brief, repair_text="", contract=None):
    from AI.skills.registry import _NODE_SHAPE

    if contract is None:
        contract = skill_contract(skill_path, repair=bool(str(repair_text or "").strip()))
    return render(task, "specialist", {
        "role": str(role or "").strip(),
        "skill_path": str(skill_path or "").strip(),
        "skill_contract": str(contract or "").strip(),
        "output_shape": _NODE_SHAPE,
        "brief": str(brief or "").strip(),
        "repair_text": str(repair_text or "").strip(),
    })


def director_messages(task, *, brief, partials, repair_text="", merge_instruction=""):
    if not str(merge_instruction or "").strip():
        from AI.skills.registry import merge_prompt

        merge_instruction = merge_prompt(task, brief)
    slots = {
        "merge_instruction": str(merge_instruction or "").strip(),
        "partials": format_partials(partials),
        "brief": str(brief or "").strip(),
        "repair_text": str(repair_text or "").strip(),
    }
    return render(task, "director", slots)


def critic_messages(task, *, role, user_body):
    return render(task, "critic", {
        "role": str(role or "").strip(),
        "user_body": str(user_body or "").strip(),
    })


__all__ = [
    "TASKS",
    "TEMPLATES_ROOT",
    "TURNS",
    "critic_messages",
    "director_messages",
    "fill",
    "format_partials",
    "list_templates",
    "load_template",
    "parse_messages",
    "render",
    "skill_contract",
    "specialist_messages",
    "template_path",
    "truthy",
]
