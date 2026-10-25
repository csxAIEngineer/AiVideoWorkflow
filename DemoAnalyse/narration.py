"""Transcribe the narration on a video soundtrack.

The primary call is Qwen3-ASR-Flash on the workspace compatible-mode endpoint.
If that model is not enabled, the same audio goes to qwen3.8-omni-flash with a
transcript-only prompt.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.request

from media import extract_speech_audio, load_project_env

ASR_MODEL = "qwen3-asr-flash"
OMNI_MODEL = "qwen3.8-omni-flash"
EMPTY_NARRATION = "没有听清旁白。"
OMNI_PROMPT = (
    "这是广告视频的音轨，里面可能有旁白和背景音乐。"
    "只输出旁白口播的原文，按说话顺序，保留原话。"
    "不要描写音乐、音效或画面，不要加标题或解释。"
    "如果没有听清的人声，只输出：没有旁白"
)
_SECRET_RE = re.compile(r"(Bearer\s+)\S+|sk-[A-Za-z0-9_\-]+")
_WORD_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


class NarrationError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def transcribe_video(video_path, work_dir):
    load_project_env()
    audio_path, mime = extract_speech_audio(video_path, work_dir)
    data_uri = _data_uri(audio_path, mime)
    try:
        text = _asr_flash(data_uri)
    except NarrationError as error:
        if not _can_fallback(error):
            raise
        text = _omni_flash(data_uri)
    cleaned = clean_transcript(text)
    if not cleaned or cleaned == "没有旁白":
        return EMPTY_NARRATION
    return cleaned


def clean_transcript(text):
    stripped = (text or "").strip().strip('"').strip("“”")
    if not stripped or _WORD_RE.search(stripped) is None:
        return ""
    return stripped


def text_from_completion(payload):
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    return _content_text(message.get("content"))


def public_error(message):
    text = _SECRET_RE.sub(lambda match: "Bearer ***" if match.group(1) else "sk-***", str(message))
    return text[:500]


def _asr_flash(data_uri):
    body = {
        "model": os.environ.get("DEMO_ASR_MODEL", ASR_MODEL),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": data_uri},
                    }
                ],
            }
        ],
        "stream": False,
        "asr_options": {"enable_itn": False},
    }
    payload = _post_json(body, timeout=180)
    return text_from_completion(payload)


def _omni_flash(data_uri):
    body = {
        "model": os.environ.get("DEMO_OMNI_MODEL", OMNI_MODEL),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": data_uri, "format": _format_of(data_uri)},
                    },
                    {"type": "text", "text": OMNI_PROMPT},
                ],
            }
        ],
        "modalities": ["text"],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    return _post_stream(body, timeout=180)


def _post_json(body, timeout):
    raw = _open(body, timeout)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise NarrationError("语音识别返回的不是 JSON。")


def _post_stream(body, timeout):
    raw = _open(body, timeout)
    return _read_sse(raw.decode("utf-8", errors="replace"))


def _open(body, timeout):
    api_key, url = _endpoint()
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        message = _http_message(error.code, detail)
        raise NarrationError(message, status=error.code)
    except urllib.error.URLError as error:
        raise NarrationError("语音识别网络错误：%s" % error.reason)


def _endpoint():
    load_project_env()
    api_key = os.environ.get("QWEN_API_KEY", "").strip()
    base_url = os.environ.get("QWEN_BASE_URL", "").strip().rstrip("/") or "https://maas.qianwenaiapi.com"
    if base_url.endswith("/compatible-mode/v1"):
        base_url = base_url[: -len("/compatible-mode/v1")].rstrip("/")
    if not api_key:
        raise NarrationError("未配置 QWEN_API_KEY，不能转写旁白。")
    return api_key, base_url + "/compatible-mode/v1/chat/completions"


def _http_message(status, detail):
    message = detail
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            message = error["message"]
        elif payload.get("message"):
            message = payload["message"]
    return public_error("语音识别请求失败（%s）：%s" % (status, message))


def _read_sse(text):
    parts = []
    for line in text.splitlines():
        payload = line.strip()
        if payload.startswith("data:"):
            payload = payload[5:].strip()
        elif not payload.startswith("{"):
            continue
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(chunk, dict) and chunk.get("error"):
            err = chunk["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise NarrationError(public_error("语音识别失败：%s" % message))
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = (choices[0] or {}).get("delta") or {}
        piece = _content_text(delta.get("content"))
        if piece:
            parts.append(piece)
    return "".join(parts)


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, list):
        return "".join(_content_text(item) for item in content)
    return ""


def _data_uri(path, mime):
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return "data:%s;base64,%s" % (mime, encoded)


def _format_of(data_uri):
    if data_uri.startswith("data:audio/mpeg"):
        return "mp3"
    return "wav"


def _can_fallback(error):
    if error.status in (400, 404):
        return True
    text = str(error)
    return "model" in text.lower() or "模型" in text
