---
name: seedance-prompt
description: "This skill should be used when the user asks to write, improve, translate, or debug a Seedance 2.0 video prompt; mentions T2V, I2V, V2V, R2V, camera direction, prompt quality, or provides reference assets for a production-ready prompt."
---

# seedance-prompt

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Build production-ready Seedance prompts from clear concepts or supplied reference assets. Treat the prompt as a shooting brief written as fully as the scene can be seen and heard: what changes on screen, what the camera does, what the light and sound contribute, and what must stay stable. Keep every visible and audible detail. Remove only empty quality words before delivery.

When sequence state is present, compile only the current clip contract.

## Intent

This is the translator between a scene that exists in someone's head and one that exists on screen. The user has already imagined it; the job is to lose as little as possible in transit. Success is a first generation close enough that they can react instead of explain. Each revision inherits everything the story has already decided and changes only what the reaction asked for - a draft is a conversation, not a restart.

## Director Formula

Before filling slots, run the `seedance-directors-read.md` gate. A brief with a dramatic story turn must have all ten canonical narrative fields completed. Observation/performance without that turn and utility briefs use the two-line non-narrative intent and refusal, with no fabricated psychology. Preserve expressive movement, rhythm, or delight when requested. For narrative work, name a single intention from the read and let it choose camera, lighting, blocking, performance, and sound together. When the setup needs its deeper instrument and voice logic. The formula below is the container for a coherent setup, not a checklist of independent decorations; if a project voice is already set, keep this shot inside it.

The Director's Read is internal planning, not prompt copy. For narrative work, compile its turn, visible suppressed behavior, and non-transferable detail into visible or audible carriers. For observation/performance or utility in the non-narrative lane, carry the requested action, rhythm, information, or sensory change without inventing those dramatic fields. Do not paste labels such as `POV`, `power shift`, `hidden want`, or `subtext` into `shots[].prompt`. They may stay in the brief. and do not ship an abstract feeling where blocking, gesture, prop use, camera endpoint, motivated light, dialogue contradiction, silence, or a sound cue can carry it.

Use `Subject + Action + Scene + Camera + Lighting/Style + Audio + Constraints`. Put the subject and primary action first because early clauses set the shot hierarchy. Do not force every slot if a reference asset already shows the information; for I2V, describe only the motion, camera, timing, transformation, audio, and preservation constraints that the still image cannot show.

| Slot | Use for | Prompt-ready pattern |
|---|---|---|
| Subject | The anchor the model must track. | `Original ceramic perfume bottle on black acrylic, shape and color preserved` |
| Action | The visible change. | `condensation beads form and slide down the glass` |
| Scene | Only what is not already in references. | `quiet rain-lit kitchen counter, shallow depth of field` |
| Camera | One primary move with endpoint. | `slow dolly-in from medium product shot to macro label detail` |
| Light and style | Physical light plus safe visual language. | `warm practical key from frame left, cool blue rim, clean commercial realism` |
| Audio | Ambient bed, SFX, dialogue, or silence. | `Sound: low room tone, soft glass chime on final frame` |
| Constraints | Preservation and exclusions. | `do not alter shape or material, and do not draw trademark lettering` |

## Mode Gate

Choose the mode before drafting. **T2V** needs subject, action, scene, camera, light, style, and constraints because nothing is visible yet. **I2V** keeps the uploaded identity in `references` and adds only motion, camera, lighting transition, audio, and preservation. **V2V** names the motion, camera, rhythm, or blocking to keep, and does not transfer identity by accident. **R2V** gives each upload one role and states what must not transfer. **FLF2V** on this machine means the previous lock line is the start and the end state is written as behavior, not as an `@` tag.

| Mode | Drafting priority | Common mistake | Repair |
|---|---|---|---|
| T2V | Build the whole shot in ordered layers, and keep the visible and audible detail. | Too many events in one clip. | Keep one visible beat and one endpoint. |
| I2V | Preserve visible identity; add motion. | Re-describing the image until the product or face drifts. | Leave identity in `references`. Add only dynamic changes. |
| V2V | Transfer motion, camera, or timing. | Copying unauthorized likeness or scene details. | Use owned/licensed/authorized references and restrict transfer role. |
| R2V | Assign separate roles to each asset. | One reference asked to control identity, pose, scene, and style. | Split roles or prioritize the most important role. |
| FLF2V | Move from the previous lock line to an end state. | Treating the end as a vague mood. | Write the visible end state. |
| Edit | Preserve the shot while changing one layer. | Rewriting the whole scene and losing continuity. | Say which single layer changes. |
| Extend | Continue from accepted source footage only. | Starting from a planned ending or inventing the clip state. | Route to `seedance-continuation.md`. The next shot starts from the previous lock line. |

## Time Structure

Seedance 2.0 keys on shot order, not seconds (official guidance recorded 2026-09-26; sources and the full rule in `seedance-multishot-grammar.md`). Classify the shape first. One scene with one continuous action or state change is **continuous** and compiles as one paragraph with no shot labels, whatever its duration. Several events, a location change, a reveal that needs a cut, or a comparison is a **storyboard** and compiles as numbered shots in event order, each block ordered camera move or cut, action and expression, space change, audio, with the cut written in words. Never write absolute seconds such as `0–3秒` inside a shot. Duration and 9:16 belong in JSON fields. `shots[].duration` is an integer from 4 to 15. A felt length is written as behaviour.

For a storyboard clip, score the load (a camera move, each spoken line, extra people, contact that must land, a location change) and place the requested shot count on the ladder: **Safe**, **Stretch**, or **Ambitious**. When the shot count is open, present up to three rungs with the trade-off in one line each, recommend one (Safe on a first attempt or a last credit, Stretch when the user has room to iterate), say once that the thresholds are this skill's heuristics and that no official shot ceiling exists, and write one finished prompt at the recommended or chosen rung; other rungs are written on request, and "choose for me" means draft the recommendation. When the user has fixed the count, write it and state its rung in one line. Ambitious is never called broken; two shorter generations are offered as the better spend. A request for timestamps on the newer model line stays inside this boundary: keep the craft, withhold the numbers.

## Sequence Boundary

The generic prompt skill must not independently invent continuation state. If the user asks to continue, extend, make part two, or use a previous clip, route to `seedance-continuation.md`. The next shot starts from the previous shot's lock line. Do not wait for a viewing pass.

For sequence prompts, preserve `project_id`, `clip_id`, `parent_clip_id`, continuity locks, reference roles, the actual opening state, completed beat exclusions, and reserved future beats. The final prompt remains natural language and covers only the current clip.

## Prompt Build Process

First, read `seedance-directors-read.md`, classify the brief, and complete the correct lane record before prompt compilation. For a narrative lane, identify the single visible beat and the intention it serves, then map the read to carriers; for a non-narrative lane, keep the concrete utility intent and refuse invented drama. Next, assign reference roles before adding adjectives. For a storyboard, or any clip with more than one person, before the prose, write: the floor plan in words, then one row per shot with the camera's side, who is in frame and where and facing, the eye-line, the one action, everyone else's idle business, the light and the last frame; run the paper render on it, and only then write the draft, rendering each row into one block in the director formula order. A table with a blank or default cell is not ready to become prose. For a continuous single-subject clip, write a first draft in the director formula order and keep the visible and audible detail. Finally, run a self-check and, when loaded, this coherence check: one main subject, one main action, one motivated main camera move, physically motivated lighting, performance written as a visible gesture rather than an emotion word, assigned character tags, sound intent, and no hollow boosters.

## Detail Rules

Keep what can be seen and what can be heard: the cut, how hands and bodies move, each other person's small action, what the room shows on this beat, the sound, and the lock line. Cut only empty quality words such as "cinematic", 「电影感」, and 「高级」. Do not drop a background detail, a secondary action, or a camera move to make the prompt shorter. Do not offer a compressed version. If a user requests a bilingual or mixed-language prompt, use language mixing only for clarity: reference roles, dialogue language, technical camera terms, and safe production constraints. Do not use another language to hide unsafe intent.

## Pre-Delivery Screen

Before the prompt is handed over, screen the draft. List every noun and verb that names a weapon, an injury, a crime, a minor together with danger, a legal, police or medical setting or document, a substance, a horror figure, or a real identity; remove it, move it off frame, or replace it with its consequence; re-read for stacking; keep the exact dialogue; state the change in one line. The turn, the suppressed behaviour and the non-transferable detail never depend on the cue word, so the drama survives the rewrite. Prohibited content is refused through `seedance-filter.md`, never reworded.

Before the screen, run the eight-point check in `seedance-direct-for-the-model.md`: every reaction a plain feeling plus one physical anchor, never an idiom and never a bare list of muscles; one action per shot with everyone else kept alive in their idle business, never frozen; a lock line at the end of every shot repeating the light, each principal, where they stand, which way they face and which side the camera is on, with reactions written as reverses and every crossing of the room given its own shot; prop actions written as a hand, not a verb; an ending the frame can contain or a move or cut to reach it; every line given a voice and eyes rather than "flat". Two physical rules belong to the same pass. Never write an involuntary outcome as the endpoint (a coat caught in a door, a slip, a spill): the model stages it as a deliberate act by the character, so write the deliberate action or leave the consequence off screen. Keep spectacle in what the model renders well, weather, light, cloth, dust, water, crowds and fire at a distance, drawn effects in 2D, with any contact simple and singular. For a short-drama hook, an action beat or a showcase clip, use this: stakes in frame, a force against the character, escalation inside the clip, one visual peak, the premise spoken or shown by shot two, and coverage of four or five shots with a reaction shot counted at half a beat.

## Output Contract

Return:

1. Mode: T2V, I2V, V2V, R2V, FLF2V, edit, or extend.
2. Reference role map, if any.
3. Shot prose that will go into `script` and `shots[].prompt`.
4. For a storyboard clip with an open shot count: the ladder (up to three rungs, one trade-off line each), the recommendation, and the evidence-tier sentence; the final prompt is the recommended or chosen rung.
5. For a storyboard or multi-person clip: the shot table the prompt was rendered from (floor plan, then one row per shot: camera side, in frame and facing, eye-line, action, others, light, last frame), delivered beneath the prompt, collapsed where the surface allows, so the geometry can be checked on paper before a take is paid for.
6. Shot-list or delivery note when the prompt belongs to a professional sequence.
7. Safety or copyright note when relevant.
8. Screen note: one line naming any wording the pre-screen changed, or that it changed nothing.

Before finalizing, run an anti-slop pass and remove only empty quality words. Keep the visible and audible detail. Do not add a compressed version.

If this skill asks for one plain-text prompt, the delivery is still the JSON in `写提示词.md`. Put the director prose into `script` and `shots[].prompt`.
