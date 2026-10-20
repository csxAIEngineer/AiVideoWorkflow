"""Agent. Turns a drill or a script task into one model request, then reads the reply.

Speech and music each make one MiniMax request. The reply is audio bytes, not JSON.
"""

from pathlib import Path

from AI.cr_api import is_thinking_model
from AI.script import (
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
from AI.skills.registry import json_shape, repair_lead


SPEECH_TIMEOUT = 60
MUSIC_TIMEOUT = 90
MUSIC_PROMPT_LIMIT = 2000


def speech_request(text):
    """Official MiniMax speech-2.8-hd body. POST /v1/t2a_v2."""
    return {
        "model": "speech-2.8-hd",
        "text": text,
        "stream": False,
        "output_format": "hex",
        "subtitle_enable": False,
        "voice_setting": {
            "voice_id": "male-qn-qingse",
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
        "pronunciation_dict": {"tone": []},
    }


def music_request(prompt):
    """Official MiniMax music-3.0 body. Instrumental: no lyrics and no seed."""
    text = str(prompt or "").strip()
    if len(text) > MUSIC_PROMPT_LIMIT:
        text = text[:MUSIC_PROMPT_LIMIT]
    return {
        "model": "music-3.0",
        "prompt": text,
        "is_instrumental": True,
        "stream": False,
        "output_format": "hex",
        "audio_setting": {
            "sample_rate": 44100,
            "bitrate": 256000,
            "format": "mp3",
        },
    }


class Agent(object):
    def prepare(self, state):
        kind = state.get("kind")
        if kind == "write":
            return self._prepare_write(state)
        if kind == "speech":
            return self._prepare_speech(state)
        if kind == "music":
            return self._prepare_music(state)
        return self._prepare_drill(state)

    def read(self, state):
        kind = state.get("kind")
        if kind in ("speech", "music"):
            return self._read_audio(state)
        if kind == "write":
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
                ready = ready_from_conversation(state.get("messages"), self._template(state))
                if ready:
                    state["output"] = ready
                    state["request"] = None
                    return state
                raise RuntimeError("模型没有返回问题。")
        state["repair"] = True
        state["request"] = {
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
            state["output"] = parse_drill_text(repaired.content or repaired.reasoning_content)
            state["request"] = None
            return state
        except ValueError:
            ready = ready_from_conversation(state.get("messages"), self._template(state))
            if ready:
                state["output"] = ready
                state["request"] = None
                return state
            reply = state.get("reply") or ""
            preview = ((repaired.content if repaired else "") or reply).strip().replace("\n", " ")[:120]
            raise RuntimeError("追问没有返回 JSON。回复开头是：%s" % preview)

    def _prepare_write(self, state):
        name = normalize_model(state.get("model"))
        system = build_system_prompt(state.get("skill_pack") or "", self._template(state))
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
                write_llm_debug(debug_dir, brief, skill_pack, result=result, template=state.get("template"))
            state["output"] = spec
            state["request"] = None
            return state
        reply = _reply_text(result)
        if not reply:
            if debug_dir:
                write_llm_debug(debug_dir, brief, skill_pack, result=result, template=state.get("template"))
            raise RuntimeError(_empty_body_error(state.get("model_name")))
        template = self._template(state)
        state["first_result"] = result
        state["reply"] = reply
        state["repair"] = True
        state["request"] = {
            "messages": [
                {"role": "system", "content": repair_lead(template) + json_shape(template)},
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
                write_llm_debug(
                    debug_dir,
                    brief,
                    skill_pack,
                    result=first,
                    repaired=repaired,
                    template=state.get("template"),
                )
            reply = state.get("reply") or ""
            preview = ((repaired.content if repaired else "") or reply).strip().replace("\n", " ")[:120]
            raise RuntimeError("模型没有返回 JSON。回复开头是：%s" % preview)
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

    def _prepare_speech(self, state):
        text = str(state.get("text") or "").strip()
        if not text:
            raise RuntimeError("没有旁白")
        if not str(state.get("dest") or "").strip():
            raise RuntimeError("没有音频文件")
        state = dict(state)
        state["text"] = text
        state["repair"] = False
        state["request"] = {
            "kind": "speech",
            "body": speech_request(text),
            "timeout": SPEECH_TIMEOUT,
        }
        return state

    def _prepare_music(self, state):
        prompt = str(state.get("text") or "").strip()
        if not prompt:
            raise RuntimeError("没有配乐描述")
        if not str(state.get("dest") or "").strip():
            raise RuntimeError("没有音频文件")
        state = dict(state)
        state["text"] = prompt
        state["repair"] = False
        state["request"] = {
            "kind": "music",
            "body": music_request(prompt),
            "timeout": MUSIC_TIMEOUT,
        }
        return state

    def _read_audio(self, state):
        state = dict(state)
        audio = state.get("result")
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise RuntimeError("没有返回音频")
        dest = Path(state.get("dest"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(bytes(audio))
        state["output"] = str(dest)
        state["request"] = None
        return state
