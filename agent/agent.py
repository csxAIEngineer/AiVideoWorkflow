"""Agent. Turns a drill or a script task into one model request, then reads the reply."""

from pathlib import Path

from cr_api import is_thinking_model
from end.pipeline import (
    JSON_SHAPE,
    OFFICIAL_MODELS,
    _empty_body_error,
    _reply_text,
    _spec_from_result,
    build_system_prompt,
    drill_system_prompt,
    normalize_model,
    parse_drill_text,
    ready_from_conversation,
    write_llm_debug,
)


class Agent(object):
    def prepare(self, state):
        if state.get("kind") == "write":
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
        )

    def _prepare_drill(self, state):
        name = normalize_model(state.get("model"))
        system = drill_system_prompt()
        chat_messages = [{"role": "system", "content": system}]
        for item in state.get("messages") or []:
            role = item.get("role")
            content = str(item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                chat_messages.append({"role": role, "content": content})
        tokens = 8000 if is_thinking_model(name) and name not in OFFICIAL_MODELS else 4000
        state = dict(state)
        state["model_name"] = name
        state["system"] = system
        state["drill_tokens"] = tokens
        state["repair"] = False
        state["request"] = {
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
            state["output"] = parse_drill_text(result.content or result.reasoning_content)
            state["request"] = None
            return state
        except ValueError:
            reply = _reply_text(result)
            state["reply"] = reply
            if not reply:
                ready = ready_from_conversation(state.get("messages"))
                if ready:
                    state["output"] = ready
                    state["request"] = None
                    return state
                raise RuntimeError("模型没有返回问题。")
        state["repair"] = True
        state["request"] = {
            "messages": [
                {"role": "system", "content": drill_system_prompt()},
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
            state["output"] = parse_drill_text(repaired.content or repaired.reasoning_content)
            state["request"] = None
            return state
        except ValueError:
            ready = ready_from_conversation(state.get("messages"))
            if ready:
                state["output"] = ready
                state["request"] = None
                return state
            reply = state.get("reply") or ""
            preview = ((repaired.content if repaired else "") or reply).strip().replace("\n", " ")[:120]
            raise RuntimeError("追问没有返回 JSON。回复开头是：%s" % preview)

    def _prepare_write(self, state):
        name = normalize_model(state.get("model"))
        system = build_system_prompt(state.get("skill_pack") or "")
        state = dict(state)
        state["model_name"] = name
        state["repair"] = False
        state["first_result"] = None
        state["request"] = {
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
        try:
            spec = _spec_from_result(result)
        except ValueError:
            spec = None
        if spec is not None:
            if debug_dir:
                write_llm_debug(debug_dir, brief, skill_pack, result=result)
            state["output"] = spec
            state["request"] = None
            return state
        reply = _reply_text(result)
        if not reply:
            if debug_dir:
                write_llm_debug(debug_dir, brief, skill_pack, result=result)
            raise RuntimeError(_empty_body_error(state.get("model_name")))
        state["first_result"] = result
        state["reply"] = reply
        state["repair"] = True
        state["request"] = {
            "messages": [
                {"role": "system", "content": "把用户给出的短剧材料收成一个 JSON 对象。" + JSON_SHAPE},
                {"role": "user", "content": reply[:12000]},
            ],
            "model": state.get("model_name"),
            "max_tokens": 16000,
            "timeout": 1200,
        }
        return state

    def _read_repair_write(self, state):
        state = dict(state)
        repaired = state.get("result")
        debug_dir = state.get("debug_dir")
        brief = state.get("brief") or ""
        skill_pack = state.get("skill_pack") or ""
        first = state.get("first_result")
        try:
            spec = _spec_from_result(repaired)
        except ValueError:
            if debug_dir:
                write_llm_debug(debug_dir, brief, skill_pack, result=first, repaired=repaired)
            reply = state.get("reply") or ""
            preview = ((repaired.content if repaired else "") or reply).strip().replace("\n", " ")[:120]
            raise RuntimeError("模型没有返回 JSON。回复开头是：%s" % preview)
        if debug_dir:
            write_llm_debug(debug_dir, brief, skill_pack, result=first, repaired=repaired)
        state["output"] = spec
        state["request"] = None
        return state
