---
name: seedance-vocab-es
description: "This skill should be used when the user asks for Spanish Seedance 2.0 prompt wording, Spanish cinematic vocabulary, or translation of camera, lighting, action, VFX, audio, and production terms into Spanish."
---

# seedance-vocab-es

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use Spanish cinematic vocabulary when the user asks for Spanish prompts, bilingual delivery, or compact translation of camera, lighting, action, VFX, audio, and production constraints. 

A written example is not an attached file.

## Intent

Spanish prompt direction should preserve production meaning and clause clarity before stylistic cadence. Keep the subject, visible action, camera endpoint, light, sound, and constraint unambiguous. The shipped independent review artifact is empty, so treat these choices as working production wording pending locale-specialist review.

## Usage Rule

Translate production meaning, not word-for-word English. Keep the prompt concrete and concise: subject, visible action, camera, light, sound, and constraint.

| Function | Spanish wording |
|---|---|
| Camera | `travelling de acercamiento`, `plano medio`, `primer plano`, `seguimiento lateral`, `cámara fija` |
| Lighting | `contraluz`, `luz suave de ventana`, `luz práctica cálida`, `sombra marcada`, `luz de contorno fría de luna` |
| Motion | `gira lentamente`, `cruza rápido el encuadre`, `avanza con estabilidad`, `las gotas se deslizan` |
| Audio | `sonido ambiente`, `diálogo claro`, `golpe metálico suave`, `sin música` |
| Constraints | `mantener el logotipo, la etiqueta y la forma sin cambios` |

## Compact Pattern

`La identidad queda en references. Solo cambia [movimiento/luz/cámara]. Cámara: [un movimiento]. Sonido: [señal].`

## De-Slop Rule

## Output Contract

Return Spanish prompt wording; add an English gloss only when requested or needed to resolve a specific ambiguity.

For a first prompt or retake:

- Use the user's supplied decisions immediately. Do not restart an interview or impose a language lesson on a complete brief.
- When directions are requested, distinguish their visible performance, staging or information tradeoffs. Keep the chosen and rejected options across revisions; do not force a menu on a specified scene.
- Put the copyable prompt apart from settings, reference requirements and review notes. A written token does not attach an asset; never invent a binding.
- Preserve exact quoted dialogue even when it is in another language, unless translation is requested. Ask about region or relationship only when it changes the requested wording.
- For a reported failure, name the criterion, propose one concrete change and retain duration, tier and remaining budget. Attribute an unseen result to the user's description. A text revision does not authorize submission; at zero budget offer an acceptable edit or stop.
- Treat authored examples as unrendered and independent language review as pending. Do not promise fluency, voice reliability or credit savings from a static check.
