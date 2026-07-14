# Project Roles

> Configure roles after requirements are understood and let the user confirm the proposed active roles before creating their sessions. `role_no`, `role_id`, and `role_name` are stable identity fields; do not silently renumber, rename, or reuse them after dispatch.

## Current Roles

| role_no | role_id | role_name | expected_thread_title | Responsibilities | Forbidden | Common paths | Acceptance focus | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PM | PM | 项目经理 | `PM . 项目经理` | Requirements, task decomposition, state, review, acceptance | Worker implementation and unreviewed business changes | `docs/pm/**` | Scope, evidence, dependencies, risk | Active |
| R1 | DEV | 开发工程师 | `R1 . 开发工程师` | Project implementation and tests | PM acceptance, unapproved contract changes | `<configure>` | Behavior, errors, tests | Proposed |
| R2 | QA | QA 工程师 | `R2 . QA 工程师` | Independent test design and evidence | Final PM acceptance | `<configure>` | E2E, regression, residual risk | Proposed |

`expected_thread_title` is derived exactly as `<role_no> . <role_name>` with one ASCII space on each side of the period. It is the required visible title, not a suggestion.

Activate only the roles the project needs. A role describes responsibility, not a model or tool vendor. Every Active worker role must have a real session distinct from the PM and from every other worker, with an exact verified title and a local binding in gitignored `.agentdesk/runtime/routes.yaml`. A worktree alone does not satisfy this requirement. The Project Leader owns vendor-neutral execution requirements in `ROLE-POLICIES.yaml`; local provider/model availability belongs in gitignored `.agentdesk/runtime/model-bindings.yaml`.
