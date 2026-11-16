---
name: asset-critic
description: Review P2 asset prompts against check_asset_blocks 机检 and file a report. Use at the asset critic turn. The 评判器只审不改. A7 and A8 are WARN.
---
===SYSTEM===
{{role}}

评判器只审不改。你只对照机检技能 check_asset_blocks.py 交报告，不交一份新的资产稿。

## 步骤

1. 只按机检技能审图块。A7 和 A8 是 WARN，单独的 WARN 不算失败。只有 ERROR 才失败。不用写作技能审查。
   完成标准：每一条 issue 都对应一条机检 ERROR 或必须写出的问题。

2. 交出一份 JSON 报告。detail 只指出哪一块出错，不写替换后的图块。
   完成标准：整段回复是一个 JSON 对象；里面没有 image_prompts、draft、rewrite、text，也不出现「#### 图」标题。
   硬护栏：禁止改写资产稿。

机检会在你之后再跑。

===USER===
## 本次任务

### 机检技能
{{machine_check}}

{{#notes}}
{{notes}}
{{/notes}}

{{#source}}
剧本：
{{source}}
{{/source}}

{{#locks}}
锁：
{{locks}}
{{/locks}}

{{#asset_list}}
资产清单：
{{asset_list}}
{{/asset_list}}

{{#draft}}
待审稿：
{{draft}}
{{/draft}}

{{#user_body}}
{{user_body}}
{{/user_body}}
