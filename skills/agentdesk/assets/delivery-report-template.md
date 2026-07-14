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

# TC-XXX · Delivery Report · Attempt N

## Summary

- `<implemented>`
- `<not implemented>`

## Changed Files

- `<path>`: `<reason>`

## Acceptance Self-Check

- [ ] `<criterion>`: `<evidence>`

## Checks

| check_id | result | evidence |
| --- | --- | --- |
| `<id>` | passed / failed / not_run | `<summary>` |

## Callback Handoff

- Destination role: `PM` (resolve through the verified runtime route; do not put its thread ID here)
- Callback ID: use the immutable `callback_id` from frontmatter for the first send and every retry
- Payload: echo task, revision, attempt, dispatch, implementation/report commits, report path, status, and requested PM action
- Completion condition: a successful callback receipt exists in gitignored `.agentdesk/runtime/transport-receipts.yaml`
- On transport failure: preserve this report and callback ID, surface the failure, and leave the same callback retryable; do not claim that PM was notified

## Deviations, Contract Changes, And Risks

- None

## Follow-Ups

- None
