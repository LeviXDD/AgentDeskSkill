# AGENTS.md

## Project

- Project ID: `{{PROJECT_ID}}`
- Adoption level: `{{MODE}}`
- Purpose: `<fill before dispatching work>`

## Repository Map

- `<path>`: `<responsibility>`

## Commands

- Build: `<trusted command or check ID>`
- Test: `<trusted command or check ID>`
- Typecheck: `<trusted command or check ID>`
- Lint: `<trusted command or check ID>`

## Iron Rules

1. `docs/pm/state/tasks.yaml` is the authority for current orchestration state.
2. Only the current logical PM lease holder changes control-plane state.
3. PM/Leader and every active worker role use real, independently addressable sessions. A worktree, a logical role, or a prompt that says “act as” is not a role session.
4. Never switch from PM/Leader to a worker role, or between worker roles, inside one session. If the platform cannot create, name, bind, address, and receive callbacks from an independent role session, stop before dispatch and ask the user for a supported path.
5. Every role session title is exactly `role_no . role_name` from `docs/pm/ROLES.md`; auto-generated titles must be renamed and verified before dispatch.
6. Workers change only task-authorized paths plus their exact `report_path`.
7. A worker report is not a callback or acceptance; the worker must actively callback the PM route, and PM must review immutable commits.
8. `Accepted` is not `Integrated`.
9. Do not commit secrets, host/session/thread IDs, worktree absolute paths, transport receipts, or other runtime routing data. Keep them only in gitignored `.agentdesk/runtime/`.
10. Role model policy is vendor-neutral; task and risk may raise its floor, never lower it.
11. Model strength never grants authority or bypasses approval, capability, or safety gates.

## Task And Report Protocol

- Task cards: `docs/pm/tasks/TC-XXX-rN-*.md`
- Reports: `docs/pm/reports/TC-XXX-rN-aN.md`
- Acceptance: `docs/pm/acceptances/TC-XXX-rN-aN-reviewN.md`
- Decisions: `docs/pm/DECISIONS.md`
- Role model policy: `docs/pm/ROLE-POLICIES.yaml`
- Logical role identity and expected titles: `docs/pm/ROLES.md`
- Local routes and dispatch/callback receipts: `.agentdesk/runtime/` (gitignored; never copy runtime IDs into cards or reports)

Read only the current card's required context by default. Use the `agentdesk` Skill for initialization, PM operation, execution, validation, integration, and recovery.
