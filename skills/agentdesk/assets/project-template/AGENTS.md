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

1. `docs/pm/state/tasks.yaml` is the authority for current execution-task orchestration state. Consultation records are separate and must never drive task transitions.
2. Only the current logical PM lease holder changes execution control-plane state. A consultation Leader may update only that consultation's separate record and artifacts; consultation authority does not grant execution-state write access.
3. PM/Leader and every active worker role use real, independently addressable sessions. A worktree, a logical role, or a prompt that says “act as” is not a role session.
4. Never switch from PM/Leader to a worker role, or between worker roles, inside one session. If the platform cannot create, name, bind, address, and receive callbacks from an independent role session, stop before dispatch and ask the user for a supported path.
5. Every role session title is exactly `role_no . role_name` from `docs/pm/ROLES.md`; auto-generated titles must be renamed and verified before dispatch.
6. Workers change only task-authorized paths plus their exact `report_path`.
7. A worker report is not a callback or acceptance; the worker must actively callback the PM route, and PM must review immutable commits.
8. `Accepted` is not `Integrated`.
9. Do not commit secrets, host/session/thread IDs, worktree absolute paths, transport receipts, or other runtime routing data. Keep them only in gitignored `.agentdesk/runtime/`.
10. Role model policy is vendor-neutral; task and risk may raise its floor, never lower it.
11. Model strength never grants authority or bypasses approval, capability, or safety gates.
12. A consultation seat is a temporary read-only perspective, not an Active role, worker, task card, spike, QA result, or acceptance authority. Never add consultation seats to `ROLES.md` or `tasks.yaml`.
13. One consultation may use one to three independently visible seats and at most two rounds. Round 1 is independent; seats never message one another or create more sessions. All prompts and proactive callbacks use the initiating Leader task as the hub.
14. Creating consultation sessions requires an explicit batch authorization for the exact visible titles, or an explicitly delegated `scoped_auto` authorization bounded to the exact topic, seat/round limits, and read-only operation. General analysis, development, initialization, or Skill-upgrade approval does not authorize them.
15. Consultation sessions are read-only and must not change files, commits, control-plane state, external systems, or credentials. Their model requirements are vendor-neutral and do not grant authority.
16. A Leader synthesis is a proposal, not a vote or decision. Only the User / Owner may confirm an exact outcome scope before it is appended to `DECISIONS.md` or converted into a task card; that confirmation never implies execution-session creation, dispatch, acceptance, merge, release, or deployment authority.

## Task And Report Protocol

- Task cards: `docs/pm/tasks/TC-XXX-rN-*.md`
- Reports: `docs/pm/reports/TC-XXX-rN-aN.md`
- Acceptance: `docs/pm/acceptances/TC-XXX-rN-aN-reviewN.md`
- Decisions: `docs/pm/DECISIONS.md`
- Consultations: `docs/pm/consultations/CNNN/` (public logical evidence only; never runtime IDs)
- Role model policy: `docs/pm/ROLE-POLICIES.yaml`
- Logical role identity and expected titles: `docs/pm/ROLES.md`
- Local routes and dispatch/callback receipts: `.agentdesk/runtime/` (gitignored; never copy runtime IDs into cards or reports)

Read only the current card or consultation brief's required context by default. Use the `agentdesk` Skill for consultation, initialization, PM operation, execution, validation, integration, and recovery.
