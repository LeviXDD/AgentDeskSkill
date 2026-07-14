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

# TC-XXX · Acceptance · Attempt N · Review N

## Decision

accepted / returned / blocked

## Scope Review

- [ ] Diff is relative to the frozen base.
- [ ] No unauthorized path changed.
- [ ] Conflict surfaces and contracts were checked.

## Criteria And Checks

- [ ] `<criterion>`: `<PM evidence>`

## Rationale And Next Integration Step

<Why this decision is justified and what baseline should receive the accepted change.>
