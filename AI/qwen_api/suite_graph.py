"""One StateGraph per writing task.

Specialist nodes run together. Each one carries the director and a single
original. Their notes collect on TextState.partials. The merge node writes
the task result.
"""

import json
import threading
import time

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from AI.qwen_api.graph import TextState, _BURST_SKILL, _burst_log, prompt_debug_enabled, prompt_text
# #region agent log
try:
    with open("/Users/shaneson/AiVideo/.cursor/debug-eab7ec.log", "a", encoding="utf-8") as _agent_log:
        _agent_log.write(json.dumps({"sessionId": "eab7ec", "runId": "post-fix", "hypothesisId": "A", "location": "suite_graph.py:15", "message": "imported burst skill", "data": {"name": getattr(_BURST_SKILL, "name", ""), "hasSet": hasattr(_BURST_SKILL, "set")}, "timestamp": int(time.time() * 1000)}, ensure_ascii=False) + "\n")
except OSError:
    pass
# #endregion
from AI.script import _empty_body_error, normalize_model
from AI.skills.registry import _is_repair_context, merge_prompt, skill_node_prompt, suite_files, suite_task_for

_LOG_LOCK = threading.Lock()
_NODE_START_GAP = 2.0
_NODE_START_LOCK = threading.Lock()
_NODE_START_AT = 0.0
_WRITE_TOKENS = 16000
_WRITE_RETRY_TOKENS = 32000
_WRITE_TIMEOUT = 1200
_DRILL_TOKENS = 4000
_DRILL_RETRY_TOKENS = 8000
_DRILL_TIMEOUT = 180
_RETRY_NOTE = "上一次没有正文。不要再写推理。整段回复只能是一个 JSON 对象，正文放在 text。"
_TASK_LABEL = {
    "lock": "立项锁定",
    "route": "赛道分流",
    "script": "剧本",
    "asset": "资产锁定",
    "storyboard": "分镜",
    "feed": "提示词封装",
    "qc": "独立质检",
    "board": "离线看板",
}


def run_suite(task, brief, model, model_name=None, template=None, trace_path="", kind="write", job_id=""):
    """Run the task graph and return the merge object plus partials."""
    del template
    files = suite_files(task, brief)
    # #region agent log
    _burst_log(
        "A",
        "suite_graph.py:run_suite",
        "suite fan size",
        {"task": task, "kind": kind, "files": len(files), "names": list(files)},
    )
    # #endregion
    app = compile_suite_graph(files, task, model, kind, repair=_is_repair_context(brief))
    name = normalize_model(model_name)
    state = app.invoke(
        {
            "kind": kind,
            "brief": str(brief or ""),
            "task": task,
            "model": name,
            "trace_path": str(trace_path or ""),
            "job_id": str(job_id or ""),
            "partials": [],
            "output": "",
            "messages": [],
        }
    )
    parsed = _object(state.get("output") or "")
    if not parsed:
        raise RuntimeError(_empty_body_error(name))
    parsed["partials"] = list(state.get("partials") or [])
    return parsed


def compile_suite_graph(files, task, model, kind, repair=False):
    graph = StateGraph(TextState)

    def fan(state):
        return [Send("skill-%s" % index, dict(state)) for index in range(len(files))]

    for index, relative in enumerate(files):
        graph.add_node("skill-%s" % index, _skill_node(relative, model, kind, repair))
    graph.add_node("merge", _merge_node(task, model, kind))
    graph.add_conditional_edges(START, fan, ["skill-%s" % index for index in range(len(files))])
    for index in range(len(files)):
        graph.add_edge("skill-%s" % index, "merge")
    graph.add_edge("merge", END)
    _emit_structure(graph, files, task)
    return graph.compile()


def describe_suite_graph(graph, files, task):
    """Nodes and edges of one task graph, read before compile."""
    lines = [
        "StateGraph %s" % _TASK_LABEL.get(task, task),
        "状态 TextState",
        "节点 %s" % len(graph.nodes),
    ]
    for name in graph.nodes:
        lines.append("%s  %s" % (name, _node_label(name, files)))
    lines.append("边")
    for start, end in _structure_edges(graph):
        lines.append("%s -> %s" % (start, end))
    return "\n".join(lines)


def _emit_structure(graph, files, task):
    if not prompt_debug_enabled():
        return
    print(describe_suite_graph(graph, files, task), flush=True)


def _node_label(name, files):
    if name == "merge":
        return "汇总"
    if str(name).startswith("skill-"):
        index = int(str(name).split("-", 1)[1])
        if 0 <= index < len(files):
            return files[index]
    return name


def _structure_edges(graph):
    edges = []
    for source, branches in graph.branches.items():
        for branch in branches.values():
            for dest in (branch.ends or {}).values():
                edges.append((source, dest))
    edges.extend(graph.edges)
    for starts, end in getattr(graph, "waiting_edges", ()):
        for start in starts:
            edges.append((start, end))
    return sorted(set(edges), key=_edge_key)


def _edge_key(item):
    start, end = item
    return (_node_rank(start), _node_rank(end), start, end)


def _node_rank(name):
    if name == START:
        return (0, 0)
    if name == "merge":
        return (2, 0)
    if name == END:
        return (3, 0)
    if str(name).startswith("skill-"):
        return (1, int(str(name).split("-", 1)[1]))
    return (1, 0)


def _skill_node(relative, model, kind, repair):
    system = skill_node_prompt(relative, repair=repair)

    def node(state):
        _mark(state, relative, "进行中")
        try:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": state.get("brief") or ""},
            ]
            _record(state.get("trace_path") or "", messages)
            raw = _complete(model, messages, state.get("model"), kind, relative)
            # #region agent log
            _agent_debug(
                "A",
                "suite_graph.py:_skill_node",
                "skill node model body",
                {
                    "task": state.get("task") or "",
                    "skill": relative,
                    "rawLen": len(str(raw or "").strip()),
                    "empty": not str(raw or "").strip(),
                },
            )
            # #endregion
            raw = _retry_empty(model, messages, state.get("model"), kind, relative, state.get("trace_path") or "", raw)
            text = _node_text(raw)
            if "p45-previs" in relative:
                brief = state.get("brief") or ""
                mark = brief.rfind("每一格都要答完六问")
                # region agent log
                _six_log(
                    "A",
                    "suite_graph.py:_skill_node",
                    "p45 specialist body",
                    {
                        "skill": relative,
                        "systemChars": len(system),
                        "briefChars": len(brief),
                        "sixFromEnd": (len(brief) - mark) if mark >= 0 else -1,
                        "textChars": len(text),
                        "hasPanel": ("第" in text and "格" in text),
                        "hasSixLabel": "六问" in text,
                        "jsonShots": "visual_content" in text or "shot_number" in text,
                    },
                )
                # endregion
        except Exception:
            _mark(state, relative, "失败")
            raise
        _mark(state, relative, "完成")
        return {"partials": [{"skill": relative, "text": text}]}

    return node


def _merge_node(task, model, kind):
    system = merge_prompt(task)

    def node(state):
        _mark(state, "merge", "进行中")
        try:
            user = _merge_user(state.get("brief") or "", state.get("partials") or [])
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            _record(state.get("trace_path") or "", messages)
            if task == "storyboard":
                mark = user.rfind("每一格都要答完六问")
                p45 = ""
                for item in state.get("partials") or []:
                    if "p45-previs" in str(item.get("skill") or ""):
                        p45 = str(item.get("text") or "")
                # region agent log
                _six_log(
                    "B",
                    "suite_graph.py:_merge_node",
                    "storyboard merge prompt",
                    {
                        "systemChars": len(system),
                        "userChars": len(user),
                        "sixFromEnd": (len(user) - mark) if mark >= 0 else -1,
                        "partials": len(state.get("partials") or []),
                        "p45Chars": len(p45),
                        "p45HasPanel": ("第" in p45 and "格" in p45),
                        "p45JsonShots": "visual_content" in p45 or "shot_number" in p45,
                    },
                )
                # endregion
            raw = _complete(model, messages, state.get("model"), kind, "merge")
            # #region agent log
            _agent_debug(
                "D",
                "suite_graph.py:_merge_node",
                "merge node model body",
                {
                    "task": task,
                    "partials": len(state.get("partials") or []),
                    "rawLen": len(str(raw or "").strip()),
                    "empty": not str(raw or "").strip(),
                },
            )
            # #endregion
            raw = _retry_empty(model, messages, state.get("model"), kind, "merge", state.get("trace_path") or "", raw)
            if not str(raw or "").strip():
                raise RuntimeError(_empty_body_error(state.get("model")))
        except Exception:
            _mark(state, "merge", "失败")
            raise
        _mark(state, "merge", "完成")
        return {"output": raw}

    return node


def _six_log(hypothesis, location, message, data):
    try:
        line = json.dumps(
            {
                "sessionId": "ee5fa7",
                "runId": "pre",
                "hypothesisId": hypothesis,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            },
            ensure_ascii=False,
        )
        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _mark(state, node_id, status):
    job_id = (state or {}).get("job_id") or ""
    if not job_id:
        return
    from AI.qwen_api.jobs import set_node

    set_node(job_id, node_id, status)


def _merge_user(brief, partials):
    blocks = ["简报：", brief, "", "各节点正文："]
    for item in partials:
        blocks.extend(["", "## %s" % (item.get("skill") or ""), str(item.get("text") or "")])
    return "\n".join(blocks)


def _retry_empty(model, messages, model_name, kind, skill, trace_path, raw, tokens=None, timeout=None):
    """One more call for this node when the first body is empty. Other nodes stay as they are."""
    if str(raw or "").strip():
        return raw
    if tokens is None:
        tokens = _DRILL_RETRY_TOKENS if kind == "drill" else _WRITE_RETRY_TOKENS
    follow = list(messages) + [{"role": "user", "content": _RETRY_NOTE}]
    _record(trace_path, follow)
    return _complete(model, follow, model_name, kind, skill, tokens=tokens, timeout=timeout)


def _pace_node(model, skill):
    """Give each live node its own start slot so a fan-out does not burst."""
    from AI.agent.model import LLMModel

    if not isinstance(model, LLMModel):
        return 0.0
    global _NODE_START_AT
    with _NODE_START_LOCK:
        now = time.time()
        slot = max(now, _NODE_START_AT)
        _NODE_START_AT = slot + _NODE_START_GAP
        wait = slot - now
    # #region agent log
    try:
        with open("/Users/shaneson/AiVideo/.cursor/debug-eab7ec.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"sessionId": "eab7ec", "runId": "post-fix", "hypothesisId": "A", "location": "suite_graph.py:_pace_node", "message": "node start slot", "data": {"skill": str(skill or ""), "waitMs": int(wait * 1000), "gapMs": int(_NODE_START_GAP * 1000)}, "timestamp": int(time.time() * 1000)}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion
    if wait > 0:
        time.sleep(wait)
    return wait


def _complete(model, messages, model_name, kind, skill="", tokens=None, timeout=None):
    if tokens is None:
        tokens = _DRILL_TOKENS if kind == "drill" else _WRITE_TOKENS
    if timeout is None:
        timeout = _DRILL_TIMEOUT if kind == "drill" else _WRITE_TIMEOUT
    token = _BURST_SKILL.set(str(skill or ""))
    try:
        _pace_node(model, skill)
        result = model.complete(
            messages,
            model_name,
            tokens,
            timeout,
            kind=kind,
        )
    finally:
        _BURST_SKILL.reset(token)
    content = getattr(result, "content", "") or ""
    raw = getattr(result, "raw", None) or {}
    # #region agent log
    _agent_debug(
        "B",
        "suite_graph.py:_complete",
        "response meta",
        {
            "skill": skill,
            "messageChars": sum(len(str((item or {}).get("content") or "")) for item in messages),
            "contentLen": len(str(content).strip()),
            "status": str(raw.get("status") or ""),
            "incomplete": str(raw.get("incompleteReason") or ""),
            "finish": str(raw.get("finishReason") or ""),
            "outputTokens": raw.get("outputTokens") or 0,
            "inputTokens": raw.get("inputTokens") or 0,
            "reasoningTokens": raw.get("reasoningTokens") or 0,
            "items": raw.get("items") or [],
            "error": str(raw.get("error") or "")[:180],
        },
    )
    # #endregion
    return content


def _agent_debug(hypothesis_id, location, message, data):
    # #region agent log
    payload = {
        "sessionId": "ee5fa7",
        "runId": "node-id",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with _LOG_LOCK:
            with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def _record(path, messages):
    body = prompt_text(messages)
    if not path:
        return
    line = json.dumps({"prompt": body}, ensure_ascii=False)
    with _LOG_LOCK:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _flatten_prompt_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_flatten_prompt_text(item) for item in value]
        return "\n\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("image_prompts", "prompt", "text", "markdown", "content"):
            if value.get(key):
                found = _flatten_prompt_text(value[key])
                if found:
                    return found
        return ""
    return ""


def _node_text(raw):
    data = _object(raw)
    text = str(data.get("text") or data.get("reply") or "").strip()
    prompts = _flatten_prompt_text(data.get("image_prompts"))
    use_prompts = bool(prompts) and ("####" in prompts) and ("####" not in text)
    # region agent log
    if use_prompts or (prompts and not text):
        _agent_debug(
            "B",
            "suite_graph.py:_node_text",
            "unwrapped image_prompts",
            {
                "textLen": len(text),
                "promptLen": len(prompts),
                "usePrompts": use_prompts,
            },
        )
    # endregion
    if use_prompts:
        return prompts
    if text:
        return text
    if prompts:
        return prompts
    fallback = str(raw or "").strip()
    if not fallback:
        raise RuntimeError("这一份原文没有正文")
    return fallback


def _object(raw):
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        newline = text.find("\n")
        if newline >= 0:
            text = text[newline + 1 :]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {"text": text}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {"text": text}
    if not isinstance(data, dict):
        return {"text": text}
    return data


def task_for_brief(brief):
    return suite_task_for(brief)
