"""Deterministic repairs. These run before a model rewrite and do not spend an AI attempt."""

from end.repair_rules.feed_unit_headers import FeedUnitHeaderRule

RULES = (FeedUnitHeaderRule(),)


def run_rules(stage, report, text, context=None):
    """Apply every matching rule whose text actually changes. Return ``(text, ids)``."""
    context = dict(context or {})
    context.setdefault("stage", stage)
    context.setdefault("report", report)
    current = str(text or "")
    applied = []
    for rule in RULES:
        if rule.stages and stage not in rule.stages:
            continue
        if not rule.match(report, stage, current, context):
            continue
        updated = rule.apply(current, context)
        if updated is None:
            continue
        updated = str(updated)
        if updated.strip() == current.strip():
            continue
        current = updated
        applied.append(rule.id)
    return current, applied
