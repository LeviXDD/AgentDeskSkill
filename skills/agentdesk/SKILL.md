---
name: agentdesk
description: "Initialize, operate, validate, and recover task-card-driven multi-agent software projects. Use when Codex needs to scaffold or upgrade a medium or large repository for PM-coordinated development with real independently named role tasks, vendor-neutral model policies, task cards, dedicated worktrees, proactive callbacks, transport receipts, acceptance, integration gates, or repository/runtime closure validation; or when acting as the PM, developer, QA, integration, validation, or recovery role."
---

# AgentDesk

## Outcome

Turn a software repository into a controlled multi-session development system:

> Versioned task cards, PM-owned canonical state, real independently visible role tasks, role-bound execution, proactive callbacks, immutable Git evidence, explicit acceptance, and recoverable handoff.

The observable loop is non-negotiable:

```text
PM/Leader task
→ verified independent role task named `role_no . role_name`
→ real dispatch delivery
→ dedicated worktree execution and durable report
→ worker proactively messages the PM/Leader source task
→ PM independently accepts and integrates
→ dependencies unlock
→ heartbeat reconciles only if the primary callback path fails
```

Repository records make this loop recoverable; they do not substitute for creating, addressing, or messaging the real tasks.

Automate coordination, not authority. Keep product decisions, risky approvals, merging, and release behind their declared gates.

## Resolve The Requested Mode

Choose one mode from the request and repository state:

1. **Initialize** — scaffold or upgrade a project workflow.
2. **PM** — clarify requirements, configure roles, create and dispatch cards, review deliveries, update state, and unlock dependencies.
3. **Worker** — implement one assigned card inside its scope and produce a delivery report.
4. **QA / Integration** — test a frozen candidate or combine accepted commits under a dedicated card.
5. **Validate** — check project structure and state without changing business code.
6. **Recover** — reconcile conflicting state, lost callbacks, stale revisions, expired leases, or invisible Git evidence.

Do not silently combine PM and worker authority. In `standard` and `automated`, a worker must run in a real task distinct from the PM/Leader task. Never satisfy this requirement by changing personas in the PM task, by creating only a worktree, or by recording a placeholder thread ID.

Creating a Codex task makes a new user-visible task. If no verified role task exists, require explicit user authorization to create that visible task. A general request such as “start development” or “use this Skill” is not that authorization. Keep the card at `Ready`, explain which `role_no . role_name` task(s) will be created, and ask. Do not silently fall back to a same-task role switch. Lite may use an explicitly authorized manual baton or same-task role switch, but must label that run Lite and must not claim the multi-task closed loop.

## Load References Progressively

Read only what the active mode requires:

- Read [references/protocol.md](references/protocol.md) before changing task state, dependencies, dispatch IDs, leases, acceptance, integration, or concurrency guards.
- Read [references/schemas-and-templates.md](references/schemas-and-templates.md) before creating or editing `tasks.yaml`, a task card, delivery report, or acceptance record.
- Read [references/events-outbox-and-validation.md](references/events-outbox-and-validation.md) before creating or editing an event, outbox message, runtime route, callback, or validator rule.
- Read [references/runbooks-and-recovery.md](references/runbooks-and-recovery.md) for PM/worker/QA/integration procedures, risk approvals, heartbeat, migration, or any failure recovery.
- Read [references/codex-runtime-adapter.md](references/codex-runtime-adapter.md) before creating, renaming, verifying, dispatching to, or calling back a Codex task. Follow its low-freedom tool order and receipt rules exactly.

Do not load all references by default. The repository and current task card remain the primary task-local context.

## Non-Negotiable Invariants

- Treat `docs/pm/state/tasks.yaml` as the only authority for current orchestration state.
- Treat a versioned task card as the only authority for that revision's work specification.
- Let only the current logical PM lease holder modify control-plane state.
- Let roles modify business paths plus their exact assigned `report_path`; keep other control-plane files read-only.
- Bind every dispatch to `task_id + revision + attempt + dispatch_id + base_commit`.
- Use separate namespaces for state `event_id`, outbound `message_id`, and role `callback_id`.
- Review immutable commits, never a moving branch name.
- Keep `Accepted` separate from `Integrated`; default dependencies require `Integrated`.
- Persist PM acceptance in the repository; do not treat chat, callbacks, or self-reported tests as acceptance.
- Keep thread IDs, local worktree paths, cursors, tokens, and timed lease details under gitignored `.agentdesk/runtime/`.
- Let the Project Leader own vendor-neutral role policy in `docs/pm/ROLE-POLICIES.yaml`; task and risk requirements may raise its floor, never lower it.
- Give every Active role one stable `role_no`, `role_id`, and `role_name`; require the real task title to equal `<role_no> . <role_name>` byte-for-byte.
- In Standard and Automated, bind every active dispatch to a verified, independently visible role task and dedicated worktree. A route is usable only when the real thread exists, its actual title matches the expected title, its host/worktree are reachable, and the PM callback route is independently verified.
- Treat explicit authorization for creating a new user-visible task as a precondition, not something implied by project initialization or development approval.
- Require the worker to proactively callback the delegation `source_thread_id`, cross-check it against the verified PM route, and persist a transport receipt before claiming its task lifecycle complete. Heartbeat is only the fallback for a failed or lost primary callback.
- Freeze role policy at `task_card_commit`; freeze the selected provider/model snapshot in dispatch and delivery evidence.
- Treat model selection as a dispatch guard, not a model launcher. If the runtime cannot honor the exact selected binding and deliberation tier, do not dispatch.
- Trace every active or delivered dispatch to the unique `TASK_DISPATCHED` event and `task.dispatch` outbox added by the same commit that first added that dispatch to canonical state; keep all nine model-selection fields identical across ledger, outbox, and report evidence.
- For `require_pm_approval` degradation, require a matching `MODEL_DEGRADATION_APPROVED` event committed with or before dispatch and not expired or independently revoked at dispatch time; never edit the approval event, and never treat an approval ID alone as evidence.
- Never treat a stronger model as authority, approval, capability permission, or permission to bypass a safety gate.
- Never claim Standard/Automated dispatch, callback, or closed-loop operation when the required runtime adapter, thread tools, route verification, or transport receipts are unavailable. Downgrade explicitly to Lite or stop at the relevant gate.

## Initialize Or Upgrade A Project

### 1. Inspect Before Writing

Read the repository root, `AGENTS.md`, build/test configuration, existing `docs/pm/`, current Git branch/status, and any equivalent project-management files.

Reuse equivalent files. Do not create a parallel workflow beside an established one. Preserve unrelated or user-owned changes.

Choose an adoption level:

- `lite`: one PM and one or two roles; manual baton and manual dispatch; no claim of automatic multi-task closure.
- `standard`: real independently visible PM/role tasks, exact role titles, dedicated worktrees, schema validation, explicit dependencies and integration gates, outbox/inbox, proactive cross-task callbacks with receipts, and heartbeat reconciliation.
- `automated`: all Standard guarantees plus timed lease renewal, automatic receipt/ack retry and dead-letter handling, orphan detection, stronger sandboxing, metrics, and recovery automation.

Start new projects at `standard` unless their size clearly calls for `lite`. Do not select `automated` before Standard has run successfully.

### 2. Run The Idempotent Initializer

Resolve the skill directory from this `SKILL.md`, then run:

```bash
python3 <skill-dir>/scripts/init_project.py --project <repo> --mode standard --dry-run
python3 <skill-dir>/scripts/init_project.py --project <repo> --mode standard
```

Add `--project-id` and `--pm-holder-id` when the defaults are ambiguous.

For a user-requested new project in an empty directory, add `--init-git` to both commands. Do not create a nested repository inside an existing worktree; use the actual repository root. For an existing non-Git directory, stop and obtain confirmation before introducing version control.

Before creating the first Ready task card, ensure the repository has a real baseline commit. Do not fabricate author identity or commit unrelated existing changes merely to remove the validator warning.

The initializer must not overwrite existing files. If it reports `MANUAL_MERGE`, inspect and merge only the required protocol section with `apply_patch`.

### 3. Configure Project-Specific Truth

After scaffolding:

1. Fill the stable project summary, repository map, commands, safety rules, and report rule in `AGENTS.md`.
2. Configure only the roles the project needs in `docs/pm/ROLES.md`; every Active role must have a stable unique `role_no`, a stable unique `role_id`, and a non-empty `role_name`. Record the derived `expected_thread_title`, which must equal exactly `<role_no> . <role_name>`.
3. Have the Project Leader configure each Active role's vendor-neutral model tier, deliberation tier, capability, and degradation policy in `docs/pm/ROLE-POLICIES.yaml`.
4. Bind locally available provider models in gitignored `.agentdesk/runtime/model-bindings.yaml`; treat its tier/capability labels as a trusted runtime attestation. If the local operator is outside the Leader's trust boundary, require a Leader-approved model registry or decision reference before enforced routing. Never put credentials, tokens, or session IDs in repository evidence.
5. Register trusted check IDs and argv arrays in `docs/pm/CHECKS.yaml`.
6. Record durable decisions in `DECISIONS.md`; do not use it as a status log.
7. Keep `tasks.yaml` empty until requirements and the first task card are concrete.

Never dispatch to an unconfirmed role or invent production credentials, external approvals, or release authority.

Before the first Standard/Automated dispatch, create or rebuild gitignored `.agentdesk/runtime/routes.yaml` using `agentdesk.routes/v2` and `.agentdesk/runtime/transport-receipts.yaml` using `agentdesk.transport-receipts/v1`. Runtime loss does not change repository truth, but it blocks dispatch until the PM route, worker route, exact actual title, host, and worktree have been rediscovered and marked `verified`.

After binding or refreshing a role route, run the live pre-dispatch guard:

```bash
python3 <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id>
```

Repository strict validation and runtime validation are separate gates; both must pass for Standard / Automated dispatch.

Before every dispatch, run the deterministic selector and use its JSON object without renaming or editing fields:

```bash
python3 <skill-dir>/scripts/select_model.py --project <repo> --task-card-commit <40-char-sha> --role-id <role-id> --risk <L0-L4> --task-min-tier <inherit|basic|standard|advanced|expert> [--required-capability <capability> ...] [--degradation-approval-id <approval-id>]
```

Copy that exact nine-field snapshot to `current_dispatch.model_selection`, outbox `model_selection`, and later the report's `executor_model`. If `require_pm_approval` permits a degraded selection, first allocate the scoped approval and persist its structured event no later than the dispatch commit. If selection or approval fails, keep the task Ready or Blocked; never hand-compute a substitute.

For dispatch, `--task-card-commit` is mandatory at the workflow level: it makes the selector read the role policy frozen in the same commit as the card. Omit it only for a pre-card configuration check against the current worktree policy.

The bundled selector and validator do not start a provider model or create/message Codex tasks. The dispatcher/runtime adapter must prove it can honor the selected provider, stable model revision, deliberation tier, capabilities, real task identity, and transport receipt before sending the outbox message. `executor_model` is an attestation, not independent provider proof. A runtime that cannot select the requested model or emit trustworthy execution and message receipts may use this feature only as an audit policy, not as an enforced routing or closed-loop claim.

### 4. Render, Preflight, Commit, And Validate

```bash
python3 <skill-dir>/scripts/render_views.py --project <repo>
python3 <skill-dir>/scripts/validate_project.py --project <repo> --pre-commit
python3 <skill-dir>/scripts/render_views.py --project <repo> --check
# inspect and commit the scoped workflow snapshot
python3 <skill-dir>/scripts/validate_project.py --project <repo>
python3 <skill-dir>/scripts/render_views.py --project <repo> --check
```

`--pre-commit` performs structural checks on a proposed snapshot and warns because it cannot prove commit atomicity while control-plane files differ from `HEAD`. After it passes, inspect the diff and commit only the scoped workflow files; never sweep unrelated existing changes into the commit. Final strict validation omits both `--pre-commit` and `--allow-legacy-model-evidence`, and proves canonical state, generated views, acceptance records, and dispatch event/outbox evidence match committed Git history. If the session is not authorized to commit an existing repository, stop with final strict validation pending rather than claiming initialization is complete.

`--allow-legacy-model-evidence` is only a migration-audit escape hatch for legacy terminal records. Its warnings are never a strict pass and must never drive automatic acceptance or dispatch.

Do not claim initialization is complete while final strict validation reports errors.

The bundled validator reconciles ledger state with committed event/outbox evidence, including ID uniqueness, first-dispatch commit atomicity, raw outbox digest, model snapshot parity, and degradation approval linkage. It does not prove a provider execution receipt, the completeness of dispatch guards, the semantic safety of `CHECKS.yaml`, or the actual implementation diff/path scope; use the full Validator / CI checklist in [references/runbooks-and-recovery.md](references/runbooks-and-recovery.md) before dispatch or acceptance.

## PM Workflow

1. Acquire or confirm the logical PM lease and current `lease_epoch`.
2. Read canonical state, the relevant task card, latest decisions, active deliveries, and blockers.
3. Clarify the next observable outcome and choose the smallest role set.
4. Create a versioned card from [assets/task-card-template.md](assets/task-card-template.md).
5. Validate Definition of Ready: role identity (`role_no`, `role_id`, `role_name`), frozen role policy, effective model tier/deliberation/capabilities, dependencies, immutable base, paths, checks, risk, approval gate, and context budget.
6. Before writing `TASK_DISPATCHED`, verify the real PM task, the distinct real role task, exact expected/actual title, callback destination, dedicated worktree/host, and single-flight capacity. If a new visible task is needed and explicit user authorization is absent, stop at `Ready` and ask. For Codex, execute [the runtime adapter](references/codex-runtime-adapter.md): `list_projects` → `create_thread` → `set_thread_title` → `read_thread`/`list_threads` verification, then require `validate_runtime.py --project <repo> --role-id <role-id>` to pass. Placeholder/local labels are not routes.
7. Allocate the attempt and IDs, run `select_model.py` with the full `task_card_commit`, and, when required, create a matching `MODEL_DEGRADATION_APPROVED` event. Copy the selector's nine fields exactly into ledger and outbox.
8. Finalize the immutable outbox, hash its repository UTF-8/LF bytes into the `TASK_DISPATCHED.payload_digest`, render views, and run pre-commit structural validation. Commit the first appearance of `current_dispatch`, dispatch event, outbox, snapshot, views, and any same-commit approval together; then run strict validation.
9. Send the immutable dispatch to the verified role `thread_id` with `send_message_to_thread`, include the runtime-only PM `source_thread_id`, and persist the provider result in `transport-receipts.yaml`. Verify visibility or obtain a matching ack before recording `InProgress`, then run `validate_runtime.py --project <repo> --role-id <role-id> --check-active`. Retries reuse the same IDs, payload, destination, and snapshot.
10. On callback, dedupe by `callback_id`, verify its receipt and source/destination routes, then inspect the report commit, implementation commit, exact diff, source, checks, deviations, and contract changes.
11. Create an immutable acceptance record from [assets/acceptance-template.md](assets/acceptance-template.md).
12. Mark a code task `Integrated` only after the target baseline contains the accepted change by ancestry or recorded equivalence.
13. Run strict validation and view `--check` after the control-plane commit.

Do not auto-merge, publish, deploy, send external messages, use production credentials, or perform irreversible migrations without the separately required authorization.

## Worker Workflow

1. Read `AGENTS.md`, the assigned card, and only its required context.
2. Verify `task_id`, `revision`, `attempt`, `dispatch_id`, `task_card_commit`, `base_commit`, role, worktree, paths, report path, and the frozen `model_selection`.
3. Stop and report stale or conflicting inputs; do not guess the latest task.
4. Work in the dedicated branch/worktree and stay inside allowed scope.
5. Use only trusted check IDs resolved through `CHECKS.yaml`.
6. Commit implementation and tests first as `implementation_commit`.
7. Create the attempt-specific report from [assets/delivery-report-template.md](assets/delivery-report-template.md), record the actual executor model snapshot, reference the implementation commit, then commit the report as `report_commit`.
8. Publish both commits to a PM-readable ref or artifact before callback.
9. Resolve the delegation's runtime-only `source_thread_id`, cross-check it against the `verified` PM route, send the callback with `send_message_to_thread`, and persist a matching entry under `callback_receipts`. A final answer inside the worker task is not a callback.
10. Retry failed or unconfirmed transport with the same `callback_id`, payload, and destination; never create a second delivery fact merely to get attention. Do not claim the worker lifecycle complete until a matching `callback_receipts[callback_id]` has `status: received`. If transport remains unavailable, preserve the report/commits, report the callback failure locally, and leave reconciliation to PM/heartbeat without pretending delivery was notified.

A worker's `completed` report means “ready for PM review,” not Accepted.

## QA And Integration Workflow

- Give QA its own card, frozen candidate commit, environment, test matrix, and report path.
- Let QA recommend pass, return, or unknown; only PM records final acceptance.
- Give integration its own card, target baseline, ordered accepted commits, conflict scope, and cross-module checks.
- Record merge, cherry-pick, or rebase mappings. Require equivalence evidence when accepted commit ancestry is not preserved.

## Validate Or Recover

For read-only repository validation, run `validate_project.py` and inspect reported errors before proposing changes. Its ordinary pass does not prove that real tasks exist, titles match, dispatch/callback messages were delivered, or receipts are genuine. For a Standard / Automated local closed-loop verdict, run both:

```bash
python3 <skill-dir>/scripts/validate_runtime.py --project <repo> --check-active
python3 <skill-dir>/scripts/validate_project.py --project <repo> --require-runtime
```

Treat either failure as a failed closed-loop validation; do not substitute one pass for the other. See [references/codex-runtime-adapter.md](references/codex-runtime-adapter.md).

For recovery:

1. Freeze new dispatch.
2. Establish one valid PM lease holder and fencing epoch.
3. Preserve branches, worktrees, reports, messages, and receipts.
4. Reconcile canonical state against Git objects, task cards, reports, acceptance, outbox, and runtime routes.
5. Deduplicate by revision, attempt, and the correct event/message/callback ID namespace.
6. Repair only facts that can be proven; send uncertain cases to PM or Owner decision.
7. Regenerate views, run pre-commit validation, commit the recovered snapshot, then run strict validation; independently re-verify runtime routes and receipts, and reopen dispatch only after both repository and runtime invariants hold.

Read [references/runbooks-and-recovery.md](references/runbooks-and-recovery.md) before mutating a broken workflow. Heartbeat may reconcile and alert; it must not silently rerun implementation, accept code, merge, or publish.

## Required Handoff

Report the mode used, files created or changed, task/state IDs, branch and commits, commands run, validation result, deviations, blockers, and the next safe action. For Standard/Automated runtime work, also report the logical role/title, whether the real PM and role routes were verified, and the dispatch/callback receipt IDs without exposing credentials.

For initialization, explicitly list existing files left untouched and any `MANUAL_MERGE` work. For worker delivery, provide both implementation and report commits. For PM acceptance, identify the exact reviewed commit and integration status.
