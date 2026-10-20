---
name: seedance-recipes
description: "This skill should be used when the user asks for a Seedance 2.0 template, genre recipe, product ad, lifestyle video, drama scene, music video, landscape shot, commercial, animation scene, or reusable production pattern."
---

# seedance-recipes

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use recipes as starting patterns, not rigid prompt templates. Pick the recipe that matches the user's outcome, then customize subject, action, camera, lighting, audio, and constraints. Recipes should preserve the one-beat discipline of a short clip.

## Intent

A recipe is a head start, never a mold. The user wants the confidence of a proven shape with their own story inside it. Bend the recipe to the story every time; a user who feels templated has been failed even if the output is competent.

## Recipe Families

| Family | Best use | Core pattern |
|---|---|---|
| Product | Ads, ecommerce, hero shots, material reveals. | `product anchor + one material change + controlled camera + logo preservation` |
| Lifestyle | Human use, food, travel, social clips. | `simple action + lived environment + handheld or natural light + ambient sound` |
| Drama | Emotion, dialogue, short narrative beats. | `character tag + gesture + motivated camera + silence or sparse sound` |
| Music video | Beat sync, dance, stylized edits. | `rhythm reference + visible beat changes + light pulses + clear character blocking` |
| Landscape | Establishing shots, nature, atmosphere. | `slow camera + weather motion + layered depth + natural sound` |
| Commercial | Brand-safe polish and function. | `problem/use/result beat + precise product constraint + clean light` |
| Animation | Original characters and stylized motion. | `medium + shape language + palette + elastic or weighted motion` |
| VFX | Transformations, particles, weather, energy. | `source + material behavior + interaction + dissipation endpoint` |
| First/last frame | In-between transitions, product state changes, character pose targets. | `first frame + last frame + continuous transition + identity locks` |
| Commercial campaign | 6/10/15/30s variants, vertical/social cutdowns, textless/localized masters. | `hook + product proof + end state + cutdown matrix + delivery notes` |
| Short drama (短剧) | Fast-cut vertical mini-drama beats: setup, reversal, cliffhanger. | `two or three labeled shots + one emotional reversal + held reaction close + cut on the sting` |
| Talking head (口播) | Presenter, explainer, livestream-style pitch to camera. | `locked medium close-up + short quoted lines + minimal head motion + caption-safe framing + room tone` |
| Home / space tour | Walkthrough of a room, venue, or property. | `single continuous take + steady forward path + light changes per zone + ambient sound only` |

## Prompt Skeletons

**Product I2V:** `The product still locks shape and materials through references. Do not redraw trademark text. [One material or light change]. Camera: [single move]. Lighting: [physical source]. Sound: [ambient/SFX].`

**Drama T2V:** `Character A [visible emotional action] in [specific setting]. Camera: [motivated framing]. Lighting: [motivated source]. Sound: [ambient or short dialogue]. End state: [changed expression/action].`

**Reference Motion:** `Write only the [camera/action/timing] you want. Do not transfer identity, costume, or environment from some other clip. New subject: [authorized/original subject]. [Action and endpoint].`

**First/Last Frame:** `The previous shot's lock line is the start. The end state is [end state]. Preserve [identity/product/scene anchors]. Camera: [locked or one controlled move]. Sound: [ambient/SFX].`

**Animation:** `Original [character archetype] [action] in [environment]. Style: [medium, line quality, texture, palette]. Motion: [rhythm]. Camera and sound: [simple support].`

## Selection Rule

If a user gives many goals, choose the recipe that protects the most fragile requirement. Product identity beats camera spectacle; lip-sync beats large head motion; character consistency beats complex choreography; first/last-frame target accuracy beats extra style changes; safety and authorization beat style mimicry.

## Sequence State

When sequence state is present, recipes must inherit the story spine, current clip scope, continuity locks, reference roles, completed beats, and reserved future beats. A recipe can propose a clip map, but it must finalize only the current unresolved prompt and leave later prompts provisional the next shot starts from the previous lock line, without waiting for a viewing pass.

## Output Contract

Return one selected recipe, why it fits, the customized prompt skeleton, compact final prompt, and campaign/delivery notes when relevant.

These are authored concepts, not observed generation results.
