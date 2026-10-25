"""Wan 3.0 video. One model covers text, first frame, first-and-last, and reference.

Submit to video-synthesis with X-DashScope-Async: enable.
SUCCEEDED becomes completed. FAILED becomes failed.
Frame roles and reference roles stay in separate requests.
"""

from AI.qwen_api.client import QwenClient, QwenError, error_text, extract_media_url

VIDEO_MODEL = "wan3.0-video"
VIDEO_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
ASYNC_HEADER = {"X-DashScope-Async": "enable"}
# wan3.0-video documents these ratios, including 21:9. Anything else stays adaptive.
DOCUMENTED_RATIOS = {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
FRAME_ROLES = frozenset(("first_frame", "last_frame"))
REFERENCE_ROLES = frozenset(("reference_image", "reference_video", "reference_audio"))
MEDIA_ROLES = FRAME_ROLES | REFERENCE_ROLES


def map_video_status(status):
    name = str(status or "")
    upper = name.upper()
    if upper == "SUCCEEDED" or name == "completed":
        return "completed"
    if upper in ("FAILED", "CANCELED", "UNKNOWN") or name == "failed":
        return "failed"
    return name


def video_ratio(aspect):
    value = str(aspect or "").strip() or "adaptive"
    if value in DOCUMENTED_RATIOS:
        return value
    return "adaptive"


def video_duration(duration):
    try:
        seconds = int(duration)
    except (TypeError, ValueError):
        raise QwenError("duration 必须是 4 到 15 的整数")
    if seconds < 4 or seconds > 15:
        raise QwenError("duration 必须是 4 到 15 的整数")
    return seconds


class VideoTask(object):
    def __init__(self, payload=None):
        data = payload if isinstance(payload, dict) else {}
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        source = output or data
        self.raw = payload
        self.id = str(source.get("task_id") or data.get("id") or data.get("task_id") or "")
        raw_status = source.get("task_status") or data.get("status") or source.get("status") or ""
        self.status = map_video_status(raw_status)
        self.poll_url = str(data.get("poll_url") or "")
        self.url = extract_media_url(source) or extract_media_url(data)
        error = data.get("error")
        if isinstance(error, dict):
            self.error = error.get("message")
        elif isinstance(error, str) and error:
            self.error = error
        else:
            self.error = error_text(data) or None


def _media(images):
    seen = []
    media = []
    for image in images or []:
        if not isinstance(image, dict):
            raise QwenError("素材需要 role（first_frame、last_frame、reference_image、reference_video、reference_audio）和 url")
        role = image.get("role")
        url = image.get("url")
        if role not in MEDIA_ROLES or not url:
            raise QwenError("素材需要 role（first_frame、last_frame、reference_image、reference_video、reference_audio）和 url")
        seen.append(role)
        media.append({"type": role, "url": url})
    if FRAME_ROLES.intersection(seen) and REFERENCE_ROLES.intersection(seen):
        raise QwenError("同一次提交不能同时使用首尾帧和参考素材")
    return media


class QwenVideoClient(object):
    def __init__(self, client=None, api_key=None, opener=None, base_url=None):
        self.client = client or QwenClient(api_key=api_key, opener=opener, base_url=base_url)

    def submit_video(
        self,
        text,
        model=None,
        duration=4,
        aspect_ratio=None,
        images=None,
        idempotency_key=None,
        timeout=180,
    ):
        del model, idempotency_key
        prompt = str(text or "").strip()
        if not prompt:
            raise QwenError("出镜提示词是空的")
        media = _media(images)
        body = {
            "model": VIDEO_MODEL,
            "input": {"prompt": prompt},
            "parameters": {
                "ratio": video_ratio(aspect_ratio),
                "duration": video_duration(duration),
                "prompt_extend": False,
            },
        }
        if media:
            body["input"]["media"] = media
        data = self.client.request("POST", VIDEO_PATH, body, timeout=timeout, headers=ASYNC_HEADER)
        task = self._finish(VideoTask(data), "")
        if not task.id:
            raise QwenError("千问出镜没有返回任务号")
        return task

    def poll_video(self, task, timeout=60):
        task_id = getattr(task, "id", "") or ""
        return self.poll(task_id, timeout=timeout, previous=task)

    def poll(self, task_id, timeout=60, previous=None):
        data = self.client.get_task(task_id, timeout=timeout)
        updated = self._finish(VideoTask(data), task_id)
        if previous is not None and not updated.poll_url:
            updated.poll_url = getattr(previous, "poll_url", "") or updated.poll_url
        return updated

    def download_video(self, task, dest, timeout=300):
        url = task if isinstance(task, str) else (getattr(task, "url", "") or "")
        return self.client.download(url, dest, timeout=timeout)

    def _finish(self, task, task_id):
        if not task.id:
            task.id = str(task_id or "")
        if task.id and not task.poll_url:
            task.poll_url = self.client.base_url + "/api/v1/tasks/" + task.id
        return task
