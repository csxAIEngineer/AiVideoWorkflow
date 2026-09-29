#!/usr/bin/env python3
"""
Submit a Seedance 2.0 Image-to-Video task to Atlas Cloud,
poll until completion, and print the video URL.

Usage:
  python3 generate_video.py --image <url> --prompt "..." \
    [--duration 5] [--resolution 720p] [--ratio 16:9] [--fast] [--out result.json]
"""
import argparse
import json
import os
import sys
import time
import urllib.request

BASE = "https://api.atlascloud.ai/api/v1"


def http(method, url, token=None, body=None, timeout=60):
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode())


def discover_model(fast=False):
    preferred = (
        ["bytedance/seedance-2.0-fast/image-to-video", "bytedance/seedance-2.0/image-to-video"]
        if fast
        else ["bytedance/seedance-2.0/image-to-video", "bytedance/seedance-2.0-fast/image-to-video"]
    )
    try:
        resp = http("GET", f"{BASE}/models")
        available = {
            m.get("model")
            for m in resp.get("data", [])
            if m.get("display_console")
        }
    except Exception:
        available = set()
    for m in preferred:
        if m in available:
            return m
    return preferred[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True, help="Image URL from Step 3")
    p.add_argument("--prompt", required=True, help="Motion / camera prompt")
    p.add_argument("--duration", type=int, default=15)
    p.add_argument("--resolution", default="720p", choices=["480p", "720p", "1080p"])
    p.add_argument("--ratio", default="16:9")
    p.add_argument("--fast", action="store_true", help="Use seedance-2.0-fast")
    p.add_argument("--model", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--poll-interval", type=float, default=5.0)
    p.add_argument("--max-attempts", type=int, default=240)
    args = p.parse_args()

    token = os.environ.get("ATLASCLOUD_API_KEY")
    if not token:
        sys.exit("ERROR: ATLASCLOUD_API_KEY not set")

    model = args.model or discover_model(fast=args.fast)

    print(f"[video] model={model} duration={args.duration}s res={args.resolution}", file=sys.stderr)

    body = {
        "model": model,
        "prompt": args.prompt,
        "image": args.image,
        "duration": args.duration,
        "resolution": args.resolution,
        "ratio": args.ratio,
    }

    submit = http("POST", f"{BASE}/model/generateVideo", token=token, body=body)
    pred_id = submit.get("data", {}).get("id")
    if not pred_id:
        sys.exit(f"ERROR: submit failed: {json.dumps(submit)[:500]}")

    print(f"[video] prediction_id={pred_id}", file=sys.stderr)

    for attempt in range(args.max_attempts):
        time.sleep(args.poll_interval)
        r = http("GET", f"{BASE}/model/prediction/{pred_id}", token=token)
        d = r.get("data", {})
        status = d.get("status", "")
        if status in ("completed", "succeeded"):
            outputs = d.get("outputs") or d.get("output") or []
            if not outputs:
                sys.exit("ERROR: completed but no outputs")
            result = {
                "prediction_id": pred_id,
                "model": model,
                "image_input": args.image,
                "prompt": args.prompt,
                "duration": args.duration,
                "resolution": args.resolution,
                "video_url": outputs[0],
            }
            out_json = json.dumps(result, ensure_ascii=False, indent=2)
            print(out_json)
            if args.out:
                os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
                with open(args.out, "w") as f:
                    f.write(out_json)
            return
        if status == "failed":
            sys.exit(f"ERROR: {d.get('error', 'unknown')}")
        print(f"[video] attempt {attempt + 1} status={status}", file=sys.stderr)

    sys.exit("ERROR: polling timeout")


if __name__ == "__main__":
    main()
