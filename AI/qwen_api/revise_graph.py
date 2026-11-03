"""One StateGraph that rewrites a single asset block or feed unit.

The node sees that block and the user's suggestion. The merge node returns
the rewritten block and does not ask for the rest of the document.
"""

import json

from langgraph.graph import END, START, StateGraph

from AI.qwen_api.graph import TextState, prompt_debug_enabled
from AI.qwen_api.suite_graph import _complete, _node_text, _object, _record, _retry_empty
from AI.script import normalize_model

_TOKENS = 8000
_RETRY_TOKENS = 8000
_TIMEOUT = 300
_SYSTEM = {
    "asset": "\n".join((
        "按用户的修改建议改这一张图的提示词。",
        "没提到的内容保持。不要写其他图。",
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。改完的这一块放在 text。",
    )),
    "shot": "\n".join((
        "按用户的修改建议改这一个投喂单元。",
        "没提到的内容保持。不要写其他镜头。",
        "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。改完的这一块放在 text。",
    )),
}


def run_block(kind, brief, model, model_name=None, trace_path="", kind_name="write"):
    """Rewrite one block and return its new text."""
    app = compile_block_graph(kind, model, kind_name)
    state = app.invoke({
        "kind": kind_name,
        "brief": str(brief or ""),
        "task": "revise",
        "model": normalize_model(model_name),
        "trace_path": str(trace_path or ""),
        "job_id": "",
        "partials": [],
        "output": "",
        "messages": [],
    })
    parsed = _object(state.get("output") or "")
    text = str(parsed.get("text") or "").strip()
    if not text:
        raise RuntimeError("没有生成新的提示词")
    return text


def compile_block_graph(kind, model, kind_name="write"):
    graph = build_block_graph(kind, model, kind_name)
    if prompt_debug_enabled():
        print(describe_block_graph(graph, kind), flush=True)
    return graph.compile()


def build_block_graph(kind, model, kind_name="write"):
    if kind not in _SYSTEM:
        raise ValueError("不能从这里改")
    graph = StateGraph(TextState)
    graph.add_node("revise", _revise_node(kind, model, kind_name))
    graph.add_node("merge", _collect_node(kind))
    graph.add_edge(START, "revise")
    graph.add_edge("revise", "merge")
    graph.add_edge("merge", END)
    return graph


def describe_block_graph(graph, kind):
    """Nodes and edges, read before compile."""
    label = "图块" if kind == "asset" else "投喂单元"
    lines = [
        "StateGraph 修改建议",
        "状态 TextState",
        "节点 %s" % len(graph.nodes),
        "revise  %s" % label,
        "merge  汇总",
        "边",
    ]
    lines.append("START -> revise")
    lines.append("revise -> merge")
    lines.append("merge -> END")
    return "\n".join(lines)


def _revise_node(kind, model, kind_name):
    system = _SYSTEM[kind]

    def node(state):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": state.get("brief") or ""},
        ]
        _record(state.get("trace_path") or "", messages)
        raw = _complete(
            model,
            messages,
            state.get("model"),
            kind_name,
            kind,
            tokens=_TOKENS,
            timeout=_TIMEOUT,
        )
        raw = _retry_empty(
            model,
            messages,
            state.get("model"),
            kind_name,
            kind,
            state.get("trace_path") or "",
            raw,
            tokens=_RETRY_TOKENS,
            timeout=_TIMEOUT,
        )
        return {"partials": [{"skill": kind, "text": _node_text(raw)}]}

    return node


def _collect_node(kind):
    def node(state):
        text = ""
        for item in state.get("partials") or []:
            if isinstance(item, dict) and item.get("skill") == kind:
                text = str(item.get("text") or "").strip()
        return {"output": json.dumps({"text": text}, ensure_ascii=False)}

    return node
