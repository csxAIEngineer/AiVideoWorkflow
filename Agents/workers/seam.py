"""Storyboard-seam worker: generate → critique → machine check.

The generator writes the seam ledger and the official H3 fields. The critic
only reports. The machine check is the hard gate. Model calls stay here.
"""

import json
from pathlib import Path
from typing import TypedDict

from Agents.worker import (
    DEFAULT_MAX_ATTEMPTS,
    base_state,
    bound_complete,
    compile_worker,
    critic_only,
    decide,
    extract_json_object,
    normalize_machine,
    parse_critic,
    result_from_state,
    revision_brief,
    run_compiled,
)
from Agents.workers.generator import run_generator
from end.seam_ledger import (
    DEFAULT_SCENE_RELATION,
    FIRST_UNIT_SCENE_RELATION,
    SCENE_RELATIONS,
    apply_submit_params,
    check_seam_ledger,
    normalize_entry_ids,
    write_unit_fields,
)

STAGE = "seam"
TASK = "seam"
SKILL_FILE = "segment-splicing/generator.md"
_CRITIC_SKILL = Path(__file__).resolve().parents[1] / "skills" / "segment-splicing" / "critic.md"

_SCENE_RELATION_RULE = (
    "scene_relation 必须恰好是这些英文之一：%s。"
    "不要写中文。第一个封装单元没有入缝，这项空着或认不出时写 %s；"
    "后面的单元空着或认不出时写 %s。已经是这些词就保持原词。"
    % (
        "、".join(SCENE_RELATIONS),
        FIRST_UNIT_SCENE_RELATION,
        DEFAULT_SCENE_RELATION,
    )
)

GENERATOR_SYSTEM = "\n".join((
    "你是分镜衔接生成员。你只写已经封装好的单元之间的衔接账本，放在 seam_ledger。",
    "一条封装单元恰好一条。entries 的条数必须等于封装单元数。不要把多个单元收成更少的段。",
    "shot_id 必须原样照抄封装单元 id。id 是「封装单元 N/M」，和标题【封装单元 N/M】同一形式，例如「封装单元 1/3」。旧稿标题若是投喂单元，id 就是「投喂单元 N/M」。不要写成「封装单元 1」，不要改成 1、2、3，也不要写成 S01。",
    "审查员只交报告，不会替你改账。机检核对条数、id、D 锁、景别、可截计划和官方 video_mode。",
    "怎么判 ①②③④，以技能原文为准。拿不准就按分流表，不要把 ① 当成唯一默认。剧情段默认 ②。",
    "第一个封装单元 d_lock 写 none。每个后面的单元恰好一条入缝。",
    "video_mode 只能是 text、first_frame、first_last_frame、reference。一次调用不能混用帧和参考图。",
    _SCENE_RELATION_RULE,
    "① 的首帧来源写 previous_tail，这一发不要带参考图。② 不要带上一段尾巴，也不要写定格。",
    "五种写法是封装策略，不要在账本里再选一套 writing_mode。",
    "审查报告里如果夹了改写账，那份账作废。",
))

CRITIC_SYSTEM = "\n".join((
    "你是分镜衔接审查员。你只审查，只交报告。",
    "禁止改写账本，禁止输出 seam_ledger、entries、draft、rewrite、text。",
    "整段回复只能是一个 JSON 对象：",
    '{"verdict":"accept 或 revise","issues":[{"target":"封装单元 2/3","severity":"blocking 或 note","detail":"一句话"}]}',
    "没有要改的地方时 verdict 为 accept，issues 为空数组。",
    "detail 只指出哪一条缝错了，不写替换后的整条。",
    "评判标准只是衔接审查技能。机检会在你之后再跑。你只交报告。",
))


class SeamState(TypedDict, total=False):
    stage: str
    task: str
    source: str
    opening: str
    draft: str
    storyboard: str
    shots: list
    units: list
    feed: str
    ledger: dict
    partials: list
    attempt: int
    max_attempts: int
    model: str
    critic_report: dict
    machine_report: dict
    history: list
    status: str
    stop_reason: str
    passed: bool
    generator_note: str
    error: str


def _critic_skill():
    return _CRITIC_SKILL.read_text(encoding="utf-8").strip()


def _looks_like_ledger(text):
    raw = str(text or "")
    return "seam_ledger" in raw or '"d_lock"' in raw


def ledger_from_raw(raw):
    data = extract_json_object(raw)
    if not isinstance(data, dict):
        return None
    body = data.get("seam_ledger")
    if isinstance(body, dict) and isinstance(body.get("entries"), list):
        return body
    if isinstance(data.get("entries"), list):
        return {"entries": data.get("entries")}
    return None


def _unit_brief(units):
    rows = []
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        rows.append({
            "id": unit.get("id"),
            "duration": unit.get("duration"),
            "images": list(unit.get("images") or []),
        })
    return rows


def _official_ids(state):
    ids = []
    for unit in (state or {}).get("units") or []:
        if not isinstance(unit, dict):
            continue
        unit_id = str(unit.get("id") or "").strip()
        if unit_id:
            ids.append(unit_id)
    return ids


def _id_line(state):
    """The same sentence the machine check uses when an id does not match."""
    ids = _official_ids(state)
    if not ids:
        return ""
    return (
        "要原样照抄这些 id：%s。不要写成「封装单元 1」，不要改成 1、2、3，也不要写成 S01。"
        % "、".join(ids)
    )


def _brief(state):
    units = (state or {}).get("units") or []
    ids = _official_ids(state)
    parts = [
        "任务：分镜衔接",
        "对象是已经封装好的单元，不是分镜镜头。一条封装单元恰好一条段缝。",
        "不要把 %s 个单元收成更少的段。" % len(ids),
        _id_line(state),
        _SCENE_RELATION_RULE,
        "只写段缝。段内五维不要写进账本。",
        "剧本：\n%s" % str(state.get("source") or "").strip(),
        "分镜：\n%s" % str(state.get("storyboard") or "").strip(),
        "封装单元：\n%s" % json.dumps(_unit_brief(units), ensure_ascii=False),
    ]
    feed = str(state.get("feed") or "").strip()
    if feed:
        parts.append("封装稿：\n%s" % feed)
    return "\n\n".join(part for part in parts if part)


def _with_ids(text, state):
    extra = _id_line(state)
    body = str(text or "").strip()
    if not extra:
        return body
    if extra in body:
        return body
    if not body:
        return extra
    return "%s\n\n%s" % (body, extra)


def _with_scene_relation(text):
    """Repair and opening briefs do not rebuild the task brief, so append the closed set."""
    rule = _SCENE_RELATION_RULE
    body = str(text or "").strip()
    if rule in body:
        return body
    if not body:
        return rule
    return "%s\n\n%s" % (body, rule)


def generator_messages(state):
    system = {"role": "system", "content": GENERATOR_SYSTEM}
    if int(state.get("attempt") or 0) >= 1:
        user = revision_brief(
            STAGE,
            "任务：分镜衔接",
            state.get("draft") or "",
            state.get("critic_report"),
            state.get("machine_report"),
        )
        return [system, {"role": "user", "content": _with_scene_relation(_with_ids(user, state))}]
    opening = str(state.get("opening") or "").strip()
    if opening:
        return [system, {"role": "user", "content": _with_scene_relation(_with_ids(opening, state))}]
    return [system, {"role": "user", "content": _with_scene_relation(_brief(state))}]


def critic_messages(state):
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": "\n\n".join((
            "审查技能（只读）：\n%s" % _critic_skill(),
            "待审分镜衔接：\n%s" % str(state.get("draft") or "").strip(),
        ))},
    ]


def _blocking_critic(report):
    found = []
    for issue in (report or {}).get("issues") or []:
        if not isinstance(issue, dict) or issue.get("severity") == "note":
            continue
        detail = str(issue.get("detail") or "").strip()
        if not detail:
            continue
        target = str(issue.get("target") or "").strip()
        found.append(("%s %s" % (target, detail)).strip())
    return found


def compile_seam_graph(complete, checker=None):
    check = checker or check_seam_ledger

    def generate(state):
        attempt = int(state.get("attempt") or 0) + 1
        brief = generator_messages(state)[-1]["content"]
        try:
            produced = run_generator(
                TASK,
                brief,
                role=GENERATOR_SYSTEM,
                complete=complete,
                partials=state.get("partials") or [],
                repair_text=brief if attempt > 1 else "",
                files=[SKILL_FILE],
            )
        except Exception as exc:
            return {
                "attempt": attempt,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "generator_note": "model_error",
            }
        raw = produced.get("raw") or ""
        parsed = ledger_from_raw(raw)
        previous = str(state.get("draft") or "")
        note = ""
        if not isinstance(parsed, dict):
            draft = previous
            note = "ledger_unreadable" if attempt > 1 or not previous else "ledger_unreadable"
            ledger = state.get("ledger") or {}
        else:
            units = state.get("units") or []
            aligned = normalize_entry_ids(parsed.get("entries") or [], units)
            entries = apply_submit_params(aligned, units)
            ledger = {"entries": entries}
            draft = json.dumps({"seam_ledger": ledger}, ensure_ascii=False)
            if previous.strip() and draft.strip() == previous.strip() and attempt > 1:
                note = "revision_kept_previous"
        return {
            "attempt": attempt,
            "draft": draft,
            "ledger": ledger if isinstance(parsed, dict) else (state.get("ledger") or {}),
            "error": "",
            "generator_note": note,
            "partials": produced.get("partials") or [],
        }

    def critique(state):
        if state.get("generator_note") == "model_error":
            return critic_only({
                "verdict": "revise",
                "issues": [{
                    "target": "",
                    "severity": "note",
                    "detail": "生成没有返回新账：%s" % (state.get("error") or ""),
                }],
                "stripped_rewrite": False,
            })
        try:
            raw = complete("critic", critic_messages(state))
        except Exception as exc:
            return critic_only({
                "verdict": "revise",
                "issues": [{
                    "target": "",
                    "severity": "note",
                    "detail": "审查调用失败：%s" % exc,
                }],
                "stripped_rewrite": False,
            })
        return critic_only(parse_critic(raw, _looks_like_ledger))

    def machine_check(state):
        ledger = state.get("ledger") if isinstance(state.get("ledger"), dict) else {}
        if not ledger:
            parsed = ledger_from_raw(state.get("draft") or "")
            ledger = parsed if isinstance(parsed, dict) else {}
        try:
            produced = check(ledger, state.get("units") or [])
        except Exception as exc:
            produced = {
                "ok": False,
                "fatal": "checker_unavailable",
                "exit_code": 2,
                "errors": ["机检没有跑起来：%s" % exc],
                "warnings": [],
                "report": "机检没有跑起来：%s" % exc,
                "needs_human": False,
            }
        produced = normalize_machine(produced)
        semantic = _blocking_critic(state.get("critic_report"))
        if semantic:
            produced["errors"] = list(produced.get("errors") or []) + semantic
            produced["ok"] = False
            produced["report"] = "\n".join(produced["errors"])
        status, reason = decide(state, produced)
        history = list(state.get("history") or [])
        history.append({
            "attempt": int(state.get("attempt") or 0),
            "draft": str(state.get("draft") or ""),
            "critic_report": dict(state.get("critic_report") or {}),
            "machine_report": produced,
            "generator_note": str(state.get("generator_note") or ""),
        })
        return {
            "machine_report": produced,
            "status": status,
            "stop_reason": reason,
            "passed": status == "pass",
            "history": history,
        }

    return compile_worker(SeamState, generate, critique, machine_check)


def run_seam_loop(
    script,
    storyboard="",
    *,
    units=None,
    shots=None,
    feed="",
    opening="",
    prior_draft="",
    model=None,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    client=None,
    timeout=None,
    complete=None,
    checker=None,
):
    """Run the seam loop once. ``complete`` and ``checker`` are test seams.

    ``units`` are the packed feed units. ``shots`` is accepted only as a
    fallback name for those same unit records.
    """
    rows = list(units if units is not None else (shots or []))
    state = base_state(
        STAGE,
        TASK,
        script,
        opening=opening,
        prior_draft=prior_draft,
        model=model,
        max_attempts=max_attempts,
        extra={
            "storyboard": str(storyboard or ""),
            "units": rows,
            "shots": rows,
            "feed": str(feed or ""),
            "ledger": {},
        },
    )
    if not rows and not str(opening or "").strip():
        state["status"] = "fail"
        state["stop_reason"] = "missing_feed"
        state["passed"] = False
        return result_from_state(state, draft_key="seam_ledger")
    app = compile_seam_graph(
        complete or bound_complete(model, client, timeout),
        checker=checker,
    )
    final = run_compiled(app, state, missing_reason="missing_feed", require_text=False)
    result = result_from_state(final, draft_key="seam_ledger")
    ledger = final.get("ledger") if isinstance(final.get("ledger"), dict) else {}
    result["seam_ledger"] = ledger
    result["feed_units"] = write_unit_fields(rows, ledger.get("entries") or [])
    result["units"] = result["feed_units"]
    return result
