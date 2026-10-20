---
name: seedance-antislop
description: "This skill should be used when a Seedance 2.0 prompt contains generic AI filler, hollow superlatives, vague cinematic language, bloated adjectives, weak verbs, or needs sharper production-specific wording."
---

# seedance-antislop

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Clarify wording that hides an unresolved production decision. Preserve useful style, genre and mood language; concrete detail should support the user's direction, not replace it with a stock look. These are editorial choices, not measured rules about which words improve Seedance output.

## Intent

Keep the intended energy, medium, era, palette and performance. A word such as "epic" may communicate a useful ambition even when the brief still needs a decision about scale or stakes. Do not infer the user's taste or confidence from their adjectives.

## Rewrite Pass

1. Identify what is already decided: creative intent, exact dialogue, actual reference tokens, continuity, safety boundaries, exclusions and requested settings. Preserve those through the edit.
2. Check whether an abstract phrase leaves a material choice unresolved. Keep it when context is sufficient. Otherwise use the brief to clarify it; if the user has delegated creative choices, propose a coherent interpretation and state any material assumption. Ask only when a necessary choice remains unresolved outside that scope.
3. Remove redundant praise where it adds no distinct intent. Surface conflicting requirements instead of silently choosing one. Add only details that serve this brief; neither a camera move nor a lighting recipe is required for every style word.
4. Apply a length limit only when the user requests it or the selected operation documents it. Do not invent a universal character budget or silently discard must-haves to fit one.

## Do Not Over-Correct

Keep useful labels such as noir, documentary or ultra-realistic when they describe the chosen look. Observable detail is a way to resolve ambiguity, not a test that every phrase must pass. A locked shot may already serve an energetic scene; "dynamic" does not automatically authorize a lateral track. Likewise, "professional" does not require a clean product tabletop.

Do not put a resolution target or a reference-count cap into the prompt.

These are contextual wording examples, not language-wide lists of useless words.

## Output Contract

Return the revised prompt in the requested format and briefly explain material edits when useful. Keep delivery settings separate from scene prose, retaining any unresolved requirement. Disclose content changes or assumptions; a wording rewrite does not authorize another submission. Provide a word-by-word removal table only when requested or needed for the review.
