---
name: image-node-luxury-suv
description: 高奢感 SUV 的 Image-Node。这个节点生成片中全部图片，主角车辆要正面、侧面、背面，路面是单张。只给 luxury-suv 模版使用。
---

# Image-Node

一条视频由多个节点组成。每个节点只处理一个分镜，或一个特定的功能。一个节点里可以有多张资产图。

Image-Node 是必须的功能节点：用来生成所有视频内用到的图片。主角车辆写正面、侧面、背面；路面是单张。再次强调，所有视频用到的图片都要先经过Image-Node处理/生成。

## 节点

- 功能节点做一件功能。Image-Node 是其中必须有的一个，而且是唯一产图的节点。提示词、分镜、拼接、字幕、成片也是功能节点，它们不产图。
- 分镜节点一个节点只处理一个分镜。它只引用 Image-Node 里已经有的图，不在这个节点里新造图片。
- 一张图只写在 Image-Node 里。分镜节点里看见的图，是从 Image-Node 拿来用的。

## 必须生成的图

主角车辆三张，`subject` 相同，`subject_kind` 用 `prop`：

- `front` 正面
- `side` 侧面
- `back` 背面

路面和空镜只写一张，`subject_kind` 用 `location`，`view` 为 `single`。天气、伤损、换色是变体：`view` 为 `single`，`depends_on` 填该车正面的 id，不要再拆三视图。

侧面和背面的 `depends_on` 填正面的 id，用来锁定同一台车。三张是三张单独的图，不要把正面、侧面、背面拼进同一张。

上传图只是参考，不是资产，也不放进镜头。要参考某张上传图时，在直接使用它的那张资产上写 `references`，例如 `["U1"]`。`depends_on` 只填另一张要生成的资产 id。正面可以带 `references`。侧面和背面依赖正面，不要再把上传图写进侧面或背面。生成出来的资产图才绑定提示词和分镜。

## 字段

每张资产图都写 `id`、`title`、`prompt`、`depends_on`、`references`、`subject`、`subject_kind`、`view`。

- `subject_kind`：`character`、`prop`、`location`、`other`
- `view`：`front`、`side`、`back`、`single`
- `references`：上传图 id 的数组。没有参考图时为 `[]`。上传图只是参考。

`prompt` 用中文，能直接拿去文生图。先写车辆锚点，再写这一面的角度。镜头的 `images` 只能填 Image-Node 里要生成的资产 id，不能填上传图 id。

## 禁止

不要在分镜、拼接或成片节点里写一张 Image-Node 没有的新图。
不要只给主角车辆正面。缺侧面或背面时，按同一台车补上。路面保持单张。
不要把上传图写成资产，也不要放进镜头的 `images` 或资产的 `depends_on`。

## 出图时接上

这段只属于高奢感 SUV 的 Image-Node。出图时把下面这一句接在每张静帧提示词末尾。

```render-image
实拍照片质感，可用光，漆面和金属可以有磨损，轮胎接地，不要悬浮，不要新造部件，不要特写车标文字，不要CG渲染。
```
