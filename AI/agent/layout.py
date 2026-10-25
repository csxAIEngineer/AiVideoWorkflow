"""Agent layout. Drill uses the intent graph, writing uses the text graph, speech uses audio.py."""

import json

from AI.qwen_api.graph import _TRACE_PROMPTS
from AI.qwen_api.suite_graph import run_suite
from AI.script import parse_drill_text
from AI.skills.registry import suite_task_for

from .agent import Agent
from .graph import LangGraph
from .model import LLMModel

_LAYOUT = None


class AgentLayout(object):
    def __init__(self, model=None, agent=None):
        self.model = model or LLMModel()
        self.agent = agent or Agent()
        self.graph = LangGraph(self.agent, self.model)

    def drill(self, messages, debug_dir=None, model=None, template=None, job_id=""):
        lines = []
        for item in messages or []:
            role = str((item or {}).get("role") or "")
            content = str((item or {}).get("content") or "").strip()
            if role in ("user", "assistant") and content:
                lines.append("%s\n%s" % (role, content))
        parsed = run_suite(
            "lock",
            "任务：立项锁定\n" + "\n\n".join(lines),
            self.model,
            model_name=model,
            template=template,
            trace_path=_TRACE_PROMPTS.get(),
            kind="drill",
            job_id=job_id,
        )
        payload = {
            "phase": parsed.get("phase") or "",
            "reply": parsed.get("reply") or "",
            "options": parsed.get("options") or [],
            "brief": parsed.get("brief") or "",
        }
        return parse_drill_text(json.dumps(payload, ensure_ascii=False), template)

    def write(self, brief, skill_pack, debug_dir=None, model=None, template=None, job_id=""):
        task = suite_task_for(brief)
        # #region agent log
        from AI.qwen_api.graph import _burst_log

        _burst_log(
            "D",
            "layout.py:write",
            "write path",
            {
                "task": task or "single",
                "briefChars": len(brief or ""),
                "feed": "任务：提示词封装" in (brief or ""),
                "repair": task == "repair",
            },
        )
        # #endregion
        if task == "repair":
            from AI.qwen_api.repair_graph import run_repair

            return run_repair(
                brief or "",
                self.model,
                model_name=model,
                trace_path=_TRACE_PROMPTS.get(),
                kind="write",
                job_id=job_id,
            )
        if task:
            return run_suite(
                task,
                brief or "",
                self.model,
                model_name=model,
                template=template,
                trace_path=_TRACE_PROMPTS.get(),
                kind="write",
                job_id=job_id,
            )
        state = self.graph.invoke(
            {
                "kind": "write",
                "model": model,
                "template": template,
                "messages": [],
                "brief": brief or "",
                "skill_pack": skill_pack or "",
                "debug_dir": debug_dir,
                "request": None,
                "result": None,
                "output": None,
                "repair": False,
            }
        )
        if state.get("output") is None:
            raise RuntimeError("模型没有返回结果")
        return state["output"]

    def speak(self, text, dest, voice_id=None):
        from AI.qwen_api.audio import synthesize

        return str(synthesize(text, dest, voice_id=voice_id))

    def compose(self, prompt, dest):
        del prompt, dest
        from AI.qwen_api.audio import reject_music

        reject_music()


def get_layout():
    global _LAYOUT
    if _LAYOUT is None:
        _LAYOUT = AgentLayout()
    return _LAYOUT
