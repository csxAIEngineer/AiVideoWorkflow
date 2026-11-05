"""Deterministic repairs. These run before a model rewrite and do not spend an AI attempt."""

from end.repair_rules.feed_unit_headers import FeedUnitHeaderRule
from end.repair_rules.storyboard_p453_sixq import StoryboardP453SixqRule

RULES = (FeedUnitHeaderRule(), StoryboardP453SixqRule())


def run_rules(stage, report, text, context=None):
    """Apply every matching rule that changes the draft or a side file.

    Return ``(text, ids)``. A rule that only rewrites another file (previs, for
    example) still counts when ``apply`` sets ``context["rule_touched"]``.
    That flag is cleared at the start of each rule.
    """
    context = dict(context or {})
    context.setdefault("stage", stage)
    context.setdefault("report", report)
    current = str(text or "")
    applied = []
    for rule in RULES:
        context.pop("rule_touched", None)
        if rule.stages and stage not in rule.stages:
            continue
        if not rule.match(report, stage, current, context):
            continue
        updated = rule.apply(current, context)
        if updated is None:
            continue
        updated = str(updated)
        touched = bool(context.get("rule_touched"))
        if updated.strip() == current.strip() and not touched:
            continue
        current = updated
        applied.append(rule.id)
    return current, applied
