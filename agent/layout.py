"""Agent layout. A task enters here and leaves as a drill turn or a script spec."""

from agent.agent import Agent
from agent.graph import LangGraph
from agent.model import LLMModel

_LAYOUT = None


class AgentLayout(object):
    def __init__(self, model=None, agent=None):
        self.model = model or LLMModel()
        self.agent = agent or Agent()
        self.graph = LangGraph(self.agent, self.model)

    def drill(self, messages, debug_dir=None, model=None):
        state = self.graph.invoke(
            {
                "kind": "drill",
                "model": model,
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

    def write(self, brief, skill_pack, debug_dir=None, model=None):
        state = self.graph.invoke(
            {
                "kind": "write",
                "model": model,
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


def get_layout():
    global _LAYOUT
    if _LAYOUT is None:
        _LAYOUT = AgentLayout()
    return _LAYOUT
