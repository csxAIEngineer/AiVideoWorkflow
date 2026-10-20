---
name: seedance-vocab-zh
description: "This skill should be used when the user asks for Chinese Seedance 2.0 prompt wording, Mandarin cinematic vocabulary, Chinese prompt compression, or translation of camera, lighting, action, VFX, audio, and production terms into Chinese."
---

# seedance-vocab-zh

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use Chinese vocabulary when the user asks for Chinese prompts, Mandarin cinematic wording, role binding, first/last-frame workflow, or maximum compactness. Chinese prompt wording is often efficient, but it must still preserve mode, action, camera, lighting, audio, and constraints.

## Intent

Chinese production wording can use compact compounds, but compression is useful only when the shot instruction remains explicit. Keep each concise phrase tied to a visible action, camera behavior, light source, sound, or preservation constraint. The shipped independent review artifact is empty, so treat these choices as working production wording pending locale-specialist review.

## Usage Rule

Use short production phrases instead of abstract adjectives.

A written example is not an attached file.

| Function | Chinese wording |
|---|---|
| Camera | `缓慢推镜`, `横向跟拍`, `固定中景`, `低角度`, `特写`, `从剪影到正面四分之三角度` |
| Lighting | `侧逆光`, `柔和窗光`, `暖色实用灯`, `冷色月光`, `轮廓光`, `体积光` |
| Motion | `慢慢转身`, `快速掠过画面`, `水珠沿表面下滑`, `薄雾贴地扩散` |
| Audio | `安静环境声`, `一句短对白`, `轻微金属声`, `无配乐`, `脚步声卡点` |
| First/last frame | `上一镜锁句是下一镜起点`, `自然过渡`, `中间动作连续，不跳切`。镜头正文不写 `@图片1`。 |
| Constraints | `严格保持形状和颜色，不画商标文字` |

## Compact Pattern

`上传图只写入 references。镜头正文只写一个动作、光线和机位变化，并严格保持外形。声音：[音效或环境声]。`

## Script Variant Rule

Preserve the user's chosen prompt script, subtitle script, locale and intended voice as separate decisions. Do not force Simplified prompting or infer a voice from written script. Keep exact dialogue unchanged; convert or localize only the text the user asked to adapt. If a missing choice materially affects that deliverable, ask one focused question or use the user's delegated choice. Never convert by find-and-replace (头发 → 頭髮, not 頭發).

## De-Slop Rule

Preserve a useful style or aesthetic term and add only the scene-specific action, framing, light, or sound needed to clarify it. The table offers authored options, not a default look or a proven model-performance rule. Keep the user's chosen energy, dialogue, and settings. Offer distinct choices only when the ambiguity changes the scene; if the user delegated the choice, decide within that scope.

## Output Contract

Return concise Chinese prompt text, optional English gloss when useful.
