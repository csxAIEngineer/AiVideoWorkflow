"""Text calls for stage agents. Uses the product Responses client.

The product model is ``deepseek-v4-flash-0731`` on the Token Plan Responses
API. ``generate_text`` already sends ``enable_thinking: false`` and does not
send ``reasoning.effort``. This module does not open its own HTTP client.
"""

import os

from AI.qwen_api.graph import TEXT_MODEL, generate_text

# Same budgets the asset write and the short drill use in the text graph.
GENERATOR_TOKENS = 16000
GENERATOR_TIMEOUT = 1200
CRITIC_TOKENS = 4000
CRITIC_TIMEOUT = 180
_BUDGET = {
    "generator": (GENERATOR_TOKENS, GENERATOR_TIMEOUT),
    "critic": (CRITIC_TOKENS, CRITIC_TIMEOUT),
}


def resolved_model(model=None):
    """Model name the product client will actually send."""
    override = str(os.environ.get("AIVIDEO_TEXT_MODEL") or "").strip()
    if override:
        return override
    return TEXT_MODEL


def complete_text(role, messages, *, model=None, client=None, timeout=None):
    """One generate or critique turn. ``role`` selects the output budget."""
    tokens, default_timeout = _BUDGET.get(role, _BUDGET["generator"])
    return generate_text(
        messages,
        model=model or TEXT_MODEL,
        max_output_tokens=tokens,
        timeout=default_timeout if timeout is None else timeout,
        client=client,
    )
