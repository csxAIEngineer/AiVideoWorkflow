# 修理规则

机检没过时先跑这里的规则，规则改不动才叫模型。规则改成功**不占用** 3 次模型返修（`repairs`）。规则自己记在 `rule_repairs`。

资产、分镜、投喂，以及投喂机检（`check == machine`），都会先走这一层。

## 加一条规则

1. 在 `end/repair_rules/` 新建一个模块。
2. 写一个对象，带这些字段：
   - `id`：短名字，测试里会看到。
   - `stages`：`"asset"` / `"storyboard"` / `"feed"`。空元组表示每个阶段都看。
   - `match(report, stage, text, context)`：用机检报告里的稳定句子判断，不要拿整段报告做相等比较。
   - `apply(text, context)`：返回改完的全文。没有改动就返回原文。
3. 能复用的函数不要重写。投喂看 `render_feed_text`、`align_feed_unit_headers`；资产看 `patch_asset_phrases`。
4. 在 `end/repair_rules/__init__.py` 的 `RULES` 里登记，放在已有规则后面。
5. 加测试：改写前能对上失败句；改写后这条失败不再出现；同一条再跑一次，正文不变。

`apply` 拿到的 `text` 是这一阶段的主稿：资产是 `assets.md`，分镜是 `分镜.md`，投喂是 `feed.md`。其它文件从 `context["folder"]` 读。

## 已有规则

`feed-unit-headers`：报告里有「未找到投喂单元（## 【投喂单元 N/M】格式）」。稿子里已有 H3 或 Seedance 正文，但单元头缺失或不是 `## 【投喂单元 N/M】`。先 `render_feed_text`，再按 `align_feed_unit_headers` 的切分把每段头改成这个格式。
