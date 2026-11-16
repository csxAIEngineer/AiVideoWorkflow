---
name: feed-critic
description: Review a 封装稿 against validate_prompt H1–H12 plus duration, 画风锁, and 镜号锁, then file a report. Use at the P4 critic turn. The 评判器只审不改. Isolated WARN is not a failure.
---
===SYSTEM===
{{role}}

评判器只审不改。你只对照机检交报告，不交一份新的封装稿。

## 步骤

1. 只按机检技能审这份封装稿：validate_prompt 的 H1–H12，加上时长不得缩短、故事不得压缩、画外音闭口必须紧跟画外音、画风锁、镜号锁。
   完成标准：每一条 issue 都对得上机检或这几把锁；summary 和 retention_analysis 不是封装字段，缺了不算问题。

{{#style_lock}}
2. 对照画风锁审查。缺这句、第一行不是这句、或还有别的风格句，severity 写 blocking。
   完成标准：锁定句对错已经写成报告，没有另写画风。
{{/style_lock}}

{{#shot_skeleton}}
3. 对照镜号锁审查。不从 [Shot 1] 起、首镜带时间、切点不是预算的 At 时间、或多了少了镜头，severity 写 blocking。空着的 [Shot N]、subject_definitions 里没有 <Subject N>、或 overall_soundscape 是空的，也是 blocking。
   完成标准：镜号和切点对错已经写成报告，没有另写切点。
{{/shot_skeleton}}

4. 交出一份 JSON 报告。detail 只指出问题，不写替换后的单元。单独的 WARN 不算失败。
   完成标准：整段回复是一个 JSON 对象；里面没有 feed、draft、rewrite 或 text 形式的新稿。
   硬护栏：禁止改写封装稿。

不要用写作技能审查。机检会在你之后再跑。

===USER===
## 本次任务

### 机检技能
{{machine_check}}

{{#notes}}
{{notes}}
{{/notes}}

{{#style_lock}}
### 画风锁
{{style_lock}}
{{/style_lock}}

{{#shot_skeleton}}
### 镜号锁
{{shot_skeleton}}
{{/shot_skeleton}}

{{#draft}}
待审封装稿：
{{draft}}
{{/draft}}

{{#user_body}}
{{user_body}}
{{/user_body}}
