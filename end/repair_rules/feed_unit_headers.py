"""Put a packing-unit heading on a feed the unit checker could not split."""

import re

from end.product_names import (
    GATE_UNIT_HEAD,
    UNIT_NAME,
    report_says_missing_units,
    unit_heading,
)

# A bracket head at line start is what the gate splits on. A bare ``## 封装单元`` is not.
_UNIT_LINE = re.compile(r"^#{1,6}[^\n]*(?:%s|投喂单元)" % UNIT_NAME)
_GATE_UNIT_HEAD = GATE_UNIT_HEAD


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
        head = unit_heading(index, total)
        if lines and _UNIT_LINE.match(lines[0]):
            lines[0] = head
        else:
            lines.insert(0, head)
        blocks.append("\n".join(lines).strip())
    out = "\n\n".join(block for block in blocks if block)
    if str(aligned).endswith("\n"):
        out += "\n"
    return out


def feed_has_unit_head(text):
    """True when a unit head is already at line start in the form the gate splits on."""
    return bool(_GATE_UNIT_HEAD.search(str(text or "")))


def recover_feed_units(value):
    """Unwrap a duration/JSON shell, then add line-start unit heads when the body can be split.

    A draft that already has a bracket packing-unit head is left as ``render_feed_text`` wrote it.
    A body that still yields no delivery unit is returned without an invented head.
    """
    from end.prompt_gate import delivery_units
    from end.stage_runner import render_feed_text

    rendered = render_feed_text(value or "")
    text = rendered if str(rendered or "").strip() else str(value or "")
    if feed_has_unit_head(text):
        return text
    recovered = _canonical_heads(text)
    if delivery_units(recovered):
        return recovered
    return text


class FeedUnitHeaderRule(object):
    id = "feed-unit-headers"
    stages = ("feed",)

    def match(self, report, stage, text, context):
        del stage, text, context
        return report_says_missing_units(report)

    def apply(self, text, context):
        del context
        return recover_feed_units(text)
