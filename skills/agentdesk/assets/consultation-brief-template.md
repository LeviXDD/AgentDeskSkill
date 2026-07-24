---
schema_version: agentdesk.consultation-brief/v1
consultation_id: C001
topic: "<full-topic>"
short_topic: "<short-topic>"
decision_owner: "<User-or-Owner>"
authorization_id: null
round_limit: 2
created_by: Leader
created_at: "<RFC3339 UTC>"
---

# C001 · 圆桌研讨简报 · <short-topic>

## 为什么需要研讨

<说明主会话缺少的视角、真实取舍或高影响不确定性；不要为了形式感开席。>

## 需要回答的问题

<用一句话表达本次研讨必须回答的问题。>

## 期望产出

- 给出可执行的推荐结论，而不是简单罗列选项。
- 指出关键假设、反例、风险和未知项。
- 说明哪些信息会改变结论。
- 给出下一步建议和置信度。

## 已知背景

- <fact-or-context>
- <fact-or-context>

## 关键假设

- <assumption-that-must-be-tested>

## 范围

- 包含：<in-scope>
- 不包含：<out-of-scope>
- 必须保持：<non-negotiable-constraint>

## 可读上下文

- <document-or-source>
- <document-or-source>

默认不读取未列出的历史交付、无关模块或其他咨询席的回答。

## 决策标准

| 标准 | 优先级 | 如何判断 |
| --- | --- | --- |
| <criterion> | high / medium / low | <observable-test> |

## 已知选项

1. `<option>`：<current-understanding>
2. `<option>`：<current-understanding>

咨询席可以提出新选项，但必须解释为什么现有选项不足。

## 公共安全和权限边界

- 本次是只读咨询，不是执行任务。
- 不修改仓库、不提交 Git、不改变 `tasks.yaml` 或其他控制面状态。
- 不验收、不合并、不发布、不使用生产凭据，不产生其他外部副作用。
- 不创建更多会话，不直接联系其他咨询席。
- 第一轮必须独立作答；不得搜索、推测或引用其他席位的回答。
- 建议只是咨询证据，不是 Owner 决策、任务卡、实现或验收证据。

## 回调要求

使用指定的 response 结构回答。完成或受阻后，必须主动回调委派中的 Leader `source_thread_id`，并原样携带运行时委派给出的 `consultation_id + seat_id + round + response_id + prompt_id + callback_id`。仅在当前咨询任务中输出最终答复不算回调。`source_thread_id`、`prompt_id`、实际 `callback_id` 和 receipt 只能保存在 gitignored 运行时；持久化 response 只保留 `response_id`，并将 `callback_id` 写为 `null`。
