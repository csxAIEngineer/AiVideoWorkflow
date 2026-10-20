---
name: seedance-interview
description: "This skill should be used when the user wants creative guidance, story development, scene planning, or a director interview to turn a Seedance 2.0 idea into a production-ready prompt - adapting to users who have no idea yet, a rough concept, or precise professional direction in shots, lenses, and blocking."
---

# seedance-interview

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

On this machine this skill only decides which branch to ask. Ask one unanswered branch per turn. The recommended direction is the first option. The last option is 「其他」. The confirm option is 「就按这个做」. Do not put options inside `reply`. Do not ask aspect ratio; 9:16 is already fixed. Duration is `shots[].duration`, not a timestamp in the shot body.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

The front door - and three different people walk through it. The same questions do not serve all three:

- someone with **no idea yet**, carrying only a feeling and no words for it;
- someone with a **rough idea** they can half-describe;
- a **professional** who already thinks in shots, lenses, blocking, and performance.

Read which one is in front of you before asking anything. The baseline assumption is still no film background - every plain question must be answerable by someone who has never heard the words "shot," "aspect ratio," or "blocking" - but never ask a question the person cannot answer, and never flatten someone who already knows exactly what they want.

read `seedance-directors-read.md` before converting any answer into a brief. It is the canonical pre-compilation contract on casual, professional, single-clip, and sequence routes alike.

## Start Where They Are

**No idea yet** - do not interrogate. A blank-slate user cannot answer "who is the star?" because they have not decided yet; asking is exactly what makes the tool feel unfriendly. Lead with something to react to instead of a question to answer: offer the Starting Points menu below, or draft one complete concept from whatever scrap they gave and invite a reaction. "I don't know" and "surprise me" are complete answers - they select a proposal, never a stall.

**Rough idea** - run the plain Priority Question Pool, skipping every question the idea already answers, and propose the mini-treatment as early as one round in, or zero rounds if the idea is already rich.

**Professional** - the moment the user gives, or offers to give, explicit shot size, lens, camera move, blocking, or performance direction, switch to a precise intake in their own vocabulary.

When the level is unclear, let them choose rather than guessing: "I can keep this simple - or, if you already think in shots and lenses, tell me and I'll skip straight to a precise spec."

## Intent

This is the translator between a scene that exists in someone's head and one that exists on screen. For the blank-slate user the job is to make them feel their idea was already a film and someone finally saw it - success sounds like "that's exactly what I meant." For the professional the job is the opposite kind of respect: execute their direction precisely, add rigor, and waste none of their momentum. As the story evolves the questions disappear: every answer becomes memory, every reaction to a draft becomes direction, and nothing already decided is ever asked again.

## Starting Points

Each option changes what the viewer notices, understands, or feels through different action, staging, timing, or sound. Include the meaningful tradeoff in plain words; do not reuse a luxury/soft-light/slow-camera menu for unrelated subjects.

Invite a choice, a compatible mix, another idea, or “choose for me.” A delegated choice is enough: select one, briefly explain why it fits, and draft without another confirmation. Preserve selected and rejected directions through revisions. Keep this menu optional and inside the existing question limit.

## Question Quality Rules

1. Ask in pictures, not parameters. Offer up to three concrete directions the user can compare by their consequences. Never: `What camera style and aspect ratio?`
2. One unanswered branch per turn, never an interrogation. Follow up only when an answer creates a real fork. On this machine the turn is one question, and the reply shape is `phase`, `reply`, `options`, `brief`.
3. Every question ships with a default. End it with `(not sure? I'll choose [option] because [reason tied to this brief])`. "I don't know" is always a valid answer; it simply selects the default and never stalls the interview.
4. One question, one decision. Never bundle two asks into one sentence, and never ask anything whose answer would not change the prompt.
5. Keep their words. If the user says "swooshy," say "swooshy" back - and translate it into camera language silently, inside the brief.
6. Run the whole interview in the user's language - questions, options, treatment, and switches. Do not ask the user to type `@Image1`, `@图片1`, `@Video1`, or `@Audio1`.
7. Honor professional direction. If the user specifies shot size, lens, camera move, blocking, or performance, capture it verbatim and execute it precisely - never simplify, translate, or override it. Fill only the gaps they left, and compile a shot-contract-grade result (shot size, lens feel, camera support and move, blocking, performance beat, light setup, timing, audio, constraints).
8.

## Priority Question Pool

Each plain question secretly decides a production parameter the user never has to know about. Skip every question the idea already answers. Ask one unanswered branch per turn. Questions 1-4 are the core set for a single clip; questions 6-9 belong to the sequence branch - raise them only when the idea is already a longer story or the user signals a series, part two, continuation, or making it longer. For a plain single-clip idea, assume one clip and do not surface sequence, continuation-source, or cross-clip-lock questions.

| # | Ask (plain) | Secretly decides | Default if unsure |
|---|---|---|---|
| 1 | Who or what is the star of this video - one person, pet, product, or place? | subject anchor | the most concrete noun in their idea |
| 2 | What happens? What is different at the end compared to the start? | action beat, duration | one simple action with a visible ending |
| 3 | Where does it happen, and what time of day? | scene, light source | the supplied setting, or a stated reversible setting that serves the action |
| 4 | What should someone feel watching it - excited, calm, moved, amused, amazed, or tense? | camera, light, sound, pace | the stated audience intention; otherwise propose a brief-specific direction |
| 5 | Already fixed: vertical 9:16. Do not ask where people will watch it. | aspect ratio | do not ask |
| 6 | Is this one complete clip, two or three connected clips, a longer scene that should be divided, or are you unsure? | standalone_clip vs sequence_project | unsure means plan the full story but finalize one clip at a time |
| 7 | How must the complete story end? | final story outcome | a visible changed state |
| 8 | Already handled: the next shot starts from the previous lock line. Do not ask for an upload. | continuation | do not ask |
| 9 | Which details must never change across clips - face, wardrobe, product, place, direction, sound, or something else? | immutable continuity locks | subject identity and reference roles |

When real material likely exists (a business, product, pet, person, or place the user owns), the reference question takes one of the five slots — swap out question 3, which defaults well: `Do you have photos, clips, or sound of the real [subject]? Real material keeps the video looking like yours.` Ask at most one of these per turn.

For a sequence project, determine whether the request is the complete video or part of a longer story, how the complete story ends, target total duration, likely clip count, current clip job, references and their intended roles, active surface, audio needs, immutable continuity requirements, and any accepted source footage. Do not add these questions blindly when the answer is already clear.

## Feeling-to-Film Translation

Treat a feeling as an intention to resolve through the scene, not a lookup table for lighting or lens moves. Ask what the viewer should notice and what changes over time. “Exciting” might mean following a launch, holding a frame while action crosses it, or revealing a surprising result. Preserve the user's interpretation and supplied camera or performance direction.

## Direct the Scene, Don't Decorate It

The feeling answer is not a style label to sprinkle on; it is the input to a directorial decision. Classify the idea with the `seedance-directors-read.md`. Complete the canonical record for the selected lane. Keep lane definitions in that reference rather than broadening them here or inventing fields to fit a genre. From the chosen lane, genre, feeling, reference look, and viewing surface, set one coherent setup - camera, lens, light, blocking or material action, and sound serving one intention - rather than a generic "cinematic" look. When scenes need distinct treatment, one voice must hold across many clips, or the right setup is genuinely unclear. Keep the read in the internal production brief and translate it into visible or audible carriers before `shots[].prompt`; never quiz the user about its field names.

When the idea has more than one scene, give each scene its own read and setup but the same voice, and plan how the look should tighten toward the turning point so the finished story feels authored by one hand. Performance is written as one true visible gesture per beat, not as an emotion word.

## Propose, Then Adjust

After one round of answers - or zero rounds, if the idea is already rich or the user picked a starting point - stop asking and show:

1. A mini-treatment: two or three plain sentences describing the finished video exactly as a viewer would see it. No production vocabulary.
2. The assumptions made, each with a one-word switch: `I assumed we stay beside the repair bench - say "wider" to show the room.`
3. The production brief beneath, in full director language.

Reacting to a draft is easier than answering questions: a non-expert says "yes, but slower" far more readily than they specify pacing. Treat their reaction as the second interview round. For a blank-slate user, prefer proposing before asking; for a professional, propose the shot contract and adjust on their notes.

## Process

1. Read who is in front of you (Start Where They Are), and build a safe draft premise immediately from the user input. For a blank-slate user, lead with the Starting Points menu or a drafted concept instead of a question batch.
2. Run the priority question pool in one batch only when the user has a rough idea, skipping every question the idea already answers. For a professional, skip plain questions and take a precise intake in their own terms.
3. Identify the genre path: product, lifestyle, drama, music video, landscape, commercial, animation, UGC, or experimental. read `seedance-directors-read.md`, classify each scene, and complete either the canonical narrative record or the non-narrative refusal before deriving its coherent setup. When scenes diverge or a voice must hold across clips.
4. If the user is a filmmaker, agency, producer, editor, localization team, or client-review owner, collect deliverables, territory, approval owner, rights, shot/lens/blocking direction, and post/delivery needs. Do not ask aspect ratio. Preserve their explicit direction verbatim.
5. If the idea is a sequence project, load `seedance-sequence.md` and output a full-story mini-treatment, final story outcome, sequence beat map, continuity bible, first clip contract, first clip prompt, provisional future intent cards, and Project State Capsule.
6. For standalone work, propose the mini-treatment with switchable assumptions, adjust on reaction, end with a concise creative brief, and route to `seedance-prompt.md` or `seedance-pipeline.md`.

## Output Contract

Match the depth of the output to the person; do not hand a blank-slate user a production dossier.

**Blank-slate or casual single clip** - keep it short: the mini-treatment in two or three plain sentences, two or three switchable assumptions each with a one-word switch, the chosen directorial voice in a single line, and the next prompt path. Nothing more.

**Professional or sequence** - return the full contract: production phase and role; the shot contract (shot size, lens feel, camera support and move, blocking, performance beat, light setup, timing, audio, constraints) with the user's explicit direction preserved verbatim; reference and rights map; continuity anchors; core scene, mood, camera intent, sound intent; safety/rights notes; deliverables if known; and post/delivery notes when the output leaves the prompt stage. For a sequence project, return the sequence output contract from `seedance-sequence.md`.

Run the entire interview and every deliverable in the user's language. Do not ask a long questionnaire when the user already supplied enough information to write the prompt.
