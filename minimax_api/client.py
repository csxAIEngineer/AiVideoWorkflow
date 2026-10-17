"""Stdlib client for MiniMax. Python 3.9, no extra packages."""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from cr_api.client import load_dotenv

BASE_URL = "https://api.minimaxi.com/v1"
SPEECH_MODEL = "speech-2.8-hd"
MUSIC_MODEL = "music-3.0"
SPEECH_PATH = "/t2a_v2"
MUSIC_PATH = "/music_generation"
VOICE_PATH = "/get_voice"


class MiniMaxError(RuntimeError):
    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


def choose_chinese_voice(voices):
    """First system voice whose id, name, or description is Chinese."""
    needles = ("chinese", "mandarin", "中文", "普通话", "汉语")
    for voice in voices or []:
        if not isinstance(voice, dict):
            continue
        voice_id = str(voice.get("voice_id") or "").strip()
        if not voice_id:
            continue
        description = voice.get("description") or ""
        if isinstance(description, list):
            description = " ".join(str(item) for item in description)
        blob = " ".join(
            [
                voice_id,
                str(voice.get("voice_name") or ""),
                str(description),
            ]
        ).lower()
        if any(token in blob for token in needles):
            return voice_id
    return ""


def remember_voice_example(voice_id, path):
    """Fill an empty MINIMAX_VOICE_ID line. A value that is already set stays."""
    voice_id = str(voice_id or "").strip()
    example = Path(path) if path else None
    if not voice_id or example is None or not example.is_file():
        return False
    text = example.read_text(encoding="utf-8")
    pattern = re.compile(r"^(MINIMAX_VOICE_ID=)(.*)$", re.M)
    match = pattern.search(text)
    if match and match.group(2).strip():
        return False
    if match:
        text = pattern.sub(lambda found: found.group(1) + voice_id, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "MINIMAX_VOICE_ID=%s\n" % voice_id
    example.write_text(text, encoding="utf-8")
    return True


def _example_env():
    return Path(__file__).resolve().parents[1] / ".env.example"


class MiniMaxClient(object):
    def __init__(self, api_key=None, env_path=None, base_url=None, example_path=None):
        load_dotenv(env_path)
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        if not self.api_key:
            raise MiniMaxError("缺少 MINIMAX_API_KEY。请写在项目根目录的 .env 里。")
        self.base_url = (base_url or os.environ.get("MINIMAX_BASE_URL") or BASE_URL).rstrip("/")
        self.example_path = Path(example_path) if example_path else _example_env()
        self._voice_id = ""

    def voice_id(self):
        if self._voice_id:
            return self._voice_id
        configured = (os.environ.get("MINIMAX_VOICE_ID") or "").strip()
        if configured:
            self._voice_id = configured
            return configured
        chosen = choose_chinese_voice(self.list_system_voices())
        if not chosen:
            raise MiniMaxError("没有可用的中文系统音色。请设置 MINIMAX_VOICE_ID。")
        remember_voice_example(chosen, self.example_path)
        self._voice_id = chosen
        return chosen

    def list_system_voices(self, timeout=60):
        data = self._request("POST", VOICE_PATH, {"voice_type": "system"}, timeout=timeout)
        voices = data.get("system_voice") or []
        return voices if isinstance(voices, list) else []

    def synthesize(self, text, dest, voice_id=None, timeout=120):
        spoken = str(text or "").strip()
        if not spoken:
            raise MiniMaxError("旁白是空的")
        voice = str(voice_id or "").strip() or self.voice_id()
        body = {
            "model": SPEECH_MODEL,
            "text": spoken,
            "stream": False,
            "language_boost": "Chinese",
            "output_format": "hex",
            "voice_setting": {
                "voice_id": voice,
                "speed": 1,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        data = self._request("POST", SPEECH_PATH, body, timeout=timeout)
        return self._write_audio(data, dest, "旁白")

    def generate_music(self, prompt, dest, timeout=300):
        description = str(prompt or "").strip()
        if not description:
            raise MiniMaxError("音乐提示词是空的")
        if len(description) > 2000:
            description = description[:2000]
        body = {
            "model": MUSIC_MODEL,
            "prompt": description,
            "is_instrumental": True,
            "stream": False,
            "output_format": "hex",
            "audio_setting": {
                "sample_rate": 44100,
                "bitrate": 256000,
                "format": "mp3",
            },
        }
        data = self._request("POST", MUSIC_PATH, body, timeout=timeout)
        return self._write_audio(data, dest, "背景音乐")

    def _write_audio(self, data, dest, label):
        payload = (data.get("data") or {}) if isinstance(data, dict) else {}
        status = payload.get("status")
        audio = payload.get("audio") or ""
        if status not in (None, 2) or not str(audio).strip():
            raise MiniMaxError("%s没有返回音频" % label)
        try:
            raw = bytes.fromhex("".join(str(audio).split()))
        except ValueError:
            raise MiniMaxError("%s音频无法解码" % label)
        if not raw:
            raise MiniMaxError("%s音频是空的" % label)
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def _check(self, data):
        base = (data or {}).get("base_resp") or {}
        code = base.get("status_code")
        if code in (None, 0):
            return
        message = base.get("status_msg") or "MiniMax 返回 %s" % code
        if code == 1002:
            raise MiniMaxError("MiniMax 网络错误：请求太频繁", status=code)
        raise MiniMaxError(str(message), status=code)

    def _request(self, method, path, body, timeout):
        response = self._open(method, path, body, timeout)
        try:
            raw = response.read().decode("utf-8")
        finally:
            response.close()
        data = {} if not raw else json.loads(raw)
        self._check(data)
        return data

    def _open(self, method, path, body, timeout):
        url = path if path.startswith("http") else self.base_url + "/" + path.lstrip("/")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise MiniMaxError("MiniMax API 返回 %s：%s" % (error.code, detail[:800]), error.code, detail)
        except urllib.error.URLError as error:
            raise MiniMaxError("MiniMax API 网络错误：%s" % error.reason)
        except TimeoutError as error:
            raise MiniMaxError("MiniMax API 网络错误：%s" % error)
