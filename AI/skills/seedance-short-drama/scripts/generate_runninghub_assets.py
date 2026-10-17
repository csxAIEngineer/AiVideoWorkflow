#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


PROMPT_TITLE_RE = re.compile(r"^###\s+([0-9A-Za-z_\-]+\.png)\s*$")
DEFAULT_API_BASE = "https://www.runninghub.cn/openapi/v2"
DEFAULT_TEXT_TO_IMAGE_ENDPOINT = "rhart-image-g-2/text-to-image"
DEFAULT_IMAGE_TO_IMAGE_ENDPOINT = "rhart-image-n-g31-flash/image-to-image"


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
        raise SystemExit("没有配置 RunningHub API，无法生成资产。")
    return {
        "api_key": api_key,
        "api_base": env_values.get("RUNNINGHUB_API_BASE")
        or os.environ.get("RUNNINGHUB_API_BASE")
        or DEFAULT_API_BASE,
        "text_to_image_endpoint": env_values.get("RUNNINGHUB_TEXT_TO_IMAGE_ENDPOINT")
        or env_values.get("RUNNINGHUB_IMAGE_ENDPOINT")
        or os.environ.get("RUNNINGHUB_TEXT_TO_IMAGE_ENDPOINT")
        or os.environ.get("RUNNINGHUB_IMAGE_ENDPOINT")
        or DEFAULT_TEXT_TO_IMAGE_ENDPOINT,
        "image_to_image_endpoint": env_values.get("RUNNINGHUB_IMAGE_TO_IMAGE_ENDPOINT")
        or os.environ.get("RUNNINGHUB_IMAGE_TO_IMAGE_ENDPOINT")
        or DEFAULT_IMAGE_TO_IMAGE_ENDPOINT,
    }


def extract_prompts(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    items = []
    index = 0
    while index < len(lines):
        match = PROMPT_TITLE_RE.match(lines[index])
        if not match:
            index += 1
            continue
        filename = match.group(1)
        index += 1
        while index < len(lines) and lines[index].strip() != "```text":
            if lines[index].startswith("### "):
                break
            index += 1
        if index >= len(lines) or lines[index].strip() != "```text":
            continue
        index += 1
        prompt_lines = []
        while index < len(lines) and lines[index].strip() != "```":
            prompt_lines.append(lines[index])
            index += 1
        items.append({"filename": filename, "prompt": "\n".join(prompt_lines).strip()})
        index += 1
    return items


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


def download_file(url: str, out_path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Codex-RunningHub-Downloader/1.0"})
    with urllib.request.urlopen(req, timeout=180) as response:
        out_path.write_bytes(response.read())


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


def submit_task(
    item: dict[str, str],
    *,
    api_base: str,
    endpoint: str,
    api_key: str,
    aspect_ratio: str,
    resolution: str,
    image_urls: list[str] | None = None,
) -> dict:
    payload = {
        "prompt": item["prompt"],
        "aspectRatio": aspect_ratio,
        "resolution": resolution,
    }
    if image_urls:
        payload["imageUrls"] = image_urls
    response = request_json(f"{api_base.rstrip('/')}/{endpoint.lstrip('/')}", api_key, payload)
    task_id = response.get("taskId")
    if not task_id:
        return {"filename": item["filename"], "status": "SUBMIT_FAILED", "response": response}
    return {"filename": item["filename"], "taskId": task_id, "status": response.get("status", "SUBMITTED")}


def resolution_for_item(filename: str, default_resolution: str) -> str:
    if "_storyboard_segment_" in filename:
        return "4k"
    return default_resolution


def poll_and_download(
    submitted: dict,
    *,
    api_base: str,
    api_key: str,
    out_dir: Path,
    poll_interval: int,
    timeout_seconds: int,
) -> dict:
    if submitted.get("status") == "SKIPPED_EXISTS":
        return submitted
    if "taskId" not in submitted:
        return submitted
    start = time.time()
    query_url = f"{api_base.rstrip('/')}/query"
    last_response = submitted
    while time.time() - start < timeout_seconds:
        time.sleep(poll_interval)
        last_response = request_json(query_url, api_key, {"taskId": submitted["taskId"]})
        status = last_response.get("status")
        if status == "SUCCESS":
            results = last_response.get("results") or []
            image_result = next(
                (
                    result
                    for result in results
                    if str(result.get("outputType", "")).lower() in {"png", "jpg", "jpeg", "webp"}
                    and result.get("url")
                ),
                None,
            )
            if not image_result:
                return {**submitted, "status": "SUCCESS_NO_IMAGE", "response": last_response}
            out_path = out_dir / submitted["filename"]
            download_file(image_result["url"], out_path)
            return {**submitted, "status": "SUCCESS", "path": str(out_path), "url": image_result["url"]}
        if status == "FAILED":
            return {**submitted, "status": "FAILED", "response": last_response}
    return {**submitted, "status": "TIMEOUT", "response": last_response}


def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_manifest(path: Path, records: list[dict]) -> None:
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def default_i2i_references() -> dict[str, list[str]]:
    storyboard_references = {
        "11_storyboard_segment_01_0-10s.png": [
            "_templates/storyboard_3x3_template.png",
            "01_character_erxi_turnaround.png",
            "02_character_popo_turnaround_before.png",
            "04_character_dianyuan_turnaround.png",
            "05_scene_store_entrance.png",
            "06_scene_boutique_interior.png",
            "08_product_dress_reference_sheet.png",
            "09_prop_old_coat_reference.png",
        ],
        "12_storyboard_segment_02_10-20s.png": [
            "_templates/storyboard_3x3_template.png",
            "01_character_erxi_turnaround.png",
            "02_character_popo_turnaround_before.png",
            "04_character_dianyuan_turnaround.png",
            "06_scene_boutique_interior.png",
            "07_scene_fitting_room_mirror_area.png",
            "08_product_dress_reference_sheet.png",
            "09_prop_old_coat_reference.png",
        ],
        "13_storyboard_segment_03_20-30s.png": [
            "_templates/storyboard_3x3_template.png",
            "01_character_erxi_turnaround.png",
            "03_character_popo_turnaround_after_dress.png",
            "04_character_dianyuan_turnaround.png",
            "07_scene_fitting_room_mirror_area.png",
            "08_product_dress_reference_sheet.png",
            "10_prop_clothing_rack_and_hanger.png",
        ],
    }
    return {
        **storyboard_references,
        "03_character_popo_turnaround_after_dress.png": [
            "02_character_popo_turnaround_before.png",
            "08_product_dress_reference_sheet.png",
        ],
    }


def reference_lock_prompt(filename: str) -> str:
    if "_storyboard_segment_" in filename:
        return (
            "\n\n严格使用上传参考图：参考图用于保持角色、服装、空间和产品一致性。"
            "故事板主体必须是黑白粗铅笔PREVIS分镜，不要写实照片，不要彩色成片截图。"
            "彩色只能用于红色/蓝色/绿色/橙色/紫色箭头和标记。"
            "不要把三视图直接拼进故事板格子里，而是把同一人物应用到剧情分镜中。"
        )
    if filename.startswith("03_character_") or "_after_" in filename:
        return (
            "\n\n严格使用上传参考图：第1张是人物身份锁定图，必须保持同一人、同样脸型、年龄感、发型、体态和气质；"
            "第2张是产品锁定图，只替换服装或产品状态。禁止年轻化，禁止换脸，禁止变成新人物。"
        )
    return ""


def split_items(items: list[dict[str, str]], i2i_refs: dict[str, list[str]]) -> tuple[list[dict], list[dict]]:
    image_to_image = [item for item in items if item["filename"] in i2i_refs]
    text_to_image = [item for item in items if item["filename"] not in i2i_refs]
    return text_to_image, image_to_image


def run_phase(
    items: list[dict[str, str]],
    *,
    phase: str,
    config: dict[str, str],
    out_dir: Path,
    manifest_path: Path,
    aspect_ratio: str,
    resolution: str,
    poll_interval: int,
    timeout_seconds: int,
    concurrency: int,
    force: bool,
    i2i_refs: dict[str, list[str]] | None = None,
) -> list[dict]:
    existing = {item["filename"]: item for item in load_manifest(manifest_path)}
    records = list(existing.values())
    records_lock = threading.Lock()
    upload_cache: dict[str, str] = {}
    upload_lock = threading.Lock()

    def upload_cached(ref: str) -> str:
        with upload_lock:
            if ref in upload_cache:
                return upload_cache[ref]
        url = upload_file(config["api_base"], config["api_key"], out_dir / ref)
        with upload_lock:
            upload_cache[ref] = url
        return url

    def write_record(record: dict) -> None:
        with records_lock:
            existing[record["filename"]] = record
            records[:] = list(existing.values())
            save_manifest(manifest_path, records)

    def worker(item: dict[str, str]) -> dict:
        out_path = out_dir / item["filename"]
        if out_path.exists() and not force:
            record = {
                "filename": item["filename"],
                "phase": phase,
                "status": "SKIPPED_EXISTS",
                "path": str(out_path),
            }
            write_record(record)
            return record
        try:
            image_urls = None
            prompt_item = item
            endpoint = config["text_to_image_endpoint"]
            if phase == "image-to-image":
                refs = (i2i_refs or {}).get(item["filename"], [])
                missing = [ref for ref in refs if not (out_dir / ref).exists()]
                if missing:
                    record = {
                        "filename": item["filename"],
                        "phase": phase,
                        "status": "MISSING_REFERENCES",
                        "references": refs,
                        "missing": missing,
                    }
                    write_record(record)
                    return record
                image_urls = [upload_cached(ref) for ref in refs]
                prompt_item = {**item, "prompt": item["prompt"] + reference_lock_prompt(item["filename"])}
                endpoint = config["image_to_image_endpoint"]
            submitted = submit_task(
                prompt_item,
                api_base=config["api_base"],
                endpoint=endpoint,
                api_key=config["api_key"],
                aspect_ratio=aspect_ratio,
                resolution=resolution_for_item(item["filename"], resolution),
                image_urls=image_urls,
            )
            submitted["phase"] = phase
            submitted["resolution"] = resolution_for_item(item["filename"], resolution)
            if image_urls is not None:
                submitted["references"] = (i2i_refs or {}).get(item["filename"], [])
            write_record(submitted)
            final = poll_and_download(
                submitted,
                api_base=config["api_base"],
                api_key=config["api_key"],
                out_dir=out_dir,
                poll_interval=poll_interval,
                timeout_seconds=timeout_seconds,
            )
            final["phase"] = phase
            if image_urls is not None:
                final["references"] = (i2i_refs or {}).get(item["filename"], [])
            write_record(final)
            return final
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            record = {
                "filename": item["filename"],
                "phase": phase,
                "status": "HTTP_ERROR",
                "code": exc.code,
                "body": body,
            }
            write_record(record)
            return record
        except Exception as exc:
            record = {
                "filename": item["filename"],
                "phase": phase,
                "status": "ERROR",
                "error": repr(exc),
            }
            write_record(record)
            return record

    if not items:
        return []
    workers = max(1, min(concurrency, 100, len(items)))
    print(f"{phase}: submitting {len(items)} task(s) with concurrency={workers}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(worker, item) for item in items]
        return [future.result() for future in concurrent.futures.as_completed(futures)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate prompt assets via RunningHub in two phases.")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--prompts", default="提示词.md")
    parser.add_argument("--out-dir", default="output/imagegen/assets")
    parser.add_argument("--aspect-ratio", default="9:16")
    parser.add_argument("--resolution", default="1k")
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--skip-image-to-image", action="store_true")
    args = parser.parse_args()

    config = resolve_config(Path(args.env))
    prompts_path = Path(args.prompts)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = extract_prompts(prompts_path)
    if args.only:
        wanted = set(args.only)
        items = [item for item in items if item["filename"] in wanted]
    if not items:
        raise SystemExit("No image prompts found.")

    i2i_refs = default_i2i_references()
    text_to_image, image_to_image = split_items(items, i2i_refs)
    manifest_path = out_dir / f"run_manifest_{time.strftime('%Y%m%d_%H%M%S')}.json"

    phase1 = run_phase(
        text_to_image,
        phase="text-to-image",
        config=config,
        out_dir=out_dir,
        manifest_path=manifest_path,
        aspect_ratio=args.aspect_ratio,
        resolution=args.resolution,
        poll_interval=args.poll_interval,
        timeout_seconds=args.timeout_seconds,
        concurrency=args.concurrency,
        force=args.force,
    )
    failed1 = [item for item in phase1 if item.get("status") not in {"SUCCESS", "SKIPPED_EXISTS"}]
    if failed1:
        print(json.dumps({"phase": "text-to-image", "failed": failed1}, ensure_ascii=False, indent=2), flush=True)
        return 1

    phase2 = []
    if not args.skip_image_to_image:
        phase2 = run_phase(
            image_to_image,
            phase="image-to-image",
            config=config,
            out_dir=out_dir,
            manifest_path=manifest_path,
            aspect_ratio=args.aspect_ratio,
            resolution=args.resolution,
            poll_interval=args.poll_interval,
            timeout_seconds=args.timeout_seconds,
            concurrency=args.concurrency,
            force=args.force,
            i2i_refs=i2i_refs,
        )

    failed2 = [item for item in phase2 if item.get("status") not in {"SUCCESS", "SKIPPED_EXISTS"}]
    print(
        json.dumps(
            {
                "text_to_image": len(phase1),
                "image_to_image": len(phase2),
                "failed": len(failed1) + len(failed2),
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 1 if failed2 else 0


if __name__ == "__main__":
    raise SystemExit(main())
