---
schema_version: agentdesk.delivery-report/v2
task_id: TC-XXX
revision: 1
role_id: DEV
delivery_status: completed
dispatch_id: "<dispatch-id>"
callback_id: "<callback-id>"
callback_destination_role_id: PM
callback_transport: runtime_route
attempt: 1
base_commit: "<base-sha>"
implementation_commit: "<implementation-sha-or-null>"
report_commit: null
report_path: docs/pm/reports/TC-XXX-rN-aN.md
branch: "<branch>"
executor_model:
  required_model_tier: "<basic|standard|advanced|expert>"
  required_model_capabilities: []
  model_binding_id: "<binding-id>"
  selected_model_provider: "<provider>"
  selected_model_id: "<stable-model-revision>"
  selected_model_tier: "<basic|standard|advanced|expert>"
  selected_deliberation_tier: "<efficient|balanced|deep>"
  selected_model_capabilities: []
  model_degradation_approval_id: null
blocked_reason: null
suggested_resume_state: null
created_at: "<RFC3339 UTC>"
---

# TC-XXX · 交付报告 · 第 N 次执行

## 摘要

- `<已实现内容>`
- `<未实现内容>`

## 修改文件

- `<路径>`：`<原因>`

## 验收自检

- [ ] `<验收标准>`：`<证据>`

## 检查结果

| check_id | result | evidence |
| --- | --- | --- |
| `<id>` | passed / failed / not_run | `<摘要>` |

## 回调交接

- 目标岗位：`PM`，通过已核验的运行时路由解析，不在此处填写线程 ID
- 回调 ID：首次发送及每次重试均使用 frontmatter 中不可变的 `callback_id`
- Payload：原样携带任务、revision、attempt、dispatch、实现/报告提交、报告路径、状态及请求 PM 执行的动作
- 完成条件：被 Git 忽略的 `.agentdesk/runtime/transport-receipts.yaml` 中存在成功回调回执
- 传输失败时：保留本报告和回调 ID，明确报告失败，并使原回调保持可重试；不得宣称 PM 已收到通知

## 偏差、契约变化与风险

- 无

## 后续事项

- 无
