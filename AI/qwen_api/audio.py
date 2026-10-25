"""Non-streaming Qwen speech. There is no instrumental model on this API."""

import os

from AI.qwen_api.client import QwenClient, QwenError, extract_media_url

SPEECH_MODEL = "qwen-audio-3.0-tts-flash"
SPEECH_PATH = "/api/v1/services/audio/tts/SpeechSynthesizer"
DEFAULT_VOICE = "longanhuan_v3.6"
MUSIC_UNAVAILABLE = "千问没有对等的器乐模型，不能生成配乐"


def voice_name(voice_id=None):
    chosen = str(voice_id or "").strip()
    if chosen:
        return chosen
    return (os.environ.get("QWEN_TTS_VOICE") or "").strip() or DEFAULT_VOICE


def reject_music():
    raise QwenError(MUSIC_UNAVAILABLE)


def synthesize(text, dest, voice_id=None, timeout=120, client=None):
    spoken = str(text or "").strip()
    if not spoken:
        raise QwenError("旁白是空的")
    if not str(dest or "").strip():
        raise QwenError("没有音频文件")
    qwen = client or QwenClient()
    body = {
        "model": SPEECH_MODEL,
        "input": {
            "text": spoken,
            "voice": voice_name(voice_id),
            "format": "mp3",
        },
    }
    data = qwen.request("POST", SPEECH_PATH, body, timeout=timeout)
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    audio = output.get("audio") if isinstance(output.get("audio"), dict) else {}
    url = audio.get("url") or extract_media_url(data)
    if not url:
        raise QwenError("旁白没有返回音频")
    return qwen.download(url, dest, timeout=timeout)
