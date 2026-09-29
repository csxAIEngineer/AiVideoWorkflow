#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_API_BASE = "https://www.runninghub.cn/openapi/v2"
DEFAULT_VIDEO_ENDPOINT = "rhart-video/sparkvideo-2.0/multimodal-video"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_config(env_path: Path) -> dict[str, str]:
    env_values = load_env(env_path)
    api_key = env_values.get("RUNNINGHUB_API_KEY") or os.environ.get("RUNNINGHUB_API_KEY")
    if not api_key:
        raise SystemExit("没有配置 RunningHub API，无法生成视频。")
    return {
        "api_key": api_key,
        "api_base": env_values.get("RUNNINGHUB_API_BASE")
        or os.environ.get("RUNNINGHUB_API_BASE")
        or DEFAULT_API_BASE,
        "video_endpoint": env_values.get("RUNNINGHUB_VIDEO_ENDPOINT")
        or os.environ.get("RUNNINGHUB_VIDEO_ENDPOINT")
        or DEFAULT_VIDEO_ENDPOINT,
    }


def request_json(url: str, api_key: str, payload: dict, timeout: int = 60) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_file(api_base: str, api_key: str, path: Path) -> str:
    boundary = f"----CodexRunningHub{int(time.time() * 1000)}"
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    file_bytes = path.read_bytes()
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + file_bytes + tail
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/media/upload/binary",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        result = json.loads(response.read().decode("utf-8"))
    data = result.get("data") or {}
    url = data.get("download_url")
    if not url:
        raise RuntimeError(f"Upload failed for {path}: {result}")
    return url


def download_file(url: str, out_path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Codex-RunningHub-Video-Downloader/1.0"})
    with urllib.request.urlopen(req, timeout=300) as response:
        out_path.write_bytes(response.read())


def parse_code_block_after(lines: list[str], start_index: int) -> tuple[list[str], int]:
    index = start_index
    while index < len(lines) and not lines[index].strip().startswith("```"):
        index += 1
    if index >= len(lines):
        return [], start_index
    index += 1
    values = []
    while index < len(lines) and not lines[index].strip().startswith("```"):
        line = lines[index].strip()
        if line:
            values.append(line.strip("`").strip())
        index += 1
    return values, index


def derive_duration(text: str, default_duration: str) -> str:
    match = re.search(r"(\d+)\s*-\s*(\d+)\s*秒", text)
    if not match:
        match = re.search(r"(\d+)\s*-\s*(\d+)s", text, re.IGNORECASE)
    if match:
        duration = max(4, min(15, int(match.group(2)) - int(match.group(1))))
        return str(duration)
    return str(default_duration)


def parse_video_prompts(path: Path, default_duration: str) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    segments = []
    current: dict | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("## ") and "段" in line:
            if current:
                segments.append(current)
            current = {
                "title": line.lstrip("#").strip(),
                "refs": [],
                "prompt": "",
                "duration": derive_duration(line, default_duration),
            }
        elif current and "参考图上传顺序" in line:
            refs, index = parse_code_block_after(lines, index + 1)
            current["refs"] = refs
        elif current and "Seedance 提示词" in line:
            prompt_lines, index = parse_code_block_after(lines, index + 1)
            current["prompt"] = "\n".join(prompt_lines).strip()
        index += 1
    if current:
        segments.append(current)
    return [segment for segment in segments if segment.get("prompt")]


def resolve_ref_paths(refs: list[str], asset_dir: Path) -> list[Path]:
    paths = []
    for ref in refs:
        cleaned = ref.strip().strip("`")
        if not cleaned:
            continue
        path = Path(cleaned)
        if not path.is_absolute():
            path = asset_dir / cleaned
        paths.append(path)
    if len(paths) > 9:
        paths = paths[:8] + [paths[-1]]
    return paths


def submit_video(
    segment: dict,
    *,
    config: dict[str, str],
    image_urls: list[str],
    resolution: str,
    ratio: str,
    generate_audio: bool,
    seed: int,
) -> dict:
    payload = {
        "prompt": segment["prompt"],
        "resolution": resolution,
        "duration": str(segment["duration"]),
        "imageUrls": image_urls,
        "videoUrls": [],
        "audioUrls": [],
        "generateAudio": generate_audio,
        "ratio": ratio,
        "realPersonMode": True,
        "conversionSlots": ["all"],
        "returnLastFrame": False,
        "seed": seed,
    }
    response = request_json(
        f"{config['api_base'].rstrip('/')}/{config['video_endpoint'].lstrip('/')}",
        config["api_key"],
        payload,
    )
    task_id = response.get("taskId")
    if not task_id:
        return {"title": segment["title"], "status": "SUBMIT_FAILED", "payload": payload, "response": response}
    return {"title": segment["title"], "taskId": task_id, "status": response.get("status", "SUBMITTED"), "payload": payload}


def poll_and_download(submitted: dict, *, config: dict[str, str], out_path: Path, poll_interval: int, timeout_seconds: int) -> dict:
    if "taskId" not in submitted:
        return submitted
    start = time.time()
    query_url = f"{config['api_base'].rstrip('/')}/query"
    last_response = submitted
    while time.time() - start < timeout_seconds:
        time.sleep(poll_interval)
        last_response = request_json(query_url, config["api_key"], {"taskId": submitted["taskId"]})
        status = last_response.get("status")
        if status == "SUCCESS":
            results = last_response.get("results") or []
            video_result = next(
                (
                    result
                    for result in results
                    if str(result.get("outputType", "")).lower() in {"mp4", "mov", "webm"}
                    and result.get("url")
                ),
                None,
            )
            if not video_result:
                return {**submitted, "status": "SUCCESS_NO_VIDEO", "response": last_response}
            download_file(video_result["url"], out_path)
            return {**submitted, "status": "SUCCESS", "path": str(out_path), "url": video_result["url"]}
        if status == "FAILED":
            return {**submitted, "status": "FAILED", "response": last_response}
    return {**submitted, "status": "TIMEOUT", "response": last_response}


def safe_filename(index: int, title: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title).strip("_")
    return f"{index:02d}_{cleaned or 'segment'}.mp4"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Seedance/SparkVideo 2.0 clips via RunningHub multimodal-video API.")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--video-prompts", default="视频提示词.md")
    parser.add_argument("--asset-dir", default="output/imagegen/assets")
    parser.add_argument("--out-dir", default="output/video/assets")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--duration", default="10")
    parser.add_argument("--poll-interval", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--generate-audio", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--seed", type=int, default=-1)
    args = parser.parse_args()

    config = resolve_config(Path(args.env))
    asset_dir = Path(args.asset_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    segments = parse_video_prompts(Path(args.video_prompts), args.duration)
    if not segments:
        raise SystemExit("No video segments found in 视频提示词.md.")

    manifest = []
    manifest_path = out_dir / f"video_manifest_{time.strftime('%Y%m%d_%H%M%S')}.json"
    upload_cache: dict[Path, str] = {}
    for index, segment in enumerate(segments, start=1):
        ref_paths = resolve_ref_paths(segment.get("refs", []), asset_dir)
        missing = [str(path) for path in ref_paths if not path.exists()]
        if missing:
            record = {"title": segment["title"], "status": "MISSING_REFERENCES", "missing": missing}
            manifest.append(record)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        image_urls = []
        for path in ref_paths:
            if path not in upload_cache:
                upload_cache[path] = upload_file(config["api_base"], config["api_key"], path)
            image_urls.append(upload_cache[path])
        submitted = submit_video(
            segment,
            config=config,
            image_urls=image_urls,
            resolution=args.resolution,
            ratio=args.ratio,
            generate_audio=args.generate_audio,
            seed=args.seed,
        )
        out_path = out_dir / safe_filename(index, segment["title"])
        final = poll_and_download(
            submitted,
            config=config,
            out_path=out_path,
            poll_interval=args.poll_interval,
            timeout_seconds=args.timeout_seconds,
        )
        final["references"] = [str(path) for path in ref_paths]
        manifest.append(final)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [item for item in manifest if item.get("status") != "SUCCESS"]
    print(json.dumps({"total": len(manifest), "failed": len(failed), "manifest": str(manifest_path)}, ensure_ascii=False, indent=2), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
