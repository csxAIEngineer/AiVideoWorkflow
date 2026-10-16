"""Read an uploaded still with Qwen3.8-Omni-Flash.

The call follows the DashScope compatible-mode chat completion used for a
finished clip: ``qwen3.8-omni-flash``, ``modalities=["text"]``, and a streaming
response. Uploads here are png, jpeg, or webp stills, so the part is an
``image_url`` data URL. Each still is shrunk to a JPEG first. An 8MB upload
would otherwise exceed the 10 million character Base64 limit.
"""

import base64
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from deepseek_api.client import load_dotenv

DEFAULT_QWEN_VISION_MODEL = "qwen3.8-omni-flash"
MAX_BASE64_CHARS = 10 * 1024 * 1024
UNREAD = "尚未读图"
READ_PROMPT = (
    "只根据这张静帧里看得见的内容，用中文写出："
    "主体种类、外观、场景，以及这张图在建议什么广告。"
    "不要编画面里没有的职业、载具或城市。"
    "看不清就写看不清，不要猜。"
)
_BASE_URL_HELP = (
    "未配置 DASHSCOPE_BASE_URL。请填写工作空间的 Chat Completions 地址，"
    "例如 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1。"
)
_SCALES = (1280, 960, 640)
_QUALITIES = (5, 10)


class QwenVisionError(RuntimeError):
    """DashScope rejected a still reading or returned no text."""


class QwenVisionReader(object):
    """Send one local still to ``qwen3.8-omni-flash`` and return its text."""

    def __init__(self, api_key=None, model=None, base_url=None, timeout=180, opener=None):
        load_dotenv()
        chosen_key = api_key if api_key is not None else os.environ.get("DASHSCOPE_API_KEY", "")
        self.api_key = str(chosen_key or "").strip()
        if not self.api_key:
            raise QwenVisionError("未配置 DASHSCOPE_API_KEY。")
        chosen_model = model if model is not None else os.environ.get("QWEN_VISION_MODEL", "")
        self.model = str(chosen_model or "").strip() or DEFAULT_QWEN_VISION_MODEL
        chosen_root = base_url if base_url is not None else os.environ.get("DASHSCOPE_BASE_URL", "")
        root = str(chosen_root or "").strip()
        if not root:
            raise QwenVisionError(_BASE_URL_HELP)
        self.base_url = root.rstrip("/")
        self.timeout = timeout
        self._opener = opener

    def read(self, image_path):
        jpeg = vision_jpeg_bytes(image_path)
        encoded = base64.b64encode(jpeg).decode("ascii")
        if len(encoded) >= MAX_BASE64_CHARS:
            raise QwenVisionError(
                "静帧 %s 缩成 JPEG 后仍有 %s 个 Base64 字符，超过读图上限"
                % (Path(image_path).name, len(encoded))
            )
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + encoded},
                        },
                        {"type": "text", "text": READ_PROMPT},
                    ],
                }
            ],
            "modalities": ["text"],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        url = self.base_url + "/chat/completions"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = self._opener or urllib.request.urlopen
        try:
            with opener(request, timeout=self.timeout) as response:
                text = _stream_text(response).strip()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise QwenVisionError("Qwen 读图请求失败（%s）：%s" % (error.code, detail[:800]))
        except urllib.error.URLError as error:
            raise QwenVisionError("Qwen 读图网络错误：%s" % error.reason)
        if not text:
            raise QwenVisionError("Qwen 没有返回读图文字：%s" % Path(image_path).name)
        return text


def read_upload(image_path, opener=None):
    """Return the Chinese reading, or ``尚未读图`` when no API key is set.

    A configured call that fails raises ``QwenVisionError`` instead of
    inventing a description.
    """
    load_dotenv()
    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        return UNREAD
    return QwenVisionReader(opener=opener).read(image_path)


def vision_jpeg_bytes(path):
    """Shrink one local still to a JPEG that fits the Base64 limit."""
    file_path = Path(path)
    if not file_path.is_file():
        raise QwenVisionError("找不到要读的静帧：%s" % file_path)
    if file_path.stat().st_size <= 0:
        raise QwenVisionError("静帧是空的：%s" % file_path.name)
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise QwenVisionError("本机没有 ffmpeg，不能把静帧缩成读图用的 JPEG")
    for scale in _SCALES:
        for quality in _QUALITIES:
            data = _ffmpeg_jpeg(ffmpeg, file_path, scale, quality)
            if data and len(base64.b64encode(data)) < MAX_BASE64_CHARS:
                return data
    raise QwenVisionError("静帧 %s 缩成 JPEG 后仍然超过读图大小限制" % file_path.name)


def _ffmpeg_bin():
    home_bin = str(Path.home() / ".local" / "bin" / "ffmpeg")
    for candidate in (
        shutil.which("ffmpeg"),
        home_bin,
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _ffmpeg_jpeg(ffmpeg, path, scale, quality):
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            "scale=%d:%d:force_original_aspect_ratio=decrease" % (scale, scale),
            "-q:v",
            str(quality),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0 or not proc.stdout:
        return b""
    return proc.stdout


def _stream_text(response):
    parts = []
    while True:
        raw = response.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        payload = line
        if payload.startswith("data:"):
            payload = payload[5:].strip()
        elif not payload.startswith("{"):
            continue
        if not payload or payload == "[DONE]":
            if payload == "[DONE]":
                break
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(chunk, dict) and chunk.get("error"):
            err = chunk["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise QwenVisionError("Qwen 读图失败：%s" % message)
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = (choices[0] or {}).get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, dict) and content.get("text"):
            parts.append(str(content["text"]))
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
    return "".join(parts)
