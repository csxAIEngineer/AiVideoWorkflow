"""LLM Model. DeepSeek official models stay on the DeepSeek API; the rest go through CR.

Speech and music are separate: they POST to MiniMax and return audio bytes.
"""

import json
import os
import urllib.error
import urllib.request

from AI.deepseek_api.client import load_dotenv

MINIMAX_BASE = "https://api.minimaxi.com"


class LLMModel(object):
    def speech(self, body, timeout):
        return self._minimax("/v1/t2a_v2", body, timeout, music=False)

    def music(self, body, timeout):
        return self._minimax("/v1/music_generation", body, timeout, music=True)

    def _minimax(self, path, body, timeout, music):
        load_dotenv()
        key = (os.environ.get("MINIMAX_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("缺少 MINIMAX_API_KEY")
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(MINIMAX_BASE + path, data=data, method="POST")
        request.add_header("Authorization", "Bearer " + key)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout or 60) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = ""
            reader = getattr(error, "read", None)
            if reader:
                try:
                    detail = reader().decode("utf-8", "replace")[:300]
                except Exception:
                    detail = ""
            raise RuntimeError("MiniMax 请求失败：%s %s" % (getattr(error, "code", ""), detail))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeError("MiniMax 网络错误：%s" % error)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise RuntimeError("MiniMax 没有返回 JSON")
        return _audio_bytes(payload, music)

    def complete(self, messages, model, max_tokens, timeout):
        from AI.script import OFFICIAL_MODELS, normalize_model

        name = normalize_model(model)
        common = {
            "model": name,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "response_format": {"type": "json_object"},
        }
        if name in OFFICIAL_MODELS:
            from AI.deepseek_api import DeepSeekClient, DeepSeekError

            try:
                return DeepSeekClient().chat(
                    messages=messages,
                    thinking={"type": "disabled"},
                    **common
                )
            except DeepSeekError as error:
                raise RuntimeError(str(error))
        from AI.cr_api import CRClient, CRError

        try:
            return CRClient().chat(messages=messages, **common)
        except CRError as error:
            raise RuntimeError(str(error))


def _audio_bytes(payload, music):
    if not isinstance(payload, dict):
        raise RuntimeError("MiniMax 没有返回音频")
    base = payload.get("base_resp") if isinstance(payload.get("base_resp"), dict) else {}
    if base.get("status_code") != 0:
        raise RuntimeError("MiniMax 没有返回音频：%s" % (base.get("status_msg") or "失败"))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if music and data.get("status") not in (2, "2"):
        raise RuntimeError("MiniMax 没有返回音频")
    audio = data.get("audio")
    if not isinstance(audio, str) or not audio.strip():
        raise RuntimeError("MiniMax 没有返回音频")
    try:
        content = bytes.fromhex(audio.strip())
    except ValueError:
        raise RuntimeError("MiniMax 音频格式不对")
    if not content:
        raise RuntimeError("MiniMax 没有返回音频")
    return content
