"""LangGraph step inside the agent layout.

One task moves Agent -> LLM Model -> Agent.
A short truncated reply continues once. An over-long truncation
walks a batch catalog, then each batch, up to 8 model calls.
"""

from AI.agent.batch import MODEL_CALL_CAP

WRITE_CALL_LIMIT = 2


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
            limit = MODEL_CALL_CAP if state.get("batch_mode") else WRITE_CALL_LIMIT
            if passes > limit:
                self._raise_if_capped(state)
                break
            trail.append("llm")
            try:
                state = self._call_model(state)
            except RuntimeError as error:
                self.agent.on_model_error(state, error)
                raise
            trail.append("agent")
            state = self._read(state, passes)
        state = dict(state)
        state["trail"] = trail
        return state

    def _read(self, state, passes):
        if state.get("batch_mode"):
            return self.agent.read_batch(state)
        if passes > 1:
            return self.agent.read_repair(state)
        return self.agent.read(state)

    def _raise_if_capped(self, state):
        if not state.get("batch_mode"):
            return
        pending = state.get("pending_batch") or {}
        batch_id = pending.get("split_from") or pending.get("id") or ""
        if batch_id:
            message = "分批超过 %s 次模型调用。批次 %s 没有写完。" % (MODEL_CALL_CAP, batch_id)
            raise RuntimeError(message)
        raise RuntimeError("分批超过 %s 次模型调用。" % MODEL_CALL_CAP)

    def _call_model(self, state):
        request = state.get("request") or {}
        kind = request.get("kind") or state.get("kind") or "write"
        kwargs = {
            "model": request.get("model"),
            "max_tokens": request.get("max_tokens"),
            "timeout": request.get("timeout"),
            "kind": kind,
        }
        if request.get("effort"):
            kwargs["effort"] = request["effort"]
        result = self.model.complete(request.get("messages") or [], **kwargs)
        state = dict(state)
        state["result"] = result
        state["request"] = None
        return state
