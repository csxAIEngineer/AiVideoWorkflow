"""Generator, critic, and the feed packing graph."""

from Agents.checks import check_feed_draft
from Agents.worker import (
    DEFAULT_MAX_ATTEMPTS,
    base_state,
    bound_complete,
    compile_worker,
    critic_only,
    decide,
    missing_skill_state,
    normalize_machine,
    parse_critic,
    resolve_skill_pack,
    result_from_state,
    revision_brief,
    run_compiled,
)
from Agents.workers.critic import critic_user
from Agents.workers.generator import run_generator
from end.product_names import DRAFT_NAME

from Agents.workers.feed.common import (
    CRITIC_SYSTEM,
    FeedState,
    STAGE,
    TASK,
)
from Agents.workers.feed.style import (
    agreed_style,
    style_lock_line,
    style_lock_rule,
)
from Agents.workers.feed.extract import (
    _extract_feed_source,
    _join_fold_source,
    _looks_like_feed,
)
from Agents.workers.feed.plan import (
    _DIALOGUE_WRITERS,
    _UNIT_WRITERS,
    _WAN_PACKING_WRITERS,
    _generator_system,
    _h3_locked,
    _machine_text,
    _patch_voiceover,
    apply_packing_plan,
    packing_plan_note,
)
from Agents.workers.feed.skeleton import (
    format_shot_skeleton,
    packing_shot_skeleton,
)
from Agents.workers.feed.repair import (
    last_complete_draft,
    merge_targeted_repair,
)
from Agents.workers.feed.debug import (
    _safe_feed_debug,
    _skill_from_messages,
    _unwrap_complete,
)


def _first_write_user(state):
    note = packing_plan_note(state)
    opening = str(state.get("opening") or "").strip()
    if opening:
        user = opening
        if note:
            user = user.rstrip() + "\n\n" + note
        return user
    pieces = ["任务：提示词封装", "分镜：\n%s" % str(state.get("source") or "").strip()]
    if note:
        pieces.append(note)
    return "\n\n".join(pieces)


def _repair_user(state):
    return revision_brief(
        STAGE,
        "任务：提示词封装",
        state.get("draft") or "",
        state.get("critic_report"),
        state.get("machine_report"),
    )


def generator_messages(state):
    system = {"role": "system", "content": _generator_system(state)}
    if int(state.get("attempt") or 0) < 1:
        return [system, {"role": "user", "content": _first_write_user(state)}]
    user = revision_brief(
        STAGE,
        "任务：提示词封装",
        state.get("draft") or "",
        state.get("critic_report"),
        state.get("machine_report"),
    )
    note = packing_plan_note(state)
    if note:
        user = user + "\n\n" + note
    errors = _machine_text(state.get("machine_report"))
    h3 = _h3_locked(state)
    length = []
    if any(mark in errors for mark in ("时长被压缩", "不能钳成", "必须把这一镜拆开")):
        if h3:
            length.extend(_WAN_PACKING_WRITERS)
        else:
            length.extend(_UNIT_WRITERS)
    if "对白被压缩" in errors:
        if h3:
            length.extend(_WAN_PACKING_WRITERS)
        else:
            length.extend(_DIALOGUE_WRITERS)
    length_paths = []
    seen = set()
    for relative in length:
        if relative in seen:
            continue
        seen.add(relative)
        length_paths.append(relative)
    if length_paths:
        user = user.rstrip() + "\n\n" + "\n".join((
            "时长或对白没过投喂机检。重跑这些节点原文，按已经算好的单元数和 duration 重写画面与对白：",
            *length_paths,
        ))
    if h3 and "画风锁" in errors:
        line = style_lock_line(agreed_style(state))
        user = user.rstrip() + "\n\n" + "\n".join((
            "画风锁没过。不要改写画风，不要翻译，也不要新写一种风格。",
            "每一段 subject_definitions 的第一行必须原样是这一句：",
            line or "（锁定句）",
            "删掉别处自拟的风格句，包括只写在房间或道具主体上的那句。",
            "重跑这些节点时只把锁定句补回去：",
            *_WAN_PACKING_WRITERS,
        ))
    if h3 and "镜号锁" in errors:
        skeleton = packing_shot_skeleton(state)
        user = user.rstrip() + "\n\n" + "\n".join((
            "镜号锁没过。切点和镜号已经按分镜算好。不要自己发明切点，不要沿用全集镜号。",
            "每一段从 [Shot 1] 起。第一镜不写时间。后面每一镜必须紧跟下面的 At MM:SS.mmm。",
            "不要增删、合并或调换镜头，不要改这些时间码。把这个骨架原样补回去，只保留已有的画面和对白。",
            "每个 [Shot N] 下面都要有这一镜的正文。只有标题、没有画面，不算写完。不要把整段散文挂在 [Shot 1] 下面再补空标题。",
            format_shot_skeleton(skeleton) or "（这一段没有分镜骨架）",
            "重跑这些节点时只恢复这个骨架：",
            *_WAN_PACKING_WRITERS,
        ))
    return [system, {"role": "user", "content": user}]


def critic_messages(state):
    notes = []
    if _h3_locked(state):
        rule = style_lock_rule(agreed_style(state))
        if rule:
            notes.append(
                "对照锁定句审查。缺这句、第一行不是这句、或还有别的风格句，severity 写 blocking。"
                "不要改稿，不要另写画风。\n%s" % rule
            )
        skeleton = packing_shot_skeleton(state)
        if skeleton:
            notes.append(
                "对照这个镜号骨架审查。不从 [Shot 1] 起、首镜带时间、切点不是下面的 At 时间、"
                "或多了少了镜头，severity 写 blocking。不要改稿，不要另写切点。\n%s"
                % format_shot_skeleton(skeleton)
            )
    notes.append("待审%s：\n%s" % (DRAFT_NAME, str(state.get("draft") or "").strip()))
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": critic_user(TASK, *notes)},
    ]


def compile_feed_graph(complete, checker=None):
    check = checker or check_feed_draft

    def generate(state):
        attempt = int(state.get("attempt") or 0) + 1
        brief = _first_write_user(state)
        calls = []
        specialist_index = {"n": 0}

        def traced(role, messages):
            text, meta = _unwrap_complete(complete, role, messages)
            try:
                if role == "specialist":
                    specialist_index["n"] += 1
                    label = "specialist-%s" % specialist_index["n"]
                else:
                    label = role or "call"
                logged = text if isinstance(text, str) else "" if text is None else str(text)
                calls.append({
                    "role": role,
                    "label": label,
                    "skill": _skill_from_messages(messages),
                    "text": logged,
                    "meta": meta,
                })
            except Exception:
                pass
            if text is None:
                return ""
            return text

        try:
            produced = run_generator(
                TASK,
                brief,
                role=_generator_system(state),
                complete=traced,
                partials=state.get("partials") or [],
                repair_text=_repair_user(state) if attempt > 1 else "",
            )
        except Exception as exc:
            message = "%s: %s" % (type(exc).__name__, exc)
            _safe_feed_debug(state, attempt, brief, calls, error=message)
            return {
                "attempt": attempt,
                "error": message,
                "generator_note": "model_error",
            }
        raw = produced.get("raw") or ""
        previous = str(state.get("draft") or "")
        extracted, extract_source = _extract_feed_source(raw, previous)
        plan_meta = {}
        planned_text = apply_packing_plan(extracted, state, meta=plan_meta)
        fold_source = _join_fold_source(extract_source, plan_meta.get("fold_source", ""))
        draft = _patch_voiceover(planned_text)
        if attempt > 1:
            base = last_complete_draft(state)
            if base.strip():
                draft = merge_targeted_repair(base, draft, state.get("machine_report"), state)
        _safe_feed_debug(
            state,
            attempt,
            brief,
            calls,
            extracted=extracted,
            planned_text=planned_text,
            fold_source=fold_source,
        )
        note = ""
        if previous.strip() and draft.strip() == previous.strip() and attempt > 1:
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
            return critic_only({
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
            return critic_only({
                "verdict": "revise",
                "issues": [{
                    "target": "",
                    "severity": "note",
                    "detail": "审查调用失败：%s" % exc,
                }],
                "stripped_rewrite": False,
            })
        return critic_only(parse_critic(raw, _looks_like_feed))

    def machine_check(state):
        try:
            produced = check(state.get("draft") or "", state)
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

    return compile_worker(FeedState, generate, critique, machine_check)


def run_feed_loop(
    storyboard,
    *,
    locks=None,
    shots=None,
    script="",
    opening="",
    prior_draft="",
    skill_pack="",
    model=None,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    client=None,
    timeout=None,
    complete=None,
    checker=None,
    project_id="",
    project_folder="",
):
    """Run the feed-packing loop once. 投喂机检 is this loop's hard gate."""
    state = base_state(
        STAGE,
        TASK,
        storyboard,
        opening=opening,
        prior_draft=prior_draft,
        model=model,
        max_attempts=max_attempts,
        extra={
            "locks": dict(locks or {}),
            "storyboard": str(storyboard or ""),
            "script": str(script or ""),
            "shots": list(shots or []),
            "skill_pack": str(skill_pack or ""),
            "project_id": str(project_id or ""),
            "project_folder": str(project_folder or ""),
        },
    )
    if not str(storyboard or "").strip() and not str(opening or "").strip() and not str(prior_draft or "").strip():
        state["status"] = "fail"
        state["stop_reason"] = "missing_storyboard"
        state["passed"] = False
        return result_from_state(state, draft_key="feed")
    try:
        state["skill_pack"] = resolve_skill_pack(
            skill_pack,
            context=opening or storyboard,
            stage="P4",
        )
    except RuntimeError as exc:
        return result_from_state(missing_skill_state(state, exc), draft_key="feed")
    app = compile_feed_graph(
        complete or bound_complete(model, client, timeout),
        checker=checker,
    )
    final = run_compiled(app, state, missing_reason="missing_storyboard", require_text=False)
    return result_from_state(final, draft_key="feed")
