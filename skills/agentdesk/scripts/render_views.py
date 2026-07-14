#!/usr/bin/env python3
"""Render human-readable PM views from the single machine state source.

The input file is JSON-compatible YAML and is parsed with Python's standard
``json`` module.  The renderer never writes back to the state source.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional


STATE_FILE = Path("docs/pm/state/tasks.yaml")
BOARD_FILE = Path("docs/pm/BOARD.md")
STATUS_FILE = Path("docs/pm/STATUS.md")
SCHEMA_VERSION = "agentdesk.tasks/v2"
STATES = (
    "draft",
    "ready",
    "dispatched",
    "in_progress",
    "review_ready",
    "returned",
    "blocked",
    "accepted",
    "integrated",
    "cancelled",
    "superseded",
)


class RenderError(Exception):
    """A user-actionable state or filesystem error."""


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_project_path(project: Path, relative: Path) -> Path:
    current = project
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RenderError(f"workflow path must not use symlinks: {relative.as_posix()}")
    try:
        resolved = current.resolve(strict=False)
        if os.path.commonpath((str(project), str(resolved))) != str(project):
            raise RenderError(f"workflow path escapes the project root: {relative.as_posix()}")
    except ValueError as exc:
        raise RenderError(f"workflow path escapes the project root: {relative.as_posix()}") from exc
    except OSError as exc:
        raise RenderError(f"cannot resolve workflow path {relative.as_posix()}: {exc}") from exc
    return current


def _load_state(project: Path) -> dict[str, Any]:
    path = _safe_project_path(project, STATE_FILE)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RenderError(f"cannot read {STATE_FILE.as_posix()}: {exc}") from exc
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RenderError(
            f"{STATE_FILE.as_posix()} is not JSON-compatible YAML: "
            f"JSON parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}. "
            "Serialize it as a JSON object; JSON is valid YAML and this skill "
            "intentionally does not depend on PyYAML."
        ) from exc
    if not isinstance(state, dict):
        raise RenderError(f"{STATE_FILE.as_posix()} must contain one JSON object at its root")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise RenderError(
            f"schema_version must be {SCHEMA_VERSION!r}, got {state.get('schema_version')!r}"
        )
    if not _nonempty_string(state.get("project_id")):
        raise RenderError("project_id must be a non-empty string")
    if not _nonempty_string(state.get("updated_at")):
        raise RenderError("updated_at must be a non-empty string")
    if not isinstance(state.get("pm_control"), dict):
        raise RenderError("pm_control must be an object")
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        raise RenderError("tasks must be an array (an empty array is valid)")
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise RenderError(f"tasks[{index}] must be an object")
        if not _nonempty_string(task.get("task_id")):
            raise RenderError(f"tasks[{index}].task_id must be a non-empty string")
        if task.get("state") not in STATES:
            raise RenderError(
                f"tasks[{index}].state must be one of: {', '.join(STATES)}"
            )
    return state


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _task_updated_at(task: dict[str, Any]) -> Any:
    timestamps = task.get("timestamps")
    return timestamps.get("updated_at") if isinstance(timestamps, dict) else None


def _task_role(task: dict[str, Any]) -> Any:
    dispatch = task.get("current_dispatch")
    return dispatch.get("role_id") if isinstance(dispatch, dict) else None


def _dispatch_model_selection(dispatch: Any) -> Optional[dict[str, Any]]:
    if not isinstance(dispatch, dict):
        return None
    selection = dispatch.get("model_selection")
    if isinstance(selection, dict):
        return selection
    # Historical ledgers may predate the nested model-selection contract.
    # Keeping the renderer tolerant makes recovery views useful; validation
    # still decides whether a lifecycle record must be migrated.
    if any(
        key in dispatch
        for key in (
            "required_model_tier",
            "selected_model_tier",
            "selected_model_provider",
            "selected_model_id",
        )
    ):
        return dispatch
    return None


def _task_model_requirement(task: dict[str, Any]) -> Any:
    selection = _dispatch_model_selection(task.get("current_dispatch"))
    return selection.get("required_model_tier") if selection is not None else None


def _task_model_selection(task: dict[str, Any]) -> Any:
    selection = _dispatch_model_selection(task.get("current_dispatch"))
    if selection is None:
        return None
    tier = selection.get("selected_model_tier")
    provider = selection.get("selected_model_provider")
    model_id = selection.get("selected_model_id")
    if not any(value not in (None, "") for value in (tier, provider, model_id)):
        return None
    identity = "/".join(str(value) for value in (provider, model_id) if value not in (None, ""))
    return f"{tier}: {identity}" if tier and identity else tier or identity


def _group_tasks(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {state: [] for state in STATES}
    for task in tasks:
        grouped[task["state"]].append(task)
    for state in STATES:
        grouped[state].sort(key=lambda item: str(item.get("task_id", "")))
    return grouped


def _generated_warning(state: dict[str, Any]) -> list[str]:
    generated_at = _cell(state.get("updated_at"))
    return [
        "<!-- GENERATED FILE: source=docs/pm/state/tasks.yaml -->",
        "> Generated view — do not edit by hand. The only current-state authority is `docs/pm/state/tasks.yaml`.",
        f"> 生成时间（取状态源 `updated_at`，确保结果可复现）：`{generated_at}`",
        "",
    ]


def _render_summary_table(grouped: dict[str, list[dict[str, Any]]]) -> list[str]:
    lines = ["| State | Count |", "| --- | ---: |"]
    for state in STATES:
        lines.append(f"| `{state}` | {len(grouped[state])} |")
    return lines


def _render_board(state: dict[str, Any]) -> str:
    tasks: list[dict[str, Any]] = state["tasks"]
    grouped = _group_tasks(tasks)
    lines = ["# Task Board", ""]
    lines.extend(_generated_warning(state))
    lines.extend(
        [
            f"Project: `{_cell(state.get('project_id'))}`",
            "",
            "## State summary",
            "",
        ]
    )
    lines.extend(_render_summary_table(grouped))

    for task_state in STATES:
        state_tasks = grouped[task_state]
        lines.extend(
            [
                "",
                f"## {task_state} ({len(state_tasks)})",
                "",
                "| Task | Revision | Attempt | Role | Required model | Selected model | Delivery | Integration | Report | Updated |",
                "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        if not state_tasks:
            lines.append("| — | — | — | — | — | — | — | — | — | — |")
            continue
        for task in state_tasks:
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(task.get("task_id")),
                        _cell(task.get("revision")),
                        _cell(task.get("attempt")),
                        _cell(_task_role(task)),
                        _cell(_task_model_requirement(task)),
                        _cell(_task_model_selection(task)),
                        _cell(task.get("delivery_state")),
                        _cell(task.get("integration_state")),
                        _cell(task.get("report_path")),
                        _cell(_task_updated_at(task)),
                    )
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def _render_status(state: dict[str, Any]) -> str:
    tasks: list[dict[str, Any]] = state["tasks"]
    grouped = _group_tasks(tasks)
    pm_control: dict[str, Any] = state["pm_control"]
    lines = ["# Project Status", ""]
    lines.extend(_generated_warning(state))
    lines.extend(
        [
            "## Project control",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Project | {_cell(state.get('project_id'))} |",
            f"| Schema | `{_cell(state.get('schema_version'))}` |",
            f"| Adoption level | `{_cell(state.get('adoption_level'))}` |",
            f"| State updated | `{_cell(state.get('updated_at'))}` |",
            f"| PM holder | {_cell(pm_control.get('holder_id'))} |",
            f"| PM lease epoch | {_cell(pm_control.get('lease_epoch'))} |",
            f"| PM control mode | `{_cell(pm_control.get('mode'))}` |",
            "",
            "## State summary",
            "",
        ]
    )
    lines.extend(_render_summary_table(grouped))
    lines.extend(
        [
            "",
            "## Queues by state",
            "",
            "| State | Tasks |",
            "| --- | --- |",
        ]
    )
    for task_state in STATES:
        task_ids = ", ".join(_cell(task.get("task_id")) for task in grouped[task_state]) or "—"
        lines.append(f"| `{task_state}` | {task_ids} |")

    lines.extend(
        [
            "",
            "## Current dispatches",
            "",
            "| Task | State | Dispatch | Role | Required model | Selected model | Binding | Branch | Base commit |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    dispatched_rows = []
    for task in sorted(tasks, key=lambda item: str(item.get("task_id", ""))):
        dispatch = task.get("current_dispatch")
        if not isinstance(dispatch, dict):
            continue
        selection = _dispatch_model_selection(dispatch) or {}
        dispatched_rows.append(
            "| "
            + " | ".join(
                (
                    _cell(task.get("task_id")),
                    _cell(task.get("state")),
                    _cell(dispatch.get("dispatch_id")),
                    _cell(dispatch.get("role_id")),
                    _cell(selection.get("required_model_tier")),
                    _cell(_task_model_selection(task)),
                    _cell(selection.get("model_binding_id")),
                    _cell(dispatch.get("branch")),
                    _cell(dispatch.get("base_commit")),
                )
            )
            + " |"
        )
    lines.extend(dispatched_rows or ["| — | — | — | — | — | — | — | — | — |"])
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RenderError(f"cannot create output directory {path.parent}: {exc}") from exc
    previous_mode: Optional[int] = None
    try:
        previous_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RenderError(f"cannot inspect {path}: {exc}") from exc

    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=str(path.parent)
        )
    except OSError as exc:
        raise RenderError(f"cannot create temporary output beside {path}: {exc}") from exc
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, previous_mode if previous_mode is not None else 0o644)
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise RenderError(f"cannot atomically write {path}: {exc}") from exc


def _check_file(project: Path, relative: Path, expected: str) -> bool:
    path = project / relative
    try:
        actual = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"ERROR {relative.as_posix()} is missing")
        return False
    except OSError as exc:
        print(f"ERROR cannot read {relative.as_posix()}: {exc}")
        return False
    if actual != expected:
        print(
            f"ERROR {relative.as_posix()} is out of date; "
            "run render_views.py without --check"
        )
        return False
    print(f"PASS  {relative.as_posix()} is up to date")
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render BOARD.md and STATUS.md from docs/pm/state/tasks.yaml."
    )
    parser.add_argument(
        "--project",
        default=".",
        help="project root (default: current directory)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare generated views without writing; exit 1 on drift",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    project = Path(args.project).expanduser().resolve()
    try:
        if not project.is_dir():
            raise RenderError(f"project path is not a directory: {project}")
        state = _load_state(project)
        board_path = _safe_project_path(project, BOARD_FILE)
        status_path = _safe_project_path(project, STATUS_FILE)
        board = _render_board(state)
        status = _render_status(state)
        if args.check:
            board_ok = _check_file(project, BOARD_FILE, board)
            status_ok = _check_file(project, STATUS_FILE, status)
            return 0 if board_ok and status_ok else 1

        # Render both documents before either replacement.  Each replacement is
        # atomic, so readers never observe a partially written Markdown file.
        _atomic_write(board_path, board)
        _atomic_write(status_path, status)
        print(f"PASS  generated {BOARD_FILE.as_posix()} atomically")
        print(f"PASS  generated {STATUS_FILE.as_posix()} atomically")
        return 0
    except RenderError as exc:
        print(f"ERROR {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
