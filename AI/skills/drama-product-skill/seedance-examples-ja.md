---
name: seedance-examples-ja
description: "This skill should be used when the user asks for Japanese Seedance 2.0 examples, Japanese prompt patterns, example rewrites, or safe versions of working Japanese video-generation prompts."
---

# seedance-examples-ja

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use Japanese examples as language-specific prompt patterns, not translated English templates.

## Intent

Japanese examples should feel like production notes a Japanese creator could actually use: polite where helpful, compact where the model needs clarity, and concrete enough that every emotion becomes framing, light, motion, sound, or post-production handling.

## Example Labels

| Label | Meaning |
|---|---|
| `safe` | Original concept, no protected identity. |
| `needs-owned-reference` | Requires user-owned, licensed, public-domain, or authorized asset. |
| `surface-specific` | Depends on the active web, API, or workflow surface. |
| `rewrite-required` | Mentions protected identity, brand, celebrity, exact scene, song, or voice. |

## Safe Example Patterns

**Product I2V:** `形状と色はアップロードに合わせ、商標文字は描かない。変化は小さな水滴が表面を下へ流れる動きと、左から横切る柔らかい暖色光だけ。Camera: locked product close-up, slow push-in. Sound: quiet room tone, one small glass tick at the end.`

**Portrait micro-performance:** `顔、髪型、衣装は定妆の references に置く。動きは小さく、一度まばたきし、視線を少し下げ、最後に控えめに微笑む。Camera: locked medium close-up, no reframing. Lighting: soft window light from frame right. Sound: quiet room tone.`

**Sequence clip 01:** `オリジナル人物Aが夜明けの駅ホームに入ってくる。「誰かを待つ」という目的が分かる最初の手がかりだけを見せる。Aは濡れた床を二歩歩き、折りたたまれた切符を見つけて拾わずに止まる。Camera: stable lateral tracking, medium-wide. このクリップでは列車到着や再会は見せない。`

**Continuation:** `前の採用済みクリップの終点から続ける。Aは切符の二歩手前で止まっている状態から開始し、ゆっくりしゃがんで切符を拾い、遠くのアナウンスに反応して顔を上げる。前の入場動作を繰り返さない。Camera: locked medium shot, slight push-in.`

**Dialogue:** `Character A sits at a cafe table in a locked medium close-up and softly says, "もう一度だけ。" セリフ中は頭を大きく動かさず、小さな口の動きだけ。Lighting: warm interior practical, cool rain reflection on wall. Sound: clear short dialogue, no music under the line.`

**Textless localization:** `縦型の画面。画幅は JSON 字段に書き、镜头正文には書かない。商品は中央、端に重要な動きなし。画面内に生成文字、字幕、広告コピーを入れない。`

## Rewrite Pattern

If the prompt contains protected names, rewrite the creative function into original Japanese descriptors: `有名キャラクターそのもの` becomes `オリジナルの仮面をつけた屋上配達員`; `特定作品そっくり` becomes `低彩度の夜景、硬いサイドライト、静かな演技、長焦点の圧縮感`.

## Output Contract

Return the Japanese example, label, risk note, and safer Japanese variant when needed. Put that prose into `script` and `shots[].prompt`. The delivery shape is the JSON in `写提示词.md`.
