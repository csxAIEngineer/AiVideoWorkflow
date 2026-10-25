"""One StateGraph for machine-check repairs.

Each failed item is its own node. The node sees only that item's suggestion
and that item's text. The merge node collects those texts and does not ask
the model to rewrite the document.
"""

import json

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from AI.qwen_api.graph import TextState, prompt_debug_enabled
from AI.qwen_api.suite_graph import _complete, _mark, _node_text, _object, _record, _retry_empty
from AI.script import normalize_model

_SYSTEM = "\n".join((
    "只修这一条建议，让列出的失败项通过机检。",
    "不要写其他图或其他段。",
    "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。改完的这一块放在 text。",
))
_ASSET_REPAIR_TOKENS = 2000
_ASSET_REPAIR_RETRY_TOKENS = 4000
_ASSET_REPAIR_TIMEOUT = 120


def repair_units_in(brief):
    """Units embedded in a 建议修补 brief. Empty when the brief has no list."""
    text = str(brief or "")
    start = text.find("[")
    if start < 0:
        return []
    try:
        data, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    units = []
    for item in data:
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get("id") or "").strip()
        if not unit_id:
            continue
        units.append({
            "id": unit_id,
            "label": str(item.get("label") or unit_id),
            "suggestion": str(item.get("suggestion") or ""),
            "body": str(item.get("body") or ""),
        })
    return units


def repair_job_nodes(brief):
    """Waiting nodes for the job list: one per failed item, then 汇总."""
    nodes = [{"id": unit["id"], "name": unit["label"]} for unit in repair_units_in(brief)]
    nodes.append({"id": "merge", "name": "汇总"})
    return nodes


def run_repair(brief, model, model_name=None, trace_path="", kind="write", job_id=""):
    """Run the repair graph and return the collected blocks."""
    units = repair_units_in(brief)
    # #region agent log
    from AI.qwen_api.graph import _burst_log

    _burst_log(
        "C",
        "repair_graph.py:run_repair",
        "repair fan size",
        {"units": len(units), "ids": [unit.get("id") for unit in units]},
    )
    # #endregion
    tokens, retry_tokens, timeout = _repair_budget(brief)
    app = compile_repair_graph(units, model, kind, tokens, retry_tokens, timeout)
    # region agent log
    try:
        import time
        payload = {
            "sessionId": "ee5fa7",
            "runId": "post-fix",
            "hypothesisId": "H-graph",
            "location": "AI/qwen_api/repair_graph.py",
            "message": "repair graph compiled",
            "data": {"nodes": len(units) + 1, "units": [unit.get("id") for unit in units]},
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-ee5fa7.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion
    state = app.invoke({
        "kind": kind,
        "brief": str(brief or ""),
        "task": "repair",
        "model": normalize_model(model_name),
        "trace_path": str(trace_path or ""),
        "job_id": str(job_id or ""),
        "partials": [],
        "output": "",
        "messages": [],
    })
    parsed = _object(state.get("output") or "")
    repairs = parsed.get("repairs")
    if not isinstance(repairs, list):
        repairs = []
    return {"repairs": repairs, "partials": list(state.get("partials") or [])}


def compile_repair_graph(units, model, kind, tokens=None, retry_tokens=None, timeout=None):
    graph = build_repair_graph(units, model, kind, tokens, retry_tokens, timeout)
    _emit_structure(graph, units)
    return graph.compile()


def build_repair_graph(units, model, kind, tokens=None, retry_tokens=None, timeout=None):
    graph = StateGraph(TextState)
    names = ["fix-%s" % index for index in range(len(units))]

    def fan(state):
        return [Send(name, dict(state)) for name in names]

    for index, unit in enumerate(units):
        graph.add_node(names[index], _repair_node(unit, model, kind, tokens, retry_tokens, timeout))
    graph.add_node("merge", _collect_node(units))
    if names:
        graph.add_conditional_edges(START, fan, names)
        for name in names:
            graph.add_edge(name, "merge")
    else:
        graph.add_edge(START, "merge")
    graph.add_edge("merge", END)
    return graph


def describe_repair_graph(graph, units):
    """Nodes and edges, read before compile."""
    lines = [
        "StateGraph 建议修补",
        "状态 TextState",
        "节点 %s" % len(graph.nodes),
    ]
    labels = {"fix-%s" % index: unit.get("label") or unit.get("id") for index, unit in enumerate(units)}
    for name in graph.nodes:
        if name == "merge":
            lines.append("merge  汇总")
        else:
            lines.append("%s  %s" % (name, labels.get(name, name)))
    lines.append("边")
    for start, end in _structure_edges(graph):
        lines.append("%s -> %s" % (start, end))
    return "\n".join(lines)


def _emit_structure(graph, units):
    if not prompt_debug_enabled():
        return
    print(describe_repair_graph(graph, units), flush=True)


def _repair_budget(brief):
    """One asset block is a short rewrite. Other repair stages keep the full write budget."""
    head = str(brief or "").split("失败项：", 1)[0]
    if "阶段：asset" in head:
        return (_ASSET_REPAIR_TOKENS, _ASSET_REPAIR_RETRY_TOKENS, _ASSET_REPAIR_TIMEOUT)
    return (None, None, None)


def _repair_node(unit, model, kind, tokens=None, retry_tokens=None, timeout=None):
    def node(state):
        _mark(state, unit["id"], "进行中")
        try:
            messages = [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _unit_user(unit)},
            ]
            _record(state.get("trace_path") or "", messages)
            raw = _complete(model, messages, state.get("model"), kind, unit["id"], tokens=tokens, timeout=timeout)
            raw = _retry_empty(
                model,
                messages,
                state.get("model"),
                kind,
                unit["id"],
                state.get("trace_path") or "",
                raw,
                tokens=retry_tokens,
                timeout=timeout,
            )
            text = _node_text(raw)
        except Exception:
            _mark(state, unit["id"], "失败")
            raise
        _mark(state, unit["id"], "完成")
        return {"partials": [{"skill": unit["id"], "text": text}]}

    return node


def _collect_node(units):
    order = [unit["id"] for unit in units]

    def node(state):
        _mark(state, "merge", "进行中")
        found = {}
        for item in state.get("partials") or []:
            if isinstance(item, dict) and item.get("skill"):
                found[str(item.get("skill"))] = str(item.get("text") or "")
        repairs = [{"id": unit_id, "text": found.get(unit_id, "")} for unit_id in order]
        _mark(state, "merge", "完成")
        return {"output": json.dumps({"repairs": repairs}, ensure_ascii=False)}

    return node


def _unit_user(unit):
    return "\n".join((
        "失败项：",
        str(unit.get("suggestion") or ""),
        "要改的这一块：",
        str(unit.get("body") or ""),
    ))


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
    return sorted(set(edges), key=lambda item: (str(item[0]), str(item[1])))
