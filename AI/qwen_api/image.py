"""Wan image generation. Text and reference images share wan2.7-image.

Token Plan does not list qwen-image-3.0. Async submit uses
X-DashScope-Async: enable, then poll GET /api/v1/tasks/{task_id}.
PENDING and RUNNING become IN_PROGRESS. SUCCEEDED stays SUCCEEDED.
wan2.7 does not accept prompt_extend; text-only calls turn thinking_mode off.
"""

from AI.qwen_api.client import QwenClient, QwenError, error_text, extract_media_url

IMAGE_MODEL = "wan2.7-image"
IMAGE_PATH = "/api/v1/services/aigc/image-generation/generation"
ASYNC_HEADER = {"X-DashScope-Async": "enable"}
MIN_PIXELS = 768 * 768
MAX_PIXELS = 2048 * 2048
ASPECT_SIZES = {
    "9:16": (768, 1344),
    "16:9": (1344, 768),
    "21:9": (1792, 768),
}


def map_image_status(status):
    name = str(status or "")
    upper = name.upper()
    if upper in ("PENDING", "RUNNING"):
        return "IN_PROGRESS"
    if upper == "SUCCEEDED":
        return "SUCCEEDED"
    if upper in ("FAILED", "CANCELED", "UNKNOWN"):
        return "FAILED"
    return name


def qwen_image_size(size):
    """Turn an aspect or WxH into width*height inside the wan2.7-image window."""
    text = str(size or "").strip()
    if not text:
        return ""
    if text in ASPECT_SIZES:
        width, height = ASPECT_SIZES[text]
    else:
        normalized = text.replace("X", "*").replace("x", "*").replace("×", "*")
        parts = normalized.split("*")
        if len(parts) != 2:
            raise QwenError("千问生图尺寸要用宽*高")
        try:
            width = int(parts[0])
            height = int(parts[1])
        except ValueError:
            raise QwenError("千问生图尺寸要用宽*高")
    if width <= 0 or height <= 0:
        raise QwenError("千问生图尺寸要用宽*高")
    pixels = width * height
    if pixels < MIN_PIXELS or pixels > MAX_PIXELS:
        raise QwenError("千问生图总像素要在 768*768 到 2048*2048 之间")
    ratio = float(width) / float(height)
    if ratio < (1.0 / 8.0) or ratio > 8.0:
        raise QwenError("千问生图画幅要在 1:8 到 8:1 之间")
    return "%d*%d" % (width, height)


class ImageTask(object):
    def __init__(self, payload=None):
        data = payload if isinstance(payload, dict) else {}
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        source = output or data
        self.raw = payload
        self.id = str(
            source.get("task_id")
            or source.get("taskId")
            or data.get("task_id")
            or data.get("taskId")
            or data.get("id")
            or ""
        )
        raw_status = source.get("task_status") or source.get("status") or data.get("status") or ""
        self.status = map_image_status(raw_status)
        self.url = extract_media_url(source) or extract_media_url(data)
        self.error = error_text(data)


def _image_urls(image):
    if not image:
        return []
    if isinstance(image, str):
        text = image.strip()
        return [text] if text else []
    values = []
    for item in image:
        text = str(item or "").strip()
        if text:
            values.append(text)
    return values


class QwenImageClient(object):
    def __init__(self, client=None, api_key=None, opener=None, base_url=None):
        self.client = client or QwenClient(api_key=api_key, opener=opener, base_url=base_url)

    def submit_image(self, prompt, size=None, quality=None, image=None, timeout=60):
        del quality
        spoken = str(prompt or "").strip()
        if not spoken:
            raise QwenError("生图提示词是空的")
        urls = _image_urls(image)
        content = [{"image": url} for url in urls]
        content.append({"text": spoken})
        parameters = {"n": 1, "watermark": False}
        if not urls:
            parameters["thinking_mode"] = False
        chosen = qwen_image_size(size)
        if chosen:
            parameters["size"] = chosen
        body = {
            "model": IMAGE_MODEL,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }
        # region agent log
        try:
            import json
            import time
            payload = {
                "sessionId": "eab7ec",
                "runId": "post-fix",
                "hypothesisId": "M",
                "location": "AI/qwen_api/image.py:submit_image",
                "message": "image submit model",
                "data": {
                    "model": IMAGE_MODEL,
                    "hasImage": bool(urls),
                    "size": parameters.get("size") or "",
                    "thinkingMode": parameters.get("thinking_mode"),
                    "promptExtend": "prompt_extend" in parameters,
                },
                "timestamp": int(time.time() * 1000),
            }
            with open("/Users/shaneson/AiVideo/.cursor/debug-eab7ec.log", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            pass
        # endregion
        data = self.client.request("POST", IMAGE_PATH, body, timeout=timeout, headers=ASYNC_HEADER)
        task = ImageTask(data)
        if not task.id:
            raise QwenError("千问生图没有返回任务号")
        if not task.status:
            task.status = "IN_PROGRESS"
        return task

    def query_status(self, task_id, timeout=60):
        data = self.client.get_task(task_id, timeout=timeout)
        task = ImageTask(data)
        if not task.id:
            task.id = str(task_id or "")
        return task

    def query_result(self, task_id, timeout=60):
        return self.query_status(task_id, timeout=timeout)

    def get_result(self, task_id, timeout=60):
        task = self.query_status(task_id, timeout=timeout)
        if str(task.status).upper() in ("FAILED", "NOT_FOUND"):
            raise QwenError(task.error or task.status)
        if not task.url:
            raise QwenError("任务完成了，但没有图片地址")
        return task

    def download_image(self, url, dest, timeout=120):
        return self.client.download(url, dest, timeout=timeout)
