"""LLM model. Every completion goes through the Qwen text graph."""


class ChatResult(object):
    def __init__(self, content, reasoning_content, model, raw=None, incomplete_reason=""):
        self.content = content or ""
        self.reasoning_content = reasoning_content or ""
        self.model = model
        raw = dict(raw) if isinstance(raw, dict) else {}
        reason = str(incomplete_reason or raw.get("incompleteReason") or "")
        if reason:
            raw["incompleteReason"] = reason
        self.raw = raw
        self.incomplete_reason = reason


class LLMModel(object):
    def __init__(self, client=None):
        self.client = client

    def complete(self, messages, model, max_tokens, timeout, kind=None, effort=None):
        from AI.qwen_api.client import QwenError
        from AI.qwen_api.graph import invoke_text
        from AI.script import normalize_model

        name = normalize_model(model)
        options = {
            "model": name,
            "max_output_tokens": max_tokens,
            "timeout": timeout,
            "client": self.client,
        }
        if effort and kind != "drill":
            options["effort"] = effort
        kind_name = "understand" if kind == "drill" else "generate"
        try:
            turned = invoke_text(kind_name, messages, **options)
        except QwenError as error:
            raise RuntimeError(str(error))
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError("文本请求失败：%s" % error)
        return _chat_result(name, turned)


def _chat_result(name, turned):
    reason = turned.get("incomplete_reason") or ""
    finish = turned.get("finish_reason") or ""
    raw = {
        "status": turned.get("status") or "",
        "outputTokens": turned.get("output_tokens") or 0,
        "inputTokens": turned.get("input_tokens") or 0,
        "reasoningTokens": turned.get("reasoning_tokens") or 0,
        "items": turned.get("items") or [],
        "error": turned.get("error") or "",
    }
    if reason:
        raw["incompleteReason"] = reason
    if finish:
        raw["finishReason"] = finish
    return ChatResult(turned.get("output") or "", "", name, raw, incomplete_reason=reason)
