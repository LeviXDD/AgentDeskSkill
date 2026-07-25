#!/usr/bin/env python3
"""Render human-readable PM views from the single machine state source.

The input file is JSON-compatible YAML and is parsed with Python's standard
``json`` module.  The renderer never writes back to the state source.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Optional


STATE_FILE = Path("docs/pm/state/tasks.yaml")
BOARD_FILE = Path("docs/pm/BOARD.md")
STATUS_FILE = Path("docs/pm/STATUS.md")
CONSULTATION_POLICY_FILE = Path("docs/pm/CONSULTATION-POLICY.yaml")
CONSULTATION_ROOT = Path("docs/pm/consultations")
CONSULTATION_VIEW_FILE = Path("docs/pm/CONSULTATIONS.md")
SCHEMA_VERSION = "agentdesk.tasks/v2"
CONSULTATION_POLICY_SCHEMA_VERSION = "agentdesk.consultation-policy/v1"
CONSULTATION_RECORD_SCHEMA_VERSION = "agentdesk.consultation/v1"
CONSULTATION_ID_RE = re.compile(r"^C[0-9]{3}$")
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
CONSULTATION_STATES = (
    "draft",
    "awaiting_authorization",
    "authorized",
    "round_1_in_progress",
    "round_1_complete",
    "round_2_in_progress",
    "round_2_complete",
    "awaiting_confirmation",
    "closed",
    "cancelled",
)


class RenderError(Exception):
    """A user-actionable state or filesystem error."""


def _isolated_python_environment() -> dict[str, str]:
    """Remove caller-controlled Python and Git injection from bundled tools."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and not key.startswith("PYTHON")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _require_version_compatibility(
    project: Path,
    *,
    operation: str,
    release_manifest: Optional[Path] = None,
) -> None:
    """Fail closed before a renderer write; keep ``--check`` read-only."""

    checker = Path(__file__).resolve().with_name("check_compatibility.py")
    command = [
        sys.executable,
        "-I",
        str(checker),
        "--project",
        str(project),
        "--operation",
        operation,
        "--json",
    ]
    if release_manifest is not None:
        command.extend(("--release-manifest", str(release_manifest)))
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            env=_isolated_python_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(
            f"cannot run AgentDesk version compatibility gate: {exc}"
        ) from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise RenderError(
            "AgentDesk version compatibility gate returned invalid JSON: "
            f"{detail}"
        ) from exc
    if not isinstance(payload, dict):
        raise RenderError(
            "AgentDesk version compatibility gate returned a non-object"
        )
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        for item in warnings:
            if isinstance(item, dict):
                print(
                    "WARN  version compatibility "
                    f"{item.get('code', 'warning')}: {item.get('message', '')}"
                )
    if result.returncode != 0 or payload.get("compatible") is not True:
        errors = payload.get("errors")
        details = (
            "; ".join(
                f"{item.get('code', 'error')}: {item.get('message', '')}"
                for item in errors
                if isinstance(item, dict)
            )
            if isinstance(errors, list)
            else ""
        )
        raise RenderError(
            f"version compatibility gate blocked {operation}: "
            f"{details or 'no diagnostic'}"
        )


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


def _read_json_object(path: Path, context: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RenderError(f"cannot read {context}: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RenderError(
            f"{context} is not JSON-compatible YAML: JSON parse error at "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise RenderError(f"{context} must contain one JSON object at its root")
    return value


def _load_optional_consultations(
    project: Path,
) -> tuple[Optional[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    """Load the optional consultation policy and per-consultation records.

    Older AgentDesk projects have neither source.  In that case the renderer
    leaves CONSULTATIONS.md alone and keeps its historical two-view contract.
    """

    policy_path = _safe_project_path(project, CONSULTATION_POLICY_FILE)
    root = _safe_project_path(project, CONSULTATION_ROOT)
    policy: Optional[dict[str, Any]] = None
    if policy_path.exists():
        if policy_path.is_symlink() or not policy_path.is_file():
            raise RenderError(
                f"{CONSULTATION_POLICY_FILE.as_posix()} must be a regular non-symlink file"
            )
        policy = _read_json_object(
            policy_path, CONSULTATION_POLICY_FILE.as_posix()
        )
        if policy.get("schema_version") != CONSULTATION_POLICY_SCHEMA_VERSION:
            raise RenderError(
                "consultation policy schema_version must be "
                f"{CONSULTATION_POLICY_SCHEMA_VERSION!r}"
            )

    records: list[tuple[str, dict[str, Any]]] = []
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise RenderError(
                f"{CONSULTATION_ROOT.as_posix()} must be a regular non-symlink directory"
            )
        try:
            entries = sorted(root.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise RenderError(
                f"cannot scan {CONSULTATION_ROOT.as_posix()}: {exc}"
            ) from exc
        for directory in entries:
            if directory.name.startswith("."):
                continue
            if directory.is_symlink():
                raise RenderError(
                    f"consultation directory must not be a symlink: {directory.name}"
                )
            if not directory.is_dir():
                continue
            record_path = directory / "consultation.yaml"
            if not record_path.exists() and not record_path.is_symlink():
                continue
            if CONSULTATION_ID_RE.fullmatch(directory.name) is None:
                raise RenderError(
                    "consultation record directory must match C followed by exactly "
                    f"three digits: {directory.name}"
                )
            if record_path.is_symlink() or not record_path.is_file():
                raise RenderError(
                    "consultation record must be a regular non-symlink file: "
                    f"{record_path.relative_to(project).as_posix()}"
                )
            context = record_path.relative_to(project).as_posix()
            record = _read_json_object(record_path, context)
            if record.get("schema_version") != CONSULTATION_RECORD_SCHEMA_VERSION:
                raise RenderError(
                    f"{context}.schema_version must be "
                    f"{CONSULTATION_RECORD_SCHEMA_VERSION!r}"
                )
            if record.get("consultation_id") != directory.name:
                raise RenderError(
                    f"{context}.consultation_id must equal directory name {directory.name!r}"
                )
            records.append((directory.name, record))

    records.sort(key=lambda item: item[0])
    return policy, records


def _validate_consultation_sources(
    project: Path,
    policy: Optional[dict[str, Any]],
    records: list[tuple[str, dict[str, Any]]],
) -> None:
    """Validate the exact in-memory public snapshot that will be projected."""

    validator_path = Path(__file__).with_name("validate_consultations.py")
    spec = importlib.util.spec_from_file_location(
        "agentdesk_validate_consultations_for_render", validator_path
    )
    if spec is None or spec.loader is None:
        raise RenderError(
            f"cannot load consultation validator: {validator_path.as_posix()}"
        )
    validator = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(validator)
    except (ImportError, OSError, RuntimeError, SyntaxError) as exc:
        raise RenderError(f"cannot load consultation validator: {exc}") from exc
    validate_snapshot = getattr(validator, "validate_public_snapshot", None)
    if not callable(validate_snapshot):
        raise RenderError(
            "consultation validator does not expose validate_public_snapshot"
        )
    diagnostics = io.StringIO()
    with redirect_stdout(diagnostics):
        reporter = validate_snapshot(project, policy, records)
    if reporter.errors:
        error_lines = [
            line
            for line in diagnostics.getvalue().splitlines()
            if line.startswith("ERROR ")
        ]
        preview = "; ".join(error_lines[:3])
        suffix = f": {preview}" if preview else ""
        raise RenderError(
            "consultation sources failed public validation "
            f"({reporter.errors} error(s)){suffix}. Run validate_consultations.py "
            "for the complete report"
        )


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


def _render_consultations(
    policy: Optional[dict[str, Any]], records: list[dict[str, Any]]
) -> str:
    grouped: dict[str, list[dict[str, Any]]] = {
        state: [] for state in CONSULTATION_STATES
    }
    for record in records:
        grouped.setdefault(str(record.get("state", "unknown")), []).append(record)

    updated_values = [
        str(record["updated_at"])
        for record in records
        if _nonempty_string(record.get("updated_at"))
    ]
    generated_at = max(updated_values) if updated_values else "—"
    enabled = policy.get("enabled") if isinstance(policy, dict) else None
    max_seats = policy.get("max_seats") if isinstance(policy, dict) else None
    max_rounds = policy.get("max_rounds") if isinstance(policy, dict) else None

    lines = [
        "# Consultation Board",
        "",
        "<!-- GENERATED FILE: source=docs/pm/consultations/*/consultation.yaml -->",
        "> Generated view — do not edit by hand. Public consultation records are the source for this view.",
        f"> 生成时间（取记录 `updated_at` 的最大值，确保结果可复现）：`{_cell(generated_at)}`",
        "",
        "## Policy",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Enabled | {_cell(enabled)} |",
        f"| Maximum seats | {_cell(max_seats)} |",
        f"| Maximum rounds | {_cell(max_rounds)} |",
        "",
        "## State summary",
        "",
        "| State | Count |",
        "| --- | ---: |",
    ]
    ordered_states = list(CONSULTATION_STATES) + sorted(
        state for state in grouped if state not in CONSULTATION_STATES
    )
    for state in ordered_states:
        lines.append(f"| `{_cell(state)}` | {len(grouped[state])} |")

    lines.extend(
        [
            "",
            "## Consultations",
            "",
            "| Consultation | Topic | State | Seats | Rounds | Outcome | Confirmation | Synthesis | Derived refs | Updated |",
            "| --- | --- | --- | ---: | ---: | --- | --- | --- | ---: | --- |",
        ]
    )
    if not records:
        lines.append("| — | — | — | — | — | — | — | — | — | — |")
    for record in records:
        seats = record.get("seats")
        rounds = record.get("rounds")
        confirmation = record.get("confirmation")
        confirmation_status = (
            confirmation.get("status")
            if isinstance(confirmation, dict)
            else confirmation
        )
        derived_refs = record.get("derived_refs")
        derived_ref_count = None
        if isinstance(derived_refs, dict):
            decision_ids = derived_refs.get("decision_ids")
            task_card_paths = derived_refs.get("task_card_paths")
            if isinstance(decision_ids, list) and isinstance(task_card_paths, list):
                derived_ref_count = len(decision_ids) + len(task_card_paths)
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(record.get("consultation_id")),
                    _cell(record.get("short_topic") or record.get("topic")),
                    _cell(record.get("state")),
                    _cell(len(seats) if isinstance(seats, list) else None),
                    _cell(len(rounds) if isinstance(rounds, list) else None),
                    _cell(record.get("outcome_status")),
                    _cell(confirmation_status),
                    _cell(record.get("synthesis_path")),
                    _cell(derived_ref_count),
                    _cell(record.get("updated_at")),
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _stage_output(path: Path, content: str) -> Path:
    """Durably stage one output beside its destination without replacing it."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RenderError(f"cannot create output directory {path.parent}: {exc}") from exc
    previous_mode: Optional[int] = None
    try:
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise RenderError(
                    f"output path must be a regular non-symlink file: {path}"
                )
            previous_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RenderError(f"cannot inspect {path}: {exc}") from exc

    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.stage.", dir=str(path.parent)
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
        return temporary
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise RenderError(f"cannot stage output for {path}: {exc}") from exc


def _reserve_backup_path(path: Path) -> Path:
    """Reserve an unused same-directory pathname for a rollback rename."""

    try:
        fd, backup_name = tempfile.mkstemp(
            prefix=f".{path.name}.rollback.", dir=str(path.parent)
        )
        os.close(fd)
        backup = Path(backup_name)
        backup.unlink()
        return backup
    except OSError as exc:
        raise RenderError(f"cannot reserve rollback path beside {path}: {exc}") from exc


def _fsync_output_directories(paths: list[Path]) -> None:
    for parent in sorted({path.parent for path in paths}, key=str):
        try:
            directory_fd = os.open(parent, os.O_RDONLY)
        except OSError:
            continue
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        finally:
            os.close(directory_fd)


def _transactional_write(outputs: list[tuple[Path, str]]) -> None:
    """Replace all generated views or restore every prior view on failure."""

    staged: list[tuple[Path, Path]] = []
    try:
        for path, content in outputs:
            staged.append((path, _stage_output(path, content)))
    except RenderError:
        for _path, temporary in staged:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise

    rollback_actions: list[tuple[Path, Optional[Path]]] = []
    try:
        for path, temporary in staged:
            backup: Optional[Path] = None
            if path.exists():
                backup = _reserve_backup_path(path)
                os.replace(path, backup)
            rollback_actions.append((path, backup))
            os.replace(temporary, path)
        _fsync_output_directories([path for path, _content in outputs])
    except (OSError, RenderError) as exc:
        rollback_errors: list[str] = []
        for path, backup in reversed(rollback_actions):
            try:
                if path.exists() or path.is_symlink():
                    path.unlink()
                if backup is not None and backup.exists():
                    os.replace(backup, path)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        for _path, temporary in staged:
            try:
                temporary.unlink()
            except OSError:
                pass
        _fsync_output_directories([path for path, _content in outputs])
        rollback_suffix = (
            f"; rollback also failed for {', '.join(rollback_errors)}"
            if rollback_errors
            else ""
        )
        raise RenderError(
            f"cannot transactionally replace generated views: {exc}{rollback_suffix}"
        ) from exc

    for _path, backup in rollback_actions:
        if backup is None:
            continue
        try:
            backup.unlink()
        except OSError:
            pass
    for _path, temporary in staged:
        try:
            temporary.unlink()
        except OSError:
            pass


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
        description=(
            "Render BOARD.md and STATUS.md from tasks.yaml and, when configured, "
            "CONSULTATIONS.md from public consultation records."
        )
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
    parser.add_argument(
        "--migration-prepare",
        action="store_true",
        help=(
            "write generated views under the explicit migrate compatibility "
            "gate while preparing an approved migration"
        ),
    )
    parser.add_argument(
        "--release-manifest",
        help=(
            "release manifest for an explicit shadow/read check or test bundle "
            "(default: the manifest beside this renderer)"
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.migration_prepare and args.check:
        parser.error("--migration-prepare cannot be combined with --check")
    if args.migration_prepare and args.release_manifest:
        parser.error(
            "--migration-prepare cannot be combined with --release-manifest"
        )
    project = Path(args.project).expanduser().resolve()
    try:
        if not project.is_dir():
            raise RenderError(f"project path is not a directory: {project}")
        if args.release_manifest and not args.check:
            raise RenderError(
                "--release-manifest is read-only and may be used only with --check; "
                "writes must use the manifest beside the executing renderer"
            )
        _require_version_compatibility(
            project,
            operation=(
                "validate"
                if args.check
                else ("migrate" if args.migration_prepare else "write")
            ),
            release_manifest=(
                Path(args.release_manifest).expanduser().resolve()
                if args.release_manifest
                else None
            ),
        )
        state = _load_state(project)
        board_path = _safe_project_path(project, BOARD_FILE)
        status_path = _safe_project_path(project, STATUS_FILE)
        board = _render_board(state)
        status = _render_status(state)
        policy, consultation_record_entries = _load_optional_consultations(project)
        _validate_consultation_sources(
            project, policy, consultation_record_entries
        )
        consultation_sources_exist = policy is not None or bool(
            consultation_record_entries
        )
        consultation_records = [
            record for _directory_name, record in consultation_record_entries
        ]
        outputs = [
            (BOARD_FILE, board_path, board),
            (STATUS_FILE, status_path, status),
        ]
        if consultation_sources_exist:
            consultation_path = _safe_project_path(project, CONSULTATION_VIEW_FILE)
            outputs.append(
                (
                    CONSULTATION_VIEW_FILE,
                    consultation_path,
                    _render_consultations(policy, consultation_records),
                )
            )
        if args.check:
            checks = [
                _check_file(project, relative, content)
                for relative, _path, content in outputs
            ]
            return 0 if all(checks) else 1

        # Stage every document before replacement, then roll all destinations
        # back if any member of the output set cannot be installed.
        _transactional_write(
            [(path, content) for _relative, path, content in outputs]
        )
        for relative, _path, _content in outputs:
            print(f"PASS  generated {relative.as_posix()} transactionally")
        return 0
    except RenderError as exc:
        print(f"ERROR {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
