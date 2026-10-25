"""Text-only LangGraph. generate writes. understand calls submit_intent once.

Image, video, and speech stay outside this graph. Polling stays in End.
"""

import contextvars
import json
import operator
import os
import threading
import time
from contextlib import contextmanager
from typing import Annotated, Any, List, TypedDict

from langgraph.graph import END, START, StateGraph

from AI.qwen_api.client import QwenClient

TEXT_MODEL = "qwen3.8-flash"
INTENT_EFFORT = "low"
WRITE_EFFORT = "low"
_EFFORTS = ("low", "high", "max")
INTENT_TOOL = "submit_intent"
_TRACE_PROMPTS = contextvars.ContextVar("aivideo_trace_prompts", default="")
_DEBUG_LOCK = threading.Lock()
_BURST_SKILL = contextvars.ContextVar("aivideo_burst_skill", default="")
_BURST_LOCK = threading.Lock()
_BURST_STATE = {"inflight": 0, "seq": 0, "last": 0.0}
# #region agent log
try:
    with open("/Users/shaneson/AiVideo/.cursor/debug-eab7ec.log", "a", encoding="utf-8") as _agent_log:
        _agent_log.write(json.dumps({"sessionId": "eab7ec", "runId": "post-fix", "hypothesisId": "A", "location": "graph.py:26", "message": "burst skill bound at import", "data": {"pid": os.getpid(), "has": "_BURST_SKILL" in globals()}, "timestamp": int(time.time() * 1000)}, ensure_ascii=False) + "\n")
except OSError:
    pass
# #endregion

SUBMIT_INTENT = {
    "type": "function",
    "name": INTENT_TOOL,
    "description": "提交这一轮意图。phase 为 ask 时继续追问，为 ready 时给出可以写稿的简报。",
    "parameters": {
        "type": "object",
        "properties": {
            "phase": {
                "type": "string",
                "enum": ["ask", "ready"],
                "description": "ask 继续追问，ready 方向已经够了",
            },
            "reply": {"type": "string", "description": "给用户看的话"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选项。ready 时为空数组",
            },
            "brief": {"type": "string", "description": "确认后的创作简报。ask 时为空字符串"},
        },
        "required": ["phase", "reply", "options", "brief"],
    },
}


class TextState(TypedDict, total=False):
    kind: str
    messages: List[Any]
    model: str
    max_output_tokens: int
    timeout: int
    effort: str
    output: str
    incomplete_reason: str
    finish_reason: str
    response_status: str
    output_tokens: int
    input_tokens: int
    reasoning_tokens: int
    response_items: List[Any]
    response_error: str
    brief: str
    task: str
    trace_path: str
    job_id: str
    partials: Annotated[List[dict], operator.add]


def responses_client(api_key=None, base_url=None, client=None):
    if client is not None:
        return client
    from openai import OpenAI

    qwen = QwenClient(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=qwen.api_key, base_url=qwen.compatible_base, max_retries=0)


def _item_field(item, name):
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _output_items(response):
    if isinstance(response, dict):
        return response.get("output") or []
    return getattr(response, "output", None) or []


def _response_meta(response):
    def field(obj, name):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    incomplete = field(response, "incomplete_details")
    usage = field(response, "usage")
    choices = field(response, "choices") or []
    finish = ""
    if choices:
        finish = str(field(choices[0], "finish_reason") or "")
    output_tokens = field(usage, "output_tokens")
    if output_tokens is None:
        output_tokens = field(usage, "completion_tokens")
    input_tokens = field(usage, "input_tokens")
    if input_tokens is None:
        input_tokens = field(usage, "prompt_tokens")
    details = field(usage, "output_tokens_details")
    if details is None:
        details = field(usage, "completion_tokens_details")
    reasoning_tokens = field(details, "reasoning_tokens")
    return {
        "status": str(field(response, "status") or ""),
        "incompleteReason": str(field(incomplete, "reason") or ""),
        "finishReason": finish,
        "outputTokens": output_tokens,
        "inputTokens": input_tokens,
        "reasoningTokens": reasoning_tokens,
    }


def _response_items(response):
    items = []
    for item in _output_items(response):
        content = _item_field(item, "content")
        text_len = 0
        if isinstance(content, str):
            text_len = len(content.strip())
        elif isinstance(content, list):
            for part in content:
                value = _item_field(part, "text")
                if isinstance(value, str):
                    text_len += len(value.strip())
        summary = _item_field(item, "summary")
        items.append(
            {
                "type": str(_item_field(item, "type") or ""),
                "textLen": text_len,
                "summaryLen": len(str(summary).strip()) if summary else 0,
            }
        )
    return items


def _response_error(response):
    error = response.get("error") if isinstance(response, dict) else getattr(response, "error", None)
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "")[:180]
    return str(error or "")[:180]


def _message_chars(messages):
    total = 0
    for item in messages or []:
        content = item.get("content") if isinstance(item, dict) else ""
        total += len(str(content or ""))
    return total


def _debug_response(messages, text, meta, items, error):
    # #region agent log
    payload = {
        "sessionId": "ee5fa7",
        "runId": "why-empty",
        "hypothesisId": "C",
        "location": "graph.py:generate",
        "message": "response shape",
        "data": {
            "messageChars": _message_chars(messages),
            "contentLen": len(str(text or "").strip()),
            "status": meta.get("status") or "",
            "incomplete": meta.get("incompleteReason") or "",
            "finish": meta.get("finishReason") or "",
            "outputTokens": meta.get("outputTokens") or 0,
            "inputTokens": meta.get("inputTokens") or 0,
            "reasoningTokens": meta.get("reasoningTokens") or 0,
            "items": items,
            "error": error,
        },
        "timestamp": int(__import__("time").time() * 1000),
    }
    try:
        with _DEBUG_LOCK:
            with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def output_text(response):
    if isinstance(response, dict):
        text = response.get("output_text") or ""
    else:
        text = getattr(response, "output_text", "") or ""
    if isinstance(text, str) and text.strip():
        return text
    parts = []
    for item in _output_items(response):
        if _item_field(item, "type") != "message":
            continue
        content = _item_field(item, "content") or []
        for part in content:
            value = _item_field(part, "text")
            if value:
                parts.append(str(value))
    return "".join(parts)


def function_arguments(response, name):
    for item in _output_items(response):
        if _item_field(item, "type") != "function_call":
            continue
        if _item_field(item, "name") != name:
            continue
        arguments = _item_field(item, "arguments")
        if isinstance(arguments, dict):
            return json.dumps(arguments, ensure_ascii=False)
        return str(arguments or "")
    return output_text(response)


def _reasoning(effort):
    """Requests accept low, high, or max. none falls back to low."""
    name = str(effort or WRITE_EFFORT)
    if name not in _EFFORTS:
        name = "low"
    return {"effort": name}


def _flag_on(name):
    value = (os.environ.get(name) or "").strip().lower()
    return value in ("1", "true", "yes", "on")


def prompt_debug_enabled():
    """True when debug should print the StateGraph structure.

    Prompt text stays in the trace file. The terminal does not print it.
    """
    from AI.qwen_api.client import load_dotenv

    load_dotenv()
    return _flag_on("AIVIDEO_DEBUG_PROMPT") or _flag_on("AIVIDEO_DEBUG_TRACE")


@contextmanager
def bind_trace_prompts(path):
    """Append each full prompt to this file for the current request."""
    token = _TRACE_PROMPTS.set(str(path or ""))
    try:
        yield
    finally:
        _TRACE_PROMPTS.reset(token)


def prompt_text(messages):
    """The text actually sent. Image payloads stay out of the terminal."""
    parts = []
    for message in messages or []:
        if isinstance(message, str):
            parts.append(message)
            continue
        if not isinstance(message, dict):
            parts.append(str(message))
            continue
        role = str(message.get("role") or "")
        content = message.get("content")
        if isinstance(content, list):
            lines = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    lines.append(str(item.get("text") or ""))
                elif isinstance(item, dict):
                    lines.append("[%s]" % (item.get("type") or "part"))
                else:
                    lines.append(str(item))
            content = "\n".join(lines)
        block = str(content or "")
        parts.append("%s\n%s" % (role, block) if role else block)
    return "\n\n".join(parts)


def log_prompt(messages):
    path = _TRACE_PROMPTS.get()
    if not path:
        return
    body = prompt_text(messages)
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"prompt": body}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _create(client, state, effort, tools=None, tool_choice=None):
    log_prompt(state.get("messages") or [])
    kwargs = {
        "model": state.get("model") or TEXT_MODEL,
        "input": state.get("messages") or [],
        "extra_body": {"enable_thinking": False},
    }
    # reasoning.effort low still returns reasoning tokens when enable_thinking is false.
    del effort
    if state.get("max_output_tokens"):
        kwargs["max_output_tokens"] = int(state["max_output_tokens"])
    if state.get("timeout"):
        kwargs["timeout"] = float(state["timeout"])
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    # region agent log
    try:
        _think = {
            "sessionId": "ee5fa7",
            "runId": "post-fix",
            "hypothesisId": "H-think",
            "location": "AI/qwen_api/graph.py:_create",
            "message": "text request flags",
            "data": {
                "model": kwargs.get("model"),
                "hasReasoning": "reasoning" in kwargs,
                "enableThinking": (kwargs.get("extra_body") or {}).get("enable_thinking"),
            },
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as _handle:
            _handle.write(json.dumps(_think, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion
    return _call_with_deadline(client, kwargs, float(state.get("timeout") or 0))


def _call_with_deadline(client, kwargs, timeout):
    """Close the socket when a model call stays open past its deadline."""
    http_client = getattr(client, "_client", None)
    done = threading.Event()

    def abort():
        if done.wait(timeout):
            return
        # region agent log
        try:
            payload = {
                "sessionId": "ee5fa7",
                "runId": "post-fix",
                "hypothesisId": "H-hang",
                "location": "AI/qwen_api/graph.py:_call_with_deadline",
                "message": "model call missed its deadline",
                "data": {"timeout": timeout},
                "timestamp": int(time.time() * 1000),
            }
            with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            pass
        # endregion
        if http_client is None:
            return
        try:
            http_client.close()
        except Exception:
            pass

    watcher = None
    if http_client is not None and timeout > 0:
        watcher = threading.Thread(target=abort, daemon=True)
        watcher.start()
    seq, started = _burst_enter(kwargs)
    try:
        return client.responses.create(**kwargs)
    except Exception as error:
        _burst_fail(seq, started, error)
        raise
    finally:
        _burst_leave()
        done.set()


def _burst_log(hypothesis_id, location, message, data):
    # #region agent log
    payload = {
        "sessionId": "8d7890",
        "runId": "pre-fix",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with _DEBUG_LOCK:
            with open("/Users/shaneson/AiVideo/.cursor/debug-8d7890.log", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def _burst_enter(kwargs):
    # #region agent log
    seq = 0
    try:
        chars = 0
        messages = kwargs.get("input") or []
        if isinstance(messages, list):
            for item in messages:
                if isinstance(item, dict):
                    chars += len(str(item.get("content") or ""))
        with _BURST_LOCK:
            _BURST_STATE["seq"] += 1
            seq = _BURST_STATE["seq"]
            _BURST_STATE["inflight"] += 1
            inflight = _BURST_STATE["inflight"]
            now = time.time()
            last = _BURST_STATE["last"]
            delta = int((now - last) * 1000) if last else -1
            _BURST_STATE["last"] = now
        _burst_log(
            "A",
            "graph.py:_call_with_deadline",
            "model call start",
            {
                "seq": seq,
                "skill": _BURST_SKILL.get(),
                "inflight": inflight,
                "deltaMs": delta,
                "model": str(kwargs.get("model") or ""),
                "messageChars": chars,
                "thread": threading.get_ident(),
            },
        )
    except Exception:
        pass
    return seq, time.time()
    # #endregion


def _burst_fail(seq, started, error):
    # #region agent log
    try:
        text = str(error)[:300]
        status = getattr(error, "status_code", None)
        _burst_log(
            "B",
            "graph.py:_call_with_deadline",
            "model call failed",
            {
                "seq": seq,
                "skill": _BURST_SKILL.get(),
                "elapsedMs": int((time.time() - started) * 1000),
                "status": status,
                "burst": ("BurstRate" in text) or ("Throttling" in text),
                "rate429": status == 429 or "429" in text,
                "errorType": type(error).__name__,
                "error": text,
            },
        )
    except Exception:
        pass
    # #endregion


def _burst_leave():
    # #region agent log
    try:
        with _BURST_LOCK:
            _BURST_STATE["inflight"] = max(0, _BURST_STATE["inflight"] - 1)
    except Exception:
        pass
    # #endregion


def compile_text_graph(client):
    def generate(state):
        effort = state.get("effort") or WRITE_EFFORT
        response = _create(client, state, effort)
        text = output_text(response)
        meta = _response_meta(response)
        items = _response_items(response)
        error = _response_error(response)
        # #region agent log
        _debug_response(state.get("messages") or [], text, meta, items, error)
        # #endregion
        return {
            "output": text,
            "incomplete_reason": meta.get("incompleteReason") or "",
            "finish_reason": meta.get("finishReason") or "",
            "response_status": meta.get("status") or "",
            "output_tokens": meta.get("outputTokens") or 0,
            "input_tokens": meta.get("inputTokens") or 0,
            "reasoning_tokens": meta.get("reasoningTokens") or 0,
            "response_items": items,
            "response_error": error,
        }

    def understand(state):
        response = _create(
            client,
            state,
            INTENT_EFFORT,
            tools=[SUBMIT_INTENT],
            tool_choice="required",
        )
        return {"output": function_arguments(response, INTENT_TOOL)}

    def route(state):
        if state.get("kind") == "understand":
            return "understand"
        return "generate"

    graph = StateGraph(TextState)
    graph.add_node("generate", generate)
    graph.add_node("understand", understand)
    graph.add_conditional_edges(
        START,
        route,
        {"generate": "generate", "understand": "understand"},
    )
    graph.add_edge("generate", END)
    graph.add_edge("understand", END)
    return graph.compile()


def invoke_text(kind, messages, model=None, max_output_tokens=None, timeout=None, client=None, effort=None):
    """Run one text turn and keep the stop reason next to the body."""
    del model
    app = compile_text_graph(responses_client(client=client))
    state = app.invoke(
        {
            "kind": kind,
            "messages": list(messages or []),
            "model": TEXT_MODEL,
            "max_output_tokens": int(max_output_tokens or 0),
            "timeout": int(timeout or 0),
            "effort": effort or "",
            "output": "",
            "incomplete_reason": "",
            "finish_reason": "",
            "response_status": "",
            "output_tokens": 0,
            "input_tokens": 0,
            "reasoning_tokens": 0,
            "response_items": [],
            "response_error": "",
        }
    )
    return {
        "output": state.get("output") or "",
        "incomplete_reason": state.get("incomplete_reason") or "",
        "finish_reason": state.get("finish_reason") or "",
        "status": state.get("response_status") or "",
        "output_tokens": state.get("output_tokens") or 0,
        "input_tokens": state.get("input_tokens") or 0,
        "reasoning_tokens": state.get("reasoning_tokens") or 0,
        "items": state.get("response_items") or [],
        "error": state.get("response_error") or "",
    }


def run_text(kind, messages, model=None, max_output_tokens=None, timeout=None, client=None, effort=None):
    return invoke_text(
        kind,
        messages,
        model=model,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        client=client,
        effort=effort,
    ).get("output") or ""


def generate_text(messages, model=None, max_output_tokens=None, timeout=None, client=None, effort=None):
    return run_text(
        "generate",
        messages,
        model=model,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        client=client,
        effort=effort,
    )


def understand_text(messages, model=None, max_output_tokens=None, timeout=None, client=None):
    return run_text(
        "understand",
        messages,
        model=model,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        client=client,
    )
