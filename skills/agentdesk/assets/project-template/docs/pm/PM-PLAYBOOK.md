# PM Playbook

## Non-Negotiable Session Contract

- PM/Leader and workers are real, independently addressable sessions. A logical role, branch, worktree, queued task, or same-session role prompt is not a worker session.
- Never execute a worker card by switching the PM session into that role. Never reuse one worker session for two role identities.
- The visible title of every session is exactly `role_no . role_name` from `ROLES.md`. Rename auto-generated titles, read the title back from the platform, and record the verified binding only in `.agentdesk/runtime/routes.yaml`.
- Cards and reports contain only logical role IDs and callback intent. Host IDs, thread IDs, absolute worktree paths, provider receipts, retry timers, and verification timestamps stay in gitignored `.agentdesk/runtime/`.
- If the platform cannot create, rename, address, or callback an independent session, stop before dispatch. Tell the user which capability is unavailable; do not silently degrade to same-session execution.

## User Confirmation Gate

Before creating the first worker session for a new or existing project:

1. Understand the requirements and propose the minimum role set with `role_no`, `role_id`, `role_name`, exact `expected_thread_title`, responsibilities, and reason for activation.
2. Tell the user that each Active worker will be a visible independent session and state how many sessions will be created or rebound.
3. Obtain explicit user confirmation for both the role allocation and creation/reuse of those independent sessions when the host requires user authority.
4. Write the confirmed roles to `ROLES.md`. Do not activate unconfirmed roles.

Initializing this control plane, creating cards, or receiving a general instruction such as “start development” does not prove that role sessions exist and does not waive this gate. Adopting the Skill in an already-developed repository uses the same gate before its first new-feature dispatch.

## Start

1. Confirm the logical PM holder and `lease_epoch`.
2. Read `state/tasks.yaml`, `ROLES.md`, relevant task cards, decisions, reports, and acceptance.
3. Bind the current PM/Leader session in `.agentdesk/runtime/routes.yaml`: populate the real local route, make its visible title equal `PM . 项目经理`, set `actual_title` to the observed title, and mark it verified only after a successful platform lookup.
4. Reconcile Active worker routes against the platform. A cached thread ID or a pre-existing worktree is insufficient; re-verify reachability, identity, title, and worktree ownership before dispatch.
5. Run repository strict validation. Before a role dispatch, also run `python3 <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id>`; repository validation cannot substitute for this runtime gate.

## Bind A Role Session

Perform this after user confirmation and before the role's first dispatch:

1. Create a new independent role session, or select the previously confirmed session dedicated to this exact role. It must be different from the PM session and every other role session.
2. Allocate the card's independent worktree and bind only that worktree to the role attempt.
3. Rename the visible session to the exact `expected_thread_title` from `ROLES.md`.
4. Read the session back through the host and verify that it exists, is addressable, is not archived, has the expected title, and is attached to the intended worktree.
5. Add or update the role entry in gitignored `.agentdesk/runtime/routes.yaml` using schema `agentdesk.routes/v2`. Store `role_no`, `role_name`, `expected_title`, transport, host/thread binding, observed `actual_title`, worktree, `status: verified`, and `verified_at`.
6. Assert that all non-null `thread_id` values are unique and that the worker route differs from the PM route. Placeholder IDs, inferred IDs, `status: unbound`, a title mismatch, or a worktree without a real thread fails this gate.
7. Run `python3 <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id>`. Do not issue a card until it passes after the live platform lookup.

Do not write any local route field to `ROLES.md`, the task card, tasks ledger, event, outbox, report, acceptance, or Git history.

## Issue A Card

1. Freeze one observable outcome, role, revision, base commit, paths, dependencies, checks, risk, model floor/capabilities, approval gate, and context budget.
2. Pass Definition of Ready, including the User Confirmation Gate and a currently verified role route whose identity/title/worktree all match. If this fails, keep the card `Ready`; do not impersonate the role in the PM session.
3. Allocate the next attempt, unique `dispatch_id`, worktree, branch, and role lease; run the selector against the full `task_card_commit`.
4. If `require_pm_approval` selects below the preferred tier, allocate a repository-unique `APR-*`, add it to task `granted_approval_ids`, and create a scoped `MODEL_DEGRADATION_APPROVED` event in the dispatch commit or its ancestor. It must match task/revision/attempt and selected/preferred tiers, be granted by PM, be unexpired and not independently revoked at dispatch, and retain `revoked_at: null`. Revoke at most once with a strict-descendant `MODEL_DEGRADATION_REVOKED` whose time/epoch is not earlier than approval.
5. Copy the selector's exact nine-field object to ledger `current_dispatch.model_selection` and outbox `model_selection`; no missing, extra, or renamed keys, and array values retain the selector's order.
6. Finalize the immutable `task.dispatch` outbox, then hash its repository UTF-8/LF bytes into the matching `TASK_DISPATCHED.payload_digest`.
7. Render views and run `validate_project.py --pre-commit`; treat it as structural preflight only and inspect its warning plus the scoped diff.
8. In one commit, first add `current_dispatch` and also add the unique matching dispatch event/outbox, updated state/views, and any same-commit approval event. Event `lease_epoch` values are integers >= 1; same-commit approval and dispatch epochs match.
9. Run strict validation without legacy flags and run the view check.
10. Resolve `destination_role_id` through the verified runtime route and deliver the self-contained outbox message to that exact independent session. The message must state the expected role identity/title, full frozen card, worktree/branch/commit facts, logical callback destination `PM`, and the prohibition on changing control-plane state.
11. Record the provider's result under the original `message_id` in `.agentdesk/runtime/transport-receipts.yaml.dispatch_receipts`. A successful entry must prove which dispatch and logical role were delivered, the actual runtime destination, the provider status/receipt, and delivery time. Never copy that receipt into Git.
12. Require a role acknowledgement that echoes `task_id + revision + attempt + dispatch_id` from the bound worker session. Only then move to `InProgress`. A transport receipt proves delivery; the acknowledgement proves the role accepted the correct card.
13. Run `python3 <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id> --check-active`. A failure freezes further lifecycle progress until reconciled.

If delivery fails or no receipt/ack arrives, keep the durable state at `Dispatched`, replay the same immutable outbox with the same IDs, and surface the failure. Do not create a new attempt, mark `InProgress`, or run the card in the PM session.

## Callback Gate

1. The worker commits implementation first, generates one stable `callback_id`, writes it into the immutable report, and commits the report second.
2. After the report commit exists, the worker actively sends the completion/blocked callback to logical destination `PM`, resolved through the verified PM route. It must echo `task_id + revision + attempt + dispatch_id`, both commit SHAs, report path, outcome, and requested PM action.
3. Transport retries reuse the same `callback_id`; they do not create a new delivery fact or attempt.
4. Record the provider result under that `callback_id` in `.agentdesk/runtime/transport-receipts.yaml.callback_receipts`. The receipt is runtime-only and must identify the callback, dispatch, logical source/destination, actual local route, provider status/receipt, and delivery time.
5. The worker must not describe orchestration as complete until the callback has a successful receipt. If sending is unavailable, report the transport failure in its visible final response and leave it retryable with the same callback ID.
6. PM validates callback identity against the current dispatch and receipt, then moves the task to review. A report found in Git is durable recovery evidence, not proof that callback delivery occurred.
7. After recording the received callback, run `python3 <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id> --check-active`. Before claiming the local loop closed, require `python3 <skill-dir>/scripts/validate_project.py --project <repo> --require-runtime` as well.

## Heartbeat Gate

Heartbeat is mandatory fallback monitoring for every active dispatch; it never replaces active worker callback.

1. During active work, periodically verify PM and worker routes, dispatch/callback receipts, current task state, worktree head, and the expected report path.
2. If a report/head exists without a callback receipt, classify it as a lost callback: wake the PM for artifact-based review, preserve the original `callback_id` when known, and repair/retry the route separately.
3. If a callback receipt exists but PM state did not advance, wake the PM and process it idempotently.
4. If a route is missing, archived, unreachable, duplicated, or title-mismatched, freeze new dispatches to that role and request route repair; never fall back to same-session role switching.
5. When nothing changed, stay quiet. Keep heartbeat observations, retry timers, and cursors in `.agentdesk/runtime/` only.

If the host cannot provide recurring wakeups, explicitly tell the user that full automatic closure is unavailable and keep a manual PM poll active. Do not claim the project has an automated callback loop.

## Review

1. Verify IDs, dispatch and callback transport receipts, both delivery commits, and the report's executor model against the original dispatch event/outbox. This trace remains required after `current_dispatch` is cleared.
2. Review exact diff, source, checks, deviations, contracts, and risk.
3. Record immutable acceptance.
4. Keep Accepted separate from Integrated.
5. After integration, unlock the next dependency-ready card and repeat the route/title/dispatch gates; a previous successful route verification is not permanent proof of reachability.

## Recover

Freeze dispatch, establish one PM lease, preserve evidence, reconcile state with Git and messages, repair only proven facts, validate, then reopen the queue.

`--allow-legacy-model-evidence` is migration-audit only. It is never a strict pass and cannot authorize automatic acceptance, dispatch, or CI release.
