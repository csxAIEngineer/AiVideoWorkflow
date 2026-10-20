---
name: seedance-camera
description: "This skill should be used when the user asks for camera movement, shot scale, lens feel, framing, one-take direction, dolly, pan, tilt, push-in, handheld, aerial, macro, or camera-transfer guidance for Seedance 2.0."
---

# seedance-camera

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use one clear camera idea per short clip unless the user asks for a multi-shot sequence. The best camera direction has a start frame, movement, speed, subject relationship, and endpoint. Avoid stacking moves that fight each other, such as drone rise, dolly-in, handheld shake, and orbit in the same five-second shot.

## Intent

When a user asks about camera, they are really asking where the viewer's body stands and what the viewer is made to feel from there. Camera grammar is empathy mechanics: a push-in is leaning closer, a locked frame is holding your breath. Choose the move that puts the audience where the user's feeling lives.

## Camera Contract

State: shot scale, angle, movement, speed, subject relationship, and endpoint. A prompt-ready camera phrase should be physically possible and tied to the subject's action.

| Need | Strong phrase | Avoid |
|---|---|---|
| Emotional realization | `slow dolly-in from medium close-up to tight close-up as Character A lowers the envelope` | `dramatic cinematic zoom` |
| Product reveal | `controlled slider move from silhouette to front three-quarter hero angle, ending on the label` | `dynamic product camera` |
| Scale | `low-angle crane up from boots to skyline, ending behind the character's shoulder` | `epic wide moving shot` |
| Instability | `subtle handheld shoulder camera, small breathing sway, subject kept centered` | `shaky chaotic camera everywhere` |
| Precision detail | `locked macro shot, focus stays on the watch gears while the second hand clicks once` | `cool close-up details` |

## Lens and Framing Anchors

Use lens anchors only when they improve direction: `24mm wide lens for spatial energy`, `35mm natural street perspective`, `50mm portrait compression`, `85mm shallow close-up`, or `macro lens for material detail`. Pair lens words with subject distance and motion; do not stack lens numbers as decoration.

## Move Selection

Use **locked-off** shots for lip-sync, product identity, and delicate VFX. Use **dolly-in** for discovery or realization. Use **tracking** for travel, pursuit, and product motion. Use **orbit** only when the subject can remain clear from all sides. Use **crane or drone** for scale, arrival, or reveal. Use **handheld** only when realism matters more than precision.

## Continuity Rules

For multi-character scenes, anchor the camera to named tags: `camera holds Character A in foreground while Character B crosses behind`. For I2V, preserve the image composition unless the user explicitly wants a reframing. For a motion reference, say whether the new shot borrows camera movement, action rhythm, or blocking. Do not let it transfer identity unless authorized.

For complex camera movement, a video reference often works better than a long verbal stack. Write the camera rhythm in words. Do not transfer performer, room, or identity.

## Conflict Rule

If the user gives several incompatible moves, choose one primary camera move and put the rest into optional variants. If the shot needs multiple beats, recommend splitting into separate clips or a time-segmented prompt.

## Sequence State

When sequence state is present, inherit the observed camera phase, screen direction, current clip scope, continuity locks, reference roles, and reserved future beats before choosing a move. A continuation camera phrase must begin from the accepted source frame or observed end state; do not restart a pan, focus pull, or tracking move unless an intentional next shot declares the reset.

## Output Contract

Return the selected camera phrase, why it fits the shot, conflicts removed, fragile anchors, endpoint, and a prompt-ready integrated sentence.
