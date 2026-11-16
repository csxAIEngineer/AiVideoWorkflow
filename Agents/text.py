"""Text calls for stage agents. Uses the product Responses client.

The product model is ``qwen4-max`` on the Token Plan Responses
API. ``invoke_text`` uses that client: ``enable_thinking: true`` and no
``reasoning.effort``. This module does not open its own HTTP client.
"""

import os

from AI.qwen_api.graph import TEXT_MODEL, bind_prompt_meta, bind_trace_prompts, invoke_text

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
        turned = complete_text(
            role,
            messages,
            return_meta=True,
            trace_path=params.get("trace_prompts") or "",
            **kwargs
        )
        set_node(job_id, node_id, DONE)
        return _job_payload(turned)

    return submit(
        [{"id": node_id, "name": _NODE_NAME.get(role, role)}],
        work,
        trace=params.get("trace_prompts") or "",
    )


def _public_turn(turned):
    """Stop metadata next to the body. Callers that only want text ignore this."""
    turned = turned or {}
    return {
        "text": turned.get("output") or "",
        "finish_reason": turned.get("finish_reason") or "",
        "incomplete_reason": turned.get("incomplete_reason") or "",
        "response_status": turned.get("status") or "",
        "input_tokens": turned.get("input_tokens") or 0,
        "output_tokens": turned.get("output_tokens") or 0,
        "reasoning_tokens": turned.get("reasoning_tokens") or 0,
        "response_error": turned.get("error") or "",
    }


def _job_payload(turned):
    """Job result stays ``{"text": ...}`` when the completion did not return metadata."""
    if isinstance(turned, dict) and "text" in turned:
        text = turned.get("text")
        payload = {"text": "" if text is None else str(text)}
        for key in (
            "finish_reason",
            "incomplete_reason",
            "response_status",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "response_error",
        ):
            if key in turned:
                payload[key] = turned[key]
        return payload
    return {"text": "" if turned is None else str(turned)}


def complete_text(role, messages, *, model=None, client=None, timeout=None, trace_path="", return_meta=False):
    """One generate or critique turn. ``role`` selects the output budget.

    The default return is the text body. ``return_meta=True`` also returns
    finish reason, token counts, and the response error from ``invoke_text``.
    """
    tokens, default_timeout = _BUDGET.get(role, _BUDGET["generator"])
    kwargs = {
        "model": model or TEXT_MODEL,
        "max_output_tokens": tokens,
        "timeout": default_timeout if timeout is None else timeout,
        "client": client,
    }
    path = str(trace_path or "").strip()
    meta = {
        "role": role,
        "model": kwargs["model"],
        "max_output_tokens": kwargs["max_output_tokens"],
        "timeout": kwargs["timeout"],
    }
    with bind_prompt_meta(meta):
        if path:
            with bind_trace_prompts(path):
                turned = invoke_text("generate", messages, **kwargs)
        else:
            turned = invoke_text("generate", messages, **kwargs)
    payload = _public_turn(turned)
    if return_meta:
        return payload
    return payload["text"]
