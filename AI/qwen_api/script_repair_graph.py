"""StateGraph that proposes a script candidate. It does not write the file.

locate, plan, and revise are three calls. review is a fourth call and does not
reuse the revise result as its own pass. A review payload that contains a
replacement script is rejected.
"""

import json

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class RepairState(TypedDict, total=False):
    requirements: str
    script: str
    issues: list
    model: str
    locations: list
    revision_plan: dict
    candidate: str
    added: list
    review_notes: dict
    review_raw: str
    locate_raw: str
    status: str
    reason: str
    calls: list


def _object(raw):
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _quote_in_source(quote, requirements, script):
    text = str(quote or "").strip()
    if not text:
        return False
    return text in str(requirements or "") or text in str(script or "")


def sourced_constraints(requirements, script):
    """Lines the user actually wrote. Nothing inferred is a hard limit."""
    rows = []
    for line in str(requirements or "").splitlines():
        body = line.strip()
        if body:
            rows.append({"text": body, "source": "用户要求", "quote": body})
    return rows


def _issue_map(issues):
    return {str(item.get("id") or ""): item for item in issues or []}


def build_script_repair_graph(complete):
    """Uncompiled graph. ``complete(node, messages)`` is one model call."""

    def locate(state):
        if state.get("status") in ("blocked", "error"):
            return {}
        raw = complete("locate", [
            {"role": "system", "content": "只标出每个问题相关的段落，不要改写剧本，不要新增情节。回复 JSON：{\"locations\":[{\"issue_id\":\"\",\"paragraphs\":[]}]}"},
            {"role": "user", "content": json.dumps({
                "requirements": state.get("requirements") or "",
                "script": state.get("script") or "",
                "issues": state.get("issues") or [],
            }, ensure_ascii=False)},
        ])
        data = _object(raw)
        calls = list(state.get("calls") or [])
        calls.append("locate")
        locate_raw = raw if isinstance(raw, str) else ""
        ids = {str(item.get("id") or "") for item in state.get("issues") or []}
        locations = []
        if isinstance(data, dict):
            for item in data.get("locations") or []:
                if not isinstance(item, dict):
                    continue
                issue_id = str(item.get("issue_id") or "")
                paragraphs = [str(part).strip() for part in (item.get("paragraphs") or []) if str(part).strip()]
                if issue_id in ids and paragraphs:
                    locations.append({"issue_id": issue_id, "paragraphs": paragraphs})
        if not locations:
            return {
                "status": "blocked",
                "reason": "没有有效定位，已停止修订",
                "locations": [],
                "locate_raw": locate_raw[:4000],
                "calls": calls,
            }
        return {"locations": locations, "locate_raw": locate_raw[:4000], "calls": calls}

    def plan(state):
        if state.get("status") in ("blocked", "error"):
            return {}
        raw = complete("plan", [
            {"role": "system", "content": "给最小修订方案。修订建议只是可选方向，不是必须照做的情节。没有原文依据的限制标 uncertain，不要写成硬事实。偏好不要改情节。回复 JSON：{\"changes\":[{\"issue_id\":\"\",\"direction\":\"\",\"linked\":false}],\"constraints\":[{\"text\":\"\",\"quote\":\"\"}]}"},
            {"role": "user", "content": json.dumps({
                "requirements": state.get("requirements") or "",
                "script": state.get("script") or "",
                "issues": state.get("issues") or [],
                "locations": state.get("locations") or [],
                "sourced_constraints": sourced_constraints(state.get("requirements"), state.get("script")),
            }, ensure_ascii=False)},
        ])
        data = _object(raw)
        calls = list(state.get("calls") or [])
        calls.append("plan")
        if not isinstance(data, dict):
            return {"status": "blocked", "reason": "修订方案不是合格 JSON，已停止修订", "calls": calls}
        issues = _issue_map(state.get("issues"))
        changes = []
        for item in data.get("changes") or []:
            if not isinstance(item, dict):
                continue
            issue = issues.get(str(item.get("issue_id") or ""))
            if issue is None or issue.get("severity") == "preference":
                continue
            changes.append({
                "issue_id": str(item.get("issue_id") or ""),
                "direction": str(item.get("direction") or ""),
                "linked": bool(item.get("linked")),
            })
        sourced = []
        uncertain = []
        for item in data.get("constraints") or []:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or item.get("text") or "")
            row = {"text": str(item.get("text") or quote), "quote": quote}
            if _quote_in_source(quote, state.get("requirements"), state.get("script")):
                row["source"] = "已有依据"
                sourced.append(row)
            else:
                row["source"] = "不确定"
                uncertain.append(row)
        needs_change = any(item.get("severity") in ("block", "suspect") for item in (state.get("issues") or []))
        if needs_change and not changes:
            return {
                "status": "blocked",
                "reason": "没有有效修订方案，已停止修订",
                "revision_plan": {"changes": [], "sourced": sourced, "uncertain": uncertain},
                "calls": calls,
            }
        return {"revision_plan": {"changes": changes, "sourced": sourced, "uncertain": uncertain}, "calls": calls}

    def revise(state):
        if state.get("status") in ("blocked", "error"):
            return {}
        raw = complete("revise", [
            {"role": "system", "content": "按方案改一版候选剧本。动作可以跨句或合句改写，不要靠标点判断边界。每一处动作细化都要申报：before 是原稿里的原句片段，after 是候选里的对应片段，declaration 必须整段摘自该问题的 revision。没有这条绑定就不要改情节。不要写机检、审稿、阻断这类说明。回复 JSON：{\"script\":\"\",\"added\":[{\"issue_id\":\"\",\"before\":\"\",\"after\":\"\",\"declaration\":\"\",\"basis\":\"\"}]}"},
            {"role": "user", "content": json.dumps({
                "requirements": state.get("requirements") or "",
                "script": state.get("script") or "",
                "revision_plan": state.get("revision_plan") or {},
                "locations": state.get("locations") or [],
            }, ensure_ascii=False)},
        ])
        data = _object(raw) or {}
        calls = list(state.get("calls") or [])
        calls.append("revise")
        added = []
        for item in data.get("added") or []:
            if isinstance(item, dict):
                added.append({
                    "issue_id": str(item.get("issue_id") or ""),
                    "text": str(item.get("text") or ""),
                    "basis": str(item.get("basis") or ""),
                    "before": str(item.get("before") or ""),
                    "after": str(item.get("after") or ""),
                    "declaration": str(item.get("declaration") or ""),
                })
        return {"candidate": str(data.get("script") or ""), "added": added, "calls": calls}

    def review(state):
        if state.get("status") in ("blocked", "error"):
            return {}
        plan = state.get("revision_plan") or {}
        raw = complete("review", [
            {"role": "system", "content": "独立审稿。不要重写，不要交替换稿。逐项对照 added 的 after 是否兑现该条 declaration、方案 direction 和 sourced 约束。uncertain 是未确定约束，不能当成依据，也不能拿来改目标。方向里的否定也要核对。after 违背否定、借修复改变目标或加入新机制时，写入 new_contradictions 或 unsupported。结构上对得上不等于语义已经许可。回复 JSON：{\"passed\":false,\"theme_ok\":false,\"resolutions\":[{\"id\":\"\",\"resolved\":false,\"evidence\":\"\"}],\"unsupported\":[],\"new_contradictions\":[]}"},
            {"role": "user", "content": json.dumps({
                "requirements": state.get("requirements") or "",
                "original": state.get("script") or "",
                "candidate": state.get("candidate") or "",
                "issues": state.get("issues") or [],
                "added": state.get("added") or [],
                "revision_plan": plan,
                "sourced": plan.get("sourced") or [],
                "uncertain": plan.get("uncertain") or [],
            }, ensure_ascii=False)},
        ])
        data = _object(raw) or {}
        calls = list(state.get("calls") or [])
        calls.append("review")
        review_raw = raw if isinstance(raw, str) else ""
        if any(key in data for key in ("script", "rewrite", "replacement")):
            return {"status": "blocked", "reason": "审稿不能交替换稿", "review_notes": {}, "review_raw": review_raw[:4000], "calls": calls}
        resolutions = data.get("resolutions") if isinstance(data.get("resolutions"), list) else []
        unsupported = [str(item) for item in (data.get("unsupported") or []) if str(item).strip()]
        contradictions = data.get("new_contradictions") if isinstance(data.get("new_contradictions"), list) else []
        review = {
            "passed": bool(data.get("passed")),
            "theme_ok": bool(data.get("theme_ok")),
            "resolutions": resolutions,
            "unsupported": unsupported,
            "new_contradictions": contradictions,
        }
        return {"review_notes": review, "review_raw": review_raw[:4000], "calls": calls, "status": "reviewed"}

    graph = StateGraph(RepairState)
    graph.add_node("locate", locate)
    graph.add_node("plan", plan)
    graph.add_node("revise", revise)
    graph.add_node("review", review)
    graph.add_edge(START, "locate")
    graph.add_edge("locate", "plan")
    graph.add_edge("plan", "revise")
    graph.add_edge("revise", "review")
    graph.add_edge("review", END)
    return graph


def compile_script_repair_graph(complete):
    return build_script_repair_graph(complete).compile()
