# RunningHub Image And Video Generation

Use this reference when the user asks to actually generate images or videos, not only write prompts.

## Environment

Do not paste or expose API keys in responses. Load the API key from the user's environment or a local `.env` file. This `runninghub-generation.md` file is documentation only; values written here are examples and are not automatically loaded as configuration.

When this skill is shared with another person, that person only needs to configure their own `RUNNINGHUB_API_KEY` locally in an environment variable or project `.env`. Do not copy your key into the skill folder or any skill reference file.

Required user variable:

```text
RUNNINGHUB_API_KEY=...
```

Use these platform defaults unless the user explicitly overrides them:

```text
RUNNINGHUB_API_BASE=https://www.runninghub.cn/openapi/v2
RUNNINGHUB_IMAGE_ENDPOINT=rhart-image-g-2/text-to-image
RUNNINGHUB_TEXT_TO_IMAGE_ENDPOINT=rhart-image-g-2/text-to-image
RUNNINGHUB_IMAGE_TO_IMAGE_ENDPOINT=rhart-image-n-g31-flash/image-to-image
RUNNINGHUB_VIDEO_ENDPOINT=rhart-video/sparkvideo-2.0/multimodal-video
```

Accept `RUNNINGHUB_IMAGE_ENDPOINT` and `RUNNINGHUB_TEXT_TO_IMAGE_ENDPOINT` as equivalent names for text-to-image. If neither is set, use `rhart-image-g-2/text-to-image`.

Minimal personal `.env` example, placed in the project folder such as `D:\Claude\codex\.env`:

```text
RUNNINGHUB_API_KEY=their-own-key
```

## Optional RH_CLI Workflow

Use RH_CLI when the user asks for simple one-off RunningHub image/video generation, wants to run a RunningHub community app, provides a RunningHub app ID/URL, or explicitly asks to use `rh`. Prefer the bundled Python scripts below for batch generation from `提示词.md`, two-phase text-to-image then image-to-image asset creation, multi-reference storyboards, or workflows that must produce a detailed manifest.

Never store an API key in the skill folder. Configure RH_CLI from the user's local environment or config:

```powershell
# one-time install from the cloned RH_CLI repo
python -m pip install .

# one-time local auth; use the user's own key
rh auth set-key YOUR_RUNNINGHUB_API_KEY
rh check
rh auth set-output-dir output\runninghub
```

Check availability before choosing this path:

```powershell
rh --version
rh --json check
```

For simple text-to-image:

```powershell
rh --json --output-dir "output\imagegen\<project-name>" model run `
  -e rhart-image-g-2/text-to-image `
  -p "<prompt from 提示词.md>" `
  --param aspectRatio=9:16 `
  --param resolution=1k
```

For a simple image-to-image/edit task with one local reference image:

```powershell
rh --json --output-dir "output\imagegen\<project-name>" model run `
  -e rhart-image-n-g31-flash/image-to-image `
  -p "<prompt plus reference-lock instructions>" `
  -i ".\refs\reference.png" `
  --param aspectRatio=9:16 `
  --param resolution=4k
```

For a simple Seedance/SparkVideo task:

```powershell
rh --json --output-dir "output\video\<project-name>" video `
  --model "Seedance" `
  -p "<Seedance segment prompt>" `
  --duration 10 `
  --param ratio=9:16 `
  --param resolution=720p `
  --param generateAudio=true
```

For a RunningHub community app:

```powershell
rh app info <webapp-id-or-url>

rh --json app run <webapp-id-or-url> `
  --node "52:prompt=<prompt text>" `
  --file "39:image=.\refs\reference.png" `
  -o "output\runninghub\<project-name>\result.png"
```

When RH_CLI returns JSON, copy `task_id`, `files`, `cost`, and `duration` into the project manifest. If RH_CLI cannot express the required endpoint parameters, multiple reference-image order, or two-phase dependency chain, fall back to the bundled Python scripts in this skill.

## Asset Generation Request Rule

When the user says something like `按照[提示词.md](提示词.md)生成资产，创建资产文件夹用于保存`, use this RunningHub workflow. Before doing any image generation:

1. Check whether `RUNNINGHUB_API_KEY` is available from the current process environment, the project `.env`, or an `.env` path the user explicitly provided.
2. Do not read `RUNNINGHUB_API_KEY` from `runninghub-generation.md`; it is only a reference document.
3. If no `RUNNINGHUB_API_KEY` is available, reply exactly and stop: `没有配置 RunningHub API，无法生成资产。`
4. Do not search for, start, or use Pillow, canvas, SVG, placeholder generation, local image libraries, or local image generation tools.
5. Do not fall back to other online or local image services.
6. Do not continue by only creating placeholder files as if generation succeeded.

## Common API Flow

Use a two-phase generation flow:

1. Parse `提示词.md`.
2. Split prompts into text-to-image assets and image-to-image assets. Image-to-image assets are the assets that require generated references, such as after-state character sheets and segmented storyboards.
3. Submit all text-to-image tasks concurrently, with concurrency capped at 100.
4. Poll and download all text-to-image results. Do not start image-to-image tasks until every required text-to-image reference has succeeded or already exists.
5. Upload the generated reference images required by image-to-image tasks.
6. Submit image-to-image tasks after phase 1 completes. These may also run concurrently, capped at 100, but only after their references exist.
7. Save a JSON manifest containing phase, filename, prompt source, reference image paths, uploaded URLs, task ID, status, result URL, and local output path.

Use `output/imagegen/<project-name>/` for images and `output/video/<project-name>/` for videos unless the user specifies another folder.

Preferred script:

```powershell
python "C:\Users\Administrator\.codex\skills\seedance-short-drama\scripts\generate_runninghub_assets.py" --env ".env" --prompts "提示词.md" --out-dir "output\imagegen\<project-name>" --concurrency 100
```

The script enforces the two-phase order: concurrent text-to-image first, then reference-dependent image-to-image.
It also forces PREVIS storyboard assets whose filenames contain `_storyboard_segment_` to use `4k` resolution.

## Upload Local References

Endpoint:

```text
POST https://www.runninghub.cn/openapi/v2/media/upload/binary
Authorization: Bearer ${RUNNINGHUB_API_KEY}
multipart form field: file=@<local-path>
```

Use returned `data.download_url` as the URL in `imageUrls`. Uploads are temporary, so upload references right before submitting generation tasks.

## Text-To-Image

Use for:

- Unified style reference.
- Character turnarounds.
- Empty scene references.
- Product reference sheet when the user did not provide a real product image.
- Simple props.

Endpoint:

```text
POST {RUNNINGHUB_API_BASE}/{RUNNINGHUB_TEXT_TO_IMAGE_ENDPOINT}
```

Payload:

```json
{
  "prompt": "<prompt from 提示词.md>",
  "aspectRatio": "9:16",
  "resolution": "1k"
}
```

Use `1k` by default. Use `2k` or `4k` only when the user asks or when product detail really needs it.

## Image-To-Image

Use for:

- A character after-state sheet that must preserve identity from a before-state sheet.
- Segmented nine-panel storyboards that must use character, scene, product, and prop references.
- Regenerating inconsistent assets.

For PREVIS segmented storyboards, set `resolution` to `4k`. This applies to assets named like `11_storyboard_segment_01_0-10s.png`.

Endpoint:

```text
POST {RUNNINGHUB_API_BASE}/{RUNNINGHUB_IMAGE_TO_IMAGE_ENDPOINT}
```

Payload:

```json
{
  "imageUrls": ["<uploaded-reference-url-1>", "<uploaded-reference-url-2>"],
  "prompt": "<prompt from 提示词.md plus reference-lock instructions>",
  "aspectRatio": "9:16",
  "resolution": "4k for PREVIS storyboard assets, otherwise the project default"
}
```

Reference order for after-state character:

1. Before-state character turnaround.
2. Product P0 reference.

Reference order for segmented storyboards:

1. Optional 3x3 storyboard template if available.
2. Current segment character references.
3. Current segment scene references.
4. Product P0 reference.
5. Current segment props.

For storyboards, add explicit instructions that the output must be a real 3x3 PREVIS storyboard, not a collage of reference sheets, and that reference images only lock identity, clothing, scene, and product consistency.

## SparkVideo 2.0 Multimodal Video

Use the SparkVideo 2.0 multimodal-video endpoint for Seedance/RunningHub video generation when the user asks to generate clips. Do not use the deprecated image-to-video endpoint.

Endpoint:

```text
POST {RUNNINGHUB_API_BASE}/{RUNNINGHUB_VIDEO_ENDPOINT}
```

Payload:

```json
{
  "prompt": "<Seedance segment prompt from 视频提示词.md>",
  "resolution": "720p",
  "duration": "10",
  "imageUrls": ["<uploaded-reference-url-1>", "<uploaded-reference-url-2>"],
  "videoUrls": [],
  "audioUrls": [],
  "generateAudio": true,
  "ratio": "9:16",
  "realPersonMode": true,
  "conversionSlots": ["all"],
  "returnLastFrame": false,
  "seed": -1
}
```

Use one task per segment. For a 30-second drama, submit three tasks: 0-10s, 10-20s, 20-30s. `duration` must be a string and must be one of `-1`, `4` through `15`; use `"10"` for a 10-second segment.

The SparkVideo 2.0 endpoint supports up to 9 image URLs. If there are more than 9 references, prioritize:

1. Product P0 image.
2. Current-segment main character references.
3. Current-segment scene reference.
4. Current-segment storyboard.
5. Props only if they are essential to the shot.

Do not upload previously generated video clips for continuity. Do not use previous clips as first-frame or last-frame references unless the user explicitly changes the workflow.

Preferred script:

```powershell
python "C:\Users\Administrator\.codex\skills\seedance-short-drama\scripts\generate_runninghub_videos.py" --env ".env" --video-prompts "视频提示词.md" --asset-dir "output\imagegen\<project-name>" --out-dir "output\video\<project-name>"
```

## Segment Upload Order

Use the upload order already written in `视频提示词.md`. For each segment, upload product first and storyboard last whenever possible:

```text
<product P0>
<characters for this segment>
<scenes for this segment>
<props if needed>
<current segmented storyboard>
```

For a character after a costume change, use the after-state character reference only in the segment where that state appears.

## Failure Handling

- If a task fails, save the raw API response in the manifest and retry only the failed item when the user asks or when the fix is obvious.
- If an output has character drift, regenerate with image-to-image and stronger identity-lock wording.
- If a storyboard is not a 3x3 PREVIS board, regenerate the storyboard before submitting video.
- If a video contains subtitles, readable text, logos, wrong product details, or storyboard arrows/labels inside the footage, mark it as failed QC.

