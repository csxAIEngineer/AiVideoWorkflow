# 去标识 Promotion Ledger

Promotion ledger 记录“哪项公共规则为什么被晋升、缩窄、暂缓或退休”，让维护者能复核
匿名评测、定位准确公共 diff 并回滚。它不是私有证据摘要，也不是排行榜。

## 记录边界

- 不得记录私有来源定位、连接信息、项目/人物名称、原句、稀有设定组合或私有卡片引用。
- 只引用完全合成的任务、匿名输出、公共 skill 版本和独立 verdict；原始私有证据继续留在
  授权隔离区，不进入 ledger。
- 不能用脚本给创作质量打分。脚本只能计算 hash、检查字段和隔离；题材适配、人物行动、
  制作翻译、边界判断与模板感由 fresh agent 产出、independent reviewer 阅读说明。
- `hold` 与 `retire` 同样记录，不能只保留成功故事。没有可复核的匿名产物时不得写
  `promotion`，只能补齐证据或保持 `hold`。

## 每个事件的最小形状

```yaml
event_version: 1
event_id: public-safe-random-id
rule_id: STY-or-other-public-rule-id
decision: promotion | narrow-and-retest | hold | retire
claim_scope: conditional-public-claim-with-applies-and-fails-boundaries
public_change:
  public_diff_hash: sha256-of-reviewed-public-diff
  baseline_skill_hash: sha256-of-anonymous-baseline-package
  candidate_skill_hash: sha256-of-anonymous-candidate-package
evaluation:
  synthetic_task_refs:
    - task_id: synthetic-task-id
      input_hash: sha256
      coverage_role: claimed-context | boundary | counterexample | transfer
  baseline_output_hashes: [sha256]
  candidate_output_hashes: [sha256]
  executor_context_refs:
    - arm: anonymous-arm-a
      context_ref: runtime-context-id
      fresh: true
    - arm: anonymous-arm-b
      context_ref: runtime-context-id
      fresh: true
review:
  reviewer_context_ref: independent-runtime-context-id
  independent: true
  verdict: promotion | narrow-and-retest | hold | retire
  evidence_summary: public-safe-comparison-of-specific-output-behavior
  counterexample_result: handled | overapplied | inconclusive
privacy:
  synthetic_only: true
  source_wording_present: false
  private_locator_present: false
rollback:
  supersedes: prior-event-id-or-null
  revert_target: public-commit-or-diff-hash
  retire_trigger: observed-overreach-no-gain-or-new-counterexample
```

## 写入与复核

先冻结 synthetic input、两个匿名 skill 包和输出 hash，再由不了解匿名映射的 reviewer 完成
逐项比较。揭盲后才写版本映射和 `verdict`。Ledger 行可以进入维护者受控的可版本化记录，
但原始输出是否公开由隐私检查决定；只有 hash 不能替代 reviewer 的证据说明，也不能证明
上下文确实 fresh。

公共 reference 变更、rubric、synthetic fixture 与 ledger 事件必须指向同一条件式 claim。
若后续证据要求缩窄，新增 superseding 事件并保留旧决定；不要原地改写历史。执行回滚时，
先撤销准确公共 diff，再让受影响规则和 fixture 指向新事件，最后用新的匿名任务重跑审查。
