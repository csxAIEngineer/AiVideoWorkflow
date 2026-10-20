---
name: seedance-interview-short
description: "This skill should be used when the user wants a fast Seedance 2.0 creative brief, a short interview, a compressed intake flow, or a quick director-style clarification before prompt writing."
---

# seedance-interview-short

On this machine this skill only decides which branch to ask. Ask one unanswered branch per turn. The recommended direction is the first option. The last option is 「其他」. The confirm option is 「就按这个做」. Do not put options inside `reply`. Do not ask aspect ratio; 9:16 is already fixed. Duration is `shots[].duration`, not a timestamp in the shot body.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use this when speed matters more than exhaustive creative discovery. The goal is to turn a vague idea into a compact director brief with no more than three questions, then route to prompt writing.

Speed does not bypass story judgment. read `seedance-directors-read.md` before producing the compact brief. Use its canonical classification and the corresponding internal record; do not duplicate or broaden the lane rules here. Never reconstruct the read from remembered craft.

## Intent

The user here knows what they want and is asking you to respect their momentum. The soul of this skill is restraint: find the one missing piece that would sink the generation, ask only that, and get out of the way. Speed is the form their trust takes.

## Process

These are the blocking questions. Ask only the next one that is still open, and only if the answer materially changes the prompt. Assume no film background: ask in everyday words, give pickable options, and attach a default so "I don't know" never stalls the brief. Prioritize:

1. What happens in the video, and what is different at the end? `(not sure? I'll pick one simple action with a visible ending)`
2. Do you have photos, clips, or sound that should define this clip's appearance, movement, or sound? `(none is fine; I'll draft from your idea)`
3. Only if the user asks for connected clips, a longer scene to divide, or continuation: how must the complete story end? The next shot starts from the previous lock line. Do not ask for an upload.

Unknown duration alone does not make a sequence. For one clip, ask at most one blocking question; otherwise state a reversible assumption and draft. Do not ask whether this should become a series before the first draft. Preserve information the user already supplied. Explicit connected-clip or continuation intent still activates sequence planning and its accepted-footage gate.

If the user already supplied enough information, do not ask. Produce a brief immediately. If the user speaks production language fluently, drop the plain phrasing and ask in director terms.

If the user gives explicit shot, lens, camera, blocking, or performance direction, keep it verbatim and compile it into a shot-contract-grade brief - never simplify or override a professional's spec. When direction is unresolved, draft with a reversible assumption or offer up to three brief-specific choices from that reference; this counts toward the existing question limit. Options change staging, attention, timing, or performance and state a real tradeoff. A “choose for me” reply means choose and draft now. Preserve chosen and rejected options, and skip the menu for a specified brief.

Even in fast mode, the brief states one motivated intention, not a generic "cinematic" look. For narrative work, derive it from the completed `seedance-directors-read.md` record and translate the turn, visible suppressed behavior, and non-transferable detail into filmable or audible carriers. For non-narrative work, serve the utility intent without inventing want, power, conflict, or subtext.

## Compact Brief Pattern

Internal lane record from the `seedance-directors-read.md`, then: `Mode: [T2V/I2V/V2V/R2V]. Subject: [anchor]. Beat: [before -> action -> final state]. Camera: [one move]. Light/style: [physical source and safe descriptor]. Sound: [dialogue/ambience/SFX/music/silence]. Constraints: [identity, IP, safety, product].`

## Routing Rule

Route to `seedance-sequence.md` when the user requests connected clips or a long story that needs multiple generations; `seedance-continuation.md` for accepted-footage continuation; `seedance-prompt.md` for a standalone production prompt, including one clip whose duration is undecided; `seedance-copyright.md` for IP/likeness risk; or `seedance-troubleshoot.md` when the user starts from a bad result.

## Output Contract

Return one compact brief under 150 words, any missing high-impact question, and a recommended skill route. When an optional choice is still pending, show the short options instead of a dossier; after selection, output only the chosen brief unless alternatives were requested. Choosing a direction does not authorize paid generation. Director's Read labels may stay in the brief and do not appear in `shots[].prompt`; show the internal record only when the user requests the planning rationale or when another agent needs the handoff. If the request is a sequence, include the complete story ending, likely clip count, current clip job, and the fact that future prompts stay provisional the next shot starts from the previous lock line, without waiting for a viewing pass.

## Pre-Delivery Screen

Screen the draft on the draft before it is shown. A beginner's brief often names the very things classifiers refuse (a knife, a wound, a break-in, a child in danger); keep the scene, drop the cue, and say in one line what changed.
