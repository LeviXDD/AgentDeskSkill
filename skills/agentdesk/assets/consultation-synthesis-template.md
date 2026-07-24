---
schema_version: agentdesk.consultation-synthesis/v1
consultation_id: C001
topic: "<full-topic>"
rounds_used: 1
outcome_status: proposed
confirmation_required: true
created_by: Leader
created_at: "<RFC3339 UTC>"
---

# C001 · 圆桌研讨综合报告

## 席位覆盖

| 席位 | Round 1 | Round 2 | 主动回调 | 缺口或恢复方式 |
| --- | --- | --- | --- | --- |
| C001.01 | received / missing / blocked | n/a | received / missing | None |

## 结论摘要

<Leader 给出的条件化推荐。不得用票数或“多数席位赞成”代替证据和判断。>

## 一致共识

- <all-seats-supported-and-evidence-backed-point>

## 条件性共识

- 如果 `<condition>`，则 `<shared-conclusion>`。

## 关键分歧

| 分歧 | 立场 A | 立场 B | 决定性证据 |
| --- | --- | --- | --- |
| <issue> | <position> | <position> | <missing-or-available-evidence> |

## 少数意见

- <minority-view-and-why-it-matters>

## 关键假设与残余风险

- 假设：<assumption>
- 风险：<risk>
- 解除方式：<mitigation-or-evidence>

## Leader 综合建议

<给出建议、适用条件、取舍和不接受其他方案的理由。>

## 总体置信度

`low | medium | high`

<说明置信度依据、缺失证据，以及什么事实会改变综合建议。>

## 需要 User / Owner 裁决

1. <specific-question>
2. <specific-question>

## 证据索引

| 席位 | 轮次 | response_id | response |
| --- | --- | --- | --- |
| C001.01 | 1 | `C001.01.R1` | `docs/pm/consultations/C001/responses/r1-C001.01.md` |

## 不完整性与降级

- 缺失回答：None
- 丢失主动回调：None
- 使用 wait/read 轮询恢复：No
- 其他偏差：None

## 确认门禁

本报告的 `outcome_status` 为 `proposed`。在 User / Owner 对明确范围给出确认、Leader 将其记录为 `confirmation.status: confirmed` 之前：

- 不得把本报告当作已生效决策；
- 不得从中派生 `DECISIONS.md` 记录或任务卡；
- 不得创建执行岗位会话或开始实现；
- 不得推定验收、合并、发布、部署或任何外部动作已获授权。
