"""Local render: iCreat stills, CR MiniMax H3 clips, ffmpeg join, optional MiniMax soundtrack, then burn 字幕.ass."""

import base64
import json
import math
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from cr_api.client import ASPECT_RATIOS, CRError
from icreat_api.client import ICreatError
from end.soundtrack import lay_soundtrack, normalize_soundtrack, soundtrack_enabled
from end.workflow_from_spec import collect_steps, ordered_assets
from image_store import get_image_store


def _agent_log(hypothesis_id, location, message, data):
    # #region agent log
    try:
        payload = {
            "sessionId": "caf74c",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open("/Users/shaneson/AiVideo/.cursor/debug-caf74c.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion


def _probe_media_url(url):
    # #region agent log
    request = urllib.request.Request(str(url), headers={"Range": "bytes=0-15"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            chunk = response.read(16)
            return {
                "status": getattr(response, "status", 200),
                "type": response.headers.get("Content-Type"),
                "length": response.headers.get("Content-Length"),
                "magic": chunk[:8].hex(),
            }
    except Exception as error:
        body = ""
        reader = getattr(error, "read", None)
        if reader:
            try:
                body = reader(180).decode("utf-8", "replace")
            except Exception:
                body = ""
        return {"status": getattr(error, "code", None), "error": str(error)[:180], "body": body}
    # #endregion

MAX_ATTEMPTS = 5
IMAGE_POLL_SEC = 4
VIDEO_POLL_SEC = 10
FACE_LOCK = "参考图是同一身份的定妆，必须锁定同一张脸和外形，不要换脸。\n"
TRANSIENT_HTTP = {429, 502, 503, 504}
IMAGE_SUCCESS = {"SUCCEEDED"}
IMAGE_FAILURE = {"FAILED", "NOT_FOUND"}
VIDEO_SUCCESS = {"completed"}
VIDEO_FAILURE = {"failed"}
SUBTITLE_FONT = "Noto Sans CJK SC"
SUBTITLE_FONTS_DIR = "/usr/share/fonts/opentype/noto"
FRAME_WIDTH = 768
FRAME_HEIGHT = 1344
CAPTION_LIMIT = 12
_INNER_PUNCT = "，。、；："

_GUARD = threading.Lock()
_LOCKS = {}
_GATES = {}
_THREADS = {}


class PauseGate(object):
    def __init__(self):
        self._event = threading.Event()
        self._event.set()
        self.blocked = threading.Event()
        self.reason = ""

    def pause(self, reason=""):
        if reason:
            self.reason = reason
        self._event.clear()

    def resume(self):
        self._event.set()

    def is_running(self):
        return self._event.is_set()

    def wait(self):
        if self._event.is_set():
            return
        self.blocked.set()
        self._event.wait()
        self.blocked.clear()


def project_lock(project_id):
    with _GUARD:
        lock = _LOCKS.get(project_id)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[project_id] = lock
        return lock


def gate_for(project_id):
    with _GUARD:
        gate = _GATES.get(project_id)
        if gate is None:
            gate = PauseGate()
            _GATES[project_id] = gate
        return gate


def is_running(project_id):
    with _GUARD:
        gate = _GATES.get(project_id)
    if gate is None:
        return True
    return gate.is_running()


def register_thread(project_id, thread=None):
    if not project_id:
        return
    with _GUARD:
        _THREADS[project_id] = thread or threading.current_thread()


def thread_alive(project_id):
    with _GUARD:
        thread = _THREADS.get(project_id)
    return thread is not None and thread.is_alive()


def empty_state():
    return {
        "phase": "",
        "paused": False,
        "pause_reason": "",
        "paused_by": "",
        "current_step": "",
        "steps": {},
    }


def blank_step(kind):
    return {
        "kind": kind,
        "status": "pending",
        "remote_task_id": "",
        "poll_url": "",
        "idempotency_key": "",
        "attempts": 0,
        "epoch": 0,
        "output_file": "",
        "public_url": "",
        "local_override": False,
        "error": "",
    }


def load_run_state(work_dir):
    path = Path(work_dir) / "run_state.json"
    if not path.is_file():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    state = empty_state()
    state.update(data)
    if not isinstance(state.get("steps"), dict):
        state["steps"] = {}
    return state


def save_run_state(work_dir, state):
    folder = Path(work_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "run_state.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def output_paths(work_dir, step_id, kind):
    root = Path(work_dir) / "outputs"
    if kind == "image":
        relative = "images/%s.png" % step_id
    elif kind == "video":
        relative = "clips/%s.mp4" % step_id
    elif kind == "audio":
        relative = "mixed.mp4"
    else:
        relative = "joined.mp4"
    return root / relative, relative


def should_resume_project(project, work_dir):
    """True when a rendering project still has an unfinished checkpoint."""
    if not project or project.get("status") != "rendering":
        return False
    folder = Path(work_dir)
    if not (folder / "spec.json").is_file():
        return False
    state = load_run_state(folder)
    video = folder / "outputs" / "joined.mp4"
    if state.get("phase") == "done" and video.is_file() and video.stat().st_size > 0:
        return False
    return True


def _output_file(work_dir, relative):
    text = str(relative or "").strip().lstrip("/")
    if not text:
        return None
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    return Path(work_dir) / "outputs" / path


def _reset_target(state):
    steps = state.get("steps") or {}
    current = state.get("current_step") or ""
    if current and current in steps and steps[current].get("status") != "done":
        return current
    for key, step in steps.items():
        if step.get("status") in ("failed", "paused", "error", "retrying", "running", "pending"):
            return key
    return ""


def reset_current_step(work_dir, project_id=None):
    """Clear the current failed or paused step so the next loop submits it again."""
    lock = project_lock(project_id or str(Path(work_dir)))
    with lock:
        state = load_run_state(work_dir)
        step_id = _reset_target(state)
        if not step_id:
            raise RuntimeError("没有可重置的步骤")
        step = state["steps"].get(step_id)
        if not step or step.get("status") == "done":
            raise RuntimeError("没有可重置的步骤")
        path = _output_file(work_dir, step.get("output_file"))
        if path is None:
            kind = step.get("kind") or "image"
            path, relative = output_paths(work_dir, step_id, kind)
            step["output_file"] = relative
        for candidate in (path, Path(str(path) + ".part"), path.with_suffix(path.suffix + ".part")):
            if candidate.is_file():
                candidate.unlink()
        step["remote_task_id"] = ""
        step["poll_url"] = ""
        step["idempotency_key"] = ""
        step["attempts"] = 0
        step["epoch"] = int(step.get("epoch") or 0) + 1
        step["status"] = "pending"
        step["error"] = ""
        step["public_url"] = ""
        step["local_override"] = False
        step["output_file"] = ""
        state["current_step"] = step_id
        state["paused"] = False
        state["pause_reason"] = ""
        state["paused_by"] = ""
        save_run_state(work_dir, state)
        return step_id


def mark_user_pause(work_dir, project_id, reason="已暂停"):
    gate_for(project_id).pause(reason)
    with project_lock(project_id):
        state = load_run_state(work_dir)
        state["paused"] = True
        state["pause_reason"] = reason
        state["paused_by"] = "user"
        current = state.get("current_step") or ""
        step = (state.get("steps") or {}).get(current)
        if step and step.get("status") not in ("done", "failed"):
            step["status"] = "paused"
        save_run_state(work_dir, state)
        return state


def note_local_image(work_dir, project_id, asset_id, public_url=None):
    """Record a user-supplied still and its public URL.

    local_override marks a replaced generated still so a later asset does not
    reuse it through depends_on. An opening upload uses the same flag, and
    Image 2 still reads that public URL when an asset lists it in references.
    A step that already has a remote task keeps that request.
    """
    with project_lock(project_id or str(Path(work_dir))):
        state = load_run_state(work_dir)
        steps = state.setdefault("steps", {})
        step = steps.get(asset_id) or blank_step("image")
        if step.get("remote_task_id") and step.get("status") in ("running", "retrying"):
            return step
        hosted = str(public_url or "").strip()
        if not _http_media_url(hosted):
            hosted = get_image_store().url(project_id, "%s.png" % asset_id)
        step["kind"] = "image"
        step["status"] = "done"
        step["output_file"] = "images/%s.png" % asset_id
        step["public_url"] = hosted
        step["local_override"] = True
        step["error"] = ""
        steps[asset_id] = step
        save_run_state(work_dir, state)
        return step


def resume_paused(work_dir, project_id):
    """Continue a paused run. A failure pause gets a fresh attempt budget."""
    gate = gate_for(project_id)
    with project_lock(project_id):
        state = load_run_state(work_dir)
        if state.get("paused_by") == "failure":
            current = state.get("current_step") or ""
            step = (state.get("steps") or {}).get(current)
            if step and step.get("status") != "done":
                step["attempts"] = 0
                if step.get("status") in ("failed", "paused", "error"):
                    step["status"] = "pending"
        state["paused"] = False
        state["pause_reason"] = ""
        state["paused_by"] = ""
        save_run_state(work_dir, state)
        gate.resume()
        return state


def is_transient(error):
    status = getattr(error, "status", None)
    if status in TRANSIENT_HTTP:
        return True
    text = str(error or "")
    lowered = text.lower()
    if "缺少 " in text and "KEY" in text:
        return False
    needles = (
        "网络错误",
        "timeout",
        "timed out",
        "connection",
        "reset by peer",
        "temporarily",
        "unreachable",
    )
    return any(token in text or token in lowered for token in needles)


def backoff_seconds(attempt):
    return min(30, 2 * max(1, int(attempt)))


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


def _clip_geometry(ffmpeg, path):
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    text = proc.stdout or ""
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    seconds = None
    if duration:
        hours, minutes, secs = duration.groups()
        seconds = int(hours) * 3600 + int(minutes) * 60 + float(secs)
    return {"duration": seconds, "has_audio": "Audio:" in text}


def ffmpeg_join(clips, target):
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("本机没有 ffmpeg")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    list_path = target.parent / "concat_list.txt"
    lines = []
    geometries = []
    for clip in clips:
        path = Path(clip).resolve()
        geometries.append(_clip_geometry(ffmpeg, path))
        lines.append("file '%s'" % path.as_posix().replace("'", "'\\''"))
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = subprocess.run(
        _normalize_join_command(ffmpeg, clips, geometries, target),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("ffmpeg 拼接失败：%s" % ((proc.stdout or "")[-400:]))
    return target


def _normalize_join_command(ffmpeg, clips, geometries, target):
    """Fit every clip into the 9:16 frame, then concat with fresh timestamps.

    Stream copy keeps each clip's own size and repeats an audio timestamp at
    every cut. Players hold the picture there and replay that sound.
    """
    filters = []
    labels = []
    for index, item in enumerate(geometries):
        filters.append(
            "[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v%d]"
            % (index, FRAME_WIDTH, FRAME_HEIGHT, FRAME_WIDTH, FRAME_HEIGHT, index)
        )
        if item.get("has_audio"):
            filters.append(
                "[%d:a]aformat=sample_rates=32000:channel_layouts=stereo,"
                "aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS[a%d]"
                % (index, index)
            )
        else:
            duration = item.get("duration") or 5
            filters.append(
                "anullsrc=r=32000:cl=stereo,atrim=0:%s,asetpts=PTS-STARTPTS[a%d]"
                % (duration, index)
            )
        labels.append("[v%d][a%d]" % (index, index))
    filters.append("%sconcat=n=%d:v=1:a=1[v][a]" % ("".join(labels), len(clips)))
    command = [ffmpeg, "-y"]
    for clip in clips:
        command.extend(["-i", str(Path(clip).resolve())])
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(target),
        ]
    )
    return command


def display_caption(text):
    """Show the script line: keep ？ and ！, turn inner pauses into a full-width space."""
    raw = str(text or "").strip().strip("「」\"'")
    while raw and raw[-1] in _INNER_PUNCT:
        raw = raw[:-1].rstrip()
    shown = "".join("\u3000" if char in _INNER_PUNCT else char for char in raw)
    shown = re.sub(r"\u3000+", "\u3000", shown).strip("\u3000")
    return shown.replace("\n", " ").strip()


def split_caption(text, limit=CAPTION_LIMIT):
    """One screen line. Longer sentences break at a pause, then by character count."""
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts = [part for part in text.split("\u3000") if part]
    if len(parts) <= 1:
        return [text[index:index + limit] for index in range(0, len(text), limit)]
    lines = []
    current = ""
    for part in parts:
        trial = part if not current else current + "\u3000" + part
        if current and len(trial) > limit:
            lines.append(current)
            current = part
        else:
            current = trial
    if current:
        lines.append(current)
    flattened = []
    for line in lines:
        if len(line) <= limit:
            flattened.append(line)
        else:
            flattened.extend(line[index:index + limit] for index in range(0, len(line), limit))
    return flattened


def subtitle_cues(shots):
    """Time each spoken line across its shot. The words stay the script's words."""
    cursor = 0.0
    cues = []
    for shot in shots or []:
        duration = float(shot.get("duration") or 0)
        narration = str(shot.get("narration") or "").strip()
        pieces = []
        if narration:
            pieces.extend(split_caption(display_caption(narration)))
        else:
            for item in shot.get("spoken") or []:
                pieces.extend(split_caption(display_caption(item.get("line") or "")))
        if pieces and duration > 0:
            weights = [max(1, len(piece.replace("\u3000", ""))) for piece in pieces]
            total = float(sum(weights))
            start = cursor
            for index, (piece, weight) in enumerate(zip(pieces, weights)):
                if index == len(pieces) - 1:
                    end = cursor + duration
                else:
                    end = start + duration * weight / total
                cues.append({"start": start, "end": end, "text": piece})
                start = end
        if duration > 0:
            cursor += duration
    return cues


def _ass_time(seconds):
    hundredths = max(0, int(round(float(seconds) * 100)))
    hours, hundredths = divmod(hundredths, 360000)
    minutes, hundredths = divmod(hundredths, 6000)
    whole, hundredths = divmod(hundredths, 100)
    return "%d:%02d:%02d.%02d" % (hours, minutes, whole, hundredths)


def _ass_text(text):
    return str(text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def build_subtitle_ass(cues, width=FRAME_WIDTH, height=FRAME_HEIGHT):
    """ASS in frame pixels: type 4.3% of height, baseline 24% up from the bottom."""
    font_size = max(18, int(round(height * 0.043)))
    outline = max(2, int(round(height * 0.0042)))
    margin_v = max(24, int(round(height * 0.24)))
    margin_h = max(24, int(round(width * 0.06)))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "PlayResX: %s\n"
        "PlayResY: %s\n"
        "\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: 正片,%s,%s,&H00FFFFFF,&H00FFFFFF,"
        "&H00120D0B,&H80000000,-1,0,0,0,100,100,0,0,1,%s,1,2,"
        "%s,%s,%s,1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    ) % (width, height, SUBTITLE_FONT, font_size, outline, margin_h, margin_h, margin_v)
    lines = [
        "Dialogue: 0,%s,%s,正片,,0,0,0,,%s"
        % (_ass_time(cue["start"]), _ass_time(cue["end"]), _ass_text(cue["text"]))
        for cue in cues
    ]
    return header + "\n".join(lines) + "\n"


def write_subtitle_ass(cues, path, width=FRAME_WIDTH, height=FRAME_HEIGHT):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_subtitle_ass(cues, width, height), encoding="utf-8")
    return path


def _ass_filter(path):
    escaped = Path(path).resolve().as_posix().replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
    filt = "ass='%s'" % escaped
    if Path(SUBTITLE_FONTS_DIR).is_dir():
        filt += ":fontsdir=%s" % SUBTITLE_FONTS_DIR
    return filt


def _missing_subtitle_glyphs(log):
    return "failed to find any fallback with glyph" in (log or "")


def ffmpeg_burn_subtitles(video, ass_path, target):
    """Burn 字幕.ass into a copy of the joined film. The source file stays intact."""
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("本机没有 ffmpeg")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    video_filter = _ass_filter(ass_path)
    base = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(video),
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
    ]
    proc = subprocess.run(
        base + ["-c:a", "copy", str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            base + ["-c:a", "aac", "-b:a", "192k", str(target)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    log = proc.stdout or ""
    if proc.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("ffmpeg 烧录字幕失败：%s" % log[-400:])
    if _missing_subtitle_glyphs(log):
        target.unlink(missing_ok=True)
        raise RuntimeError("字幕字体没有中文字形，成片没有烧上字")
    return target


def burn_joined_subtitles(joined, ass_path, burner=None, source=None):
    """Keep the unsubtitled concat, then replace the deliverable with the burned film.

    source is the mixed master when a soundtrack is on, so a later burn does not
    replace that mix with the quiet concat.
    """
    joined = Path(joined)
    if source:
        raw = Path(source)
        if not raw.is_file():
            raise RuntimeError("混音母带不存在，不能烧字幕")
    else:
        raw = joined.with_name("concat.mp4")
        if not raw.is_file():
            raw.write_bytes(joined.read_bytes())
    burned = joined.with_name("joined.burn.mp4")
    if burner:
        burner(raw, ass_path, burned)
    else:
        ffmpeg_burn_subtitles(raw, ass_path, burned)
    if not burned.is_file() or burned.stat().st_size <= 0:
        raise RuntimeError("烧录字幕没有写出成片")
    burned.replace(joined)
    return joined


def join_clips(clips, target, joiner=None):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) == 1:
        source = Path(clips[0])
        if source.resolve() != target.resolve():
            target.write_bytes(source.read_bytes())
        return target
    if joiner:
        return joiner(clips, target)
    return ffmpeg_join(clips, target)


def data_url(path):
    file_path = Path(path)
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(file_path.suffix.lower(), "image/png")
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return "data:%s;base64,%s" % (mime, encoded)


def compact_data_url(path):
    """JPEG at the video frame size. Full PNGs time out while uploading to CR."""
    ffmpeg = _ffmpeg_bin()
    source = Path(path)
    if ffmpeg and source.is_file():
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                "scale=768:1344:force_original_aspect_ratio=decrease",
                "-q:v",
                "4",
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
        if proc.returncode == 0 and proc.stdout:
            encoded = base64.b64encode(proc.stdout).decode("ascii")
            return "data:image/jpeg;base64," + encoded
    return data_url(source)


def image_prompt(asset, reference=False, template=None):
    from skills.registry import template_render

    prompt = (asset.get("prompt") or "").strip()
    suffix = template_render(template)["image"]
    if suffix and suffix not in prompt:
        prompt = prompt + "\n" + suffix
    if reference:
        prompt = FACE_LOCK + prompt
    return prompt


_DIALOGUE_MARK = re.compile(
    r"==\s*([^：:\n＝]{1,16}?)(?:（[^）]{0,24}）)?[：:]\s*([^＝\n]+?)\s*=="
)


def spoken_lines(script):
    lines = []
    for match in _DIALOGUE_MARK.finditer(script or ""):
        speaker = match.group(1).strip()
        line = match.group(2).strip()
        if speaker in ("场景", "人物外形锁定", "车辆锁定", "司机锁定", "路面", "配比", "镜头机位", "风格"):
            continue
        if "岁" in line or len(line) > 40:
            continue
        lines.append({"speaker": speaker, "line": line})
    return lines


def _norm_line(text):
    return re.sub(r"[。！？!?…\s]+$", "", (text or "").strip())


def _contains_line(blob, line):
    text = blob or ""
    if line and line in text:
        return True
    bare = _norm_line(line)
    if bare and len(bare) >= 4 and bare in text:
        return True
    for quote in re.findall(r"[「“\"]([^」”\"]{1,40})[」”\"]", text):
        if bare and _norm_line(quote) == bare:
            return True
    return False


def _numbered_blocks(text):
    blocks = []
    current = []
    for line in (text or "").splitlines():
        if re.match(r"\d+\.\s", line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _explicit_lines(text):
    items = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        match = re.match(r"^([^：:]{1,16})(?:（[^）]{0,24}）)?[：:](.+)$", raw)
        if match:
            items.append({"speaker": match.group(1).strip(), "line": match.group(2).strip()})
        else:
            items.append({"speaker": "角色", "line": raw})
    return items


def assign_dialogues(spec, shots):
    """Pair each shot with the spoken lines the script already wrote."""
    lines = spoken_lines((spec or {}).get("script") or "")
    blocks = _numbered_blocks((spec or {}).get("storyboard") or "")
    beats = _numbered_blocks((spec or {}).get("script") or "")
    used = set()
    assigned = [[] for _ in shots]
    for index, shot in enumerate(shots):
        explicit = _explicit_lines(shot.get("dialogue") or "")
        if explicit:
            assigned[index] = explicit
            for item in explicit:
                used.add(item["line"])
            continue
        blob = shot.get("prompt") or ""
        if index < len(blocks):
            blob += "\n" + blocks[index]
        found = [item for item in lines if item["line"] not in used and _contains_line(blob, item["line"])]
        assigned[index] = found
        for item in found:
            used.add(item["line"])
    for beat_index, beat in enumerate(beats):
        if beat_index >= len(assigned):
            break
        extra = [item for item in lines if item["line"] not in used and _contains_line(beat, item["line"])]
        assigned[beat_index].extend(extra)
        for item in extra:
            used.add(item["line"])
    order = {item["line"]: index for index, item in enumerate(lines)}
    for group in assigned:
        group.sort(key=lambda item: order.get(item["line"], 10**6))
    return assigned


VIDEO_MODES = ("text", "first_frame", "first_last_frame", "reference")
VIDEO_MODE_MODELS = {
    "text": "minimax-h3-text-to-video",
    "first_frame": "minimax-h3-image-to-video",
    "first_last_frame": "minimax-h3-image-to-video",
    "reference": "minimax-h3-reference-to-video",
}
FRAME_OPENING = "从这张首帧的可见内容开始，主体、材质和构图保持一致，只做本镜写的光影和轻微运镜。"


def image_size(path):
    """Width and height from a PNG or JPEG. Unreadable files return None."""
    if not path:
        return None
    try:
        with Path(path).open("rb") as handle:
            header = handle.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24 and header[12:16] == b"IHDR":
                width, height = struct.unpack(">II", header[16:24])
                if width and height:
                    return width, height
            if header[:2] == b"\xff\xd8":
                found = _jpeg_size(header + handle.read())
                if found:
                    return found
    except OSError:
        return None
    return None


def _jpeg_size(data):
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    index = 2
    while index + 1 < len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        index += 2
        while marker == 0xFF and index < len(data):
            marker = data[index]
            index += 1
        if marker in (0xD8, 0xD9):
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            return None
        length = struct.unpack(">H", data[index : index + 2])[0]
        if length < 2 or index + length > len(data):
            return None
        if marker in sof and length >= 7:
            height, width = struct.unpack(">HH", data[index + 3 : index + 7])
            if width and height:
                return width, height
        index += length
    return None


def nearest_aspect_ratio(width, height):
    """Legal H3 ratio closest to width/height. 2160×2880 is 3:4."""
    if not width or not height:
        return "9:16"
    ratio = float(width) / float(height)
    best = "9:16"
    best_gap = None
    for label in ASPECT_RATIOS:
        wide, tall = label.split(":")
        target = float(wide) / float(tall)
        gap = abs(math.log(ratio) - math.log(target))
        if best_gap is None or gap < best_gap:
            best = label
            best_gap = gap
    return best


def aspect_for_image(path):
    size = image_size(path)
    if not size:
        return "9:16"
    return nearest_aspect_ratio(size[0], size[1])


def choose_video_mode(shot, user_image_ids=(), continues=False):
    """Pick text, first frame, first-and-last, or reference for one shot.

    An explicit video_mode wins. Otherwise a single user still with no end
    frame opens on that still. A start image plus end_image uses both frames.
    Other pictured shots, and later shots that continue the previous clip,
    stay on reference. A pictureless opening shot is text-to-video.
    """
    explicit = str((shot or {}).get("video_mode") or "").strip()
    if explicit:
        if explicit not in VIDEO_MODES:
            raise RuntimeError("未知的 video_mode：%s" % explicit)
        return explicit
    images = list((shot or {}).get("images") or [])
    end_image = str((shot or {}).get("end_image") or "").strip()
    user_ids = {str(item) for item in user_image_ids}
    if len(images) == 1 and images[0] in user_ids and not end_image:
        return "first_frame"
    if images and end_image:
        return "first_last_frame"
    if images or continues:
        return "reference"
    return "text"


def assemble_shot_media(shot, mode, url_for, tail_url=None):
    """Roles and aspect for one H3 request. The previous clip's mp4 is never sent."""
    images = list((shot or {}).get("images") or [])
    shot_id = (shot or {}).get("id") or ""
    if mode == "text":
        return "9:16", []
    if mode == "first_frame":
        if not images:
            raise RuntimeError("首帧模式需要一张起始图")
        url, path = url_for(images[0])
        if not url:
            raise RuntimeError("镜头 %s 缺少参考图 %s" % (shot_id, images[0]))
        return aspect_for_image(path), [{"role": "first_frame", "url": url}]
    if mode == "first_last_frame":
        end_id = str((shot or {}).get("end_image") or "").strip()
        if not images or not end_id:
            raise RuntimeError("首尾帧模式需要起始图和结束帧")
        start_url, start_path = url_for(images[0])
        end_url, _end_path = url_for(end_id)
        if not start_url:
            raise RuntimeError("镜头 %s 缺少参考图 %s" % (shot_id, images[0]))
        if not end_url:
            raise RuntimeError("镜头 %s 缺少结束帧 %s" % (shot_id, end_id))
        return aspect_for_image(start_path), [
            {"role": "first_frame", "url": start_url},
            {"role": "last_frame", "url": end_url},
        ]
    if mode != "reference":
        raise RuntimeError("未知的 video_mode：%s" % mode)
    media = []
    for asset_id in images:
        url, _path = url_for(asset_id)
        if not url:
            raise RuntimeError("镜头 %s 缺少参考图 %s" % (shot_id, asset_id))
        media.append({"role": "reference_image", "url": url})
    if tail_url:
        media.append({"role": "reference_image", "url": tail_url})
    if not media:
        raise RuntimeError("参考模式没有可用的参考图")
    return "9:16", media


def delivery_prompt(shot, spoken, index, has_tail, mode="reference", soundtrack="off", template=None):
    """Prompt sent to MiniMax. Vehicle lines come only from the active template's skills.

    Music or narration is mixed after the join, so those shots keep only room tone.
    """
    from skills.registry import template_render

    base = (shot.get("prompt") or "").strip()
    render = template_render(template)
    if mode in ("first_frame", "first_last_frame"):
        parts = [base, FRAME_OPENING, "全程无字幕、无对白文字叠加。"]
    else:
        parts = [base]
        if render["video_lock"]:
            parts.append(render["video_lock"])
        else:
            parts.append("全程无字幕、无对白文字叠加。")
        if index > 0 and render["video_next"]:
            parts.append(render["video_next"])
        if has_tail and render["video_tail"]:
            parts.append(render["video_tail"])
    post = soundtrack_enabled(soundtrack)
    narration = str(shot.get("narration") or "").strip()
    if narration and narration not in base and not post:
        parts.append("画外旁白，人物不要对口型，嘴保持自然闭合：<d>[Chinese] %s</d>" % narration)
    elif "<d>" not in base:
        if spoken and not post:
            for item in spoken:
                parts.append(
                    "%s在画内开口，口型只跟这一句，其他人嘴唇保持闭合，不要额外人声，不要外语开场：<d>[Chinese] %s</d>"
                    % (item["speaker"], item["line"])
                )
        else:
            parts.append("本镜无对白。保留环境声和音效，不要凭空说话。")
    if "non_diegetic_music" not in base:
        parts.append("non_diegetic_music: N/A")
    return "\n".join(parts)


def _tail_frame(clip_path, dest):
    """Last frame of a clip. H3 rejects a local data: URI, so this is not submitted."""
    ffmpeg = _ffmpeg_bin()
    source = Path(clip_path)
    if not ffmpeg or not source.is_file():
        return None
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [ffmpeg, "-y", "-sseof", "-0.12", "-i", str(source), "-frames:v", "1", "-q:v", "3", str(dest)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        return None
    return dest


def _should_skip(step, path):
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    return step.get("status") in ("done", "running")


class RenderContext(object):
    def __init__(
        self,
        work_dir,
        on_progress=None,
        project_id="",
        icreat=None,
        cr=None,
        sleeper=None,
        joiner=None,
        burner=None,
        soundtrack="off",
        brief="",
        minimax=None,
        mixer=None,
        duration_of=None,
        template=None,
    ):
        self.work_dir = Path(work_dir)
        self.template = template
        self.on_progress = on_progress
        self.project_id = project_id or ""
        self.icreat = icreat
        self.cr = cr
        self.sleeper = sleeper
        self.joiner = joiner
        self.burner = burner
        self.soundtrack = normalize_soundtrack(soundtrack)
        self.brief = brief or ""
        self.minimax = minimax
        self.mixer = mixer
        self.duration_of = duration_of
        self.soundtrack_note = None
        self.lock = project_lock(self.project_id) if self.project_id else threading.RLock()
        self.gate = gate_for(self.project_id) if self.project_id else PauseGate()
        self.state = load_run_state(self.work_dir)
        self.done_steps = 0
        self.total_steps = 1
        self.last_remote_id = ""
        self.log_path = self.work_dir / "outputs" / "render.log"

    def note(self, text):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not str(text).endswith("\n"):
                handle.write("\n")

    def percent(self):
        if self.total_steps <= 0:
            return 0
        return max(0, min(99, int(100 * self.done_steps / self.total_steps)))

    def emit(self, message, phase, task_id, task_state, project_status="rendering", current=None, total=None, percent=None):
        self.note(message)
        if not self.on_progress:
            return
        payload = {
            "message": message,
            "percent": self.percent() if percent is None else percent,
            "phase": phase,
            "task_id": task_id,
            "task_state": task_state,
            "project_status": project_status,
        }
        if current is not None:
            payload["current"] = current
        if total is not None:
            payload["total"] = total
        if soundtrack_enabled(self.soundtrack):
            payload["soundtrack"] = self.soundtrack
        if self.soundtrack_note is not None:
            payload["soundtrack_note"] = self.soundtrack_note
        self.on_progress(payload)

    def load_locked(self):
        self.state = load_run_state(self.work_dir)
        return self.state

    def write_locked(self):
        save_run_state(self.work_dir, self.state)

    def reload_step(self, step_id, kind):
        with self.lock:
            state = self.load_locked()
            step = state["steps"].get(step_id)
            if step is None:
                step = blank_step(kind)
                state["steps"][step_id] = step
                self.write_locked()
            elif not step.get("kind"):
                step["kind"] = kind
            return step

    def block_while_paused(self):
        if self.gate.is_running():
            return
        self.gate.wait()
        with self.lock:
            self.load_locked()

    def sleep(self, seconds):
        if self.sleeper:
            self.sleeper(seconds)
            self.block_while_paused()
            return
        deadline = time.monotonic() + max(0, float(seconds))
        while time.monotonic() < deadline:
            if not self.gate.is_running():
                self.block_while_paused()
                return
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    def pause_for_failure(self, step_id, kind, reason):
        reason = str(reason or "失败")
        self.gate.pause(reason)
        with self.lock:
            state = self.load_locked()
            step = state["steps"].get(step_id) or blank_step(kind)
            state["steps"][step_id] = step
            step["kind"] = kind
            step["status"] = "failed"
            step["error"] = reason
            state["paused"] = True
            state["pause_reason"] = reason
            state["paused_by"] = "failure"
            state["current_step"] = step_id
            state["phase"] = "paused"
            self.write_locked()
        self.emit(
            "已暂停：" + reason,
            "paused",
            step_id,
            "error",
            project_status="paused",
        )
        self.block_while_paused()

    def _attempts(self, step):
        try:
            return int(step.get("attempts") or 0)
        except (TypeError, ValueError):
            return 0

    def submit_once(self, step_id, kind, submit):
        with self.lock:
            state = self.load_locked()
            step = state["steps"].get(step_id) or blank_step(kind)
            state["steps"][step_id] = step
            if step.get("remote_task_id"):
                return dict(step)
            if not step.get("idempotency_key"):
                step["idempotency_key"] = "step-%s-%s" % (step_id, uuid.uuid4().hex)
            step["attempts"] = self._attempts(step) + 1
            step["status"] = "running"
            step["kind"] = kind
            state["current_step"] = step_id
            state["phase"] = "images" if kind == "image" else "videos" if kind == "video" else kind
            self.write_locked()
            key = step["idempotency_key"]
            epoch = int(step.get("epoch") or 0)
        task = submit(key)
        remote_id = str(getattr(task, "id", "") or "")
        if not remote_id:
            raise RuntimeError("提交成功了，但没有任务号")
        poll_url = str(getattr(task, "poll_url", "") or "")
        with self.lock:
            state = self.load_locked()
            step = state["steps"].get(step_id) or blank_step(kind)
            epoch_now = int(step.get("epoch") or 0)
            reset_during_submit = epoch_now != epoch or (
                step.get("status") == "pending" and not step.get("remote_task_id") and self._attempts(step) == 0
            )
            if reset_during_submit:
                return dict(step)
            step["remote_task_id"] = remote_id
            if poll_url:
                step["poll_url"] = poll_url
            step["status"] = "running"
            step["kind"] = kind
            self.write_locked()
            self.last_remote_id = remote_id
            return dict(step)

    def on_submit_error(self, step_id, kind, label, error):
        with self.lock:
            step = (self.load_locked().get("steps") or {}).get(step_id) or blank_step(kind)
            attempts = self._attempts(step)
        if is_transient(error):
            self.emit(
                "重新连接：%s，第 %s/%s 次" % (label, attempts, MAX_ATTEMPTS),
                "reconnect",
                step_id,
                "retrying",
            )
            if attempts >= MAX_ATTEMPTS:
                self.pause_for_failure(step_id, kind, "重新连接失败：" + str(error))
            else:
                self.sleep(backoff_seconds(attempts))
            return
        with self.lock:
            state = self.load_locked()
            step = state["steps"].get(step_id) or blank_step(kind)
            state["steps"][step_id] = step
            step["idempotency_key"] = ""
            self.write_locked()
        self.pause_for_failure(step_id, kind, str(error))

    def on_poll_error(self, step_id, kind, label, error):
        with self.lock:
            state = self.load_locked()
            step = state["steps"].get(step_id) or blank_step(kind)
            state["steps"][step_id] = step
            if is_transient(error):
                step["attempts"] = self._attempts(step) + 1
                step["status"] = "retrying"
                step["error"] = str(error)
                self.write_locked()
                attempts = self._attempts(step)
                transient = True
            else:
                step["error"] = str(error)
                self.write_locked()
                attempts = self._attempts(step)
                transient = False
        if not transient:
            self.pause_for_failure(step_id, kind, str(error))
            return
        self.emit(
            "重新连接：%s，第 %s/%s 次" % (label, attempts, MAX_ATTEMPTS),
            "reconnect",
            step_id,
            "retrying",
        )
        if attempts >= MAX_ATTEMPTS:
            self.pause_for_failure(step_id, kind, "重新连接失败：" + str(error))
            return
        self.sleep(backoff_seconds(attempts))

    def on_remote_failure(self, step_id, kind, label, message):
        with self.lock:
            state = self.load_locked()
            step = state["steps"].get(step_id) or blank_step(kind)
            state["steps"][step_id] = step
            step["remote_task_id"] = ""
            step["poll_url"] = ""
            step["idempotency_key"] = ""
            step["status"] = "failed"
            step["error"] = message
            step["kind"] = kind
            state["current_step"] = step_id
            self.write_locked()
            attempts = self._attempts(step)
        if attempts >= MAX_ATTEMPTS:
            self.pause_for_failure(step_id, kind, message)
            return
        self.emit(
            "重试：%s，第 %s/%s 次（重新提交）" % (label, attempts, MAX_ATTEMPTS),
            "retry",
            step_id,
            "retrying",
        )
        self.sleep(backoff_seconds(attempts))

    def mark_done(self, step_id, kind, relative, public_url, generated=False):
        with self.lock:
            state = self.load_locked()
            step = state["steps"].get(step_id) or blank_step(kind)
            state["steps"][step_id] = step
            step["kind"] = kind
            step["status"] = "done"
            step["output_file"] = relative
            step["error"] = ""
            if public_url:
                step["public_url"] = public_url
            if generated:
                step["local_override"] = False
            self.write_locked()
            return dict(step)

    def run_remote(self, step_id, kind, label, phase, submit, poll, download, interval, index, group_total):
        path, relative = output_paths(self.work_dir, step_id, kind)
        success = IMAGE_SUCCESS if kind == "image" else VIDEO_SUCCESS
        failure = IMAGE_FAILURE if kind == "image" else VIDEO_FAILURE
        seen = None
        while True:
            self.block_while_paused()
            step = self.reload_step(step_id, kind)
            if _should_skip(step, path):
                fresh = self.mark_done(step_id, kind, relative, step.get("public_url") or "")
                self.done_steps += 1
                self.emit(
                    "%s 已完成：%s" % (label, step_id),
                    phase,
                    step_id,
                    "done",
                    current=index,
                    total=group_total,
                )
                return fresh
            if not step.get("remote_task_id") and self._attempts(step) >= MAX_ATTEMPTS:
                self.pause_for_failure(step_id, kind, step.get("error") or "失败次数已用完")
                seen = None
                continue
            if not step.get("remote_task_id"):
                self.emit(
                    label,
                    phase,
                    step_id,
                    "running",
                    current=index,
                    total=group_total,
                )
                try:
                    step = self.submit_once(step_id, kind, submit)
                except (ICreatError, CRError, OSError, RuntimeError) as error:
                    self.on_submit_error(step_id, kind, label, error)
                    seen = None
                    continue
                if not step.get("remote_task_id"):
                    seen = None
                    continue
                seen = None
            try:
                current = poll(step["remote_task_id"])
            except (ICreatError, CRError, OSError, RuntimeError) as error:
                self.on_poll_error(step_id, kind, label, error)
                continue
            fresh = self.reload_step(step_id, kind)
            if fresh.get("remote_task_id") != step.get("remote_task_id"):
                seen = None
                continue
            if int(fresh.get("epoch") or 0) != int(step.get("epoch") or 0):
                seen = None
                continue
            status = str(getattr(current, "status", "") or "")
            if kind == "image":
                status = status.upper()
            if status and status != seen:
                seen = status
                self.emit(
                    "%s：%s" % (label, status),
                    phase,
                    step_id,
                    "running",
                    current=index,
                    total=group_total,
                )
            if status in success:
                try:
                    public_url = download(current, path) or ""
                except (ICreatError, CRError, OSError, RuntimeError) as error:
                    self.on_poll_error(step_id, kind, label, error)
                    continue
                self.mark_done(step_id, kind, relative, public_url, generated=True)
                self.done_steps += 1
                self.emit(
                    "%s 完成" % label,
                    phase,
                    step_id,
                    "done",
                    current=index,
                    total=group_total,
                )
                return self.reload_step(step_id, kind)
            if status in failure:
                message = getattr(current, "error", "") or status
                # #region agent log
                raw = getattr(current, "raw", None)
                _agent_log(
                    "C",
                    "render_loop.py:run_remote",
                    "remote task failed",
                    {
                        "step_id": step_id,
                        "kind": kind,
                        "status": status,
                        "error": str(message or "")[:500],
                        "raw": raw if isinstance(raw, dict) else str(raw)[:800],
                    },
                )
                # #endregion
                self.on_remote_failure(step_id, kind, label, str(message or status))
                seen = None
                continue
            self.sleep(interval)

    def run_join(self, clips):
        step_id = "join"
        path, relative = output_paths(self.work_dir, step_id, "join")
        while True:
            self.block_while_paused()
            step = self.reload_step(step_id, "join")
            if _should_skip(step, path):
                self.mark_done(step_id, "join", relative, "")
                self.done_steps += 1
                self.emit("镜头已拼接", "join", step_id, "done")
                return path
            if self._attempts(step) >= MAX_ATTEMPTS and step.get("status") == "failed":
                self.pause_for_failure(step_id, "join", step.get("error") or "拼接失败")
                continue
            self.emit("正在拼接成片", "join", step_id, "running")
            try:
                with self.lock:
                    state = self.load_locked()
                    step = state["steps"].get(step_id) or blank_step("join")
                    state["steps"][step_id] = step
                    step["attempts"] = self._attempts(step) + 1
                    step["status"] = "running"
                    step["kind"] = "join"
                    state["current_step"] = step_id
                    state["phase"] = "join"
                    self.write_locked()
                join_clips(clips, path, self.joiner)
            except (OSError, RuntimeError) as error:
                reason = str(error)
                with self.lock:
                    state = self.load_locked()
                    step = state["steps"].get(step_id) or blank_step("join")
                    state["steps"][step_id] = step
                    step["status"] = "failed"
                    step["error"] = reason
                    self.write_locked()
                    attempts = self._attempts(step)
                if is_transient(error) and attempts < MAX_ATTEMPTS:
                    self.emit(
                        "重新连接：拼接成片，第 %s/%s 次" % (attempts, MAX_ATTEMPTS),
                        "reconnect",
                        step_id,
                        "retrying",
                    )
                    self.sleep(backoff_seconds(attempts))
                    continue
                self.pause_for_failure(step_id, "join", reason)
                continue
            self.mark_done(step_id, "join", relative, "")
            self.done_steps += 1
            with self.lock:
                state = self.load_locked()
                state["phase"] = "audio" if soundtrack_enabled(self.soundtrack) else "subtitles"
                self.write_locked()
            self.emit("镜头已拼接", "join", step_id, "done")
            return path

    def _media_seconds(self, path):
        if self.duration_of:
            return float(self.duration_of(path))
        ffmpeg = _ffmpeg_bin()
        if not ffmpeg:
            raise RuntimeError("本机没有 ffmpeg")
        seconds = (_clip_geometry(ffmpeg, path) or {}).get("duration")
        if not seconds or seconds <= 0:
            raise RuntimeError("读不到镜头时长：%s" % path)
        return float(seconds)

    def _invalidate_subtitles(self):
        """A fresh mix has to be burned again, or the deliverable keeps the old sound."""
        with self.lock:
            state = self.load_locked()
            step = (state.get("steps") or {}).get("subtitles")
            if not step or step.get("status") != "done":
                return
            step["status"] = "pending"
            step["output_file"] = ""
            self.write_locked()

    def run_audio(self, joined, clips, shots, spec=None):
        """After the join: music, and narration when that option is on. concat.mp4 stays unmixed."""
        step_id = "audio"
        path, relative = output_paths(self.work_dir, step_id, "audio")
        while True:
            self.block_while_paused()
            step = self.reload_step(step_id, "audio")
            if _should_skip(step, path):
                self.mark_done(step_id, "audio", relative, "")
                self.done_steps += 1
                self.emit("声轨已铺好", "audio", step_id, "done")
                return path
            if self._attempts(step) >= MAX_ATTEMPTS and step.get("status") == "failed":
                self.pause_for_failure(step_id, "audio", step.get("error") or "声轨失败")
                continue
            self.emit("正在铺声轨", "audio", step_id, "running")
            try:
                with self.lock:
                    state = self.load_locked()
                    step = state["steps"].get(step_id) or blank_step("audio")
                    state["steps"][step_id] = step
                    step["attempts"] = self._attempts(step) + 1
                    step["status"] = "running"
                    step["kind"] = "audio"
                    step["output_file"] = relative
                    state["current_step"] = step_id
                    state["phase"] = "audio"
                    self.write_locked()
                concat = Path(joined).with_name("concat.mp4")
                if not concat.is_file() or concat.stat().st_size <= 0:
                    concat.write_bytes(Path(joined).read_bytes())
                fresh = read_spec_file(self.work_dir, spec)
                by_id = {}
                title = ""
                if fresh:
                    _assets, fresh_shots = collect_steps(fresh)
                    by_id = {shot["id"]: shot for shot in fresh_shots}
                    title = str(fresh.get("title") or "")
                current_shots = [by_id.get(shot["id"], shot) for shot in shots]
                durations = [self._media_seconds(clip) for clip in clips]
                result = lay_soundtrack(
                    self.work_dir,
                    current_shots,
                    durations,
                    self.soundtrack,
                    concat,
                    brief=self.brief,
                    client=self.minimax,
                    mixer=self.mixer,
                    duration_of=self.duration_of,
                    title=title,
                    target=path,
                )
                self.soundtrack_note = result.get("note") or ""
            except (OSError, RuntimeError) as error:
                reason = str(error)
                with self.lock:
                    state = self.load_locked()
                    step = state["steps"].get(step_id) or blank_step("audio")
                    state["steps"][step_id] = step
                    step["status"] = "failed"
                    step["error"] = reason
                    self.write_locked()
                    attempts = self._attempts(step)
                if is_transient(error) and attempts < MAX_ATTEMPTS:
                    self.emit(
                        "重新连接：铺声轨，第 %s/%s 次" % (attempts, MAX_ATTEMPTS),
                        "reconnect",
                        step_id,
                        "retrying",
                    )
                    self.sleep(backoff_seconds(attempts))
                    continue
                self.pause_for_failure(step_id, "audio", reason)
                continue
            self._invalidate_subtitles()
            self.mark_done(step_id, "audio", relative, "")
            self.done_steps += 1
            with self.lock:
                state = self.load_locked()
                state["phase"] = "subtitles"
                self.write_locked()
            if self.soundtrack_note:
                message = "没有旁白，保留镜头原声"
            else:
                message = "声轨已铺好"
            self.emit(message, "audio", step_id, "done")
            return path

    def _mark_film_done(self):
        with self.lock:
            state = self.load_locked()
            state["phase"] = "done"
            state["paused"] = False
            state["pause_reason"] = ""
            state["paused_by"] = ""
            self.write_locked()

    def run_subtitles(self, joined, shots, spec=None):
        """Last step: write 字幕.ass from the script lines and burn it with ffmpeg."""
        step_id = "subtitles"
        ass_path = self.work_dir / "outputs" / "字幕.ass"
        while True:
            self.block_while_paused()
            step = self.reload_step(step_id, "subtitles")
            if step.get("status") == "done":
                self.done_steps += 1
                self._mark_film_done()
                self.emit("成片已生成，可以下载", "done", step_id, "done", percent=100)
                return joined
            if self._attempts(step) >= MAX_ATTEMPTS and step.get("status") == "failed":
                self.pause_for_failure(step_id, "subtitles", step.get("error") or "烧录字幕失败")
                continue
            shots = shots_for_subtitles(self.work_dir, shots, spec)
            for shot in shots:
                clip, _relative = output_paths(self.work_dir, shot["id"], "video")
                if clip.is_file() and clip.stat().st_size > 0:
                    shot["duration"] = self._media_seconds(clip)
            cues = subtitle_cues(shots)
            mixed = None
            if soundtrack_enabled(self.soundtrack):
                mixed, _relative = output_paths(self.work_dir, "audio", "audio")
            if not cues:
                if mixed is not None and mixed.is_file() and mixed.resolve() != Path(joined).resolve():
                    Path(joined).write_bytes(mixed.read_bytes())
                self.mark_done(step_id, "subtitles", "", "")
                self.done_steps += 1
                self._mark_film_done()
                self.emit("这一集没有对白，成片不烧字幕", "done", step_id, "done", percent=100)
                return joined
            self.emit("正在把字幕烧进成片", "subtitles", step_id, "running")
            try:
                with self.lock:
                    state = self.load_locked()
                    step = state["steps"].get(step_id) or blank_step("subtitles")
                    state["steps"][step_id] = step
                    step["attempts"] = self._attempts(step) + 1
                    step["status"] = "running"
                    step["kind"] = "subtitles"
                    state["current_step"] = step_id
                    state["phase"] = "subtitles"
                    self.write_locked()
                write_subtitle_ass(cues, ass_path)
                burn_joined_subtitles(joined, ass_path, self.burner, source=mixed)
            except (OSError, RuntimeError) as error:
                reason = str(error)
                with self.lock:
                    state = self.load_locked()
                    step = state["steps"].get(step_id) or blank_step("subtitles")
                    state["steps"][step_id] = step
                    step["status"] = "failed"
                    step["error"] = reason
                    self.write_locked()
                    attempts = self._attempts(step)
                if is_transient(error) and attempts < MAX_ATTEMPTS:
                    self.emit(
                        "重新连接：烧录字幕，第 %s/%s 次" % (attempts, MAX_ATTEMPTS),
                        "reconnect",
                        step_id,
                        "retrying",
                    )
                    self.sleep(backoff_seconds(attempts))
                    continue
                self.pause_for_failure(step_id, "subtitles", reason)
                continue
            self.mark_done(step_id, "subtitles", "字幕.ass", "")
            self.done_steps += 1
            self._mark_film_done()
            self.emit("成片已生成，可以下载", "done", step_id, "done", percent=100)
            return joined


def read_spec_file(work_dir, fallback=None):
    """Prefer spec.json so edits made after the loop started are visible."""
    path = Path(work_dir) / "spec.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            return data
    return fallback if isinstance(fallback, dict) else None


def find_asset(spec, asset_id, fallback):
    if spec:
        assets, _shots = collect_steps(spec)
        for item in assets:
            if item["id"] == asset_id:
                return item
    return fallback


def find_shot(spec, shot_id, fallback):
    shots = []
    if spec:
        _assets, shots = collect_steps(spec)
        for index, item in enumerate(shots):
            if item["id"] == shot_id:
                return item, shots, index
    if fallback:
        return fallback, [fallback], 0
    return None, [], 0


def shots_for_subtitles(work_dir, shots, spec=None):
    """Burn the script currently on disk. Finished runs never reach this."""
    fresh = read_spec_file(work_dir, spec)
    if not fresh:
        return shots
    _assets, fresh_shots = collect_steps(fresh)
    if not fresh_shots:
        return shots
    groups = assign_dialogues(fresh, fresh_shots)
    for shot, lines in zip(fresh_shots, groups):
        shot["spoken"] = lines
    return fresh_shots


def dependent_image_url(ctx, asset):
    """Public URL of a generated parent. A replaced still is not reused."""
    parent_id = (asset or {}).get("depends_on") or ""
    if not parent_id:
        return None
    step = ctx.reload_step(parent_id, "image")
    if step.get("local_override"):
        return None
    url = str(step.get("public_url") or "")
    if not url:
        raise RuntimeError("资产 %s 依赖的 %s 没有公网地址" % (asset["id"], parent_id))
    return url


def image_input_urls(ctx, asset):
    """Image 2 `image` urls for one generated asset.

    references: the upload public URLs, even when that step is local_override.
    depends_on, and no references: the previous generated still.
    neither: no image is sent.
    """
    asset = asset or {}
    refs = [str(ref).strip() for ref in (asset.get("references") or []) if str(ref).strip()]
    if refs:
        urls = []
        for ref_id in refs:
            step = ctx.reload_step(ref_id, "image")
            url = _publish_step_url(ctx, ref_id, step)
            if not _http_media_url(url):
                raise RuntimeError("资产 %s 的参考图 %s 没有公网地址" % (asset.get("id") or "", ref_id))
            urls.append(url)
        return urls
    url = dependent_image_url(ctx, asset)
    return [url] if url else []


def _publish_step_url(ctx, asset_id, step):
    """Replace a URL on our own media host with the address remote workers can open."""
    current = str((step or {}).get("public_url") or "").strip()
    filename = "%s.png" % asset_id
    output = str((step or {}).get("output_file") or "")
    if output:
        name = Path(output).name
        if name:
            filename = name
    source = _output_file(ctx.work_dir, output) if output else None
    if source is None:
        source, _relative = output_paths(ctx.work_dir, asset_id, "image")
    store = get_image_store()
    if current and not store.owns_url(current):
        return current
    if not ctx.project_id:
        return current
    hosted = store.ensure_remote(ctx.project_id, filename, current, source=source)
    if hosted and hosted != current:
        _remember_public_url(ctx.work_dir, ctx.project_id, asset_id, hosted)
    return hosted


def _remember_public_url(work_dir, project_id, asset_id, url):
    with project_lock(project_id or str(Path(work_dir))):
        state = load_run_state(work_dir)
        step = (state.get("steps") or {}).get(asset_id)
        if not step or step.get("public_url") == url:
            return
        step["public_url"] = url
        save_run_state(work_dir, state)


def _http_media_url(value):
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return text
    return ""


def _saved_image(ctx, asset_id):
    """A still that is already on disk but was not part of this run's image tasks."""
    with ctx.lock:
        state = ctx.load_locked()
        step = dict((state.get("steps") or {}).get(asset_id) or {})
    if not step:
        return None
    path, _relative = output_paths(ctx.work_dir, asset_id, "image")
    saved = _output_file(ctx.work_dir, step.get("output_file") or "")
    if saved is not None:
        path = saved
    if not path.is_file() and not _http_media_url(step.get("public_url")):
        return None
    return {
        "path": path,
        "url": step.get("public_url") or "",
        "local_override": bool(step.get("local_override")),
    }


def _reference_url(info, project_id=""):
    """CR 的图生视频和参考图生视频只接受它能打开的 HTTP(S) 网址。

    An existing http(s) address wins, including iCreat's own URL. A still that
    only has this service's /media address is published to COS first.
    """
    url = _http_media_url((info or {}).get("url"))
    filename = Path(str((info or {}).get("path") or "")).name
    store = get_image_store()
    if url and not store.owns_url(url):
        return url
    if (info or {}).get("local_override") and project_id and filename:
        return store.ensure_remote(project_id, filename, url, source=(info or {}).get("path"))
    if url:
        return url
    raise RuntimeError("参考图没有公网地址，CR 的图生视频只接受 HTTP(S) 网址")


def _video_handle(task_id, poll_url):
    from cr_api.client import VideoTask

    return VideoTask({"id": task_id, "poll_url": poll_url or "", "status": "submitted"})


class LayoutAudio(object):
    """Speech and music enter the existing LangGraph. There is no separate client."""

    def __init__(self, layout=None):
        self.layout = layout

    def _layout(self):
        if self.layout is None:
            from agent.layout import get_layout

            self.layout = get_layout()
        return self.layout

    def synthesize(self, text, dest, voice_id=None):
        del voice_id
        return self._layout().speak(text, dest)

    def generate_music(self, prompt, dest):
        return self._layout().compose(prompt, dest)


def run_local_pipeline(
    spec,
    work_dir,
    on_progress=None,
    project_id=None,
    icreat=None,
    cr=None,
    sleeper=None,
    joiner=None,
    burner=None,
    images_only=False,
    soundtrack="off",
    brief="",
    minimax=None,
    mixer=None,
    duration_of=None,
    template=None,
):
    """Generate stills, then clips, join them, optionally mix a soundtrack, then burn 字幕.ass.

    images_only stops after the stills. Video, join, soundtrack, and subtitles are left for a later run.
    """
    assets, shots = collect_steps(spec)
    if images_only:
        if not assets:
            raise ValueError("没有可用的资产图")
    elif not assets or not shots:
        raise ValueError("没有可用的资产图或镜头")
    assets = ordered_assets(assets)
    if icreat is None:
        from icreat_api import ICreatClient

        icreat = ICreatClient()
    if not images_only and cr is None:
        from cr_api import CRClient

        cr = CRClient()
    mode = normalize_soundtrack(soundtrack)
    if soundtrack_enabled(mode) and not images_only and minimax is None:
        from minimax_api import MiniMaxClient

        minimax = MiniMaxClient()
    ctx = RenderContext(
        work_dir,
        on_progress=on_progress,
        project_id=project_id or "",
        icreat=icreat,
        cr=cr,
        sleeper=sleeper,
        joiner=joiner,
        burner=burner,
        soundtrack=mode,
        brief=brief or "",
        minimax=minimax,
        mixer=mixer,
        duration_of=duration_of,
        template=template,
    )
    if project_id:
        register_thread(project_id)
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    (ctx.work_dir / "outputs" / "images").mkdir(parents=True, exist_ok=True)
    (ctx.work_dir / "outputs" / "clips").mkdir(parents=True, exist_ok=True)
    tail_steps = 3 if soundtrack_enabled(mode) else 2
    ctx.total_steps = len(assets) if images_only else len(assets) + len(shots) + tail_steps
    if images_only:
        ctx.emit("Debug：开始生图", "images", assets[0]["id"], "running")
    else:
        steps = "iCreat 生图 / CR MiniMax H3 / 本机拼接"
        if soundtrack_enabled(mode):
            steps += " / MiniMax 声轨"
        steps += " / ffmpeg 烧字幕"
        ctx.emit(
            "开始出片：" + steps,
            "images",
            assets[0]["id"],
            "running",
        )

    images = {}
    for index, asset in enumerate(assets, start=1):
        label = "生图 %s/%s：%s %s" % (index, len(assets), asset["id"], asset["title"])

        def submit(key, asset_id=asset["id"], fallback=asset):
            del key
            current = find_asset(read_spec_file(ctx.work_dir, spec), asset_id, fallback)
            urls = image_input_urls(ctx, current)
            image = urls or None
            return icreat.submit_image(
                image_prompt(current, reference=bool(urls), template=ctx.template),
                image=image,
            )

        def poll(task_id):
            return icreat.query_status(task_id)

        def download(task, path):
            result = icreat.get_result(getattr(task, "id", "") or "")
            icreat.download_image(result.url, path)
            return result.url

        fresh = ctx.run_remote(
            asset["id"],
            "image",
            label,
            "images",
            submit,
            poll,
            download,
            IMAGE_POLL_SEC,
            index,
            len(assets),
        )
        saved_path, _relative = output_paths(ctx.work_dir, asset["id"], "image")
        images[asset["id"]] = {
            "path": saved_path,
            "url": fresh.get("public_url") or "",
            "local_override": bool(fresh.get("local_override")),
        }

    if images_only:
        with ctx.lock:
            state = ctx.load_locked()
            state["phase"] = "images"
            state["paused"] = False
            state["pause_reason"] = ""
            state["paused_by"] = ""
            ctx.write_locked()
        ctx.emit(
            "Debug：图片已生成，视频、拼接和字幕暂不执行",
            "image_debug",
            assets[-1]["id"],
            "done",
            percent=100,
        )
        return {
            "prompt_id": ctx.last_remote_id,
            "video_file": "",
            "image_files": {key: Path(value["path"]).name for key, value in images.items()},
            "clip_files": [],
            "stopped_after": "images",
        }

    for shot, spoken in zip(shots, assign_dialogues(spec, shots)):
        shot["spoken"] = spoken

    clips = []

    for index, shot in enumerate(shots, start=1):
        label = "视频 %s/%s：%s" % (index, len(shots), shot["id"])
        shot_id = shot["id"]

        def submit(key, shot_id=shot["id"], fallback=shot, index=index):
            fresh = read_spec_file(ctx.work_dir, spec)
            current, fresh_shots, shot_index = find_shot(fresh, shot_id, fallback)
            if fresh and fresh_shots:
                spoken_groups = assign_dialogues(fresh, fresh_shots)
                spoken = spoken_groups[shot_index] if shot_index < len(spoken_groups) else []
                prompt_index = shot_index
            else:
                spoken = list(fallback.get("spoken") or [])
                prompt_index = index - 1
            asset_ids = list(current.get("images") or [])
            user_ids = [
                asset_id
                for asset_id in asset_ids
                if ctx.reload_step(asset_id, "image").get("local_override")
            ]
            continues = bool(clips)
            mode = choose_video_mode(current, user_image_ids=user_ids, continues=continues)
            # The previous clip's tail frame is only a local file. H3 rejects data: URIs.
            tail_url = None

            def url_for(asset_id):
                info = images.get(asset_id) or _saved_image(ctx, asset_id)
                if not info:
                    raise RuntimeError("镜头 %s 缺少参考图 %s" % (current["id"], asset_id))
                images[asset_id] = info
                url = _reference_url(info, project_id=ctx.project_id)
                if url and url != (info.get("url") or ""):
                    info["url"] = url
                    _remember_public_url(ctx.work_dir, ctx.project_id, asset_id, url)
                return url, info.get("path")

            _aspect, media = assemble_shot_media(current, mode, url_for, tail_url)
            text = delivery_prompt(
                current,
                spoken,
                prompt_index,
                bool(tail_url),
                mode,
                soundtrack=ctx.soundtrack,
                template=ctx.template,
            )
            # #region agent log
            _agent_log(
                "A",
                "render_loop.py:video_submit",
                "video submit media",
                {
                    "shot_id": current.get("id"),
                    "mode": mode,
                    "duration": current.get("duration"),
                    "images": asset_ids,
                    "user_ids": user_ids,
                    "media": [
                        {"role": item.get("role"), "url": item.get("url"), "probe": _probe_media_url(item.get("url"))}
                        for item in (media or [])
                    ],
                    "prompt_len": len(text or ""),
                },
            )
            # #endregion
            # Only text-to-video accepts aspect_ratio. Image and reference follow the input image.
            ratio = "9:16" if mode == "text" else None
            return cr.submit_video(
                text,
                model=VIDEO_MODE_MODELS[mode],
                duration=current["duration"],
                aspect_ratio=ratio,
                images=media or None,
                idempotency_key=key,
                timeout=180,
            )

        def poll(task_id, shot_id=shot_id):
            step = ctx.reload_step(shot_id, "video")
            return cr.poll_video(_video_handle(task_id, step.get("poll_url") or ""))

        def download(task, path):
            temporary = Path(str(path) + ".part")
            cr.download_video(task, temporary)
            if temporary.is_file():
                temporary.replace(path)
            return getattr(task, "url", "") or ""

        ctx.run_remote(
            shot_id,
            "video",
            label,
            "videos",
            submit,
            poll,
            download,
            VIDEO_POLL_SEC,
            index,
            len(shots),
        )
        clip_path, _relative = output_paths(ctx.work_dir, shot_id, "video")
        clips.append(clip_path)

    joined = ctx.run_join(clips)
    if soundtrack_enabled(ctx.soundtrack):
        ctx.run_audio(joined, clips, shots, spec)
    joined = ctx.run_subtitles(joined, shots, spec)
    return {
        "prompt_id": ctx.last_remote_id,
        "video_file": joined.name,
        "image_files": {key: Path(value["path"]).name for key, value in images.items()},
        "clip_files": [path.name for path in clips],
    }
