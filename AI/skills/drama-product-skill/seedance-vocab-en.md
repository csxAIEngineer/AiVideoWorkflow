---
name: seedance-vocab-en
description: "This skill should be used when an English Seedance 2.0 prompt needs clearer production wording, less generic prose, or precise vocabulary for camera, lighting, motion, VFX, audio, and constraints. Route blocked prompts through seedance-filter for context and boundary review."
---

# seedance-vocab-en

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use this skill when the user chooses English prompt wording. Concrete production language can make the intended scene easier to review; it is not a demonstrated cure for moderation errors. This repository has no matched evidence that English receives heavier moderation than other languages. 

A written example is not an attached file.

## Intent

Help the user express the intended action, camera, light and sound clearly. Preserve their chosen language, exact dialogue, reference bindings and creative constraints. Separate a wording clarification from a proposal to change what happens in the scene.

## Usage Rule

Use visible or audible detail when a phrase leaves a relevant production choice unclear. Keep useful genre, style and mood labels. The examples below are optional vocabulary, not required camera moves, lighting setups or limits on scene complexity.

| Function | English wording |
|---|---|
| Camera | `slow push-in`, `locked medium shot`, `stable lateral tracking`, `pull back to reveal`, `macro close-up` |
| Lighting | `soft backlight`, `warm practical light from the left`, `cool moonlight rim`, `wet asphalt reflecting neon` |
| Motion | `a slow head turn that stops`, `droplets merge and slide down`, `fabric settles after the gesture` |
| Audio | `quiet room tone`, `one clear spoken line in quotes`, `no music until after the line` |
| Constraints | `keep the logo, label, and shape unchanged`, `one action, one camera move`, `nothing else moves` |

## De-Slop Pass

Preserve the intended energy and useful style choices; clarify ambiguity and remove redundant praise in context. Do not turn every "cinematic" into a slow push-in or every "epic" into a crowd. Preserve exact dialogue, actual reference tokens, safety boundaries and chosen settings; respect decisions already delegated by the user.

## Filter-Aware Wording

For a blocked prompt, load `seedance-filter.md` and assess the actual request before rewriting. A rejection alone does not establish a false positive or identify a trigger word. Do not invent ownership, consent, prop status or harmless intent. If an alternative changes the content, state that change and offer it as a choice; never disguise prohibited intent.

## Compact Pattern

`Identity stays in references. Only [motion/light/camera] changes. Camera: [one move]. Sound: [one cue]. Constraints: [lock].`

## Output Contract

Return the English prompt and a brief explanation of material edits. Identify any proposed content change separately. Do not label the result filter-approved or imply that a rewrite authorizes another submission.
