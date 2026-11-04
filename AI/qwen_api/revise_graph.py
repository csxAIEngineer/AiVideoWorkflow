"""One StateGraph that rewrites a single asset block or feed unit.

The node sees that block and the user's suggestion. The merge node returns
the rewritten block and does not ask for the rest of the document.
Before that text is returned, a light check rejects asset ids and shot refs
that were not already in the block, and a shot that is no longer one
recognizable feed unit. It does not build a diff.
"""

import json
import re

from langgraph.graph import END, START, StateGraph

from AI.qwen_api.graph import TextState, prompt_debug_enabled
from AI.qwen_api.suite_graph import _complete, _node_text, _object, _record, _retry_empty
from AI.script import normalize_model

_TOKENS = 8000
_RETRY_TOKENS = 8000
_TIMEOUT = 300
_KEEP = "只改建议点到的句子。没提到的句子、人名、顺序和未点名的内容保持。"
_IDS = "禁止新增或改写当前块里未出现的资产 ID 和镜头引用。"
_JSON = "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。改完的这一块放在 text。"
_SYSTEM = {
    "asset": "\n".join((
        "按用户的感觉和修改建议，只改当前这一块：这一张图的提示词。",
        _KEEP,
        "不要写其他图。",
        _IDS,
        _JSON,
    )),
    "shot": "\n".join((
        "按用户的感觉和修改建议，只改当前这一块：这一个镜头对应的投喂单元。不要整份改写 feed。",
        _KEEP,
        "不要写其他镜头。",
        "保住已有的投喂单元标题行，让现有投喂识别还能认出这一单元。不要改镜号，不要再写第二个单元。",
        _IDS,
        _JSON,
    )),
}
_GUARD_ERROR = "改写超出了当前这一块"
# Revise briefs name the current block, then the suggestion. Unlabeled text is the whole block.
_CURRENT_LABEL = {
    "asset": "当前图块：",
    "shot": "当前投喂单元：",
}
_SUGGESTION_LABEL = "修改建议："
_ASSET_ID = re.compile(
    r"(?<![A-Za-z0-9])(?P<latin>[APU])(?P<latin_n>\d+)(?![A-Za-z0-9])|图\s*(?P<figure>\d+)",
    re.IGNORECASE,
)
_SHOT_REF = re.compile(
    r"\[Shot\s*(?P<bracket>\d+)\]"
    r"|(?<!\[)(?<![A-Za-z])Shot\s*(?P<shot>\d+)(?!\d)"
    r"|(?<![A-Za-z0-9])S(?P<sid>\d{2,})(?![A-Za-z0-9])"
    r"|投喂单元\s*(?P<unit_n>\d+)\s*/\s*(?P<unit_m>\d+)",
    re.IGNORECASE,
)
# A markdown heading that names 投喂单元. Delivery splits units on this shape.
_UNIT_HEAD = re.compile(r"(?m)^#{1,6}[^\n]*投喂单元[^\n]*$")
_FIGURE_HEAD = re.compile(r"(?m)^#{1,6}[^\n]*图\s*\d+[^\n]*$")


def block_system(kind):
    """System text for one asset block or one shot unit."""
    if kind not in _SYSTEM:
        raise ValueError("不能从这里改")
    return _SYSTEM[kind]


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
    _guard_block(kind, brief, text)
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
    system = block_system(kind)

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


def _source_text(kind, brief):
    """The current block, when the brief labels it. Otherwise the whole brief."""
    text = str(brief or "")
    label = _CURRENT_LABEL.get(kind) or ""
    start = text.find(label)
    if start < 0:
        return text
    start += len(label)
    end = text.find(_SUGGESTION_LABEL, start)
    if end < 0:
        return text[start:]
    return text[start:end]


def _ref_tokens(text):
    found = set()
    for match in _ASSET_ID.finditer(text or ""):
        if match.group("latin"):
            found.add("%s%s" % (match.group("latin").upper(), match.group("latin_n")))
        else:
            found.add("图%s" % match.group("figure"))
    for match in _SHOT_REF.finditer(text or ""):
        if match.group("bracket") or match.group("shot"):
            number = match.group("bracket") or match.group("shot")
            found.add("SHOT%s" % int(number))
        elif match.group("sid"):
            found.add("S%s" % match.group("sid"))
        elif match.group("unit_n"):
            found.add("UNIT%s/%s" % (int(match.group("unit_n")), int(match.group("unit_m"))))
    return found


def _unit_heads(text):
    return [line.strip() for line in _UNIT_HEAD.findall(text or "")]


def _norm_head(line):
    compact = re.sub(r"\s+", "", str(line or "").strip())
    return re.sub(r"^#{1,6}", "#", compact)


def _first_line(text):
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _guard_block(kind, brief, text):
    """Reject a rewrite that leaves this block or invents an id the block never had."""
    source = _source_text(kind, brief)
    rewritten = str(text or "").strip()
    if _ref_tokens(rewritten) - _ref_tokens(source):
        raise RuntimeError(_GUARD_ERROR)
    source_heads = _unit_heads(source)
    rewritten_heads = _unit_heads(rewritten)
    if kind == "shot":
        expected = [_norm_head(line) for line in source_heads[:1]]
        got = [_norm_head(line) for line in rewritten_heads]
        if got != expected:
            raise RuntimeError(_GUARD_ERROR)
        if expected:
            first = _first_line(rewritten)
            if not _UNIT_HEAD.match(first) or _norm_head(first) != expected[0]:
                raise RuntimeError(_GUARD_ERROR)
        return
    if len(rewritten_heads) > len(source_heads):
        raise RuntimeError(_GUARD_ERROR)
    if len(_FIGURE_HEAD.findall(rewritten)) > len(_FIGURE_HEAD.findall(source)):
        raise RuntimeError(_GUARD_ERROR)
