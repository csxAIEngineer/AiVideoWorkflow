"""Local render: iCreat stills, CR MiniMax H3 clips, ffmpeg join, with checkpoints."""

import base64
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from cr_api.client import CRError
from icreat_api.client import ICreatError
from web.workflow_from_spec import collect_steps, ordered_assets

MAX_ATTEMPTS = 5
IMAGE_POLL_SEC = 4
VIDEO_POLL_SEC = 10
FACE_LOCK = "参考图是同一身份的定妆，必须锁定同一张脸和外形，不要换脸。\n"
TRANSIENT_HTTP = {429, 502, 503, 504}
IMAGE_SUCCESS = {"SUCCEEDED"}
IMAGE_FAILURE = {"FAILED", "NOT_FOUND"}
VIDEO_SUCCESS = {"completed"}
VIDEO_FAILURE = {"failed"}

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


def ffmpeg_join(clips, target):
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("本机没有 ffmpeg")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    list_path = target.parent / "concat_list.txt"
    lines = []
    for clip in clips:
        path = Path(clip).resolve().as_posix().replace("'", "'\\''")
        lines.append("file '%s'" % path)
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                str(target),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg 拼接失败：%s" % ((proc.stdout or "")[-400:]))
    return target


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


LIVE_ACTION = (
    "实拍照片质感，可用光，材质有磨损和褶皱，自然皮肤纹理，不要磨皮，不要塑料皮肤，不要CG渲染。"
)


def image_prompt(asset):
    prompt = (asset.get("prompt") or "").strip()
    if "自然皮肤纹理" not in prompt:
        prompt = prompt + "\n" + LIVE_ACTION
    if asset.get("depends_on"):
        return FACE_LOCK + prompt
    return prompt


_DIALOGUE_MARK = re.compile(
    r"==\s*([^：:\n＝]{1,16}?)(?:（[^）]{0,24}）)?[：:]\s*([^＝\n]+?)\s*=="
)


def spoken_lines(script):
    lines = []
    for match in _DIALOGUE_MARK.finditer(script or ""):
        speaker = match.group(1).strip()
        line = match.group(2).strip()
        if speaker in ("场景", "人物外形锁定"):
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


def delivery_prompt(shot, spoken, index, has_tail):
    """Prompt actually sent to MiniMax: performance, dialogue tags, and the cut."""
    base = (shot.get("prompt") or "").strip()
    parts = [
        base,
        "实拍短剧：自然皮肤纹理和毛孔，可用光，衣料有皱，轻微手持，把这一个主动作演完再停住，"
        "不要定妆摆拍，不要磨皮，不要塑料皮肤，不要CG，不要慢动作漂浮。全程无字幕、无对白文字叠加。",
    ]
    if index > 0:
        parts.append(
            "这一镜是上一镜的下一刀。同一场就接上服装、光线和空间，用视线或动作交代切点；"
            "换场也要用动作或声音动机，不要突然换脸、换光或换房间。"
        )
    if has_tail:
        parts.append("最后一张参考图是上一镜的实际尾帧，只承接光线、服装和空间，不要照抄它的景别和构图。")
    if "<d>" not in base:
        if spoken:
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
    def __init__(self, work_dir, on_progress=None, project_id="", icreat=None, cr=None, sleeper=None, joiner=None):
        self.work_dir = Path(work_dir)
        self.on_progress = on_progress
        self.project_id = project_id or ""
        self.icreat = icreat
        self.cr = cr
        self.sleeper = sleeper
        self.joiner = joiner
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

    def mark_done(self, step_id, kind, relative, public_url):
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
                self.mark_done(step_id, kind, relative, public_url)
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
                self.emit("成片已生成，可以下载", "done", step_id, "done", percent=100)
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
                state["phase"] = "done"
                state["paused"] = False
                state["pause_reason"] = ""
                state["paused_by"] = ""
                self.write_locked()
            self.emit("成片已生成，可以下载", "done", step_id, "done", percent=100)
            return path


def _reference_url(info):
    """Send the local still. iCreat's signed URL is often unreachable from CR."""
    path = (info or {}).get("path")
    if path and Path(path).is_file() and Path(path).stat().st_size > 0:
        return compact_data_url(path)
    url = str((info or {}).get("url") or "")
    if url:
        return url
    raise RuntimeError("缺少参考图")


def _video_handle(task_id, poll_url):
    from cr_api.client import VideoTask

    return VideoTask({"id": task_id, "poll_url": poll_url or "", "status": "submitted"})


def run_local_pipeline(spec, work_dir, on_progress=None, project_id=None, icreat=None, cr=None, sleeper=None, joiner=None, images_only=False):
    """Generate stills, then clips, then a local concat. Completed files are skipped.

    images_only stops after the stills. Video and join are left for a later run.
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
    ctx = RenderContext(
        work_dir,
        on_progress=on_progress,
        project_id=project_id or "",
        icreat=icreat,
        cr=cr,
        sleeper=sleeper,
        joiner=joiner,
    )
    if project_id:
        register_thread(project_id)
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    (ctx.work_dir / "outputs" / "images").mkdir(parents=True, exist_ok=True)
    (ctx.work_dir / "outputs" / "clips").mkdir(parents=True, exist_ok=True)
    ctx.total_steps = len(assets) if images_only else len(assets) + len(shots) + 1
    if images_only:
        ctx.emit("Debug：开始生图", "images", assets[0]["id"], "running")
    else:
        ctx.emit(
            "开始出片：iCreat 生图 / CR MiniMax H3 / 本机拼接",
            "images",
            assets[0]["id"],
            "running",
        )

    images = {}
    for index, asset in enumerate(assets, start=1):
        label = "生图 %s/%s：%s %s" % (index, len(assets), asset["id"], asset["title"])

        def submit(key, asset=asset):
            del key
            image = None
            if asset.get("depends_on"):
                parent = images.get(asset["depends_on"]) or {}
                url = str(parent.get("url") or "")
                if not url:
                    raise RuntimeError("资产 %s 依赖的 %s 没有公网地址" % (asset["id"], asset["depends_on"]))
                image = [url]
            return icreat.submit_image(image_prompt(asset), image=image)

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
        images[asset["id"]] = {"path": saved_path, "url": fresh.get("public_url") or ""}

    if images_only:
        with ctx.lock:
            state = ctx.load_locked()
            state["phase"] = "images"
            state["paused"] = False
            state["pause_reason"] = ""
            state["paused_by"] = ""
            ctx.write_locked()
        ctx.emit(
            "Debug：图片已生成，视频和拼接暂不执行",
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

        def submit(key, shot=shot, index=index):
            refs = []
            for asset_id in shot["images"]:
                info = images.get(asset_id)
                if not info:
                    raise RuntimeError("镜头 %s 缺少参考图 %s" % (shot["id"], asset_id))
                refs.append({"role": "reference_image", "url": _reference_url(info)})
            frame = None
            if clips:
                frame = _tail_frame(
                    clips[-1],
                    ctx.work_dir / "outputs" / "frames" / ("%s-tail.jpg" % clips[-1].stem),
                )
            if frame:
                refs.append({"role": "reference_image", "url": data_url(frame)})
            spoken = shot.get("spoken") or []
            text = delivery_prompt(shot, spoken, index - 1, bool(frame))
            return cr.submit_video(
                text,
                model="minimax-h3-reference-to-video",
                duration=shot["duration"],
                aspect_ratio="9:16",
                images=refs,
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
    return {
        "prompt_id": ctx.last_remote_id,
        "video_file": joined.name,
        "image_files": {key: Path(value["path"]).name for key, value in images.items()},
        "clip_files": [path.name for path in clips],
    }
