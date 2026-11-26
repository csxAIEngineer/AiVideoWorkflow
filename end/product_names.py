"""Compatibility shim: the canonical module is the root-level product_names.

It lives at the repo root so end/, AI/, and Agents/ can all import it without
crossing the process-split boundary (AI must not import end; end must not
import AI). Existing `end.product_names` imports keep working.
"""

from product_names import *  # noqa: F401,F403
from product_names import (  # noqa: F401
    CANONICAL_HEAD,
    DRAFT_NAME,
    EMPTY_DRAFT,
    EMPTY_DRAFT_REPLY,
    FEED_PASS,
    GATE_UNIT_HEAD,
    H3_SUBJECT_SOUND_RULE,
    HEADING_RULE,
    LEGACY_EMPTY_DRAFT,
    LEGACY_HEAD,
    LEGACY_MISSING_UNITS,
    LEGACY_UNIT_NAME,
    MACHINE_BLOCKED,
    MACHINE_FAIL,
    MACHINE_GATE,
    MISSING_UNITS,
    REWRITE_LABEL,
    STAGE_ID,
    STAGE_NAME,
    STORYBOARD_PASS,
    UNIT_CLAIM,
    UNIT_HEAD_ANY,
    UNIT_HEAD_LINE,
    UNIT_NAME,
    UNIT_RENUMBER,
    UNIT_TOKEN,
    WRITE_LABEL,
    has_unit_heading,
    is_empty_draft,
    mentions_unit,
    report_says_empty_draft,
    report_says_missing_units,
    unit_heading,
)