"""Place narration on the joined film.

In and out points follow each clip's real duration. A line may speak across
later shots that have no narration. The next narration cuts it off, and
anything past the film is dropped. Unchanged text is not recorded again.
Background music is not generated.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path

SOUNDTRACK_OFF = "off"
SOUNDTRACK_NARRATION = "narration"
_LEGACY_NARRATION = "music_narration"
SOUNDTRACK_MODES = (SOUNDTRACK_OFF, SOUNDTRACK_NARRATION)

AMBIENT_GAIN = "0.35"
MUSIC_GAIN = "0.18"


def normalize_soundtrack(value):
    if not isinstance(value, str):
        return SOUNDTRACK_OFF
    text = value.strip().lower()
    if text in (SOUNDTRACK_NARRATION, _LEGACY_NARRATION):
        return SOUNDTRACK_NARRATION
    return SOUNDTRACK_OFF


def soundtrack_enabled(value):
    return normalize_soundtrack(value) != SOUNDTRACK_OFF


def project_soundtrack(project):
    progress = (project or {}).get("progress") or {}
    return normalize_soundtrack(progress.get("soundtrack"))


def content_hash(text):
    return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()


def voice_filename(shot_id):
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(shot_id or "line")).strip("_") or "line"
    return name + ".mp3"


def instrumental_prompt(brief, title=""):
    """One instrumental car cue from the project brief. No lyrics."""
    text = re.sub(r"\s+", " ", str(brief or "")).strip()
    if not text:
        text = re.sub(r"\s+", " ", str(title or "")).strip()
    if not text:
        text = "一辆车在路上行驶"
    if len(text) > 120:
        text = text[:120].rstrip("，。,. ")
    sentence = "纯器乐汽车配乐，没有人声，没有歌词，%s。" % text.rstrip("。")
    return sentence[:2000]


def soundtrack_note(mode, shots):
    if normalize_soundtrack(mode) != SOUNDTRACK_NARRATION:
        return ""
    for shot in shots or []:
        if str((shot or {}).get("narration") or "").strip():
            return ""
    return "没有旁白"


def schedule_voice(shots, durations, lines, film_end=None):
    """In point is the shot start on the joined film. Out point is the take, cut by the next line."""
    shots = list(shots or [])
    durations = [float(item) for item in durations]
    if len(durations) != len(shots):
        raise ValueError("镜头时长数量和镜头不一致")
    length_of = {}
    for item in lines or []:
        if isinstance(item, dict) and item.get("shot_id"):
            length_of[item["shot_id"]] = float(item.get("audio_duration") or 0)
    starts = []
    cursor = 0.0
    for duration in durations:
        starts.append(cursor)
        cursor += duration
    if film_end is None:
        film_end = cursor
    film_end = float(film_end)
    voiced = []
    for index, shot in enumerate(shots):
        text = str((shot or {}).get("narration") or "").strip()
        if not text:
            continue
        shot_id = shot.get("id")
        voiced.append(
            {
                "shot_id": shot_id,
                "text": text,
                "start": starts[index],
                "audio_duration": length_of.get(shot_id, 0.0),
                "file": voice_filename(shot_id),
            }
        )
    cues = []
    for index, item in enumerate(voiced):
        end = item["start"] + item["audio_duration"]
        if index + 1 < len(voiced):
            end = min(end, voiced[index + 1]["start"])
        end = min(end, film_end)
        if end <= item["start"]:
            continue
        cues.append(
            {
                "shot_id": item["shot_id"],
                "text": item["text"],
                "start": item["start"],
                "end": end,
                "file": item["file"],
            }
        )
    return cues


def load_cues(path):
    file_path = Path(path)
    empty = {"lines": [], "music": {}, "timeline": [], "note": ""}
    if not file_path.is_file():
        return empty
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    empty.update(data)
    return empty


def save_cues(path, doc):
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(file_path)
    return file_path


def _file_ready(path):
    file_path = Path(path)
    return file_path.is_file() and file_path.stat().st_size > 0


def _line_index(doc):
    found = {}
    for item in (doc or {}).get("lines") or []:
        if not isinstance(item, dict):
            continue
        found[(str(item.get("shot_id") or ""), str(item.get("hash") or ""))] = item
    return found


def _measure(path, duration_of):
    if duration_of:
        seconds = float(duration_of(path))
    else:
        seconds = _probe_duration(path)
    if seconds <= 0:
        raise RuntimeError("旁白没有时长：%s" % path)
    return seconds


def _probe_duration(path):
    from end.render_loop import _clip_geometry, _ffmpeg_bin

    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("本机没有 ffmpeg")
    seconds = (_clip_geometry(ffmpeg, path) or {}).get("duration")
    if not seconds:
        raise RuntimeError("读不到时长：%s" % path)
    return float(seconds)


def prepare_lines(voice_dir, shots, doc, client, duration_of, mode):
    """One mp3 per narration. The same text and a file on disk skip the request."""
    voice_dir = Path(voice_dir)
    voice_dir.mkdir(parents=True, exist_ok=True)
    if normalize_soundtrack(mode) != SOUNDTRACK_NARRATION:
        return [], 0
    cached = _line_index(doc)
    lines = []
    requests = 0
    for shot in shots or []:
        text = str((shot or {}).get("narration") or "").strip()
        if not text:
            continue
        shot_id = str(shot.get("id") or "")
        digest = content_hash(text)
        filename = voice_filename(shot_id)
        dest = voice_dir / filename
        previous = cached.get((shot_id, digest))
        if previous and _file_ready(dest):
            length = previous.get("audio_duration")
            if not length:
                length = _measure(dest, duration_of)
            lines.append(
                {
                    "shot_id": shot_id,
                    "text": text,
                    "hash": digest,
                    "file": filename,
                    "audio_duration": float(length),
                }
            )
            continue
        if client is None:
            raise RuntimeError("没有旁白客户端，不能合成旁白")
        client.synthesize(text, dest)
        requests += 1
        lines.append(
            {
                "shot_id": shot_id,
                "text": text,
                "hash": digest,
                "file": filename,
                "audio_duration": _measure(dest, duration_of),
            }
        )
    return lines, requests


def _seconds(value):
    return "%.3f" % float(value)


def mix_command(ffmpeg, video, music, cues, voice_dir, target, film_end):
    """Ambient ducked, music lower, narration at its recorded level. Loop or trim the cue."""
    command = [ffmpeg, "-y", "-i", str(video)]
    filters = [
        "[0:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=%s[amb]" % AMBIENT_GAIN,
    ]
    labels = ["[amb]"]
    first_voice = 1
    if music:
        command.extend(["-stream_loop", "-1", "-i", str(music)])
        filters.append(
            "[1:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=%s,atrim=0:%s,asetpts=PTS-STARTPTS[mus]"
            % (MUSIC_GAIN, _seconds(film_end))
        )
        labels.append("[mus]")
        first_voice = 2
    for index, cue in enumerate(cues or []):
        command.extend(["-i", str(Path(voice_dir) / cue["file"])])
        span = max(0.01, float(cue["end"]) - float(cue["start"]))
        delay = max(0, int(round(float(cue["start"]) * 1000)))
        label = "n%d" % index
        filters.append(
            "[%d:a]aformat=sample_rates=44100:channel_layouts=stereo,atrim=0:%s,adelay=%d|%d[%s]"
            % (index + first_voice, _seconds(span), delay, delay, label)
        )
        labels.append("[%s]" % label)
    filters.append(
        "%samix=inputs=%d:duration=first:dropout_transition=0:normalize=0[a]"
        % ("".join(labels), len(labels))
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            _seconds(film_end),
            "-movflags",
            "+faststart",
            str(target),
        ]
    )
    return command


def ffmpeg_mix(video, music, cues, voice_dir, target, film_end):
    from end.render_loop import _ffmpeg_bin

    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("本机没有 ffmpeg")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        mix_command(ffmpeg, video, music, cues, voice_dir, target, film_end),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("ffmpeg 混音失败：%s" % ((proc.stdout or "")[-400:]))
    return target


def lay_soundtrack(
    work_dir,
    shots,
    clip_durations,
    mode,
    video,
    brief="",
    client=None,
    mixer=None,
    duration_of=None,
    title="",
    target=None,
):
    """Record what changed, then mix. Same narration text keeps the mp3 and only the mix runs again."""
    mode = normalize_soundtrack(mode)
    work_dir = Path(work_dir)
    if not soundtrack_enabled(mode):
        return {"note": "", "timeline": [], "mixed": None, "voice_requests": 0, "music_requests": 0}
    durations = [float(item) for item in clip_durations]
    film_end = float(sum(durations))
    if film_end <= 0:
        raise RuntimeError("成片没有时长，不能铺声轨")
    voice_dir = work_dir / "outputs" / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    doc = load_cues(voice_dir / "cues.json")
    lines, voice_requests = prepare_lines(voice_dir, shots, doc, client, duration_of, mode)
    timeline = schedule_voice(shots, durations, lines, film_end=film_end)
    note = soundtrack_note(mode, shots)
    save_cues(
        voice_dir / "cues.json",
        {"lines": lines, "music": {}, "timeline": timeline, "note": note},
    )
    mixed = Path(target) if target else work_dir / "outputs" / "mixed.mp4"
    if not timeline:
        mixed.parent.mkdir(parents=True, exist_ok=True)
        mixed.write_bytes(Path(video).read_bytes())
    else:
        mix = mixer or ffmpeg_mix
        mix(video, None, timeline, voice_dir, mixed, film_end)
    return {
        "note": note,
        "timeline": timeline,
        "mixed": mixed,
        "voice_requests": voice_requests,
        "music_requests": 0,
    }
