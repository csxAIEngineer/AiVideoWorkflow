"""MiniMax-H3-Image stills.

Text-only assets use POST /v1/images/generations. Any reference image,
including 参考图, uses POST /v1/images/edits. The call is synchronous and
not idempotent: one attempt, timeout 600s, no automatic retry.

The returned URL expires in 24 hours. Callers download it immediately and
publish the bytes through the image store. This module does not keep that
URL as the asset address.
"""

import json
import os
import threading
import uuid
import urllib.error
import urllib.request

from AI.qwen_api.client import load_dotenv

DEFAULT_BASE_URL = "https://api.minimax.cn/v1"
IMAGE_MODEL = "MiniMax-H3-Image"
KEY_VAR = "MINIMAX_API_KEY"
GENERATIONS_PATH = "/images/generations"
EDITS_PATH = "/images/edits"
REQUEST_TIMEOUT = 600
MIN_AREA = 655360
MAX_AREA = 20000000
MIN_RATIO = 1.0 / 3.0
MAX_RATIO = 3.0
# Same pixels the Qwen still path already sent for these locked frames.
PRODUCT_SIZES = {
    "9:16": (768, 1344),
    "16:9": (1344, 768),
    "21:9": (1792, 768),
}
DEFAULT_AREA = "2K"

_TASKS = {}
_TASKS_LOCK = threading.Lock()


class MiniMaxImageError(RuntimeError):
    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


class ImageTask(object):
    def __init__(self, task_id, status, url="", error="", revised_prompt=""):
        self.id = str(task_id or "")
        self.status = status or ""
        self.url = url or ""
        self.error = error or ""
        self.revised_prompt = revised_prompt or ""


def minimax_api_key(api_key=None):
    """The stills key. Video does not use this variable."""
    if api_key is not None:
        return str(api_key).strip()
    return str(os.environ.get(KEY_VAR) or "").strip()


def image_size_fields(size):
    """Explicit WxH when the product already picked pixels. Otherwise ratio + 2K."""
    text = str(size or "").strip()
    if not text:
        return {"area": DEFAULT_AREA}
    if text in PRODUCT_SIZES:
        width, height = PRODUCT_SIZES[text]
        return {"size": "%dx%d" % (width, height)}
    if ":" in text and "x" not in text.lower() and "*" not in text:
        _aspect_ratio(text)
        return {"aspect_ratio": text, "area": DEFAULT_AREA}
    width, height = _parse_wh(text)
    _check_pixels(width, height)
    return {"size": "%dx%d" % (width, height)}


def _parse_wh(text):
    normalized = str(text).replace("X", "x").replace("×", "x").replace("*", "x")
    parts = normalized.split("x")
    if len(parts) != 2:
        raise MiniMaxImageError("生图尺寸要用宽x高，或 16:9 这样的画幅")
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError:
        raise MiniMaxImageError("生图尺寸要用宽x高，或 16:9 这样的画幅")
    if width <= 0 or height <= 0:
        raise MiniMaxImageError("生图尺寸要用宽x高，或 16:9 这样的画幅")
    return width, height


def _aspect_ratio(text):
    parts = str(text).split(":")
    if len(parts) != 2:
        raise MiniMaxImageError("生图画幅要用 16:9 这样的比例")
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError:
        raise MiniMaxImageError("生图画幅要用 16:9 这样的比例")
    if width < 1 or height < 1 or width > 300 or height > 300:
        raise MiniMaxImageError("生图画幅要在 1:3 到 3:1 之间")
    _check_ratio(width / float(height))
    return text


def _check_pixels(width, height):
    area = width * height
    if area < MIN_AREA or area > MAX_AREA:
        raise MiniMaxImageError("生图总像素要在 655360 到 20000000 之间")
    _check_ratio(width / float(height))


def _check_ratio(ratio):
    if ratio < MIN_RATIO - 1e-6 or ratio > MAX_RATIO + 1e-6:
        raise MiniMaxImageError("生图画幅要在 1:3 到 3:1 之间")


def _image_urls(image):
    if not image:
        return []
    if isinstance(image, str):
        text = image.strip()
        return [text] if text else []
    values = []
    for item in image:
        text = str(item or "").strip()
        if text and text not in values:
            values.append(text)
    return values


class MiniMaxImageClient(object):
    def __init__(self, api_key=None, opener=None, base_url=None, env_path=None):
        load_dotenv(env_path)
        self.api_key = minimax_api_key(api_key)
        if not self.api_key:
            raise MiniMaxImageError("未配置 %s" % KEY_VAR)
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self._opener = opener or urllib.request.urlopen

    def submit_image(self, prompt, size=None, quality=None, image=None, timeout=None):
        del quality
        spoken = str(prompt or "").strip()
        if not spoken:
            raise MiniMaxImageError("生图提示词是空的")
        urls = _image_urls(image)
        if len(urls) > 16:
            raise MiniMaxImageError("参考图最多 16 张")
        fields = image_size_fields(size)
        body = {
            "model": IMAGE_MODEL,
            "prompt": spoken,
            "n": 1,
            "response_format": "url",
            "output_format": "png",
        }
        body.update(fields)
        if urls:
            body["images"] = [{"image_url": url} for url in urls]
            path = EDITS_PATH
        else:
            path = GENERATIONS_PATH
        limit = REQUEST_TIMEOUT if timeout is None else int(timeout)
        if limit < REQUEST_TIMEOUT:
            limit = REQUEST_TIMEOUT
        data = self._post(path, body, limit)
        return self._remember(self._task_from_response(data))

    def query_status(self, task_id, timeout=60):
        del timeout
        return self._stored(task_id)

    def query_result(self, task_id, timeout=60):
        return self.query_status(task_id, timeout=timeout)

    def get_result(self, task_id, timeout=60):
        task = self.query_status(task_id, timeout=timeout)
        if str(task.status).upper() != "SUCCEEDED" or not task.url:
            raise MiniMaxImageError(task.error or "生图没有返回图片地址")
        return task

    def download_image(self, url, dest, timeout=120):
        if not url:
            raise MiniMaxImageError("没有可下载的图片地址")
        target = _destination(dest)
        request = urllib.request.Request(str(url), method="GET")
        try:
            response = self._opener(request, timeout=timeout)
            try:
                payload = response.read()
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise MiniMaxImageError("下载图片失败 %s：%s" % (error.code, detail[:500]), error.code, detail)
        except urllib.error.URLError as error:
            raise MiniMaxImageError("下载网络错误：%s" % error.reason)
        except TimeoutError as error:
            raise MiniMaxImageError("下载网络错误：%s" % error)
        if not payload:
            raise MiniMaxImageError("下载的图片是空的")
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(payload)
        temporary.replace(target)
        return target

    def _post(self, path, body, timeout):
        url = self.base_url + "/" + path.lstrip("/")
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = self._opener(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise MiniMaxImageError(_http_message(error.code, detail), error.code, detail)
        except urllib.error.URLError as error:
            raise MiniMaxImageError("生图没有收到结果，没有自动重试，避免重复计费：%s" % error.reason)
        except TimeoutError:
            raise MiniMaxImageError("生图超过 %s 秒，没有自动重试，避免重复计费" % timeout)
        try:
            raw = response.read().decode("utf-8")
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
        if not raw:
            raise MiniMaxImageError("生图没有返回 JSON")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise MiniMaxImageError("生图没有返回 JSON")
        if not isinstance(data, dict):
            raise MiniMaxImageError("生图没有返回 JSON")
        return data

    def _task_from_response(self, data):
        items = data.get("data")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise MiniMaxImageError("生图没有返回图片", status=500)
        item = items[0]
        if item.get("error") or not _http_url(item.get("url")):
            _raise_item_error(item)
        revised = str(item.get("revised_prompt") or "")
        task_id = str(data.get("request_id") or "").strip() or uuid.uuid4().hex
        return ImageTask(task_id, "SUCCEEDED", url=item.get("url"), revised_prompt=revised)

    def _remember(self, task):
        with _TASKS_LOCK:
            _TASKS[task.id] = task
        return task

    def _stored(self, task_id):
        with _TASKS_LOCK:
            task = _TASKS.get(str(task_id or ""))
        if task is None:
            raise MiniMaxImageError("生图任务已不在本进程，没有重新生成")
        return task


def _http_url(value):
    text = str(value or "").strip()
    if text.startswith("https://") or text.startswith("http://"):
        return text
    return ""


def _raise_item_error(item):
    error = item.get("error") if isinstance(item, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or "")
        code = str(error.get("code") or "")
    else:
        message = str(error or "")
        code = ""
    safety = code == "1027" or "safety" in message.lower() or "安全" in message
    if safety:
        raise MiniMaxImageError("生图未通过内容安全检查：%s" % (message or "内容安全"), status=422)
    raise MiniMaxImageError("生图失败：%s" % (message or "没有图片地址"), status=500)


def _http_message(status, body):
    message = _body_message(body)
    if status == 422:
        lead = "生图未通过内容安全检查"
    elif status == 402:
        lead = "生图余额不足"
    elif status == 429:
        lead = "生图被限流"
    elif status == 400:
        lead = "生图参数错误"
    else:
        lead = "生图失败"
    if message:
        return "%s：%s" % (lead, message)
    return "%s（%s）" % (lead, status)


def _body_message(body):
    try:
        data = json.loads(body or "")
    except (TypeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict):
        return str(body or "").strip()[:500]
    error = data.get("error")
    if isinstance(error, dict):
        text = str(error.get("message") or error.get("code") or "")
    elif isinstance(error, str):
        text = error
    else:
        text = ""
    base = data.get("base_resp") if isinstance(data.get("base_resp"), dict) else {}
    if not text:
        text = str(base.get("status_msg") or data.get("message") or "")
    request_id = str(data.get("request_id") or "").strip()
    text = text.strip()
    if request_id and request_id not in text:
        text = ("%s，请求号 %s" % (text, request_id)).strip("，")
    return text[:500]


def _destination(dest):
    from pathlib import Path

    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
