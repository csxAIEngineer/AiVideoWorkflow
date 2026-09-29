#!/usr/bin/env python3
"""
Submit a GPT Image 2 (or fallback 1.5) generation task to Atlas Cloud,
poll until completion, and print the image URL.

Usage:
  python3 generate_image.py --prompt "..." --aspect "16:9" [--out result.json]
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = "https://api.atlascloud.ai/api/v1"
ASPECT_TO_SIZE = {
    "16:9": "1664x936",
    "9:16": "936x1664",
    "1:1": "1024x1024",
    "4:3": "1280x960",
    "3:4": "960x1280",
    "21:9": "1920x816",
}


def http(method, url, token=None, body=None, timeout=60):
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode())


def discover_model():
    """Prefer openai/gpt-image-2/text-to-image, fallback to 1.5."""
    preferred = [
        "openai/gpt-image-2/text-to-image",
        "openai/gpt-image-1.5/text-to-image",
        "openai/gpt-image-1/text-to-image",
    ]
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
    p.add_argument("--prompt", required=True)
    p.add_argument("--aspect", default="16:9")
    p.add_argument("--model", default=None, help="Override model ID")
    p.add_argument("--out", default=None, help="Write result JSON to this path")
    p.add_argument("--poll-interval", type=float, default=3.0)
    p.add_argument("--max-attempts", type=int, default=200)
    args = p.parse_args()

    token = os.environ.get("ATLASCLOUD_API_KEY")
    if not token:
        sys.exit("ERROR: ATLASCLOUD_API_KEY not set")

    model = args.model or discover_model()
    size = ASPECT_TO_SIZE.get(args.aspect, "1664x936")

    print(f"[image] model={model} size={size}", file=sys.stderr)

    submit = http(
        "POST",
        f"{BASE}/model/generateImage",
        token=token,
        body={"model": model, "prompt": args.prompt, "image_size": size},
    )
    pred_id = submit.get("data", {}).get("id")
    if not pred_id:
        sys.exit(f"ERROR: submit failed: {json.dumps(submit)[:500]}")

    print(f"[image] prediction_id={pred_id}", file=sys.stderr)

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
                "prompt": args.prompt,
                "aspect_ratio": args.aspect,
                "image_url": outputs[0],
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
        print(f"[image] attempt {attempt + 1} status={status}", file=sys.stderr)

    sys.exit("ERROR: polling timeout")


if __name__ == "__main__":
    main()
