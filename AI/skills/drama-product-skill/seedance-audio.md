---
name: seedance-audio
description: "This skill should be used when the user asks for Seedance 2.0 audio, dialogue, lip-sync, music, sound effects, ambience, beat-sync, audio-reference mapping, desync troubleshooting, or sound-driven visual timing."
---

# seedance-audio

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use this for dialogue, lip-sync, sound layers, music, ambience, beat-sync, audio-reference mapping, desync troubleshooting, or sound-driven visual timing. Audio should support the visible beat instead of becoming a second competing prompt.

When the user needs stems, M&E, dubbing, loudness, sync, mix, or delivery guidance.

## Intent

Half of every emotion enters through the ears, and users almost always forget sound until its absence makes the clip feel dead. The soul here is giving every scene its sound before being asked - the room's breath, the action's evidence, the line that lands. When they hear it, they realize it was always part of what they meant.

## Core Rules

Preserve the user's exact dialogue, language, script, and register. Quote each line and name its speaker. Stable framing and one speaker turn are useful starting heuristics when mouth accuracy matters; do not remove required performance or rewrite words without authorization. Check spoken duration and inspect the returned words, performance, and visible sync separately.

Write the sound role in the shot: dialogue, ambience, SFX, or silence. Use a spoken-voice workflow only when the active operation supports it, with rights-cleared material; do not promise exact playback or guaranteed lip-sync. Offer native generation, a supported reference workflow, and post dubbing with their tradeoffs. No universal Mandarin-first ranking or per-language line ceiling is established by this repository.

## Sound Layer Pattern

Use compact layers: `Dialogue:... Sound:... SFX:... Music:... Silence:...`. Include only the layers that matter. Silence is valid when it sharpens drama or avoids confusing lip-sync.

| Need | Stable audio direction |
|---|---|
| Lip-sync | `Character A, locked medium close-up, says "I found it." Clear dry dialogue, no head turn.` |
| Product ad | `Sound: low room tone. SFX: magnetic click on lid open, soft glass chime at final frame.` |
| Beat sync | `Light pulses and foot taps match the downbeat. Tempo is described, not attached as an @ tag.` |
| Drama | `Distant rain and refrigerator hum; no music during the line.` |
| Action | `Breathing grows louder, shoe squeak at landing, metal door buzzer at endpoint.` |

## Multi-Character Dialogue

Use one speaker per short clip when reliability matters. If two characters must speak, separate turns and keep the camera stable: `Character A says... pause. Character B answers...`. For complex exchanges, recommend generating controlled single-speaker clips and compositing in post.

## Failure Fixes

If dialogue desyncs, offer a shorter line if wording can change, lock the camera, remove head turns, clean the audio role, and reduce competing SFX. If the wrong speaker talks, assign tags and split lines by speaker. If audio is ignored, remove extra music/SFX instructions and make the reference role explicit.

If audio and video references fight each other, mute the reference video before upload when possible, or make the priority explicit: `Camera and tempo are written in the shot. They do not share one upload.`

## Sequence State

When sequence state is present, inherit completed dialogue, active dialogue, ambience, music phase, SFX phase, current clip scope, continuity locks, reference roles, and reserved future beats. Do not repeat completed dialogue unless the user explicitly asks for a reprise. Continue or intentionally change the audio phase instead of restarting it by accident.

## Output Contract

Return speaker map, quoted dialogue, sound layers, audio reference role, lip-sync constraints, post/delivery notes if needed, and a compact prompt-ready audio block.

These are authored concepts, not observed generation results.
