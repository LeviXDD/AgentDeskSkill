---
schema_version: agentdesk.acceptance/v2
task_id: TC-XXX
revision: 1
decision: accepted
reviewed_dispatch_id: "<dispatch-id>"
attempt: 1
type: implementation
role_id: DEV
reviewer_role_id: PM
reviewer_id: "<pm-holder-id>"
lease_epoch: 1
base_commit: "<base-sha>"
implementation_commit: "<reviewed-sha>"
report_commit: "<report-sha>"
accepted_commit: "<reviewed-sha-or-null>"
owner_approval:
  gate: none
  approval_ids: []
evidence_refs: []
residual_risks: []
created_at: "<RFC3339 UTC>"
---

# TC-XXX · 验收记录 · 第 N 次执行 · 第 N 次审查

## 结论

accepted / returned / blocked

## 范围审查

- [ ] diff 相对于冻结基线计算。
- [ ] 未修改任何未授权路径。
- [ ] 已检查冲突面和契约。

## 验收标准与检查

- [ ] `<验收标准>`：`<PM 复核证据>`

## 结论依据与下一步集成

<说明该结论的依据，以及已验收改动应进入哪个基线。>
