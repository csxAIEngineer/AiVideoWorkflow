"""Stdlib client for https://api.deepseek.com. Python 3.9, no extra packages."""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://api.deepseek.com"

TEXT_MODELS = (
    "deepseek-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-chat",
    "deepseek-reasoner",
)


class DeepSeekError(RuntimeError):
    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


class ChatResult(object):
    def __init__(self, content, reasoning_content, model, raw):
        self.content = content or ""
        self.reasoning_content = reasoning_content or ""
        self.model = model
        self.raw = raw


def load_dotenv(path=None, override=False):
    """Read KEY=VALUE lines into the process environment. Existing values stay unless override."""
    env_path = Path(path) if path else _find_env()
    if env_path is None or not env_path.is_file():
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        if not override and os.environ.get(key):
            continue
        os.environ[key] = value
    return env_path


def _find_env():
    package_root = Path(__file__).resolve().parents[1]
    for candidate in (package_root / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            return candidate
    return None


class DeepSeekClient(object):
    def __init__(self, api_key=None, env_path=None, base_url=None):
        load_dotenv(env_path)
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise DeepSeekError("缺少 DEEPSEEK_API_KEY。请写在项目根目录的 .env 里。")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or BASE_URL).rstrip("/")

    def chat(
        self,
        messages,
        model="deepseek-v4-pro",
        max_tokens=None,
        temperature=None,
        response_format=None,
        thinking=None,
        timeout=300,
    ):
        body = {"model": model, "messages": messages}
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        if temperature is not None:
            body["temperature"] = float(temperature)
        if response_format is not None:
            body["response_format"] = response_format
        if thinking is not None:
            body["thinking"] = thinking
        data = self._request("POST", "/chat/completions", body, timeout=timeout)
        message = ((data.get("choices") or [{}])[0].get("message") or {})
        return ChatResult(
            content=message.get("content") or "",
            reasoning_content=message.get("reasoning_content") or "",
            model=data.get("model") or model,
            raw=data,
        )

    def _request(self, method, path, body, timeout):
        url = path if path.startswith("http") else self.base_url + "/" + path.lstrip("/")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise DeepSeekError(
                "DeepSeek API 返回 %s：%s" % (error.code, detail[:800]),
                error.code,
                detail,
            )
        except urllib.error.URLError as error:
            raise DeepSeekError("DeepSeek API 网络错误：%s" % error.reason)
        if not raw:
            return {}
        return json.loads(raw)
