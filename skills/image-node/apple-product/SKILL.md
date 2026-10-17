---
name: image-node-apple-product
description: Apple 风产品特写的 Image-Node。这个节点生成片中全部图片。一件产品写英雄图、四分之三、微距，背景是单张。只给 apple-product 模版使用。不写车辆三视图。
---

# Image-Node

一条视频由多个节点组成。每个节点只处理一个分镜，或一个特定的功能。一个节点里可以有多张资产图。

Image-Node 是必须的功能节点：用来生成所有视频内用到的图片。这件产品写英雄图、四分之三、微距；背景是单张。再次强调，所有视频用到的图片都要先经过Image-Node处理/生成。

## 节点

- 功能节点做一件功能。Image-Node 是其中必须有的一个，而且是唯一产图的节点。提示词、分镜、拼接、字幕、成片也是功能节点，它们不产图。
- 分镜节点一个节点只处理一个分镜。它只引用 Image-Node 里已经有的图，不在这个节点里新造图片。
- 一张图只写在 Image-Node 里。分镜节点里看见的图，是从 Image-Node 拿来用的。

## 必须生成的图

这件产品三张，`subject` 相同，`subject_kind` 用 `prop`：

- `hero` 英雄图
- `three_quarter` 四分之三
- `macro` 微距

背景只写一张，`subject_kind` 用 `location`，`view` 为 `single`。不要给其他道具再拆三张。不要写 front、side、back。不要写车辆。

微距的 `depends_on` 填英雄图的 id。英雄图和四分之三的 `depends_on` 留空。三张是三张单独的图，不要把几个角度拼进同一张。

上传图只是参考，不是资产，也不放进镜头。要参考某张上传图时，写在英雄图的 `references`，例如 `["U1"]`。不要写进四分之三或微距，也不要写进 `depends_on`。生成出来的资产图才绑定提示词和分镜。

## 字段

每张资产图都写 `id`、`title`、`prompt`、`depends_on`、`references`、`subject`、`subject_kind`、`view`。

- `subject_kind`：`character`、`prop`、`location`、`other`
- `view`：`hero`、`three_quarter`、`macro`、`single`
- `references`：上传图 id 的数组。没有参考图时为 `[]`。上传图只是参考。

`prompt` 用中文，能直接拿去文生图。先写产品锚点，再写这一面的角度。镜头的 `images` 只能填 Image-Node 里要生成的资产 id，不能填上传图 id。

## 禁止

不要在分镜、拼接或成片节点里写一张 Image-Node 没有的新图。
不要只给产品一张英雄图。缺四分之三或微距时，按同一件产品补上。背景保持单张。
不要写 front、side、back，不要写车辆。
不要把上传图写成资产，也不要放进镜头的 `images` 或资产的 `depends_on`。
