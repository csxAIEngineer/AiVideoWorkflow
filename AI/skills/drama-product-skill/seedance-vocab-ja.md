---
name: seedance-vocab-ja
description: "This skill should be used when the user asks for Japanese Seedance 2.0 prompt wording, Japanese cinematic vocabulary, or translation of camera, lighting, action, VFX, audio, and production terms into Japanese."
---

# seedance-vocab-ja

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use Japanese cinematic vocabulary when the user asks for Japanese prompt wording, bilingual delivery, compact translation, or production vocabulary for camera, lighting, motion, VFX, and audio. 

A written example is not an attached file.

## Intent

Japanese prompt direction can combine concise clauses with established production loanwords. Choose one readable clause order, keep each term attached to a concrete shot instruction, and avoid literal English syntax where a shorter production construction is available. The shipped independent review artifact is empty, so treat these choices as working production wording pending locale-specialist review.

## Usage Rule

Prefer concise production Japanese over literal translation. Keep the structure readable: subject, action, camera, lighting, sound, and preservation constraint.

| Function | Japanese wording |
|---|---|
| Camera | `ゆっくりドリーイン`, `横移動のトラッキング`, `固定の中景`, `低いアングル`, `クローズアップ` |
| Lighting | `逆光`, `柔らかい窓光`, `暖かいプラクティカルライト`, `冷たい月明かり`, `輪郭光` |
| Motion | `ゆっくり振り返る`, `画面を素早く横切る`, `水滴が下へ流れる`, `煙が薄く広がる` |
| Audio | `静かな環境音`, `短い台詞`, `金属音`, `音楽なし` |
| Constraints | `ロゴ、ラベル、形状を正確に維持する` |

## Compact Pattern

`外見は references に置き、プロンプトには書かない。変化は[動き/光/カメラ]のみ。カメラ：[一つの動き]。音：[音声指示]。`

## Register Rule

Preserve user-supplied dialogue. Offer register choices only where they affect the brief. Measure the actual spoken line; do not use a fixed multiplier or change politeness to fit an invented sync budget. Record any unresolved locale-review need.

## De-Slop Rule

Preserve a useful style or aesthetic term and add only the scene-specific action, framing, light, or sound needed to clarify it. The table offers authored options, not a default look or a proven model-performance rule. Keep the user's chosen energy, dialogue, and settings. Offer distinct choices only when the ambiguity changes the scene; if the user delegated the choice, decide within that scope.

## Output Contract

Return Japanese prompt wording, optional English gloss when useful.
