---
name: seedance-continuation
description: "This skill should be used when a Seedance 2.0 user asks to continue, extend, make the next part, repair the tail, bridge between known frames, re-anchor drift, or create a successor prompt from accepted footage."
---

# seedance-continuation

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use this for seamless continuation, intentional next shots, bridge clips, tail repair, and re-anchoring after drift. A continuation prompt starts from the previous shot's lock line, not from a later beat.

## Intent

The next shot continues from the previous shot's lock line, not from a later beat the plan hoped to reach. Do not ask the user to watch a clip before the next shot is written. This machine uses the tail frame as the handoff.

## Required Input Gate

Before writing the next shot, require:

- the previous shot's lock line, which is this shot's opening;
- what this shot does, and which later beat it must not play;
- continuity locks: face, wardrobe, product shape, place, and which side the camera is on;
- the visible or audible carriers for this shot. Internal labels stay in the brief.

The previous shot's lock line is the source. Do not ask for an upload, and do not wait for someone to watch the clip. If that lock line is missing, write it from the brief before drafting the next shot. Do not invent a later beat and play it now.

After the source gate is satisfied and before the next prompt is compiled, classify the current clip with the `seedance-directors-read.md`. Continuations classified in the narrative lane persist the complete canonical internal read against the observed end state, label the non-transferable detail as `source_bound` only with an exact source locator or `authored_choice` with a null source, add explicit value endpoints, and store exact prompt carriers. Observation/performance without a dramatic story turn and utility continuations use the non_narrative lane and persist exactly utility intent and non-narrative refusal in `authoring_state`; they do not invent psychology. Translate a narrative read into blocking, visible suppressed behavior, the chosen replacement for the refused stock move, camera endpoint, light, and sound; never paste internal labels into `shots[].prompt`; they may stay in the brief.

## Continuation Types

`seamless_continuation`: same shot, same geography, same open motion, and the previous lock line as the opening.

`intentional_next_shot`: an editorial cut is appropriate. Story continuity matters, but exact frame continuity is not promised. Do not call it seamless.

`bridge_between_known_states`: a defined start state and end state must be connected, often with first/last-frame generation when the active surface supports it.

`repair_tail`: the previous final seconds failed. Repair, edit, or regenerate the tail before continuing because continuing from a failed tail amplifies the error.

`reanchor_after_drift`: identity, detail, geography, motion, audio, or world continuity degraded. Return to canonical identity, the strongest accepted final frame, a stable source clip, or a new intentional shot using canonical references.

## Scene Boundary Rule

Crossing a scene boundary defaults to `intentional_next_shot` opening from canonical references. Do not promise `seamless_continuation` across a scene boundary; if the user explicitly asks for one, record the reason and treat the result as high drift risk.

## Canon Rule

The previous shot's lock line overrides a hoped-for later state. If that lock line leaves the subject two steps from the door, the next shot starts two steps from the door. It does not replay the finished walk, and it does not start inside the car.

Rejected footage never updates canon and never becomes a parent source. Pixels can confirm a carrier, not authored psychology. If an accepted deviation changes the turn, preserve the historical planned contract, reconcile the nearest narrative ancestor's `value_after` with the successor's `value_before` across utility inserts, then revise the next obstacle/tactic and carriers before prompt compilation.

Track `extension_depth` as consecutive output-sourced generations since the last canonical re-anchor; it resets to 0 when a clip opens from canonical references. At the scene's `max_chain_depth` (default 2, hard ceiling 3), re-anchor by schedule instead of extending again. Visible drift before the cap is an immediate `reanchor_after_drift`.

## Output Contract

Return:

1. Continuation type.
2. Source evidence used.
3. Observed end state.
4. Next clip contract, including the internal authoring handoff when applicable.
5. Intent echo: one line - "this clip exists so the viewer feels X" - confirmed before generation spends money.
6. Continuity locks and allowed changes.
7. Completed beats to exclude.
8. Reserved future beats to exclude.
9. Final natural-language Seedance prompt for the current clip only; compile the exact visible or audible carriers, never the internal authoring labels or explanations.
10. Updated Project State Capsule or a request for missing source evidence.

## 本机镜头续接

本文件用在镜头之间。上一镜的锁句是下一镜的起点。不提前演后面的拍。本机用尾帧续接，不另做「生成后等人看片再写」的循环。
