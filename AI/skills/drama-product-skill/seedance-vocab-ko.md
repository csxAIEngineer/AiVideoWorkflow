---
name: seedance-vocab-ko
description: "This skill should be used when the user asks for Korean Seedance 2.0 prompt wording, Korean cinematic vocabulary, or translation of camera, lighting, action, VFX, audio, and production terms into Korean."
---

# seedance-vocab-ko

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use Korean cinematic vocabulary when the user asks for Korean prompt wording, bilingual delivery, compact translation, or production vocabulary for camera, lighting, action, VFX, audio, and constraints. 

A written example is not an attached file.

## Intent

Korean prompt direction should preserve useful mood or style terms and clarify ambiguous intent through observable light, framing, blocking, or timing. Keep the relationship and speech level explicit whenever dialogue is added. The shipped independent review artifact is empty, so treat these choices as working production wording pending locale-specialist review.

## Usage Rule

Translate the production intention rather than every English word. Keep the Korean prompt compact and concrete: subject, action, camera, light, sound, and preservation constraint.

| Function | Korean wording |
|---|---|
| Camera | `느린 돌리 인`, `측면 트래킹 샷`, `고정된 미디엄 샷`, `로우 앵글`, `클로즈업` |
| Lighting | `역광`, `부드러운 창문 빛`, `따뜻한 프랙티컬 조명`, `차가운 달빛`, `림 라이트` |
| Motion | `천천히 돌아선다`, `프레임을 빠르게 가로지른다`, `물방울이 아래로 흐른다`, `연기가 얇게 퍼진다` |
| Audio | `조용한 환경음`, `짧은 대사`, `부드러운 금속음`, `음악 없음` |
| Constraints | `로고, 라벨, 형태를 정확히 유지한다` |

## Compact Pattern

`외형은 references 에 두고 프롬프트에는 쓰지 않는다. 변화는 [동작/조명/카메라]만 적용한다. 카메라: [한 가지 움직임]. 사운드: [음향 지시].`

## Speech Level Rule

Preserve user-supplied dialogue. Offer register choices only where they affect the brief. Measure the actual spoken line; do not use a fixed multiplier or change politeness to fit an invented sync budget. Record any unresolved locale-review need.

## De-Slop Rule

Preserve a useful style or aesthetic term and add only the scene-specific action, framing, light, or sound needed to clarify it. The table offers authored options, not a default look or a proven model-performance rule. Keep the user's chosen energy, dialogue, and settings. Offer distinct choices only when the ambiguity changes the scene; if the user delegated the choice, decide within that scope.

## Output Contract

Return Korean prompt wording, optional English gloss when useful.
