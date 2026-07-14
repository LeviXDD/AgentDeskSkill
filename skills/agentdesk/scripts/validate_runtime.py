#!/usr/bin/env python3
"""Validate the live multi-session routing and callback closure.

Repository validation proves durable task-card evidence.  This companion check
proves that the local runtime has recorded verified role-thread bindings and,
optionally, the transport receipts required by active task states.  Runtime
files are JSON-compatible YAML so this script remains dependency-free.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROUTES_SCHEMA = "agentdesk.routes/v2"
RECEIPTS_SCHEMA = "agentdesk.transport-receipts/v1"
TASKS_SCHEMA = "agentdesk.tasks/v2"
ACTIVE_TASK_STATES = {"dispatched", "in_progress", "review_ready"}
DISPATCH_SENT_STATES = {"sent", "acknowledged"}
CODEX_THREAD_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
ROLE_NAME_HEADERS = {"role_name", "role name", "display name", "岗位名称"}
PLACEHOLDER_FRAGMENTS = (
    "<",
    "pending",
    "unbound",
    "待绑定",
    "local-codex-current",
    "local-codex-worker",
)


@dataclass(frozen=True)
class Role:
    role_no: str
    role_id: str
    role_name: str
    status: str

    @property
    def expected_title(self) -> str:
        return f"{self.role_no} . {self.role_name}"


class Reporter:
    def __init__(self) -> None:
        self.passes = 0
        self.errors = 0

    def passed(self, message: str) -> None:
        self.passes += 1
        print(f"PASS  {message}")

    def error(self, message: str) -> None:
        self.errors += 1
        print(f"ERROR {message}")

    def finish(self) -> int:
        print(f"SUMMARY {self.passes} pass(es), {self.errors} error(s)")
        return 1 if self.errors else 0


def _load_json(path: Path, context: str, reporter: Reporter) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        reporter.error(f"missing {context}: {path}")
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        reporter.error(f"cannot parse {context} as JSON-compatible YAML: {exc}")
        return None
    if not isinstance(value, dict):
        reporter.error(f"{context} must contain an object")
        return None
    return value


def _is_separator(cells: Iterable[str]) -> bool:
    joined = "".join(cells)
    return bool(joined) and set(joined) <= {"-", ":", " "}


def _load_roles(project: Path, reporter: Reporter) -> dict[str, Role]:
    path = project / "docs/pm/ROLES.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        reporter.error(f"cannot read docs/pm/ROLES.md: {exc}")
        return {}

    header: list[str] | None = None
    indexes: dict[str, int] = {}
    roles: dict[str, Role] = {}
    role_nos: dict[str, str] = {}
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        normalized = [cell.lower() for cell in cells]
        if (
            header is None
            and "role_no" in normalized
            and "role_id" in normalized
            and "expected_thread_title" in normalized
            and "status" in normalized
        ):
            name_index = next(
                (index for index, name in enumerate(normalized) if name in ROLE_NAME_HEADERS),
                None,
            )
            if name_index is None:
                reporter.error(
                    "docs/pm/ROLES.md role table must contain role_name, Role name, Display name, or 岗位名称"
                )
                return {}
            header = cells
            indexes = {
                "role_no": normalized.index("role_no"),
                "role_id": normalized.index("role_id"),
                "role_name": name_index,
                "expected_thread_title": normalized.index("expected_thread_title"),
                "status": normalized.index("status"),
            }
            continue
        if header is None or _is_separator(cells):
            continue
        if max(indexes.values(), default=-1) >= len(cells):
            continue
        role = Role(
            role_no=cells[indexes["role_no"]],
            role_id=cells[indexes["role_id"]],
            role_name=cells[indexes["role_name"]],
            status=cells[indexes["status"]],
        )
        if not all((role.role_no, role.role_id, role.role_name, role.status)):
            reporter.error("every role row must define role_no, role_id, role_name, and Status")
            continue
        expected_title = cells[indexes["expected_thread_title"]]
        if expected_title != role.expected_title:
            reporter.error(
                f"role {role.role_id} expected_thread_title must equal {role.expected_title!r}"
            )
        if role.role_id in roles:
            reporter.error(f"duplicate role_id in docs/pm/ROLES.md: {role.role_id}")
            continue
        if role.role_no in role_nos:
            reporter.error(
                f"duplicate role_no in docs/pm/ROLES.md: {role.role_no} "
                f"({role_nos[role.role_no]} and {role.role_id})"
            )
            continue
        roles[role.role_id] = role
        role_nos[role.role_no] = role.role_id

    if header is None:
        reporter.error(
            "docs/pm/ROLES.md must contain "
            "role_no/role_id/role_name/expected_thread_title/Status columns"
        )
        return {}
    if roles:
        reporter.passed(f"parsed {len(roles)} stable role identity record(s)")
    return roles


def _non_placeholder(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    lowered = value.strip().lower()
    return not any(fragment in lowered for fragment in PLACEHOLDER_FRAGMENTS)


def _validate_route(
    role: Role,
    route: Any,
    reporter: Reporter,
) -> str | None:
    context = f"route[{role.role_id}]"
    starting_errors = reporter.errors
    if not isinstance(route, dict):
        reporter.error(f"{context} is missing or is not an object")
        return None
    expected = {
        "role_no": role.role_no,
        "role_name": role.role_name,
        "expected_title": role.expected_title,
    }
    for key, value in expected.items():
        if route.get(key) != value:
            reporter.error(f"{context}.{key} must equal {value!r}")
    if route.get("actual_title") != role.expected_title:
        reporter.error(
            f"{context}.actual_title must equal the verified title {role.expected_title!r}"
        )
    if route.get("status") != "verified":
        reporter.error(f"{context}.status must be 'verified'")
    if not _non_placeholder(route.get("verified_at")):
        reporter.error(f"{context}.verified_at must record the last live verification")
    if not _non_placeholder(route.get("host_id")):
        reporter.error(f"{context}.host_id must identify the verified host")
    worktree = route.get("worktree")
    if not _non_placeholder(worktree) or not Path(str(worktree)).is_absolute():
        reporter.error(f"{context}.worktree must be a verified absolute path")
    elif not Path(str(worktree)).is_dir():
        reporter.error(f"{context}.worktree does not exist or is not a directory")
    thread_id = route.get("thread_id")
    if not _non_placeholder(thread_id):
        reporter.error(f"{context}.thread_id must be a real provider thread ID")
        return None
    if route.get("transport") == "codex" and not CODEX_THREAD_ID.fullmatch(str(thread_id)):
        reporter.error(f"{context}.thread_id must be a resolved Codex UUID, not a client or placeholder ID")
        return None
    if reporter.errors != starting_errors:
        return None
    reporter.passed(
        f"{context} records a verified thread attestation for {role.expected_title!r}"
    )
    return str(thread_id)


def _validate_routes(
    project: Path,
    roles: dict[str, Role],
    selected_role_ids: set[str] | None,
    reporter: Reporter,
) -> tuple[dict[str, Any], dict[str, str]]:
    data = _load_json(
        project / ".agentdesk/runtime/routes.yaml",
        ".agentdesk/runtime/routes.yaml",
        reporter,
    )
    if data is None:
        return {}, {}
    if data.get("schema_version") != ROUTES_SCHEMA:
        reporter.error(
            f"runtime routes schema_version must be {ROUTES_SCHEMA!r}; migrate legacy routes before dispatch"
        )
    routes = data.get("routes")
    if not isinstance(routes, dict):
        reporter.error("runtime routes.routes must be an object")
        return {}, {}

    required = {
        role_id
        for role_id, role in roles.items()
        if role.status.lower() == "active"
    }
    if selected_role_ids is not None:
        unknown = selected_role_ids - set(roles)
        for role_id in sorted(unknown):
            reporter.error(f"requested role_id is not registered: {role_id}")
        for role_id in sorted(selected_role_ids & set(roles)):
            if role_id != "PM" and roles[role_id].status.lower() != "active":
                reporter.error(
                    f"requested role_id {role_id} is not Active in docs/pm/ROLES.md"
                )
        required = (selected_role_ids & set(roles)) | {"PM"}
    else:
        required.add("PM")

    thread_ids: dict[str, str] = {}
    worktrees: dict[str, str] = {}
    for role_id in sorted(required):
        role = roles.get(role_id)
        if role is None:
            reporter.error(f"required runtime role is absent from ROLES.md: {role_id}")
            continue
        thread_id = _validate_route(role, routes.get(role_id), reporter)
        if thread_id is not None:
            if thread_id in thread_ids.values():
                other = next(key for key, value in thread_ids.items() if value == thread_id)
                reporter.error(
                    f"roles {other} and {role_id} share thread_id {thread_id}; real role sessions must be distinct"
                )
            thread_ids[role_id] = thread_id
            route = routes.get(role_id)
            worktree = route.get("worktree") if isinstance(route, dict) else None
            if isinstance(worktree, str):
                if worktree in worktrees.values():
                    other = next(key for key, value in worktrees.items() if value == worktree)
                    reporter.error(
                        f"roles {other} and {role_id} share worktree {worktree}; "
                        "active role sessions must be isolated"
                    )
                worktrees[role_id] = worktree
    pm_thread = thread_ids.get("PM")
    for role_id, thread_id in thread_ids.items():
        if role_id != "PM" and pm_thread is not None and thread_id == pm_thread:
            reporter.error(f"worker role {role_id} must not reuse the PM thread")
    return routes, thread_ids


def _load_receipts(project: Path, reporter: Reporter) -> dict[str, Any]:
    data = _load_json(
        project / ".agentdesk/runtime/transport-receipts.yaml",
        ".agentdesk/runtime/transport-receipts.yaml",
        reporter,
    )
    if data is None:
        return {}
    if data.get("schema_version") != RECEIPTS_SCHEMA:
        reporter.error(f"transport receipts schema_version must be {RECEIPTS_SCHEMA!r}")
    for key in ("dispatch_receipts", "callback_receipts"):
        if not isinstance(data.get(key), dict):
            reporter.error(f"transport receipts {key} must be an object")
    return data


def _report_callback_id(
    project: Path,
    task: dict[str, Any],
    context: str,
    reporter: Reporter,
) -> str | None:
    report_path = task.get("report_path")
    if not isinstance(report_path, str) or not report_path.strip():
        reporter.error(f"{context} review_ready task must define report_path")
        return None
    relative = Path(report_path)
    if relative.is_absolute() or ".." in relative.parts:
        reporter.error(f"{context} report_path must stay inside the project")
        return None
    path = project / relative
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        reporter.error(f"{context} cannot read delivery report for callback identity: {exc}")
        return None
    if not lines or lines[0].strip() != "---":
        reporter.error(f"{context} delivery report must start with YAML frontmatter")
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("callback_id:"):
            value = line.split(":", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if _non_placeholder(value) and value.lower() not in {"null", "none", "~"}:
                return value
            break
    reporter.error(f"{context} delivery report must declare a non-empty callback_id")
    return None


def _matching_callback(
    callback_receipts: dict[str, Any],
    callback_id: str,
    dispatch_id: str,
    worker_thread_id: str | None,
    pm_thread_id: str | None,
) -> tuple[str, dict[str, Any]] | None:
    receipt = callback_receipts.get(callback_id)
    if not isinstance(receipt, dict):
        return None
    if receipt.get("dispatch_id") != dispatch_id:
        return None
    if receipt.get("status") != "received":
        return None
    if worker_thread_id is not None and receipt.get("source_thread_id") != worker_thread_id:
        return None
    if pm_thread_id is not None and receipt.get("destination_thread_id") != pm_thread_id:
        return None
    return callback_id, receipt


def _validate_active_tasks(
    project: Path,
    roles: dict[str, Role],
    routes: dict[str, Any],
    thread_ids: dict[str, str],
    receipts: dict[str, Any],
    reporter: Reporter,
) -> None:
    tasks_data = _load_json(
        project / "docs/pm/state/tasks.yaml",
        "docs/pm/state/tasks.yaml",
        reporter,
    )
    if tasks_data is None:
        return
    if tasks_data.get("schema_version") != TASKS_SCHEMA:
        reporter.error(f"tasks schema_version must be {TASKS_SCHEMA!r}")
        return
    tasks = tasks_data.get("tasks")
    if not isinstance(tasks, list):
        reporter.error("tasks.yaml tasks must be an array")
        return
    dispatch_receipts = receipts.get("dispatch_receipts", {})
    callback_receipts = receipts.get("callback_receipts", {})
    if not isinstance(dispatch_receipts, dict) or not isinstance(callback_receipts, dict):
        return

    active_count = 0
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or task.get("state") not in ACTIVE_TASK_STATES:
            continue
        active_count += 1
        context = f"tasks[{index}]({task.get('task_id')})"
        dispatch = task.get("current_dispatch")
        if not isinstance(dispatch, dict):
            reporter.error(f"{context} active state requires current_dispatch")
            continue
        dispatch_id = dispatch.get("dispatch_id")
        role_id = dispatch.get("role_id")
        if not isinstance(dispatch_id, str) or not dispatch_id:
            reporter.error(f"{context} current_dispatch.dispatch_id is missing")
            continue
        if role_id not in roles:
            reporter.error(f"{context} references unregistered role_id {role_id!r}")
            continue
        if role_id not in thread_ids:
            reporter.error(f"{context} has no verified live route for role {role_id}")
        receipt = dispatch_receipts.get(dispatch_id)
        if not isinstance(receipt, dict):
            reporter.error(f"{context} has no transport receipt for dispatch {dispatch_id}")
            continue
        if receipt.get("dispatch_id") != dispatch_id or receipt.get("role_id") != role_id:
            reporter.error(f"{context} dispatch receipt identity does not match the ledger")
        if not _non_placeholder(receipt.get("message_id")):
            reporter.error(f"{context} dispatch receipt must include message_id")
        if not _non_placeholder(receipt.get("provider_receipt")):
            reporter.error(f"{context} dispatch receipt must include provider_receipt")
        if not _non_placeholder(receipt.get("sent_at")):
            reporter.error(f"{context} dispatch receipt must include sent_at")
        pm_thread = thread_ids.get("PM")
        if pm_thread is not None and receipt.get("source_thread_id") != pm_thread:
            reporter.error(f"{context} dispatch receipt must originate from the PM thread")
        expected_thread = thread_ids.get(str(role_id))
        if expected_thread is not None and receipt.get("destination_thread_id") != expected_thread:
            reporter.error(f"{context} dispatch receipt targets the wrong thread")
        status = receipt.get("status")
        task_state = task.get("state")
        if task_state == "dispatched" and status not in DISPATCH_SENT_STATES:
            reporter.error(f"{context} dispatched state requires a sent transport receipt")
        if task_state in {"in_progress", "review_ready"} and status != "acknowledged":
            reporter.error(f"{context} {task_state} requires an acknowledged dispatch receipt")
        if task_state in {"in_progress", "review_ready"} and not _non_placeholder(
            receipt.get("acknowledged_at")
        ):
            reporter.error(f"{context} acknowledged dispatch requires acknowledged_at")
        if task_state == "review_ready":
            expected_callback_id = _report_callback_id(project, task, context, reporter)
            callback = None
            if expected_callback_id is not None:
                callback = _matching_callback(
                    callback_receipts,
                    expected_callback_id,
                    dispatch_id,
                    expected_thread,
                    thread_ids.get("PM"),
                )
            if expected_callback_id is not None and callback is None:
                reporter.error(
                    f"{context} review_ready requires received callback receipt "
                    f"{expected_callback_id}"
                )
            elif callback is not None:
                callback_id, callback_receipt = callback
                if callback_receipt.get("callback_id") != callback_id:
                    reporter.error(f"{context} callback receipt key and callback_id differ")
                if callback_receipt.get("source_role_id") != role_id:
                    reporter.error(f"{context} callback receipt has the wrong source role")
                if callback_receipt.get("destination_role_id") != "PM":
                    reporter.error(f"{context} callback receipt must target logical role PM")
                if not _non_placeholder(callback_receipt.get("provider_receipt")):
                    reporter.error(f"{context} callback receipt must include provider_receipt")
                if not _non_placeholder(callback_receipt.get("received_at")):
                    reporter.error(f"{context} callback receipt must include received_at")
                reporter.passed(f"{context} callback {callback[0]} was received by PM")
        if status in DISPATCH_SENT_STATES:
            reporter.passed(f"{context} has runtime dispatch status {status}")
    reporter.passed(f"checked live closure for {active_count} active task(s)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate locally recorded role-thread attestations, exact titles, "
            "and transport receipts."
        )
    )
    parser.add_argument("--project", required=True, help="Repository worktree root")
    parser.add_argument(
        "--role-id",
        action="append",
        dest="role_ids",
        help="Validate a specific role plus PM; may be repeated",
    )
    parser.add_argument(
        "--check-active",
        action="store_true",
        help="Require dispatch and callback receipts implied by active task states",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    project = Path(args.project).expanduser().resolve()
    reporter = Reporter()
    if not project.is_dir():
        reporter.error(f"project is not a directory: {project}")
        return reporter.finish()
    roles = _load_roles(project, reporter)
    selected = set(args.role_ids) if args.role_ids else None
    routes, thread_ids = _validate_routes(project, roles, selected, reporter)
    receipts: dict[str, Any] = {}
    if args.check_active:
        receipts = _load_receipts(project, reporter)
        _validate_active_tasks(project, roles, routes, thread_ids, receipts, reporter)
    return reporter.finish()


if __name__ == "__main__":
    sys.exit(main())
