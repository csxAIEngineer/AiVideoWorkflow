---
name: drama-product-skill2
description: "Use when 要把一件商品嵌进一条竖屏小短剧，并且按导演技能来分镜。选这支模版后，导演规则以 director/SKILL.md 为准，再由适配层交 9:16 的中文 JSON。和「短剧植入」是两支模版。"
metadata:
  version: "1.0.0"
  source: product-template
  upstream: "https://github.com/wuwangzhang1216/DirectorSKILL"
  upstream_commit: "c65ae0d14457053efb1e354c7e7f7e120d97fad1"
  upstream_version: "2.1.0"
  license: MIT
---

# 短剧植入 · 导演

导演规则以 `director/SKILL.md` 为准。本目录其余文件只把那套交付收进本机的 JSON。

一件商品通过一场小戏被用到。观众先看见人和卡点，商品在动作里出现。问什么以意图分析为准。镜头怎么写，以 `director/` 里的原文为准；交稿格式以 JSON 为准。两边冲突时，JSON 压过导演技能的默认值。

## 何时使用

- 用户要把广告放进短剧、小剧场、一段关系或一个生活卡点，并且要按导演的分镜、光线和不变量来写。
- 商品要被拿起、递出、打开、戴上或用完，而不是单独陈列。
- 画幅是 9:16，工具是即梦/Seedance，一条片子，每镜 4 到 15 秒，一镜一个机位。

另外三支不要混用。`drama-product-skill` 是同一类短剧植入，但不带这套导演原文。高奢感 SUV 拍的是同一台车在路上走。Apple 风产品特写拍的是一件静物和它的光线。

## 文件

| 文件 | 作用 |
| --- | --- |
| director/ | DirectorSKILL `c65ae0d` 的原文。来源见 `director/SOURCE.md` |
| 导演.md | 这是短剧植入 · 导演。问哪一枝指向 Drill me，不再自带决策树 |
| 意图补充.md | 回复格式和已经定死的约束。图和文字两层意图 |
| 意图分析.md | Drill me。先确认导演风格，再问画里是谁和结尾差、地点年代、商品怎么被用到、要锁住的外观 |
| 写提示词.md | 把导演交付放进 JSON，并接上出镜围栏 |
| 成稿.md | JSON 字段。分镜表写全列，每一镜一张九段关键帧。机器约束压过导演默认值 |
| 案例.md | 留在磁盘上，不进成稿包 |
| 生图.md | 定妆、商品三张、场景单张只锁身份。每一镜另有一张关键帧 |

追问只装配导演、意图补充、意图分析。用户确认一位导演之后，下一轮追问再追加 `director/references/director_styles/` 里对应的那一个文件，一次一份。没有确认就不追加。追问不追加 `failure-modes.md`。

成稿时再装配导演、成稿，以及 `template.json` 的 `skills`。`director/SKILL.md` 常驻。商品、类型、即梦/Seedance、导演书、分镜表、关键帧、图像适配器和视频提示词模板也常驻。`案例.md` 不进成稿包。其余参考留在磁盘上，由 `director/SKILL.md` 点名，不进默认包。成稿仍是简报点名才追加一份风格。简报里出现一位导演的中文名或英文名时，只追加 `director/references/director_styles/` 里对应的那一个文件。出现「失败 / PPT / 换脸 / 不动」时追加 `director/references/failure-modes.md`。一次最多一个风格镜头。风格路由和失败路由不改。

## 成片顺序

1. 守住用户第一段背景。读图已经写明的人和商品不要改写。
2. 先确认一位导演。风格没定，不问后面的枝，也不能 ready。用户确认后，追问追加那一份风格文件，再问画里是谁和结尾差、地点和年代、商品怎么被用到、要锁住的参考和外观。选项服从这份风格。说清了就 phase 为 ready。简报只写这一个中文名，并写上必须看得见的光线、色彩、机位和动作终点。
3. 生成前以已经拼进提示词的 `director/SKILL.md` 为准：故事功能、走位、一镜一个主动作、动作要有终点、不变量原句重复。
4. 分镜表按列写全。每一镜的关键帧按九段写，放进 `images` 第一位，出片用它当首帧。定妆、商品三视图和场景单张只锁身份。
5. 导演交付放进 `script`、`storyboard`、关键帧和 `shots[].prompt`。`shots[].prompt` 仍是一个主动作、一个机位运动、一个终点。交稿格式以 JSON 为准。
