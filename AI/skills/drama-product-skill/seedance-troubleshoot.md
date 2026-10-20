---
name: seedance-troubleshoot
description: "This skill should be used when a Seedance 2.0 output is blurry, jittery, off-prompt, morphing, blocked, visually generic, unstable, desynced, inconsistent, or otherwise fails and needs root-cause diagnosis."
---

# seedance-troubleshoot

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Record the failed criterion, observed or user-reported evidence, useful parts of the take, one plausible repair, and the remaining authorized limits. Diagnose before rewriting; repeated failures do not prove the prompt is wrong. Consider mode mismatch, conflicting references, ambiguity, unsupported controls, model variation, or missing preservation constraints. A review or rewrite request does not authorize another generation. At zero/exhausted budget, submit nothing; preserve the JSON duration and the reference roles. Do not add a resolution or a reference-count cap unless an already authorized change covers them.

When the diagnostic tree has no row for the failure, propose a testable hypothesis from the observed failure: conflicting priorities, ambiguous constraints, action timing, continuity state, reference roles, detail readability, or audio attribution. Do not present a diagnosis as proof of hidden model internals. When the failure involves continuation, an edit, a source clip, or sound, diagnose against continuity locks, completed beats, reference roles, and reserved future beats.

## Intent

A failed generation feels personal - the user showed the machine their idea and the machine returned something broken. The soul of this skill is rescue without blame: describe a plausible failure mechanism without blaming the user; preserve the idea as well as the prompt. They should leave with a fix and their confidence intact.

## Diagnostic Tree

| Symptom | Likely cause | First repair |
|---|---|---|
| Product or face changes | I2V prompt re-described visible identity or overloaded motion. | Add preservation constraints; remove duplicate static detail. |
| Camera jumps | Several incompatible moves or no endpoint. | Choose one move with start and finish. |
| Generic output | Hollow style words and weak action. | Replace with physical action, source light, material, and sound. |
| Motion ignored | Static prompt or no visible consequence. | Add actor, verb, timing, and changed end state. |
| Lip-sync poor | Moving head/camera, long dialogue, unassigned speaker. | Lock framing, shorten line, assign speaker. |
| VFX noisy | Effect has no source, physics, or dissipation. | Add source, material, path, interaction, and endpoint. |
| Prompt blocked | Protected IP, real-person, graphic, or bypass-like wording. | Rewrite intent in safe production language without evasion. |
| Extension quality degrades | The next shot changed too many things at once. | Keep the previous lock line and change one variable. Tail-frame continuation is already the handoff. |
| Audio reference ignored | Competing video sound, no visual beat mapping, or unsupported combo. | Mute competing video and map one visible event to the beat. |
| Text/logos break | Small text asked to move or be redrawn. | Keep text static, centered, and protected; animate light around it. |
| Client QC fails | Prompt output treated as final delivery without post/QC. | Route to delivery preflight, post fix, or regenerate only the failing shot. |
| Continuation assumed a later beat | The opening ignored the previous lock line. | Open on the previous shot's lock line. |
| Previous action restarted | Completed beat was not marked already_happened. | Add completed beat exclusion. |
| Future beat leaked | Reserved beat entered the current prompt. | Remove future beat and stop earlier. |
| Identity reference conflicts with continuity source | Source clip controlled transient state and identity at once. | Re-anchor identity from canonical reference. |
| Screen direction reset | Axis relation was not locked or intentionally reset. | Preserve screen direction or declare new-shot axis reset. |
| Open motion lost | Subject or camera vector was not inherited. | Carry motion vector into the opening sentence. |
| Camera phase restarted | Parent camera endpoint was not recorded. | Start from observed camera phase. |
| Prop state contradicted | Owner, position, or condition was missing. | Add prop state handoff. |
| Audio phase restarted | Completed dialogue or music phase was not logged. | Continue or intentionally change the audio phase. |

## Repair Process

First quote the failing phrase or missing element. Then state a plausible cause or testable hypothesis, with its uncertainty; call it a root cause only when the evidence establishes causation. Next, remove conflicts rather than adding complexity. Recommend one primary repair variable rather than adding more adjectives. Finally, produce one conservative retry prompt and one optional creative variant only if the user wants exploration.

## Conservative Retry Pattern

`[Reference role if any]. Preserve [identity/product/environment] exactly. One visible action: [specific verb and consequence]. Camera: [single move]. Lighting: [physical source]. Sound: [ambient/SFX/dialogue]. Constraints: [what must not change].`

## Escalation Rules

If the same error repeats, split the scene into shorter clips, reduce characters, simplify hand or face motion, use stronger reference role mapping, or change the mode. For unstable text/logos, keep them static, centered, and protected; do not ask the model to redraw small text during motion.

For edit/extend failures, preserve the source clip first and change only the failing layer. If a surface supports returned last frames, use that still as the next first-frame anchor before extending.

## Output Contract

Return the failed criterion, observed or user-reported evidence, a plausible cause or testable hypothesis with uncertainty, and a repaired prompt when useful. Add a conservative retry proposal only when it serves the diagnosis; preserve the user's existing settings and authorization. Do not present uncertain causation as a proven root cause or submit a generation merely because a revision was requested.
