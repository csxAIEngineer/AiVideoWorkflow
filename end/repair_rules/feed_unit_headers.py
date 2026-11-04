"""Put ``## 【投喂单元 N/M】`` on a feed the unit checker could not split."""

import re

_MARK = "未找到投喂单元"
_UNIT_LINE = re.compile(r"^#{1,6}[^\n]*投喂单元")


def _canonical_heads(text):
    """Renumber with the same unit split as ``align_feed_unit_headers``, then force the bracket head."""
    from end.prompt_gate import align_feed_unit_headers, delivery_units

    aligned = align_feed_unit_headers(text)
    units = delivery_units(aligned)
    if not units:
        return aligned
    total = len(units)
    blocks = []
    for index, unit in enumerate(units, 1):
        lines = unit.splitlines()
        head = "## 【投喂单元 %s/%s】" % (index, total)
        if lines and _UNIT_LINE.match(lines[0]):
            lines[0] = head
        else:
            lines.insert(0, head)
        blocks.append("\n".join(lines).strip())
    out = "\n\n".join(block for block in blocks if block)
    if str(aligned).endswith("\n"):
        out += "\n"
    return out


class FeedUnitHeaderRule(object):
    id = "feed-unit-headers"
    stages = ("feed",)

    def match(self, report, stage, text, context):
        del stage, text, context
        return _MARK in str(report or "")

    def apply(self, text, context):
        del context
        from end.stage_runner import render_feed_text

        rendered = render_feed_text(text or "")
        if str(rendered or "").strip():
            text = rendered
        return _canonical_heads(text)
