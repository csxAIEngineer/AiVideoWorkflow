"""Text calls for stage agents. Uses the product Responses client.

The product model is ``deepseek-v4-flash-0731`` on the Token Plan Responses
API. ``generate_text`` already sends ``enable_thinking: false`` and does not
send ``reasoning.effort``. This module does not open its own HTTP client.
"""

import os

from AI.qwen_api.graph import TEXT_MODEL, bind_trace_prompts, generate_text

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


_NODE_NAME = {
    "specialist": "专员",
    "director": "汇总",
    "critic": "审查",
    "generator": "生成",
}


def complete_node_job(params):
    """One specialist, director, or critic completion. Not ``write`` and not a suite."""
    from AI.qwen_api.jobs import submit

    params = params or {}
    messages = params.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages 不能为空")
    role = str(params.get("role") or "generator").strip() or "generator"
    model = params.get("model") or None
    raw_timeout = params.get("timeout")
    timeout = None if raw_timeout in (None, "") else float(raw_timeout)
    node_id = role

    def work(job_id):
        from AI.qwen_api.jobs import DONE, RUNNING, set_node

        set_node(job_id, node_id, RUNNING)
        kwargs = {"model": model}
        if timeout is not None:
            kwargs["timeout"] = timeout
        text = complete_text(role, messages, **kwargs)
        set_node(job_id, node_id, DONE)
        return {"text": "" if text is None else str(text)}

    return submit(
        [{"id": node_id, "name": _NODE_NAME.get(role, role)}],
        work,
        trace=params.get("trace_prompts") or "",
    )


def complete_text(role, messages, *, model=None, client=None, timeout=None, trace_path=""):
    """One generate or critique turn. ``role`` selects the output budget."""
    tokens, default_timeout = _BUDGET.get(role, _BUDGET["generator"])
    kwargs = {
        "model": model or TEXT_MODEL,
        "max_output_tokens": tokens,
        "timeout": default_timeout if timeout is None else timeout,
        "client": client,
    }
    path = str(trace_path or "").strip()
    if not path:
        return generate_text(messages, **kwargs)
    with bind_trace_prompts(path):
        return generate_text(messages, **kwargs)
