---
name: seedance-short-drama
description: "End-to-end workflow for Chinese vertical short-drama production prompts. Use when the user wants to turn an idea, script, product placement, or shot outline into a 15-60 second 9:16 drama package: script and shot breakdown, GPT-image/RunningHub reference-image prompts, segmented PREVIS nine-panel storyboards, and Seedance 2.0 video prompts with character/product continuity. Also use when the user asks to generate assets/images from 提示词.md, such as 按照提示词.md生成资产 or 创建资产文件夹用于保存."
---

# Seedance Short Drama

## Overview

Use this skill to build a reusable short-drama pipeline from a story idea to Seedance-ready generation prompts and, when requested, RunningHub image/video generation tasks. Always create Markdown files for the script, image prompts, and video prompts; do not provide these deliverables only in chat.

- `剧本-分镜头.md`
- `提示词.md`
- image asset folder and manifest, if image generation is requested
- `视频提示词.md`

For detailed reusable prompt patterns and QC rules, read [workflow-rules.md](references/workflow-rules.md) before writing image/storyboard/video prompts. When the user asks to actually generate images or videos, also read [runninghub-generation.md](references/runninghub-generation.md).

## Prompt Document Review Gate

After creating the prompt documents, ask the user for revision suggestions and stop. Do not directly call the RunningHub API to generate images or videos in the same turn.

- By default, this skill only writes the script, image/storyboard prompt document, and Seedance/video prompt document.
- When the prompt documents are complete, summarize the created files and ask the user what they want to modify.
- Do not submit RunningHub image or video tasks automatically, even if `RUNNINGHUB_API_KEY` exists.
- Only call RunningHub after the user explicitly asks to generate assets/images/videos after reviewing or approving the prompt documents.

## Asset Generation Guardrail

When the user asks to generate assets/images from `提示词.md` and create an asset folder:

- Confirm that this is a post-review generation request, not the initial prompt-document creation step.
- Use RunningHub only.
- Check `RUNNINGHUB_API_KEY` from the current environment, the project `.env`, or an explicitly provided `.env` path.
- Do not treat `references/runninghub-generation.md` or any skill documentation file as API configuration.
- If no `RUNNINGHUB_API_KEY` is available, reply exactly: `没有配置 RunningHub API，无法生成资产。`
- If the key exists, submit real RunningHub text-to-image/image-to-image tasks and save the downloaded results.
- If the `rh` command is installed and the request fits a simple RunningHub model/app command, use the RH_CLI workflow in [runninghub-generation.md](references/runninghub-generation.md). Use the bundled Python scripts for prompt-file batch generation, multi-reference storyboards, and manifest-heavy workflows.
- Generate independent text-to-image assets concurrently first, with concurrency capped at 100; after all required text-to-image references exist, generate reference-dependent image-to-image assets.
- Do not use Pillow, canvas, SVG, placeholder images, local image libraries, local model services, or any other image generation service as a fallback.

## Workflow

1. Create the script first.
   - Keep the requested duration, usually 30 seconds, and preserve the user's emotional tone and twist.
   - Include dialogue, time ranges, shot list, product presentation notes, and filming notes.
   - Write the result to `剧本-分镜头.md` every time this skill produces a script or shot breakdown.

2. Create reference-image prompts second.
   - Write `提示词.md` for GPT-image/RunningHub assets every time this skill produces image prompts.
   - All asset prompts must share one unified visual style. Character multi-view prompts must use the fixed character template in [workflow-rules.md](references/workflow-rules.md).
   - Generate or request these asset categories in order: unified style look, character turnarounds, scene references, product/prop references, segmented storyboards.
   - Treat the user's real product image as the strongest source of truth. If no real product image exists, create a product reference sheet and mark it as the P0 product lock.
   - If the user asks to generate images, follow [runninghub-generation.md](references/runninghub-generation.md): text-to-image for clean references, image-to-image for after-state characters and segmented storyboards.
   - If the user asks to generate assets from `提示词.md` and create an asset folder, first check only the RunningHub API key as described in [runninghub-generation.md](references/runninghub-generation.md). If no key is configured, answer that the RunningHub API is not configured and stop.

3. Build storyboards as segmented PREVIS boards.
   - Do not create a single overall nine-grid storyboard for the whole film.
   - Create one 9:16, 3x3, nine-panel PREVIS storyboard per approximately 10 seconds of story.
   - Generate PREVIS storyboard images at `4k` resolution.
   - Each board should cover richer motion beats for its segment and should align with the script timecode.
   - Use text-to-image for clean character/scene/product references first, then image-to-image with those references to generate each storyboard.

4. Create Seedance prompts last.
   - Write `视频提示词.md` every time this skill produces Seedance/video generation prompts.
   - One storyboard image corresponds to one Seedance segment.
   - Keep each segment 15 seconds or less, usually 10 seconds for a 30-second drama.
   - Do not upload, cite, or depend on previously generated video clips for continuity. Maintain continuity through product images, character turnarounds, scenes, and the current storyboard only.
   - If the user asks to generate video, submit one SparkVideo 2.0 multimodal-video task per segment using the reference order in `视频提示词.md`; save each downloaded clip and create a manifest.

5. Ask for review before generation.
   - After the prompt documents are generated, ask the user for modification suggestions.
   - Do not directly call RunningHub APIs to generate images or videos at this stage.
   - Continue to asset or video generation only after the user explicitly confirms a post-review generation request.

## Continuity Rules

- Product reference images are P0 locks. They override storyboard drift for product color, material, cut, shape, scale, logo/text absence, and visible details.
- Character turnarounds lock identity, age, face, hair, body type, clothing, and temperament.
- Scene references lock layout, light, geography, and real-world scale.
- Storyboards lock shot order, composition, action, staging, and emotional beats only.
- If a storyboard conflicts with product or character references, explicitly demote that storyboard detail to structure-only in the Seedance prompt.

## Output Style

- Write in Chinese when the user's project is Chinese.
- Keep prompts production-ready, direct, and specific.
- Avoid subtitles, readable screen text, price tags, brand logos, watermarks, corner marks, background music, impossible body/object motion, and incoherent reflections.
- For product placement, make the product emotionally motivated by the story instead of only describing it as expensive or beautiful.

## Validation

Before finishing, check that:

- The script, image prompts, storyboards, and video prompts share the same time ranges and plot beats.
- There is no conflict between an overall storyboard and segmented storyboards.
- Each Seedance segment lists the correct reference upload order.
- Any generated image or video task has a manifest with task IDs, saved paths, statuses, and failed items.
- The final segment uses the post-change character reference when a character changes clothing or state.
- The product remains locked to the product reference in every relevant shot.
