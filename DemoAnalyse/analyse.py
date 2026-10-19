"""Run shot detection and narration transcription for one video."""

from pathlib import Path

from media import MediaError, extract_poster
from narration import NarrationError, transcribe_video
from shots import detect_shots, format_clock

NO_AUDIO = "这条视频没有音轨，听不到旁白。"


class AnalyseError(RuntimeError):
    pass


def analyse_video(video_path, work_dir):
    source = Path(video_path)
    if not source.is_file():
        raise AnalyseError("找不到视频：%s" % source.name)
    if source.stat().st_size <= 0:
        raise AnalyseError("视频文件是空的。")
    folder = Path(work_dir)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        detected = detect_shots(source, folder)
    except MediaError as error:
        raise AnalyseError(str(error))
    shots = _with_posters(source, folder, detected["shots"])
    narration, narration_error = _narration(source, folder, detected["has_audio"])
    return {
        "duration": detected["duration"],
        "duration_label": _duration_label(detected["duration"]),
        "shot_count": len(shots),
        "shots": shots,
        "narration": narration,
        "narration_error": narration_error,
    }


def envelope(data=None, error=None):
    return {"success": error is None, "data": data, "error": error}


def _with_posters(video_path, folder, shots):
    posted = []
    for shot in shots:
        name = "shot-%02d.jpg" % shot["index"]
        midpoint = (float(shot["start"]) + float(shot["end"])) / 2
        wrote = extract_poster(video_path, folder / name, midpoint)
        posted.append(dict(shot, poster=name if wrote else None))
    return posted


def _narration(video_path, folder, has_audio):
    if not has_audio:
        return None, NO_AUDIO
    try:
        return transcribe_video(video_path, folder), None
    except (NarrationError, MediaError) as error:
        return None, str(error)


def _duration_label(duration):
    return format_clock(duration)
