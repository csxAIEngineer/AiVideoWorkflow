---
name: seedance-copyright
description: "This skill should be used when a Seedance 2.0 prompt mentions named characters, franchises, studios, celebrities, public figures, private people, brand logos, copyrighted scenes, songs, voices, or real-person likeness workflows and needs an IP-safe rewrite."
---

# seedance-copyright

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use this before finalizing prompts involving protected IP, named brands, public figures, private people, voices, logos, songs, studios, exact scenes, or lookalike character requests. The goal is not to dilute the idea; the goal is to preserve the creative function with original, authorized, and safer production language.

## Intent

The user pointing at protected work is not trying to steal - they are showing you the clearest example of what they love that exists. The job is to find what the love is made of and give it back as something safely theirs. Protect the rights-holder and the user's heart in the same move.

## Rewrite Principle

Preserve the scene function, genre, mood, camera logic, emotional beat, and production intent. Replace protected identity with an original archetype, original costume logic, original world details, and descriptive style layers.

| Risk | Replace with |
|---|---|
| Named character or franchise | Original archetype, genre function, and non-identical costume language |
| Studio or living-creator style | Medium, texture, palette, composition, line quality, and motion rhythm |
| Celebrity, public figure, or private person | An original performer. Do not keep the famous face. |
| Someone else's brand, logo, or trademark text | Drop the other brand and the trademark lettering. The user's own product keeps the uploaded shape, color, and material. Do not turn that product into an unnamed object. |
| Song, voice, or a recorded performance | Tempo, energy, instrumentation, or mood. Do not name the song. |
| Exact scene recreation | Original scene with similar narrative function and different setting/blocking |

Celebrities, songs, other people's brands, and trademark lettering can be changed. The user's product keeps the shape, color, and material of the upload. Do not rename it into a generic object, and do not replace it with a different product.

## Authorization Gate

If the user clearly owns the brand, asset, or likeness rights, keep the authorized elements but still preserve them with explicit constraints. If authorization is unclear, ask a short confirmation or provide a safe original rewrite. Do not assume rights from an uploaded image, song, or video.

For real human faces, portraits, or voices, separate three questions: does the active surface support the input, does the user have authorization, and does the prompt avoid imitation of a public figure or private person without consent. Some surfaces use verified virtual portrait assets or authorization flows; do not collapse those into a universal allow or deny rule.

## Safe Replacement Example

Instead of a named superhero swinging through a recognizable franchise city, write: `original masked rooftop courier in a red weatherproof jacket leaps between rain-slick buildings, low handheld tracking camera, blue police lights far below, no logos or franchise symbols`.

## Output Contract

Return risk category, what was changed, safe replacement prompt, authorization requirement, and any residual constraints.
