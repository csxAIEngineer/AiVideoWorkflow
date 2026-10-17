"""LangGraph step inside the agent layout.

One task moves Agent -> LLM Model -> Agent. A reply that is not JSON goes around once.
"""


class LangGraph(object):
    def __init__(self, agent, model):
        self.agent = agent
        self.model = model

    def invoke(self, state):
        state = self.agent.prepare(dict(state))
        trail = ["agent"]
        passes = 0
        while state.get("request") and state.get("output") is None:
            passes += 1
            if passes > 2:
                break
            trail.append("llm")
            try:
                state = self._call_model(state)
            except RuntimeError as error:
                self.agent.on_model_error(state, error)
                raise
            trail.append("agent")
            if passes > 1:
                state = self.agent.read_repair(state)
            else:
                state = self.agent.read(state)
        state = dict(state)
        state["trail"] = trail
        return state

    def _call_model(self, state):
        request = state.get("request") or {}
        kind = request.get("kind") or ""
        if kind == "speech":
            result = self.model.speech(request.get("body") or {}, timeout=request.get("timeout"))
        elif kind == "music":
            result = self.model.music(request.get("body") or {}, timeout=request.get("timeout"))
        else:
            result = self.model.complete(
                request.get("messages") or [],
                model=request.get("model"),
                max_tokens=request.get("max_tokens"),
                timeout=request.get("timeout"),
            )
        state = dict(state)
        state["result"] = result
        state["request"] = None
        return state
