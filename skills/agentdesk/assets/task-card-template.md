---
schema_version: agentdesk.task-card/v2
task_id: TC-XXX
revision: 1
type: implementation
role_id: DEV
priority: P1
risk: L1
min_model_tier: inherit
required_model_capabilities: []
depends_on: []
base_commit: "<40-char-git-sha>"
allowed_paths: []
blocked_paths: []
conflict_surfaces:
  paths: []
  symbols: []
  contracts: []
  migrations: []
required_checks: []
owner_approval:
  gate: none
  required_capabilities: []
  approver_role_id: Owner
callback_destination_role_id: PM
callback_transport: runtime_route
callback_required: true
created_at: "<RFC3339 UTC>"
---

# TC-XXX · <Observable outcome>

## Goal

<One concrete result.>

## Context Budget

Required:
- `AGENTS.md`
- This task card
- `<minimal source or contract>`

Read if needed:
- `<nearby material for uncertainty>`

Do not read by default:
- Full historical reports
- Unrelated modules
- Full project archive

## Scope

Allowed:
- `<behavior or module>`

Forbidden:
- `<contract, behavior, or directory>`

## Acceptance Criteria

- [ ] `<observable behavior and evidence>`
- [ ] `<failure or boundary case>`
- [ ] All required checks have reviewable evidence

## Delivery

Use the dispatch-provided branch, attempt, IDs, base commit, and exact report path. Commit implementation first, then the report. A completed report requests PM review; it is not a callback or acceptance.

After the report commit exists, actively send one completion/blocked callback to logical role `callback_destination_role_id` through its verified runtime route. Reuse the same `callback_id` for every transport retry and require a successful runtime receipt before declaring the handoff complete. If callback delivery fails, preserve the report and callback ID, surface the transport failure, and leave the callback retryable.

Never place a host ID, session/thread ID, absolute worktree path, provider receipt, retry timer, credential, or other runtime routing value in this card or the report. Those values belong only in gitignored `.agentdesk/runtime/`.
