---
name: seedance-vocab-ru
description: "This skill should be used when the user asks for Russian Seedance 2.0 prompt wording, Russian cinematic vocabulary, or translation of camera, lighting, action, VFX, audio, and production terms into Russian."
---

# seedance-vocab-ru

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use Russian cinematic vocabulary when the user asks for Russian prompt wording, bilingual delivery, compact translation, role binding, first/last-frame workflow, or production vocabulary for camera, lighting, action, VFX, audio, and constraints. 

A written example is not an attached file.

## Intent

Russian prompt direction should separate verified platform limits from field-observed workarounds and label each accordingly. Keep dialogue, lip-sync, and pronunciation guidance testable rather than promising a result the evidence does not support. The shipped independent review artifact is empty, so treat these choices as working production wording pending locale-specialist review.

## Usage Rule

Translate production intent, not every English word. Russian prompts should stay compact, concrete, and ordered by subject, action, camera, light, sound, and constraint.

| Function | Russian wording |
|---|---|
| Camera | `медленный наезд камеры`, `боковое сопровождение`, `фиксированный средний план`, `нижний ракурс`, `крупный план` |
| Lighting | `контровой свет`, `мягкий свет из окна`, `теплый практический источник`, `холодный лунный свет`, `контурная подсветка` |
| Motion | `медленно поворачивается`, `быстро проходит через кадр`, `капли стекают вниз`, `дым мягко рассеивается` |
| Audio | `тихий фон помещения`, `короткая реплика`, `мягкий металлический щелчок`, `без музыки` |
| First/last frame | `上一镜锁句是下一镜起点`, `естественный переход`。镜头正文不写 `@Image1`。 |
| Constraints | `сохранить логотип, этикетку и форму без изменений` |

## Compact Pattern

`Внешность берётся из загруженного кадра через references. Меняются только [движение/свет/камера]. Камера: [одно движение]. Звук: [аудиосигнал].`

## De-Slop Rule

## Dialogue Rule

Preserve exact supplied dialogue and Cyrillic spelling; do not transliterate, shorten, or change register without agreement. Time the actual performance. Offer supported voice-reference or post-dub options as user choices, not automatic replacements or guarantees.

## Output Contract

Return Russian prompt wording; add an English gloss only when requested or needed to resolve a specific ambiguity.

For a first prompt or retake:

- Use the user's supplied decisions immediately. Do not restart an interview or impose a language lesson on a complete brief.
- When directions are requested, distinguish their visible performance, staging or information tradeoffs. Keep the chosen and rejected options across revisions; do not force a menu on a specified scene.
- Put the copyable prompt apart from settings, reference requirements and review notes. A written token does not attach an asset; never invent a binding.
- Preserve exact quoted dialogue even when it is in another language, unless translation is requested. Ask about region or relationship only when it changes the requested wording.
- For a reported failure, name the criterion, propose one concrete change and retain duration, tier and remaining budget. Attribute an unseen result to the user's description. A text revision does not authorize submission; at zero budget offer an acceptable edit or stop.
- Treat authored examples as unrendered and independent language review as pending. Do not promise fluency, voice reliability or credit savings from a static check.
