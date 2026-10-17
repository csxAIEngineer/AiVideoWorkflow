"""Provider proxies. Render keeps the retry loop; the HTTP calls stay in AI."""

from end.ai_client import RemoteError, call


class RemoteTask(object):
    def __init__(self, payload):
        data = payload or {}
        self.id = str(data.get("id") or "")
        self.status = data.get("status") or ""
        self.url = data.get("url") or ""
        self.error = data.get("error") or ""
        self.poll_url = data.get("poll_url") or ""


class RemoteImageClient(object):
    def submit_image(self, prompt, size=None, quality=None, image=None, timeout=60):
        del size, quality, timeout
        return RemoteTask(call("image.submit", {"prompt": prompt or "", "image": image}, timeout=90))

    def query_status(self, task_id, timeout=60):
        del timeout
        return RemoteTask(call("image.query", {"task_id": task_id}, timeout=90))

    def get_result(self, task_id, timeout=60):
        del timeout
        return RemoteTask(call("image.result", {"task_id": task_id}, timeout=90))

    def download_image(self, url, dest, timeout=120):
        del timeout
        result = call("image.download", {"url": url or "", "dest": str(dest)}, timeout=180)
        return result.get("path") or str(dest)


class RemoteVideoClient(object):
    def submit_video(
        self,
        text,
        model="minimax-h3-text-to-video",
        duration=4,
        aspect_ratio="9:16",
        images=None,
        idempotency_key=None,
        timeout=180,
    ):
        return RemoteTask(
            call(
                "video.submit",
                {
                    "text": text or "",
                    "model": model,
                    "duration": duration,
                    "aspect_ratio": aspect_ratio,
                    "images": images,
                    "idempotency_key": idempotency_key,
                    "timeout": timeout,
                },
                timeout=int(timeout or 180) + 30,
            )
        )

    def poll_video(self, task, timeout=60):
        return RemoteTask(
            call(
                "video.poll",
                {
                    "task_id": getattr(task, "id", "") or "",
                    "poll_url": getattr(task, "poll_url", "") or "",
                    "timeout": timeout,
                },
                timeout=int(timeout or 60) + 30,
            )
        )

    def download_video(self, task, dest, timeout=300):
        result = call(
            "video.download",
            {
                "task_id": getattr(task, "id", "") or "",
                "poll_url": getattr(task, "poll_url", "") or "",
                "url": getattr(task, "url", "") or "",
                "status": getattr(task, "status", "") or "",
                "dest": str(dest),
                "timeout": timeout,
            },
            timeout=int(timeout or 300) + 30,
        )
        return result.get("path") or str(dest)


class RemoteAudio(object):
    """MiniMax speech and music. The render loop still decides when to ask."""

    def synthesize(self, text, dest, voice_id=None):
        result = call(
            "audio.speak",
            {"text": text or "", "dest": str(dest), "voice_id": voice_id or ""},
            timeout=180,
        )
        return result.get("path") or str(dest)

    def generate_music(self, prompt, dest):
        result = call(
            "audio.compose",
            {"prompt": prompt or "", "dest": str(dest)},
            timeout=360,
        )
        return result.get("path") or str(dest)


class LayoutClient(object):
    """Speech and music through the agent layout, for callers that pass no layout."""

    def speak(self, text, dest):
        result = call("layout.speak", {"text": text or "", "dest": str(dest)}, timeout=120)
        return result.get("path") or str(dest)

    def compose(self, prompt, dest):
        result = call("layout.compose", {"prompt": prompt or "", "dest": str(dest)}, timeout=150)
        return result.get("path") or str(dest)


__all__ = [
    "LayoutClient",
    "RemoteAudio",
    "RemoteError",
    "RemoteImageClient",
    "RemoteTask",
    "RemoteVideoClient",
]
