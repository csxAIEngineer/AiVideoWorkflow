"""Agent. Turns a drill or a script task into one model request, then reads the reply.

Drill calls go to the intent graph. Writing calls go to the text graph.
Speech is not a model request here.
"""

from pathlib import Path

from AI.agent.batch import begin_batches, incomplete_reason, is_overlong, read_catalog, read_item
from AI.script import (
    TruncatedJSON,
    _continued_spec,
    _empty_body_error,
    _reply_text,
    _spec_from_result,
    _truncated_body,
    build_system_prompt,
    drill_system_prompt,
    explain_parse_failure,
    normalize_model,
    parse_drill_text,
    ready_from_conversation,
    write_llm_debug,
)
from AI.skills.registry import json_shape, repair_lead

CONTINUE_TOKENS = 32000
CONTINUE_LEAD = (
    "下面这段 JSON 在中途被截断了。"
    "从断点接着写，把同一个对象补完。"
    "不要重写已经写过的内容，不要加说明。"
    "只输出断点之后的字符。"
)


class Agent(object):
    def prepare(self, state):
        kind = state.get("kind")
        if kind == "write":
            return self._prepare_write(state)
        return self._prepare_drill(state)

    def read(self, state):
        if state.get("kind") == "write":
            return self._read_write(state)
        return self._read_drill(state)

    def read_repair(self, state):
        if state.get("kind") == "write":
            return self._read_repair_write(state)
        return self._read_repair_drill(state)

    def on_model_error(self, state, _error):
        if state.get("kind") != "write" or not state.get("debug_dir"):
            return
        write_llm_debug(
            state.get("debug_dir"),
            state.get("brief") or "",
            state.get("skill_pack") or "",
            result=state.get("first_result") if state.get("repair") else None,
            template=state.get("template"),
        )

    def _template(self, state):
        return state.get("template")

    def _drill_prompt(self, state):
        return drill_system_prompt(self._template(state), state.get("messages") or [])

    def _prepare_drill(self, state):
        name = normalize_model(state.get("model"))
        system = self._drill_prompt(state)
        chat_messages = [{"role": "system", "content": system}]
        for item in state.get("messages") or []:
            role = item.get("role")
            content = str(item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                chat_messages.append({"role": role, "content": content})
        tokens = 4000
        state = dict(state)
        state["model_name"] = name
        state["system"] = system
        state["drill_tokens"] = tokens
        state["repair"] = False
        state["request"] = {
            "kind": "drill",
            "messages": chat_messages,
            "model": name,
            "max_tokens": tokens,
            "timeout": 180,
        }
        return state

    def _read_drill(self, state):
        state = dict(state)
        result = state.get("result")
        debug_dir = state.get("debug_dir")
        if debug_dir:
            folder = Path(debug_dir)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "drill_skill.txt").write_text(state.get("system") or "", encoding="utf-8")
            (folder / "drill_response.txt").write_text(_reply_text(result), encoding="utf-8")
        try:
            state["output"] = parse_drill_text(
                result.content or result.reasoning_content,
                self._template(state),
            )
            state["request"] = None
            return state
        except ValueError:
            reply = _reply_text(result)
            state["reply"] = reply
            if not reply:
                ready = ready_from_conversation(state.get("messages"), self._template(state))
                if ready:
                    state["output"] = ready
                    state["request"] = None
                    return state
                raise RuntimeError("模型没有返回问题。")
        state["repair"] = True
        state["request"] = {
            "kind": "drill",
            "messages": [
                {"role": "system", "content": self._drill_prompt(state)},
                {"role": "user", "content": "把下面这次追问收成规定的 JSON。\n" + reply[:8000]},
            ],
            "model": state.get("model_name"),
            "max_tokens": state.get("drill_tokens") or 4000,
            "timeout": 180,
        }
        return state

    def _read_repair_drill(self, state):
        state = dict(state)
        repaired = state.get("result")
        debug_dir = state.get("debug_dir")
        if debug_dir:
            folder = Path(debug_dir)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "drill_response_repaired.txt").write_text(_reply_text(repaired), encoding="utf-8")
        try:
            state["output"] = parse_drill_text(
                repaired.content or repaired.reasoning_content,
                self._template(state),
            )
            state["request"] = None
            return state
        except ValueError as error:
            ready = ready_from_conversation(state.get("messages"), self._template(state))
            if ready:
                state["output"] = ready
                state["request"] = None
                return state
            raise RuntimeError(
                explain_parse_failure(
                    error,
                    "追问改写",
                    "第二次仍没有收成规定的 JSON。",
                    title="追问没有返回 JSON。",
                )
            ) from error

    def _prepare_write(self, state):
        name = normalize_model(state.get("model"))
        system = build_system_prompt(
            state.get("skill_pack") or "",
            self._template(state),
            state.get("brief") or "",
        )
        state = dict(state)
        state["model_name"] = name
        state["repair"] = False
        state["first_result"] = None
        state["request"] = {
            "kind": "write",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": state.get("brief") or ""},
            ],
            "model": name,
            "max_tokens": 16000,
            "timeout": 1200,
        }
        return state

    def _read_write(self, state):
        state = dict(state)
        result = state.get("result")
        debug_dir = state.get("debug_dir")
        brief = state.get("brief") or ""
        skill_pack = state.get("skill_pack") or ""
        truncated = False
        try:
            spec = _spec_from_result(result, brief)
        except TruncatedJSON:
            spec = None
            truncated = True
        except ValueError:
            spec = None
        # region agent log
        if spec is None:
            from AI.script import _debug_log

            reply_head = _reply_text(result)[:80]
            _debug_log(
                "C",
                "AI/agent/agent.py:_read_write",
                "first reply missed spec keys",
                {
                    "step": brief.split("剧本步：", 1)[-1][:1] if "剧本步：" in brief else "",
                    "asksForText": "放在 text" in (skill_pack or "") or "放在 text" in brief,
                    "replyStartsWithText": reply_head.lstrip().startswith('{"text"'),
                    "replyHead": reply_head,
                },
            )
        # endregion
        if spec is not None:
            if debug_dir:
                write_llm_debug(debug_dir, brief, skill_pack, result=result, template=state.get("template"))
            state["output"] = spec
            state["request"] = None
            return state
        reply = _reply_text(result)
        if not reply:
            if debug_dir:
                write_llm_debug(debug_dir, brief, skill_pack, result=result, template=state.get("template"))
            raise RuntimeError(_empty_body_error(state.get("model_name")))
        state["first_result"] = result
        state["repair"] = True
        capped = incomplete_reason(result) == "max_output_tokens"
        if truncated or capped:
            body = _truncated_body(result) if truncated else reply
            state["reply"] = body
            if is_overlong(result, body):
                return begin_batches(state, body)
            state["continue_write"] = True
            state["request"] = self._continue_request(state, body)
            return state
        state["continue_write"] = False
        state["reply"] = reply
        state["request"] = self._rewrite_request(state, reply)
        return state

    def _continue_request(self, state, reply):
        return {
            "kind": "write",
            "messages": [
                {"role": "system", "content": CONTINUE_LEAD},
                {"role": "user", "content": reply},
            ],
            "model": state.get("model_name"),
            "max_tokens": CONTINUE_TOKENS,
            "timeout": 1200,
            "effort": "low",
        }

    def _rewrite_request(self, state, reply):
        template = self._template(state)
        brief = state.get("brief") or ""
        shape = repair_lead(template) + "\n" + json_shape(template, brief)
        # region agent log
        from AI.script import _debug_log

        _debug_log(
            "B",
            "AI/agent/agent.py:_rewrite_request",
            "rewrite contract",
            {
                "step": brief.split("剧本步：", 1)[-1][:1] if "剧本步：" in brief else "",
                "shapeAsksText": "放在 text" in shape,
                "shapeAsksScript": "放在 script" in shape,
                "shapeListsSpecFields": "assets 数组" in shape,
                "replyStartsWithText": str(reply or "").lstrip().startswith('{"text"'),
            },
        )
        # endregion
        return {
            "kind": "write",
            "messages": [
                {
                    "role": "system",
                    "content": shape,
                },
                {"role": "user", "content": reply[:12000]},
            ],
            "model": state.get("model_name"),
            "max_tokens": 16000,
            "timeout": 1200,
        }

    def read_batch(self, state):
        if state.get("batch_phase") == "item":
            return read_item(state)
        return read_catalog(state)

    def _read_repair_write(self, state):
        state = dict(state)
        repaired = state.get("result")
        debug_dir = state.get("debug_dir")
        brief = state.get("brief") or ""
        skill_pack = state.get("skill_pack") or ""
        first = state.get("first_result")
        try:
            if state.get("continue_write"):
                spec = _continued_spec(state.get("reply") or "", repaired, brief)
            else:
                spec = _spec_from_result(repaired, brief)
        except ValueError as error:
            if debug_dir:
                write_llm_debug(
                    debug_dir,
                    brief,
                    skill_pack,
                    result=first,
                    repaired=repaired,
                    template=state.get("template"),
                )
            step = "续写" if state.get("continue_write") else "改写"
            why = (
                "第一次回复被判定为写到一半被截断，第二次仍没有补成规格 JSON。"
                if state.get("continue_write")
                else "第一次回复不是合法规格 JSON，第二次改写后仍然不是。"
            )
            raise RuntimeError(explain_parse_failure(error, step, why)) from error
        if debug_dir:
            write_llm_debug(
                debug_dir,
                brief,
                skill_pack,
                result=first,
                repaired=repaired,
                template=state.get("template"),
            )
        state["output"] = spec
        state["request"] = None
        return state
