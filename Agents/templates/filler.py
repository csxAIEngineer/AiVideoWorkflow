"""Rule-based template filling. No LLM is involved."""

import re

_COND = re.compile(
    r"\{\{(#|\^)([A-Za-z][A-Za-z0-9_]*)\}\}(.*?)\{\{/\2\}\}",
    re.S,
)
_SLOT = re.compile(r"\{\{(?!#)(?!\^)(?!/)(?!@)([A-Za-z][A-Za-z0-9_]*)\}\}")
_SECTION = re.compile(r"^===(SYSTEM|USER)===\s*$", re.M)


def truthy(value):
    if value is None or value is False:
        return False
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return bool(str(value).strip())


def apply_conditionals(template, slots=None):
    """Resolve ``{{#slot}}`` / ``{{^slot}}`` blocks without inserting values."""
    slots = dict(slots or {})
    text = str(template or "")

    def replace_cond(match):
        kind, name, body = match.group(1), match.group(2), match.group(3)
        present = truthy(slots.get(name))
        if kind == "#":
            return body if present else ""
        return "" if present else body

    previous = None
    while previous != text:
        previous = text
        text = _COND.sub(replace_cond, text)
    return text


def fill(template, slots=None):
    """Substitute ``{{slot}}`` and optional ``{{#slot}}`` / ``{{^slot}}`` blocks.

    ``{{#slot}}...{{/slot}}`` is kept when the slot is truthy.
    ``{{^slot}}...{{/slot}}`` is kept when the slot is falsy.

    Values are inserted literally and are not scanned again, so a payload
    that happens to contain ``{{name}}`` cannot rewrite another slot.
    Missing slots become empty strings. Conditionals are false for None,
    False, empty collections, and blank strings.
    """
    slots = dict(slots or {})
    text = apply_conditionals(template, slots)

    def replace_slot(match):
        value = slots.get(match.group(1), "")
        if value is None:
            return ""
        return str(value)

    return _SLOT.sub(replace_slot, text)


def parse_messages(text):
    """Split a filled template into system and user chat messages."""
    body = str(text or "")
    matches = list(_SECTION.finditer(body))
    if not matches:
        stripped = body.strip()
        if not stripped:
            raise RuntimeError("模板是空的")
        return [{"role": "user", "content": stripped}]
    sections = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        role = match.group(1).lower()
        if content:
            sections.append({"role": role, "content": content})
    if not sections:
        raise RuntimeError("模板填写后没有内容")
    return sections
