"""Robust Comfy Cloud upload loop: one still, wait, then one clip, with retries."""

import json
import shutil
import subprocess
import time
from pathlib import Path

from end.pipeline import (
    CLOUD_LOGIN_HINT,
    CLOUD_STATUS,
    _cloud_message,
    _comfy_bin,
    _comfy_error,
    _json_events,
    _prompt_id,
    _require_cloud_login,
    _run_comfy,
    _status_payload,
)
from end.workflow_from_spec import (
    build_image_workflow,
    build_join_workflow,
    build_shot_workflow,
    collect_steps,
    ordered_assets,
)

IMAGE_GAP_SEC = 25
VIDEO_GAP_SEC = 20
MAX_ATTEMPTS = 5
POLL_SEC = 8
JOB_TIMEOUT_SEC = 3600
RETRYABLE = (
    "429",
    "rate limit",
    "rate_limit",
    "timeout",
    "timed out",
    "temporarily",
    "connection",
    "reset by peer",
    "unavailable",
    "503",
    "502",
    "504",
    "not signed in",
)


def _emit(on_progress, message, percent=None, phase=None, current=None, total=None, task_id=None, task_state=None):
    if not on_progress:
        return
    payload = {"message": message}
    if percent is not None:
        payload["percent"] = max(0, min(100, int(percent)))
    if phase is not None:
        payload["phase"] = phase
    if current is not None:
        payload["current"] = current
    if total is not None:
        payload["total"] = total
    if task_id is not None:
        payload["task_id"] = task_id
    if task_state is not None:
        payload["task_state"] = task_state
    on_progress(payload)


def _retryable(message):
    text = str(message or "").lower()
    return any(token in text for token in RETRYABLE)


def _pick_file(files, suffixes):
    paths = []
    for item in files or []:
        if isinstance(item, dict) and item.get("path"):
            paths.append(Path(item["path"]))
        elif isinstance(item, str):
            paths.append(Path(item))
    matched = [path for path in paths if path.suffix.lower() in suffixes and path.is_file()]
    if not matched:
        return None
    matched.sort(key=lambda path: path.stat().st_size)
    return matched[-1]


def _download_files(events):
    files = []
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        if isinstance(data, dict) and data.get("files"):
            files = data["files"]
    return files


def _stable_copy(src, dest_dir, preferred_name):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / preferred_name
    if src.resolve() != target.resolve():
        target.write_bytes(Path(src).read_bytes())
    return target


def _upload_name_map(events, local_paths):
    """Map local file paths to the cloud input names returned by `comfy upload`."""
    mapping = {}
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        if not isinstance(data, dict):
            continue
        for item in data.get("uploads") or []:
            if not isinstance(item, dict):
                continue
            local = item.get("local_path") or ""
            cloud = item.get("cloud_name") or ""
            if local and cloud:
                mapping[str(Path(local).resolve())] = cloud
                mapping[Path(local).name] = cloud
    # Fall back to basename only when the CLI did not report cloud_name (local runs).
    for path in local_paths:
        key = str(Path(path).resolve())
        if key not in mapping:
            mapping[key] = Path(path).name
            mapping[Path(path).name] = Path(path).name
    return mapping


def _cloud_name_for(mapping, path):
    path = Path(path)
    return mapping.get(str(path.resolve())) or mapping.get(path.name) or path.name


class CloudLoop(object):
    def __init__(self, work_dir, on_progress=None):
        self.work_dir = Path(work_dir)
        self.output_dir = self.work_dir / "outputs"
        self.workflow_dir = self.work_dir / "cloud_jobs"
        self.image_dir = self.output_dir / "images"
        self.clip_dir = self.output_dir / "clips"
        self.log_path = self.output_dir / "comfy.log"
        self.on_progress = on_progress
        self.comfy = _comfy_bin()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        self.last_prompt_id = ""

    def note(self, text):
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")

    def progress(self, message, **kwargs):
        self.note("# " + message)
        _emit(self.on_progress, message, **kwargs)

    def ensure_login(self):
        try:
            _require_cloud_login(self.comfy)
        except RuntimeError as error:
            self.progress(str(error) or CLOUD_LOGIN_HINT, percent=0, phase="login")
            raise

    def reconnect(self):
        self.progress("连接异常，正在重新检查 Comfy Cloud 登录", phase="reconnect")
        time.sleep(3)
        self.ensure_login()
        self.progress("已重新连上 Comfy Cloud", phase="reconnect")

    def run_job(self, workflow_path, label, timeout=JOB_TIMEOUT_SEC):
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                if attempt > 1:
                    self.progress(
                        "%s失败，第 %s/%s 次重试：%s"
                        % (label, attempt, MAX_ATTEMPTS, last_error[:160]),
                        phase="retry",
                    )
                    if _retryable(last_error):
                        try:
                            self.reconnect()
                        except RuntimeError as error:
                            last_error = str(error)
                            time.sleep(min(60, 8 * attempt))
                            continue
                    time.sleep(min(90, 12 * attempt))
                return self._submit_wait_download(workflow_path, label, timeout)
            except Exception as error:
                last_error = str(error)
                self.note("ERROR %s attempt %s: %s\n" % (label, attempt, last_error))
                if attempt >= MAX_ATTEMPTS or not _retryable(last_error):
                    raise RuntimeError("%s失败：%s" % (label, last_error))
        raise RuntimeError("%s失败：%s" % (label, last_error or "未知错误"))

    def _submit_wait_download(self, workflow_path, label, timeout):
        self.progress("正在上传 %s" % label)
        code, output = _run_comfy(
            [
                self.comfy,
                "run",
                "--json",
                "--workflow",
                str(workflow_path),
                "--where",
                "cloud",
                "--allow-spend",
                "--no-watch",
                "--no-notify",
            ],
            timeout=180,
        )
        self.note(output)
        events = _json_events(output)
        prompt_id = _prompt_id(events)
        if code != 0 or not prompt_id:
            raise RuntimeError(_comfy_error(events, output))
        self.last_prompt_id = prompt_id
        self.progress("已提交 %s，任务 %s" % (label, prompt_id[:8]))

        deadline = time.time() + timeout
        last_status = ""
        while time.time() < deadline:
            code, output = _run_comfy(
                [self.comfy, "--json", "jobs", "status", prompt_id, "--where", "cloud"],
                timeout=60,
            )
            self.note(output)
            events = _json_events(output)
            payload = _status_payload(events)
            status = str(payload.get("status") or "")
            if code != 0 and not status:
                message = _comfy_error(events, output)
                if _retryable(message):
                    self.reconnect()
                    time.sleep(POLL_SEC)
                    continue
                raise RuntimeError(message)
            if status and status != last_status:
                last_status = status
                self.progress("%s：%s" % (label, CLOUD_STATUS.get(status, status)))
            if status == "completed":
                break
            if status in ("error", "cancelled", "canceled", "failed"):
                message = payload.get("error_message") or status
                raise RuntimeError(_cloud_message(message))
            time.sleep(POLL_SEC)
        else:
            raise RuntimeError("%s 超过 %s 分钟还没有完成" % (label, timeout // 60))

        self.progress("正在下载 %s 的产出" % label)
        code, output = _run_comfy(
            [
                self.comfy,
                "--json",
                "download",
                prompt_id,
                "--where",
                "cloud",
                "-o",
                str(self.output_dir),
            ],
            timeout=600,
        )
        self.note(output)
        events = _json_events(output)
        if code != 0:
            raise RuntimeError(_comfy_error(events, output))
        return _download_files(events)

    def upload_files(self, paths):
        """Upload files and return {local_resolved_path|basename: cloud_name}."""
        file_paths = [Path(path) for path in paths if path and Path(path).is_file()]
        if not file_paths:
            return {}
        files = [str(path) for path in file_paths]
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                code, output = _run_comfy(
                    [self.comfy, "--json", "upload", "--where", "cloud", "--overwrite"] + files,
                    timeout=180,
                )
                self.note(output)
                events = _json_events(output)
                if code != 0:
                    raise RuntimeError(_comfy_error(events, output))
                mapping = _upload_name_map(events, file_paths)
                for path in file_paths:
                    cloud = _cloud_name_for(mapping, path)
                    self.progress("已上传参考文件 %s → %s" % (path.name, cloud), phase="upload")
                return mapping
            except Exception as error:
                last_error = str(error)
                if attempt >= MAX_ATTEMPTS or not _retryable(last_error):
                    raise RuntimeError("上传参考文件失败：%s" % last_error)
                self.progress("上传失败，正在重试：%s" % last_error[:160], phase="retry")
                self.reconnect()
                time.sleep(min(60, 8 * attempt))
        raise RuntimeError("上传参考文件失败：%s" % last_error)

    def wait_gap(self, seconds, reason):
        if seconds <= 0:
            return
        self.progress("冷却 %s 秒，避免限流（%s）" % (seconds, reason), phase="cooldown")
        time.sleep(seconds)


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


def _ffmpeg_join(clips, target):
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("本机没有 ffmpeg")
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


def _join_clips(loop, clip_files):
    joined = loop.output_dir / "joined.mp4"
    if len(clip_files) == 1:
        return _stable_copy(clip_files[0], loop.output_dir, "joined.mp4")
    try:
        return _ffmpeg_join(clip_files, joined)
    except Exception as local_error:
        loop.progress("本机拼接不可用（%s），改用 Cloud 拼接" % str(local_error)[:120], phase="join")
        mapping = loop.upload_files(clip_files)
        names = [_cloud_name_for(mapping, path) for path in clip_files]
        workflow = build_join_workflow(names)
        path = loop.workflow_dir / "workflow_join.json"
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
        files = loop.run_job(path, "拼接成片")
        video = _pick_file(files, (".mp4", ".webm", ".mov", ".mkv"))
        if video is None:
            raise RuntimeError("Cloud 拼接完成了，但没有下载到成片")
        return _stable_copy(video, loop.output_dir, "joined.mp4")


def run_cloud_pipeline(spec, work_dir, on_progress=None):
    """workflow0 stills one-by-one, then workflow1 clips one-by-one, then local join."""
    assets, shots = collect_steps(spec)
    if not assets or not shots:
        raise ValueError("没有可用的资产图或镜头")
    assets = ordered_assets(assets)
    loop = CloudLoop(work_dir, on_progress=on_progress)
    loop.ensure_login()

    image_steps = len(assets)
    video_steps = len(shots)
    total_steps = image_steps + video_steps + 1
    done_steps = 0
    image_files = {}
    cloud_names = {}

    for index, asset in enumerate(assets, start=1):
        percent = int(100 * done_steps / total_steps)
        loop.progress(
            "生图 %s/%s：%s %s" % (index, image_steps, asset["id"], asset["title"]),
            percent=percent,
            phase="images",
            current=index,
            total=image_steps,
            task_id=asset["id"],
            task_state="running",
        )
        ref_name = None
        if asset["depends_on"]:
            parent_path = image_files.get(asset["depends_on"])
            if not parent_path:
                raise RuntimeError("资产 %s 依赖的 %s 还没有生成" % (asset["id"], asset["depends_on"]))
            mapping = loop.upload_files([parent_path])
            ref_name = _cloud_name_for(mapping, parent_path)
            loop.progress(
                "变体 %s 使用云端参考图 %s" % (asset["id"], ref_name),
                phase="images",
                task_id=asset["id"],
                task_state="running",
            )
        workflow = build_image_workflow(asset, reference_filename=ref_name)
        path = loop.workflow_dir / ("workflow0_%s.json" % asset["id"])
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
        files = loop.run_job(path, "生图 " + asset["id"])
        image = _pick_file(files, (".png", ".jpg", ".jpeg", ".webp"))
        if image is None:
            raise RuntimeError("生图 %s 完成了，但没有下载到图片" % asset["id"])
        saved = _stable_copy(image, loop.image_dir, "%s%s" % (asset["id"], image.suffix.lower() or ".png"))
        image_files[asset["id"]] = saved
        cloud_names[asset["id"]] = saved.name
        done_steps += 1
        loop.progress(
            "生图完成：%s" % asset["id"],
            percent=int(100 * done_steps / total_steps),
            phase="images",
            current=index,
            total=image_steps,
            task_id=asset["id"],
            task_state="done",
        )
        if index < image_steps:
            loop.wait_gap(IMAGE_GAP_SEC, "下一张图")

    clip_files = []
    for index, shot in enumerate(shots, start=1):
        percent = int(100 * done_steps / total_steps)
        loop.progress(
            "视频 %s/%s：%s" % (index, video_steps, shot["id"]),
            percent=percent,
            phase="videos",
            current=index,
            total=video_steps,
            task_id=shot["id"],
            task_state="running",
        )
        needed = []
        for asset_id in shot["images"]:
            path = image_files.get(asset_id)
            if not path:
                raise RuntimeError("镜头 %s 缺少参考图 %s" % (shot["id"], asset_id))
            needed.append(path)
        mapping = loop.upload_files(needed)
        names = {}
        for asset_id in shot["images"]:
            names[asset_id] = _cloud_name_for(mapping, image_files[asset_id])
        workflow = build_shot_workflow(shot, names)
        path = loop.workflow_dir / ("workflow1_%s.json" % shot["id"])
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
        files = loop.run_job(path, "视频 " + shot["id"])
        video = _pick_file(files, (".mp4", ".webm", ".mov", ".mkv"))
        if video is None:
            raise RuntimeError("视频 %s 完成了，但没有下载到成片" % shot["id"])
        saved = _stable_copy(video, loop.clip_dir, "%s%s" % (shot["id"], video.suffix.lower() or ".mp4"))
        clip_files.append(saved)
        done_steps += 1
        loop.progress(
            "视频完成：%s" % shot["id"],
            percent=int(100 * done_steps / total_steps),
            phase="videos",
            current=index,
            total=video_steps,
            task_id=shot["id"],
            task_state="done",
        )
        if index < video_steps:
            loop.wait_gap(VIDEO_GAP_SEC, "下一镜")

    loop.progress(
        "正在拼接成片",
        percent=int(100 * done_steps / total_steps),
        phase="join",
        task_id="join",
        task_state="running",
    )
    joined = _join_clips(loop, clip_files)
    done_steps += 1
    loop.progress(
        "成片已生成，可以下载",
        percent=100,
        phase="done",
        task_id="join",
        task_state="done",
    )
    return {
        "prompt_id": loop.last_prompt_id,
        "video_file": joined.name,
        "image_files": {key: Path(value).name for key, value in image_files.items()},
        "clip_files": [path.name for path in clip_files],
    }
