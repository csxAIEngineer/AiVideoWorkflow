"""P2 asset-prompt loop: generator, critic, then the asset-block script.

One call runs one stage and returns. The graph is::

    generate → critique → machine_check
                      ↑          |
                      └── retry ─┘
                             halt when the script passes, or the attempt
                             budget is spent, or the script cannot run

The critic reviews and reports. It never rewrites the draft. The next
``generate`` step is what applies a revision, using the critic report and the
machine-check failures. Passing blocks stay when a revision returns only the
blocks it changed.

The hard gate is ``check_asset_blocks.py`` A1–A8, via
``Agents.machine_check``. Exit 0 passes, including when the script only
printed WARN (A8, and A7 without grid language). This loop does not run
``validate_prompt.py``.

``DEFAULT_MAX_ATTEMPTS`` is 3: the first draft plus two revisions. Attempt 1
counts. The loop stops when ``attempt`` reaches that budget without a passing
script. A later worker should call ``run_p2_asset_loop`` once and return the
dict. It should not keep this process bound to later stages.
"""

import json
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from Agents.machine_check import (
    blocks_of,
    check_asset_draft,
    empty_machine_report,
    load_checker,
)
from Agents.text import complete_text, resolved_model
from Agents.worker import missing_skill_state, resolve_skill_pack
from Agents.workers.critic import critic_user
from Agents.workers.generator import run_generator

STAGE = "P2"
TASK = "asset"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MIN_BLOCKS = 1

_FENCE = re.compile(r"^#{2,6}\s*图", re.M)
_REWRITE_KEYS = (
    "draft",
    "rewrite",
    "rewritten",
    "patched",
    "patch",
    "image_prompts",
    "text",
    "replacement",
    "revised",
    "revised_draft",
    "markdown",
    "content",
)
_CRITIC_STATE_KEYS = ("draft", "image_prompts", "script", "locks", "skill_pack", "asset_list")

GENERATOR_SYSTEM = "\n".join((
    "你是 P2 资产提示词的生成员。你只写资产出图提示词。",
    "审查员只交报告，不会替你改稿。机检是 check_asset_blocks.py，不是第三个模型。",
    "整段回复只能是一个 JSON 对象，不要 Markdown 围栏。资产稿放在 image_prompts 字符串里。",
    "怎么写图块，以技能原文为准。不要把技能原文换成别的摘要。",
    "不要跑 validate_prompt.py，也不要写成封装稿。资产稿还没有封装。",
    "再次提交时交出完整稿。已经通过的图块原样保留，只改失败项点到的块。",
    "审查报告里如果夹了改写稿，那份稿作废。按失败项和审查意见自己重写。",
))

CRITIC_SYSTEM = "\n".join((
    "你是 P2 资产提示词的审查员。你只审查，只交报告。",
    "禁止改写、补丁、重排，禁止交一份新的资产稿。",
    "禁止输出 image_prompts、draft、rewrite、text、markdown，禁止出现「#### 图」标题。",
    "整段回复只能是一个 JSON 对象，不要 Markdown 围栏：",
    '{"verdict":"accept 或 revise","issues":[{"target":"图N","severity":"blocking 或 note","detail":"一句话"}]}',
    "没有要改的地方时 verdict 为 accept，issues 为空数组。",
    "detail 只指出问题，不写替换后的整块正文。",
    "评判标准只是机检技能里的 check_asset_blocks.py（A1–A8）。不要用写作技能原文。",
    "机检脚本会在你之后再跑。你只交报告。WARN 不算失败。",
))


class P2State(TypedDict, total=False):
    """Explicit state for one P2 run. A worker can pass this to the compiled graph."""

    stage: str
    task: str
    script: str
    locks: dict
    asset_list: list
    skill_pack: str
    draft: str
    partials: list
    attempt: int
    max_attempts: int
    min_blocks: int
    model: str
    critic_report: dict
    machine_report: dict
    history: list
    status: str
    stop_reason: str
    passed: bool
    generator_note: str
    error: str


def empty_critic_report():
    return {"verdict": "revise", "issues": [], "stripped_rewrite": False}


def initial_state(
    script,
    *,
    locks=None,
    asset_list=None,
    skill_pack="",
    prior_draft="",
    model=None,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    min_blocks=DEFAULT_MIN_BLOCKS,
):
    """State a worker can show, store, or pass into the compiled graph."""
    budget = int(max_attempts)
    if budget < 1:
        raise ValueError("max_attempts must be >= 1")
    floor = int(min_blocks)
    if floor < 0:
        raise ValueError("min_blocks must be >= 0")
    return {
        "stage": STAGE,
        "task": TASK,
        "script": str(script or ""),
        "locks": dict(locks or {}),
        "asset_list": list(asset_list or []),
        "skill_pack": str(skill_pack or ""),
        "draft": str(prior_draft or ""),
        "attempt": 0,
        "max_attempts": budget,
        "min_blocks": floor,
        "model": resolved_model(model),
        "critic_report": empty_critic_report(),
        "machine_report": empty_machine_report(),
        "history": [],
        "status": "running",
        "stop_reason": "",
        "passed": False,
        "generator_note": "",
        "error": "",
    }


def run_p2_asset_loop(
    script,
    *,
    locks=None,
    asset_list=None,
    skill_pack="",
    prior_draft="",
    model=None,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    min_blocks=DEFAULT_MIN_BLOCKS,
    client=None,
    timeout=None,
    complete=None,
    checker=None,
):
    """Run the P2 asset loop once and return a JSON-ready result.

    Production callers omit ``complete`` and ``checker``. Those two arguments
    exist so tests can substitute the model and, if needed, the gate. The
    default gate is the asset-block script. The default model call is
    ``generate_text`` (``deepseek-v4-flash-0731``).

    ``status`` is ``pass`` only when the script exits 0. ``fail`` carries the
    last critic report and machine report. ``stop_reason`` is
    ``machine_check_pass``, ``budget_exhausted``, ``checker_unavailable``, or
    ``missing_script`` or ``missing_skill_pack``.
    """
    state = initial_state(
        script,
        locks=locks,
        asset_list=asset_list,
        skill_pack=skill_pack,
        prior_draft=prior_draft,
        model=model,
        max_attempts=max_attempts,
        min_blocks=min_blocks,
    )
    if not state["script"].strip():
        state["status"] = "fail"
        state["stop_reason"] = "missing_script"
        state["passed"] = False
        return result_from_state(state)
    try:
        state["skill_pack"] = resolve_skill_pack(
            state.get("skill_pack"),
            template=None,
            context=state.get("script") or "",
            stage="P2",
        )
    except RuntimeError as exc:
        return result_from_state(missing_skill_state(state, exc))
    app = compile_p2_asset_graph(
        complete or _bound_complete(model=model, client=client, timeout=timeout),
        checker=checker,
    )
    final = app.invoke(
        state,
        config={"recursion_limit": state["max_attempts"] * 4 + 4},
    )
    return result_from_state(final)


def compile_p2_asset_graph(complete, checker=None):
    """Compile generate → critique → machine_check.

    ``complete(role, messages)`` returns the model text. ``role`` is
    ``generator`` or ``critic``. ``checker(text, min_blocks)`` returns a
    machine report. The default checker is the asset-block script.
    """
    check = checker or check_asset_draft
    graph = StateGraph(P2State)

    def generate(state):
        attempt = int(state.get("attempt") or 0) + 1
        brief = generator_messages(state)[-1]["content"]
        repair = brief if attempt > 1 else ""
        try:
            produced = run_generator(
                TASK,
                brief,
                role=GENERATOR_SYSTEM,
                complete=complete,
                partials=state.get("partials") or [],
                repair_text=repair,
            )
        except Exception as exc:
            return {
                "attempt": attempt,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "generator_note": "model_error",
            }
        previous = str(state.get("draft") or "")
        draft = apply_generator_output(produced.get("raw") or "", previous=previous)
        note = ""
        if previous.strip() and draft.strip() == previous.strip():
            note = "revision_kept_previous"
        return {
            "attempt": attempt,
            "draft": draft,
            "partials": produced.get("partials") or [],
            "error": "",
            "generator_note": note,
        }

    def critique(state):
        if state.get("generator_note") == "model_error":
            return _critic_only({
                "verdict": "revise",
                "issues": [{
                    "target": "",
                    "severity": "note",
                    "detail": "生成没有返回新稿：%s" % (state.get("error") or ""),
                }],
                "stripped_rewrite": False,
            })
        try:
            raw = complete("critic", critic_messages(state))
        except Exception as exc:
            return _critic_only({
                "verdict": "revise",
                "issues": [{
                    "target": "",
                    "severity": "note",
                    "detail": "审查调用失败：%s" % exc,
                }],
                "stripped_rewrite": False,
            })
        return _critic_only(parse_critic(raw))

    def machine_check(state):
        try:
            report = check(
                state.get("draft") or "",
                min_blocks=int(state.get("min_blocks") or DEFAULT_MIN_BLOCKS),
            )
        except Exception as exc:
            report = empty_machine_report()
            report["fatal"] = "checker_unavailable"
            report["errors"] = ["机检没有跑起来：%s" % exc]
            report["report"] = report["errors"][0]
        report = _normalize_machine(report)
        status, reason = _decision(state, report)
        history = list(state.get("history") or [])
        history.append({
            "attempt": int(state.get("attempt") or 0),
            "draft": str(state.get("draft") or ""),
            "critic_report": dict(state.get("critic_report") or empty_critic_report()),
            "machine_report": report,
            "generator_note": str(state.get("generator_note") or ""),
        })
        return {
            "machine_report": report,
            "status": status,
            "stop_reason": reason,
            "passed": status == "pass",
            "history": history,
        }

    graph.add_node("generate", generate)
    graph.add_node("critique", critique)
    graph.add_node("machine_check", machine_check)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "critique")
    graph.add_edge("critique", "machine_check")
    graph.add_conditional_edges(
        "machine_check",
        route_after_check,
        {"generate": "generate", "halt": END},
    )
    return graph.compile()


def route_after_check(state):
    """Return to generate while the script fails and the budget remains."""
    status, _reason = _decision(state, state.get("machine_report") or {})
    if status == "running":
        return "generate"
    return "halt"


def result_from_state(state):
    """Terminal state a later workflow can store. The draft is the artifact."""
    draft = str(state.get("draft") or "")
    return {
        "stage": STAGE,
        "task": TASK,
        "status": state.get("status") or "fail",
        "passed": bool(state.get("passed")),
        "stop_reason": state.get("stop_reason") or "",
        "draft": draft,
        "image_prompts": draft,
        "attempts": int(state.get("attempt") or 0),
        "max_attempts": int(state.get("max_attempts") or DEFAULT_MAX_ATTEMPTS),
        "model": state.get("model") or resolved_model(None),
        "critic_report": dict(state.get("critic_report") or empty_critic_report()),
        "machine_report": dict(state.get("machine_report") or empty_machine_report()),
        "history": list(state.get("history") or []),
        "blocks": blocks_of(draft),
        "generator_note": state.get("generator_note") or "",
    }


def generator_messages(state):
    sections = [
        "阶段：P2 资产提示词。",
        "剧本：\n%s" % str(state.get("script") or "").strip(),
    ]
    locks = format_locks(state.get("locks"))
    if locks:
        sections.append("锁：\n%s" % locks)
    listed = format_asset_list(state.get("asset_list"))
    if listed:
        sections.append("要写的资产：\n%s" % listed)
    draft = str(state.get("draft") or "").strip()
    attempt = int(state.get("attempt") or 0)
    if draft and attempt >= 1:
        sections.append("上一稿（通过的图块原样保留）：\n%s" % draft)
        critic = format_critic(state.get("critic_report"))
        if critic:
            sections.append("审查报告（只读。审查员没有改稿权）：\n%s" % critic)
        machine = format_machine(state.get("machine_report"))
        if machine:
            sections.append("机检失败与警告（FAIL 必须清掉）：\n%s" % machine)
        sections.append(
            "按审查报告和机检失败项修改上一稿，交出完整 image_prompts。"
            "不要采用审查员夹带的改写。"
        )
    elif draft:
        sections.append("已有草稿。请写成完整资产稿：\n%s" % draft)
    else:
        sections.append("请根据剧本写出完整资产稿。")
    return [
        {"role": "system", "content": GENERATOR_SYSTEM},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def critic_messages(state):
    sections = [
        "阶段：P2 资产提示词审查。只交 JSON 报告，不要改稿。",
        "剧本：\n%s" % str(state.get("script") or "").strip(),
    ]
    locks = format_locks(state.get("locks"))
    if locks:
        sections.append("锁：\n%s" % locks)
    listed = format_asset_list(state.get("asset_list"))
    if listed:
        sections.append("资产清单：\n%s" % listed)
    sections.append("待审稿：\n%s" % str(state.get("draft") or "").strip())
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": critic_user(TASK, *sections)},
    ]


def format_locks(locks):
    """Read-only lock lines the asset writer needs. Asset boards stay 16:9."""
    if not isinstance(locks, dict) or not locks:
        return ""
    lines = ["锁只读。不要改写。"]
    pairs = (
        ("track", "赛道"),
        ("source", "来源"),
        ("aspect", "成片画幅"),
        ("aspect_ratio", "成片画幅"),
        ("style_look", "画风"),
        ("style", "画风"),
        ("combat", "动作强度"),
        ("material_mode", "素材形态"),
    )
    seen = set()
    for key, label in pairs:
        value = str(locks.get(key) or "").strip()
        if not value or label in seen:
            continue
        seen.add(label)
        lines.append("%s：%s" % (label, value))
    model = str(locks.get("model") or "").strip()
    if model == "h3":
        lines.append("出镜：MiniMax H3")
    elif model:
        lines.append("模型：%s" % model)
    lines.append("角色、场景、道具、九宫格整板写 16:9。")
    if str(locks.get("material_mode") or "M1") != "M2":
        lines.append("没有看见图。不要写已核验，也不要写与资产图册一致。")
    return "\n".join(lines)


def format_asset_list(items):
    lines = []
    for item in items or []:
        if isinstance(item, str):
            lines.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or item.get("subject_kind") or ""
        title = item.get("title") or item.get("name") or ""
        asset_id = item.get("id") or ""
        lines.append("%s · %s · %s" % (asset_id, title, kind))
    return "\n".join(line for line in lines if line.strip())


def format_critic(report):
    report = report or {}
    lines = ["verdict: %s" % (report.get("verdict") or "revise")]
    for issue in report.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        lines.append(
            "[%s] %s %s" % (
                issue.get("severity") or "blocking",
                issue.get("target") or "",
                issue.get("detail") or "",
            )
        )
    if report.get("stripped_rewrite"):
        lines.append("审查员附带了改写稿，已丢弃。不要寻找或采用那份稿。")
    return "\n".join(lines).strip()


def format_machine(report):
    report = report or {}
    lines = []
    for error in report.get("errors") or []:
        lines.append(str(error))
    for warning in report.get("warnings") or []:
        lines.append("WARN %s" % warning)
    if not lines:
        text = str(report.get("report") or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def parse_critic(raw):
    """Keep issues. Drop any rewritten draft the model tried to return."""
    data = extract_json_object(raw)
    stripped = _looks_like_draft(raw)
    if not isinstance(data, dict):
        return {
            "verdict": "revise",
            "issues": [{
                "target": "",
                "severity": "blocking",
                "detail": "审查员没有返回审查报告。",
            }],
            "stripped_rewrite": stripped,
        }
    if any(key in data for key in _REWRITE_KEYS):
        stripped = True
    issues = []
    for item in data.get("issues") or []:
        if isinstance(item, str):
            detail, stripped = _clean_detail(item, stripped)
            if detail:
                issues.append({"target": "", "severity": "blocking", "detail": detail})
            continue
        if not isinstance(item, dict):
            continue
        detail, stripped = _clean_detail(
            item.get("detail") or item.get("message") or "",
            stripped,
        )
        if not detail:
            continue
        severity = item.get("severity") or "blocking"
        if severity not in ("blocking", "note"):
            severity = "blocking"
        target = str(item.get("target") or item.get("figure") or "")
        issues.append({"target": target, "severity": severity, "detail": detail})
    blocking = any(issue["severity"] == "blocking" for issue in issues)
    verdict = data.get("verdict")
    if verdict not in ("accept", "revise"):
        verdict = "revise" if blocking else "accept"
    if verdict == "accept" and blocking:
        verdict = "revise"
    if verdict == "revise" and not blocking:
        verdict = "accept"
    return {"verdict": verdict, "issues": issues, "stripped_rewrite": stripped}


def apply_generator_output(raw, previous=""):
    """Turn a generator reply into the next draft.

    A shorter reply is a partial revision: same figure number and title
    replace that block, and blocks the reply did not mention stay.
    """
    text = extract_draft_text(raw).strip()
    previous = str(previous or "")
    if not text:
        return previous
    if not _looks_like_draft(text):
        return previous if previous.strip() else _with_newline(text)
    if previous.strip() and _block_count(text) < _block_count(previous):
        return merge_drafts(previous, text)
    return _with_newline(text)


def extract_draft_text(raw):
    data = extract_json_object(raw)
    if isinstance(data, dict) and "image_prompts" in data:
        return coerce_image_prompts(data.get("image_prompts"))
    if isinstance(data, dict) and isinstance(data.get("text"), str) and _looks_like_draft(data.get("text")):
        return data.get("text") or ""
    cleaned = strip_fence(raw)
    if _looks_like_draft(cleaned):
        return cleaned
    return ""


def coerce_image_prompts(value):
    """Accept the string the prompt asks for, or a list of blocks."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, str):
                parts.append(item.strip())
                continue
            if isinstance(item, dict):
                parts.append(_card_block(index, item))
        return "\n\n".join(part for part in parts if part.strip())
    if isinstance(value, dict):
        parts = []
        for index, (title, body) in enumerate(value.items(), start=1):
            parts.append(_card_block(index, {"title": title, "prompt": body}))
        return "\n\n".join(part for part in parts if part.strip())
    return str(value).strip()


def merge_drafts(previous, new):
    """Keep unmentioned blocks. Replace a block when number and title match."""
    old = _split(previous)
    fresh = _split(new)
    if not fresh:
        return previous if str(previous).endswith("\n") or not previous else previous + "\n"
    if len(fresh) >= len(old):
        return _with_newline(new)
    fresh_by_key = {(number, title): body for number, title, body in fresh}
    used = set()
    merged = []
    for number, title, body in old:
        key = (number, title)
        if key in fresh_by_key:
            merged.append(fresh_by_key[key])
            used.add(key)
        else:
            merged.append(body)
    for number, title, body in fresh:
        key = (number, title)
        if key not in used:
            merged.append(body)
            used.add(key)
    return "\n\n".join(part.strip() for part in merged if str(part).strip()) + "\n"


def extract_json_object(raw):
    text = strip_fence(raw)
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def strip_fence(raw):
    cleaned = str(raw or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _bound_complete(model, client, timeout):
    def complete(role, messages):
        return complete_text(role, messages, model=model, client=client, timeout=timeout)

    return complete


def _critic_only(report):
    update = {"critic_report": report}
    leaked = [key for key in _CRITIC_STATE_KEYS if key in update]
    if leaked:
        raise RuntimeError("审查节点不能改稿：%s" % "、".join(leaked))
    return update


def _decision(state, report):
    report = report or {}
    if report.get("ok"):
        return "pass", "machine_check_pass"
    if report.get("fatal"):
        return "fail", str(report.get("fatal"))
    attempt = int(state.get("attempt") or 0)
    budget = int(state.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    if attempt >= budget:
        return "fail", "budget_exhausted"
    return "running", ""


def _normalize_machine(report):
    base = empty_machine_report()
    if not isinstance(report, dict):
        base["errors"] = ["机检没有返回报告"]
        base["report"] = base["errors"][0]
        return base
    base.update(report)
    base["ok"] = bool(report.get("ok"))
    base["errors"] = [str(item) for item in (report.get("errors") or [])]
    base["warnings"] = [str(item) for item in (report.get("warnings") or [])]
    base["fatal"] = str(report.get("fatal") or "")
    return base


def _clean_detail(detail, stripped):
    text = str(detail or "").strip()
    if _looks_like_draft(text):
        return "审查意见夹带了稿件正文，已丢弃。", True
    if len(text) > 500:
        text = text[:500].rstrip()
    return text, stripped


def _looks_like_draft(text):
    return bool(_FENCE.search(str(text or "")))


def _card_block(index, item):
    title = str(item.get("title") or item.get("name") or "").strip()
    body = str(item.get("prompt") or item.get("text") or "").strip()
    heading = title.lstrip("#").strip()
    if _looks_like_draft(title) or _looks_like_draft(heading):
        head = heading if heading.startswith("#") else "#### %s" % heading
        return "%s\n%s" % (head, body) if body else head
    if re.search(r"图\s*\d+", heading):
        return "#### %s\n%s" % (heading, body) if body else "#### %s" % heading
    label = heading or ("图%d" % index)
    block = "#### 图%d · %s" % (index, label)
    return "%s\n%s" % (block, body) if body else block


def _split(text):
    try:
        return list(load_checker().split_blocks(str(text or "")))
    except (OSError, RuntimeError, AttributeError):
        return []


def _block_count(text):
    return len(_split(text))


def _with_newline(text):
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    return cleaned + "\n"

