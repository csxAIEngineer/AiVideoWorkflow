---
name: seedance-examples-zh
description: "This skill should be used when the user asks for Chinese Seedance 2.0 examples, Chinese prompt patterns, example rewrites, or safe versions of working Chinese video-generation prompts."
---

# seedance-examples-zh

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use examples as language-specific patterns, not content to copy blindly. They are not a language certification. Chinese prompts should stay compact, concrete, and production-oriented.

## Intent

Some users learn by rule, but the ones who come here learn by seeing. The purpose of these examples is recognition - a working prompt close enough to their idea that they can feel the gap and close it themselves. An example that teaches is worth ten instructions.

## Example Labels

| Label | Meaning |
|---|---|
| `safe` | Original concept, no protected identity. |
| `needs-owned-reference` | Requires user-owned, licensed, public-domain, or authorized asset. |
| `surface-specific` | Depends on the active web, API, or workflow surface. |
| `rewrite-required` | Mentions protected identity, brand, celebrity, exact scene, song, or voice. |

## Safe Example Patterns

**Product I2V:** `瓶身形状和颜色跟上传图，商标文字不写进画面。镜头缓慢推进；左侧暖光扫过玻璃，水珠沿瓶身下滑，停在瓶身轮廓上。背景保持暗色静止。声音：轻微环境声，结尾一声清脆玻璃音。`

**Character drama:** `原创角色A站在狭窄走廊，缓慢放下信封，目光停在紧闭的门上。镜头为稳定中近景并轻微推进。暖色顶灯微微闪烁，墙面有冷色雨光反射。声音：远处雨声，无配乐。`

**Action:** `原创快递员在雨夜屋顶奔跑，跃过一道狭窄缝隙，落地后单膝撑住并冲向即将关闭的铁门。低机位手持跟拍，冷色屋顶灯和湿滑地面反光。声音：急促呼吸、雨声、铁门提示音。`

**Safe animation:** `原创二维动画沙漠信使，夸张围巾，驾驶小型风力木车穿过浅色沙丘。手绘背景质感，圆润角色造型，柔和粉彩色调，车轮带出细小沙尘。镜头侧向稳定跟拍。`

**First/last frame:** `上一镜的锁句是起点。保持同一原创角色、服装和房间布局，角色从椅子上缓慢站起，走到窗边并停住。动作连续，不改变脸、衣服或房间。镜头固定中景，仅轻微推镜。声音：安静室内环境声。`

**Sequence Clip 01:** `原创角色A在清晨机场出口停下，手里握着一张折起的纸条。本段只建立“她在等待某人”的线索，不出现重逢或离开。镜头横向稳定跟拍到中景，A看向到达屏并停住。声音：机场环境声、远处广播、无配乐。`

**Continuation:** `从上一段已接受视频的真实结尾继续：A站在到达屏前，纸条仍在右手。本段只让A打开纸条，看到名字后向画面左侧抬头，不重复走出机场的动作，不提前出现对方。镜头固定中近景，轻微推镜。`

**Camera-reference:** `定妆只锁人和服装，不锁运镜。角色在雨夜街道从画面左侧跑入，停在路灯下回头。镜头横向稳定跟拍，最后固定中景。冷色雨光，地面反射霓虹。声音：雨声、脚步声，无配乐。`

**Dialogue:** `原创角色A坐在厨房桌前，固定中近景。对白期间头部保持稳定，只做轻微表情变化。角色轻声说：“我找到钥匙了。”柔和窗光来自画面右侧，背景有暖色台灯。声音：清晰短对白、低室内环境声、无配乐。`

## Rewrite Pattern

If the prompt contains protected names, translate the intent into original descriptors: `知名IP角色` becomes `原创蒙面屋顶信使`; `某导演风格` becomes `低饱和胶片质感、硬侧光、长焦压缩空间、静默表演`.

## Output Contract

Return the Chinese example, label, risk note, and safer Chinese variant when needed. Put that prose into `script` and `shots[].prompt`. The delivery shape is the JSON in `写提示词.md`.
