"""Agent layout. A task enters here and leaves as a drill turn, a script spec, or an audio file."""

from .agent import Agent
from .graph import LangGraph
from .model import LLMModel

_LAYOUT = None


class AgentLayout(object):
    def __init__(self, model=None, agent=None):
        self.model = model or LLMModel()
        self.agent = agent or Agent()
        self.graph = LangGraph(self.agent, self.model)

    def drill(self, messages, debug_dir=None, model=None, template=None):
        state = self.graph.invoke(
            {
                "kind": "drill",
                "model": model,
                "template": template,
                "messages": messages or [],
                "brief": "",
                "skill_pack": "",
                "debug_dir": debug_dir,
                "request": None,
                "result": None,
                "output": None,
                "repair": False,
            }
        )
        if state.get("output") is None:
            raise RuntimeError("模型没有返回问题。")
        return state["output"]

    def write(self, brief, skill_pack, debug_dir=None, model=None, template=None):
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

    def speak(self, text, dest):
        return self._audio("speech", text, dest, "没有生成旁白")

    def compose(self, prompt, dest):
        return self._audio("music", prompt, dest, "没有生成音乐")

    def _audio(self, kind, text, dest, empty):
        state = self.graph.invoke(
            {
                "kind": kind,
                "model": None,
                "messages": [],
                "brief": "",
                "skill_pack": "",
                "debug_dir": None,
                "request": None,
                "result": None,
                "output": None,
                "repair": False,
                "text": text or "",
                "dest": str(dest),
            }
        )
        if not state.get("output"):
            raise RuntimeError(empty)
        return state["output"]


def get_layout():
    global _LAYOUT
    if _LAYOUT is None:
        _LAYOUT = AgentLayout()
    return _LAYOUT
