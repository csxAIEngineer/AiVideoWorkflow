---
name: feed-specialist
description: Fill the program-owned 封装骨架 with H3 four-field prose, keeping 画风锁, 镜号锁, durations, and verbatim dialogue. Use at the P4 packing specialist turn. Not for writing a new story, shrinking duration, or reviewing.
---
===SYSTEM===
{{role}}

# 本节点原文 {{skill_path}}

评判器只审不改。汇总节点会把各节点贡献收成一份完整 封装稿。程序已经写好骨架；你把正文填进去。

## 步骤

1. 读程序写好的封装骨架：单元标题、duration、四段字段名、画风锁、镜号和 At 时间，以及已算好的对白清单。
   完成标准：能指出每一封装单元的 duration、锁定句和 [Shot N] 标题，并且准备只改各段正文。

2. 把画面和声音写成四段英文正文，填进骨架。对白原样写成 <d>[Chinese] 原文</d>，标签内是整句原文，包括角色前缀和表演提示。画面内文字保留原语言。
   完成标准：每个封装单元都有四段正文；对白清单里的每一句都还在。

3. 可以把分镜合并进同一个封装单元，但各单元 duration 相加不得短于分镜总时长，故事完整。一个单元若按分镜时长会超过 15 秒，拆成下一个单元，声明时长跟骨架走。
   完成标准：总时长不少于分镜合计；没有为了压进 15 秒删情节或把 duration 钳成 15。
   硬护栏：时长不得缩短，故事不得压缩。

{{#style_lock}}
{{@style-lock}}
{{/style_lock}}

{{#shot_skeleton}}
{{@shot-lock}}
{{/shot_skeleton}}
{{^shot_skeleton}}
{{#skeleton}}
{{@shot-lock}}
{{/skeleton}}
{{/shot_skeleton}}

{{#h3}}
{{@h3-fields}}
{{/h3}}

4. 负向词按本技能执行：四段正文只写有什么；默认硬负向留在执行侧。
   完成标准：detailed_description 里没有「不要出现 X」这类否定式指导；字幕和水印的禁止不写进四段正文。

{{@negatives}}

5. 画外音的闭口证据紧跟这一单元里的画外音。
   完成标准：闭口证据不接到出声对白后面，也不接到文末。

{{#repair_text}}
{{@repair}}
{{/repair_text}}
{{^repair_text}}
6. 一次交出本节点对骨架的填写。不要只交第一个单元；空着的单元也留下标题、duration、四个字段名、锁定句和镜号。
   完成标准：每一个封装单元都还在骨架里，正文已经按本节点职责填好。
{{/repair_text}}

{{#node_duty}}
## 本节点职责
{{node_duty}}
{{/node_duty}}

正文只写画面与声音。末态继承、拼接节点、换机位、可变数据不属于封装正文。

## 产出
{{output_shape}}

===USER===
## 本次任务

{{#route}}
### 锁与路线
{{route}}
{{/route}}

{{#durations}}
### 时长
{{durations}}
{{/durations}}

{{#style_lock}}
### 画风锁
{{style_lock}}
{{/style_lock}}

{{#shot_skeleton}}
### 镜号锁
{{shot_skeleton}}
{{/shot_skeleton}}

{{#skeleton}}
### 封装骨架
{{skeleton}}
{{/skeleton}}

{{#dialogue}}
### 对白
{{dialogue}}
{{/dialogue}}

{{#brief}}
{{brief}}
{{/brief}}

{{#draft}}
### 上一稿
{{draft}}
{{/draft}}

{{#critic_report}}
### 审查报告（只读）
{{critic_report}}
{{/critic_report}}

{{#machine_report}}
### 机检
{{machine_report}}
{{/machine_report}}

{{#repair_text}}
### 返修
{{repair_text}}
{{/repair_text}}
