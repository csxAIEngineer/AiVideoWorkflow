"""LLM Model. DeepSeek official models stay on the DeepSeek API; the rest go through CR."""

class LLMModel(object):
    def complete(self, messages, model, max_tokens, timeout):
        from end.pipeline import OFFICIAL_MODELS, normalize_model

        name = normalize_model(model)
        common = {
            "model": name,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "response_format": {"type": "json_object"},
        }
        if name in OFFICIAL_MODELS:
            from deepseek_api import DeepSeekClient, DeepSeekError

            try:
                return DeepSeekClient().chat(
                    messages=messages,
                    thinking={"type": "disabled"},
                    **common
                )
            except DeepSeekError as error:
                raise RuntimeError(str(error))
        from cr_api import CRClient, CRError

        try:
            return CRClient().chat(messages=messages, **common)
        except CRError as error:
            raise RuntimeError(str(error))
