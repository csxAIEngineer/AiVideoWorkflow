"""ffmpeg helpers for the demo analyser. Python 3.9, standard library only."""

import os
import re
import shutil
import subprocess
from pathlib import Path

DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
MAX_AUDIO_BASE64 = 9_000_000


class MediaError(RuntimeError):
    """ffmpeg is missing or rejected the file."""


def load_project_env():
    """Load the demo .env, then the repo .env. Existing variables stay."""
    here = Path(__file__).resolve().parent
    for path in (here / ".env", here.parent / ".env"):
        if path.is_file():
            _apply_env(path)


def find_ffmpeg():
    home_bin = Path.home() / ".local" / "bin" / "ffmpeg"
    for candidate in (
        shutil.which("ffmpeg"),
        str(home_bin),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def require_ffmpeg():
    binary = find_ffmpeg()
    if not binary:
        raise MediaError("本机没有 ffmpeg，无法分析视频。")
    return binary


def inspect_av(video_path):
    """Return duration in seconds, plus whether a video stream and an audio stream exist."""
    ffmpeg = require_ffmpeg()
    proc = _run([ffmpeg, "-hide_banner", "-i", _abs(video_path)], timeout=30)
    detail = proc.stderr.decode("utf-8", errors="replace")
    match = DURATION_RE.search(detail)
    if not match:
        raise MediaError("读不到视频时长。请换一个 mp4、mov 或 webm。")
    hours, minutes, seconds = match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if duration <= 0:
        raise MediaError("视频时长是 0。")
    return {
        "duration": duration,
        "has_video": "Video:" in detail,
        "has_audio": "Audio:" in detail,
    }


def write_scene_metadata(video_path, work_dir):
    """Write ffmpeg scene scores above 0.15 into work_dir/scenes.txt and return the text."""
    ffmpeg = require_ffmpeg()
    folder = Path(work_dir)
    folder.mkdir(parents=True, exist_ok=True)
    proc = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            _abs(video_path),
            "-filter:v",
            "select='gt(scene,0.15)',metadata=print:file=scenes.txt",
            "-an",
            "-f",
            "null",
            "-",
        ],
        cwd=str(folder),
        timeout=180,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise MediaError("分镜检测失败：%s" % (detail[-400:] or "ffmpeg 退出码 %s" % proc.returncode))
    scene_file = folder / "scenes.txt"
    if not scene_file.is_file():
        return ""
    return scene_file.read_text(encoding="utf-8", errors="replace")


def extract_poster(video_path, dest, timestamp):
    ffmpeg = require_ffmpeg()
    proc = _run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "%.3f" % max(0.0, float(timestamp)),
            "-i",
            _abs(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=360:-1",
            "-q:v",
            "4",
            str(dest),
        ],
        timeout=30,
    )
    if proc.returncode != 0 or not Path(dest).is_file():
        return False
    return True


def extract_speech_audio(video_path, work_dir):
    """Write a mono 16 kHz file small enough for the ASR request. Returns (path, mime)."""
    ffmpeg = require_ffmpeg()
    folder = Path(work_dir)
    folder.mkdir(parents=True, exist_ok=True)
    wav_path = folder / "speech.wav"
    _encode_audio(
        ffmpeg,
        video_path,
        wav_path,
        ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"],
    )
    if _base64_size(wav_path) <= MAX_AUDIO_BASE64:
        return wav_path, "audio/wav"
    mp3_path = folder / "speech.mp3"
    _encode_audio(
        ffmpeg,
        video_path,
        mp3_path,
        ["-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k"],
    )
    if _base64_size(mp3_path) > MAX_AUDIO_BASE64:
        raise MediaError("音轨超过语音识别的大小限制（编码后 10MB）。请换一条 4 分钟以内的视频。")
    return mp3_path, "audio/mpeg"


def _encode_audio(ffmpeg, video_path, dest, codec_args):
    proc = _run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            _abs(video_path),
            "-vn",
        ]
        + list(codec_args)
        + [str(dest)],
        timeout=120,
    )
    if proc.returncode != 0 or not Path(dest).is_file() or Path(dest).stat().st_size <= 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise MediaError("提取音轨失败：%s" % (detail[-400:] or "ffmpeg 没有写出音频"))


def _base64_size(path):
    size = Path(path).stat().st_size
    return ((size + 2) // 3) * 4


def _run(args, cwd=None, timeout=60):
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise MediaError("ffmpeg 处理超时。")


def _abs(path):
    return str(Path(path).resolve())


def _apply_env(path):
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
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
        if key and not os.environ.get(key):
            os.environ[key] = value
