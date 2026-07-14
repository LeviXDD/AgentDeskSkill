#!/usr/bin/env python3
"""Validate an AgentDesk task-card project without third-party packages.

``tasks.yaml`` is deliberately serialized as JSON-compatible YAML.  JSON is a
subset of YAML, which lets this validator keep a zero-dependency contract and
still provide precise parse errors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional


STATE_FILE = Path("docs/pm/state/tasks.yaml")
ROLE_POLICY_FILE = Path("docs/pm/ROLE-POLICIES.yaml")
MODEL_BINDINGS_FILE = Path(".agentdesk/runtime/model-bindings.yaml")
RUNTIME_PRIVATE_FILES = (
    MODEL_BINDINGS_FILE,
    Path(".agentdesk/runtime/routes.yaml"),
    Path(".agentdesk/runtime/transport-receipts.yaml"),
    Path(".agentdesk/runtime/pm-lease.yaml"),
)
SCHEMA_VERSION = "agentdesk.tasks/v2"
ROLE_POLICY_SCHEMA_VERSION = "agentdesk.role-policies/v1"
MODEL_BINDINGS_SCHEMA_VERSION = "agentdesk.model-bindings/v1"
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
ACTIVE_DISPATCH_STATES = frozenset({"dispatched", "in_progress", "review_ready"})
TERMINAL_STATES = frozenset({"integrated", "cancelled", "superseded"})
DELIVERY_STATES = frozenset(
    {"none", "working", "submitted", "invalid", "accepted", "rejected"}
)
INTEGRATION_STATES = frozenset(
    {"not_applicable", "pending", "integrated", "failed"}
)
BLOCKED_KINDS = frozenset(
    {
        "external_approval",
        "credentials",
        "environment",
        "dependency",
        "role_timeout",
        "report_unreachable",
        "integration_conflict",
        "decision_required",
        "other",
    }
)
TASK_TYPES = frozenset(
    {"implementation", "qa", "integration", "review", "architecture", "docs", "ops", "spike"}
)
PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
RISKS = frozenset({"L0", "L1", "L2", "L3", "L4"})
MODEL_TIERS = ("basic", "standard", "advanced", "expert")
MODEL_TIER_INDEX = {tier: index for index, tier in enumerate(MODEL_TIERS)}
DELIBERATION_TIERS = ("efficient", "balanced", "deep")
DELIBERATION_TIER_INDEX = {
    tier: index for index, tier in enumerate(DELIBERATION_TIERS)
}
DEGRADATION_POLICIES = frozenset(
    {"block", "require_pm_approval", "allow_to_minimum"}
)
TASK_ID_RE = re.compile(r"^TC-[0-9]{3,}$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
PAYLOAD_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EVENT_ID_RE = re.compile(r"^EVT-.+")
MESSAGE_ID_RE = re.compile(r"^MSG-.+")
APPROVAL_ID_RE = re.compile(r"^APR-.+")

REQUIRED_FILES = (
    Path("AGENTS.md"),
    STATE_FILE,
    Path("docs/pm/STATUS.md"),
    Path("docs/pm/BOARD.md"),
    Path("docs/pm/ROLES.md"),
    ROLE_POLICY_FILE,
    Path("docs/pm/CHECKS.yaml"),
    Path("docs/pm/DECISIONS.md"),
    Path("docs/pm/PM-PLAYBOOK.md"),
    Path(".gitignore"),
)
REQUIRED_DIRECTORIES = (
    Path("docs/pm/state"),
    Path("docs/pm/tasks"),
    Path("docs/pm/reports"),
    Path("docs/pm/acceptances"),
    Path("docs/pm/events"),
    Path("docs/pm/outbox"),
    Path(".agentdesk/runtime"),
    Path(".agentdesk/runtime/inbox"),
)
TASK_FIELDS = (
    "task_id",
    "revision",
    "task_card_path",
    "task_card_commit",
    "state",
    "attempt",
    "current_dispatch",
    "report_path",
    "granted_approval_ids",
    "delivery_state",
    "integration_state",
    "implementation_commit",
    "report_commit",
    "accepted_commit",
    "acceptance_path",
    "integrated_commit",
    "blocked_reason",
    "blocked_kind",
    "blocked_owner",
    "unblock_condition",
    "review_after",
    "blocked_attempt_valid",
    "resume_state",
    "timestamps",
)
BLOCKED_STRING_FIELDS = (
    "blocked_reason",
    "blocked_kind",
    "blocked_owner",
    "unblock_condition",
)
BLOCKED_ENVELOPE_FIELDS = BLOCKED_STRING_FIELDS + (
    "review_after",
    "blocked_attempt_valid",
    "resume_state",
)
TIMESTAMP_FIELDS = (
    "created_at",
    "ready_at",
    "dispatched_at",
    "started_at",
    "delivered_at",
    "blocked_at",
    "accepted_at",
    "integrated_at",
    "updated_at",
)
LIFECYCLE_TIMESTAMPS = (
    "created_at",
    "ready_at",
    "dispatched_at",
    "started_at",
    "delivered_at",
    "accepted_at",
    "integrated_at",
    "updated_at",
)
SHA_FIELDS = (
    "task_card_commit",
    "implementation_commit",
    "report_commit",
    "accepted_commit",
    "integrated_commit",
)
MODEL_SELECTION_FIELDS = (
    "required_model_tier",
    "required_model_capabilities",
    "model_binding_id",
    "selected_model_provider",
    "selected_model_id",
    "selected_model_tier",
    "selected_deliberation_tier",
    "selected_model_capabilities",
    "model_degradation_approval_id",
)


class Reporter:
    """Collect and print stable, grep-friendly validation diagnostics."""

    def __init__(self, allow_legacy_model_evidence: bool = False) -> None:
        self.passes = 0
        self.warnings = 0
        self.errors = 0
        self.allow_legacy_model_evidence = allow_legacy_model_evidence

    def passed(self, message: str) -> None:
        self.passes += 1
        print(f"PASS  {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"WARN  {message}")

    def error(self, message: str) -> None:
        self.errors += 1
        print(f"ERROR {message}")

    def summary(self) -> None:
        if self.allow_legacy_model_evidence:
            self.warn(
                "legacy model-evidence migration mode was enabled; this result "
                "must not be claimed as a strict validation pass or used for "
                "automatic acceptance"
            )
        print(
            "SUMMARY "
            f"{self.passes} pass(es), {self.warnings} warning(s), "
            f"{self.errors} error(s)"
        )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_keys(
    value: dict[str, Any], keys: Iterable[str], context: str, reporter: Reporter
) -> bool:
    missing = [key for key in keys if key not in value]
    if missing:
        reporter.error(f"{context} is missing required key(s): {', '.join(missing)}")
        return False
    return True


def _parse_rfc3339_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or "T" not in value:
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed


def _safe_relative_path(
    project: Path,
    raw_path: Any,
    context: str,
    reporter: Reporter,
    required_prefix: Optional[PurePosixPath] = None,
) -> Optional[Path]:
    if not _nonempty_string(raw_path):
        reporter.error(f"{context} must be a non-empty project-relative path")
        return None

    # State paths are always slash-separated, including on Windows checkouts.
    logical = PurePosixPath(raw_path)
    if logical.is_absolute() or ".." in logical.parts or "." in logical.parts:
        reporter.error(f"{context} must not be absolute or contain '.'/'..': {raw_path!r}")
        return None
    if required_prefix is not None:
        prefix_parts = required_prefix.parts
        if logical.parts[: len(prefix_parts)] != prefix_parts:
            reporter.error(f"{context} must be under {required_prefix.as_posix()}/")
            return None

    candidate = project.joinpath(*logical.parts)
    try:
        if os.path.commonpath((str(project), str(candidate.resolve(strict=False)))) != str(project):
            reporter.error(f"{context} escapes the project root: {raw_path!r}")
            return None
    except ValueError:
        reporter.error(f"{context} escapes the project root: {raw_path!r}")
        return None
    return candidate


def _layout_path_is_safe(project: Path, relative: Path, reporter: Reporter) -> bool:
    current = project
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            reporter.error(f"workflow path must not use symlinks: {relative.as_posix()}")
            return False
    try:
        resolved = (project / relative).resolve(strict=False)
        if os.path.commonpath((str(project), str(resolved))) != str(project):
            reporter.error(f"workflow path escapes the project root: {relative.as_posix()}")
            return False
    except (OSError, ValueError) as exc:
        reporter.error(f"cannot resolve workflow path {relative.as_posix()}: {exc}")
        return False
    return True


def _load_state(path: Path, reporter: Reporter) -> Optional[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        reporter.error(f"cannot read {STATE_FILE.as_posix()}: {exc}")
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        reporter.error(
            f"{STATE_FILE.as_posix()} is not JSON-compatible YAML: "
            f"JSON parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}. "
            "Serialize it as a JSON object; JSON is valid YAML and this skill "
            "intentionally does not depend on PyYAML."
        )
        return None
    if not isinstance(value, dict):
        reporter.error(f"{STATE_FILE.as_posix()} must contain one JSON object at its root")
        return None
    reporter.passed(f"parsed {STATE_FILE.as_posix()} as JSON-compatible YAML")
    return value


def _parse_json_compatible_object(
    content: str, context: str, reporter: Reporter
) -> Optional[dict[str, Any]]:
    """Parse a zero-dependency JSON-compatible YAML object."""

    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        reporter.error(
            f"{context} is not JSON-compatible YAML: JSON parse error at "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        )
        return None
    if not isinstance(value, dict):
        reporter.error(f"{context} must contain one JSON object at its root")
        return None
    return value


def _read_json_compatible_object(
    path: Path, context: str, reporter: Reporter
) -> Optional[dict[str, Any]]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        reporter.error(f"cannot read {context}: {exc}")
        return None
    return _parse_json_compatible_object(content, context, reporter)


def _capability_list(
    value: Any, context: str, reporter: Reporter
) -> Optional[list[str]]:
    if not isinstance(value, list):
        reporter.error(f"{context} must be an array of non-empty strings")
        return None
    if any(not _nonempty_string(item) for item in value):
        reporter.error(f"{context} must be an array of non-empty strings")
        return None
    capabilities = [item.strip() for item in value]
    if len(capabilities) != len(set(capabilities)):
        reporter.error(f"{context} must not contain duplicates")
        return None
    return capabilities


def _validate_role_policy_object(
    value: dict[str, Any], context: str, reporter: Reporter
) -> dict[str, Any]:
    """Validate and normalize a canonical role/model policy object."""

    _require_keys(
        value,
        (
            "schema_version",
            "tier_order",
            "deliberation_tier_order",
            "risk_floors",
            "roles",
        ),
        context,
        reporter,
    )
    if value.get("schema_version") != ROLE_POLICY_SCHEMA_VERSION:
        reporter.error(
            f"{context}.schema_version must be {ROLE_POLICY_SCHEMA_VERSION!r}"
        )

    if value.get("tier_order") != list(MODEL_TIERS):
        reporter.error(
            f"{context}.tier_order must be exactly {list(MODEL_TIERS)!r}"
        )
    if value.get("deliberation_tier_order") != list(DELIBERATION_TIERS):
        reporter.error(
            f"{context}.deliberation_tier_order must be exactly "
            f"{list(DELIBERATION_TIERS)!r}"
        )

    normalized_floors: dict[str, str] = {}
    risk_floors = value.get("risk_floors")
    if not isinstance(risk_floors, dict):
        reporter.error(f"{context}.risk_floors must be an object keyed by L0 through L4")
    else:
        for risk in sorted(RISKS):
            tier = risk_floors.get(risk)
            if tier not in MODEL_TIER_INDEX:
                reporter.error(
                    f"{context}.risk_floors.{risk} must be one of: "
                    f"{', '.join(MODEL_TIERS)}"
                )
            else:
                normalized_floors[risk] = tier
        extra_risks = sorted(set(risk_floors) - RISKS)
        if extra_risks:
            reporter.error(
                f"{context}.risk_floors contains unsupported key(s): "
                f"{', '.join(extra_risks)}"
            )
        previous_index = -1
        for risk in sorted(RISKS):
            tier = normalized_floors.get(risk)
            if tier is None:
                continue
            tier_index = MODEL_TIER_INDEX[tier]
            if tier_index < previous_index:
                reporter.error(
                    f"{context}.risk_floors must not decrease from L0 through L4"
                )
                break
            previous_index = tier_index

    normalized_roles: dict[str, dict[str, Any]] = {}
    roles = value.get("roles")
    if not isinstance(roles, dict) or not roles:
        reporter.error(f"{context}.roles must be a non-empty object keyed by role_id")
    else:
        for role_id, raw_policy in roles.items():
            role_context = f"{context}.roles.{role_id}"
            if not _nonempty_string(role_id):
                reporter.error(f"{context}.roles keys must be non-empty role IDs")
                continue
            if not isinstance(raw_policy, dict):
                reporter.error(f"{role_context} must be an object")
                continue
            _require_keys(
                raw_policy,
                (
                    "default_tier",
                    "minimum_tier",
                    "deliberation_tier",
                    "required_capabilities",
                    "degradation_policy",
                ),
                role_context,
                reporter,
            )
            default_tier = raw_policy.get("default_tier")
            minimum_tier = raw_policy.get("minimum_tier")
            deliberation_tier = raw_policy.get("deliberation_tier")
            degradation_policy = raw_policy.get("degradation_policy")
            if default_tier not in MODEL_TIER_INDEX:
                reporter.error(
                    f"{role_context}.default_tier must be one of: "
                    f"{', '.join(MODEL_TIERS)}"
                )
            if minimum_tier not in MODEL_TIER_INDEX:
                reporter.error(
                    f"{role_context}.minimum_tier must be one of: "
                    f"{', '.join(MODEL_TIERS)}"
                )
            if (
                default_tier in MODEL_TIER_INDEX
                and minimum_tier in MODEL_TIER_INDEX
                and MODEL_TIER_INDEX[default_tier] < MODEL_TIER_INDEX[minimum_tier]
            ):
                reporter.error(
                    f"{role_context}.default_tier must be at least minimum_tier"
                )
            if deliberation_tier not in DELIBERATION_TIER_INDEX:
                reporter.error(
                    f"{role_context}.deliberation_tier must be one of: "
                    f"{', '.join(DELIBERATION_TIERS)}"
                )
            if degradation_policy not in DEGRADATION_POLICIES:
                reporter.error(
                    f"{role_context}.degradation_policy must be one of: "
                    f"{', '.join(sorted(DEGRADATION_POLICIES))}"
                )
            capabilities = _capability_list(
                raw_policy.get("required_capabilities"),
                f"{role_context}.required_capabilities",
                reporter,
            )
            if (
                default_tier in MODEL_TIER_INDEX
                and minimum_tier in MODEL_TIER_INDEX
                and deliberation_tier in DELIBERATION_TIER_INDEX
                and degradation_policy in DEGRADATION_POLICIES
                and capabilities is not None
            ):
                normalized_roles[role_id] = {
                    "default_tier": default_tier,
                    "minimum_tier": minimum_tier,
                    "deliberation_tier": deliberation_tier,
                    "required_capabilities": sorted(capabilities),
                    "degradation_policy": degradation_policy,
                }

    return {
        "risk_floors": normalized_floors,
        "roles": normalized_roles,
    }


def _load_role_policy(project: Path, reporter: Reporter) -> Optional[dict[str, Any]]:
    path = project / ROLE_POLICY_FILE
    if path.is_symlink() or not path.is_file():
        return None
    value = _read_json_compatible_object(path, ROLE_POLICY_FILE.as_posix(), reporter)
    if value is None:
        return None
    starting_errors = reporter.errors
    policy = _validate_role_policy_object(value, ROLE_POLICY_FILE.as_posix(), reporter)
    if reporter.errors == starting_errors:
        reporter.passed(f"parsed and validated {ROLE_POLICY_FILE.as_posix()}")
    return policy


def _validate_model_bindings_object(
    value: dict[str, Any], context: str, reporter: Reporter
) -> dict[str, dict[str, Any]]:
    _require_keys(
        value,
        ("schema_version", "updated_at", "bindings"),
        context,
        reporter,
    )
    if value.get("schema_version") != MODEL_BINDINGS_SCHEMA_VERSION:
        reporter.error(
            f"{context}.schema_version must be {MODEL_BINDINGS_SCHEMA_VERSION!r}"
        )
    updated_at = value.get("updated_at")
    if updated_at is not None and _parse_rfc3339_utc(updated_at) is None:
        reporter.error(f"{context}.updated_at must be a UTC RFC3339 timestamp or null")

    normalized: dict[str, dict[str, Any]] = {}
    bindings = value.get("bindings")
    if not isinstance(bindings, dict):
        reporter.error(f"{context}.bindings must be an object keyed by binding ID")
        return normalized
    for binding_id, raw_binding in bindings.items():
        binding_context = f"{context}.bindings.{binding_id}"
        if not _nonempty_string(binding_id):
            reporter.error(f"{context}.bindings keys must be non-empty binding IDs")
            continue
        if binding_id != binding_id.strip():
            reporter.error(
                f"{binding_context} binding ID must not contain outer whitespace"
            )
            continue
        if not isinstance(raw_binding, dict):
            reporter.error(f"{binding_context} must be an object")
            continue
        _require_keys(
            raw_binding,
            (
                "provider",
                "model_id",
                "tier",
                "deliberation_tier",
                "capabilities",
                "enabled",
            ),
            binding_context,
            reporter,
        )
        provider = raw_binding.get("provider")
        model_id = raw_binding.get("model_id")
        tier = raw_binding.get("tier")
        deliberation_tier = raw_binding.get("deliberation_tier")
        enabled = raw_binding.get("enabled")
        if not _nonempty_string(provider):
            reporter.error(f"{binding_context}.provider must be a non-empty string")
        elif provider != provider.strip():
            reporter.error(
                f"{binding_context}.provider must not contain outer whitespace"
            )
        if not _nonempty_string(model_id):
            reporter.error(f"{binding_context}.model_id must be a non-empty string")
        elif model_id != model_id.strip():
            reporter.error(
                f"{binding_context}.model_id must not contain outer whitespace"
            )
        if tier not in MODEL_TIER_INDEX:
            reporter.error(
                f"{binding_context}.tier must be one of: {', '.join(MODEL_TIERS)}"
            )
        if deliberation_tier not in DELIBERATION_TIER_INDEX:
            reporter.error(
                f"{binding_context}.deliberation_tier must be one of: "
                f"{', '.join(DELIBERATION_TIERS)}"
            )
        if not isinstance(enabled, bool):
            reporter.error(f"{binding_context}.enabled must be boolean")
        capabilities = _capability_list(
            raw_binding.get("capabilities"),
            f"{binding_context}.capabilities",
            reporter,
        )
        if (
            _nonempty_string(provider)
            and _nonempty_string(model_id)
            and provider == provider.strip()
            and model_id == model_id.strip()
            and tier in MODEL_TIER_INDEX
            and deliberation_tier in DELIBERATION_TIER_INDEX
            and isinstance(enabled, bool)
            and capabilities is not None
        ):
            normalized[binding_id] = {
                "provider": provider,
                "model_id": model_id,
                "tier": tier,
                "deliberation_tier": deliberation_tier,
                "capabilities": sorted(capabilities),
                "enabled": enabled,
            }
    return normalized


def _load_model_bindings(
    project: Path, reporter: Reporter
) -> Optional[dict[str, dict[str, Any]]]:
    path = project / MODEL_BINDINGS_FILE
    logical_path = MODEL_BINDINGS_FILE.as_posix()
    if not path.exists():
        reporter.warn(f"optional runtime file not created yet: {logical_path}")
        return None
    if path.is_symlink() or not path.is_file():
        reporter.error(f"{logical_path} must be a regular non-symlink file")
        return None

    value = _read_json_compatible_object(path, MODEL_BINDINGS_FILE.as_posix(), reporter)
    if value is None:
        return None
    starting_errors = reporter.errors
    bindings = _validate_model_bindings_object(
        value, MODEL_BINDINGS_FILE.as_posix(), reporter
    )
    if reporter.errors == starting_errors:
        reporter.passed(f"parsed and validated {MODEL_BINDINGS_FILE.as_posix()}")
    return bindings


def _validate_runtime_privacy(project: Path, reporter: Reporter) -> None:
    """Require every local runtime evidence file to stay out of Git."""

    for relative in RUNTIME_PRIVATE_FILES:
        logical_path = relative.as_posix()
        tracked = _run_git(
            project, ["ls-files", "--error-unmatch", "--", logical_path]
        )
        if tracked is None:
            reporter.warn(f"could not verify that {logical_path} is untracked")
        elif tracked.returncode == 0:
            reporter.error(
                f"{logical_path} must not be Git tracked; remove it from the index "
                "without deleting the local runtime file"
            )
        elif tracked.returncode == 1:
            reporter.passed(f"runtime file is not Git tracked: {logical_path}")
        else:
            reporter.warn(f"could not verify that {logical_path} is untracked")

        ignored = _run_git(
            project,
            ["check-ignore", "--quiet", "--no-index", "--", logical_path],
        )
        if ignored is None:
            reporter.warn(f"could not verify that {logical_path} is gitignored")
        elif ignored.returncode == 0:
            reporter.passed(f"runtime file is gitignored: {logical_path}")
        elif ignored.returncode == 1:
            reporter.error(
                f"{logical_path} must be excluded by an effective Git ignore rule"
            )
        else:
            reporter.warn(f"could not verify that {logical_path} is gitignored")


def _validate_project_layout(project: Path, reporter: Reporter) -> None:
    for relative in REQUIRED_DIRECTORIES:
        if not _layout_path_is_safe(project, relative, reporter):
            continue
        path = project / relative
        if path.is_dir():
            reporter.passed(f"required directory exists: {relative.as_posix()}/")
        elif path.exists():
            reporter.error(f"required directory is not a directory: {relative.as_posix()}")
        else:
            reporter.error(f"required directory is missing: {relative.as_posix()}/")

    for relative in REQUIRED_FILES:
        if not _layout_path_is_safe(project, relative, reporter):
            continue
        path = project / relative
        if path.is_file():
            reporter.passed(f"required file exists: {relative.as_posix()}")
        elif path.exists():
            reporter.error(f"required file is not a regular file: {relative.as_posix()}")
        else:
            reporter.error(f"required file is missing: {relative.as_posix()}")

    gitignore = project / ".gitignore"
    if not gitignore.is_file():
        return
    try:
        entries = {
            line.strip()
            for line in gitignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    except OSError as exc:
        reporter.error(f"cannot read .gitignore: {exc}")
        return
    if ".agentdesk/runtime/" in entries:
        reporter.passed(".gitignore contains .agentdesk/runtime/")
    else:
        reporter.error(".gitignore must contain the exact entry '.agentdesk/runtime/'")

    # These files are runtime products, not initialization prerequisites.  A
    # missing file is useful operational context, but it must not make a fresh
    # project invalid.
    for relative in (
        Path(".agentdesk/runtime/routes.yaml"),
        Path(".agentdesk/runtime/pm-lease.yaml"),
    ):
        path = project / relative
        if path.is_file():
            reporter.passed(f"runtime file exists: {relative.as_posix()}")
        elif path.exists():
            reporter.warn(f"runtime path is not a regular file: {relative.as_posix()}")
        else:
            reporter.warn(f"runtime file not created yet: {relative.as_posix()}")


def _validate_git_worktree(
    project: Path, reporter: Reporter, require_committed: bool
) -> None:
    result = _run_git(project, ["rev-parse", "--show-toplevel"])
    if result is None:
        reporter.error("Git is unavailable; immutable workflow evidence cannot be verified")
        return
    if result.returncode != 0:
        reporter.error("project is not a Git worktree; initialize Git before using task cards")
        return
    try:
        root = Path(result.stdout.strip()).resolve()
    except OSError as exc:
        reporter.error(f"cannot resolve Git worktree root: {exc}")
        return
    if root != project:
        reporter.error(
            f"project must be the Git worktree root; detected repository root: {root}"
        )
        return
    reporter.passed("project is the Git worktree root")

    replace_refs = _run_git(
        project, ["for-each-ref", "--format=%(refname)", "refs/replace"]
    )
    if replace_refs is None or replace_refs.returncode != 0:
        reporter.warn("could not inspect Git replace refs")
    elif replace_refs.stdout.strip():
        reporter.error(
            "Git replace refs are not allowed for immutable workflow evidence"
        )
    else:
        reporter.passed("Git replace refs are absent")

    graft_path_result = _run_git(project, ["rev-parse", "--git-path", "info/grafts"])
    if graft_path_result is None or graft_path_result.returncode != 0:
        reporter.warn("could not inspect legacy Git grafts")
    else:
        graft_path = Path(graft_path_result.stdout.strip())
        if not graft_path.is_absolute():
            graft_path = project / graft_path
        try:
            has_grafts = graft_path.is_file() and bool(
                graft_path.read_text(encoding="utf-8").strip()
            )
        except OSError as exc:
            reporter.error(f"cannot inspect legacy Git grafts: {exc}")
        else:
            if has_grafts:
                reporter.error(
                    "legacy Git grafts are not allowed for immutable workflow evidence"
                )
            else:
                reporter.passed("legacy Git grafts are absent")
    head_commit = _git_head_commit(project)
    if head_commit is None:
        reporter.warn("could not verify the repository HEAD")
    elif head_commit:
        reporter.passed("repository HEAD resolves to a full commit SHA")
        control_paths = (
            "AGENTS.md",
            "docs/pm/state/tasks.yaml",
            "docs/pm/BOARD.md",
            "docs/pm/STATUS.md",
            "docs/pm/ROLES.md",
            "docs/pm/ROLE-POLICIES.yaml",
            "docs/pm/CHECKS.yaml",
            "docs/pm/DECISIONS.md",
            "docs/pm/PM-PLAYBOOK.md",
            ".gitignore",
        )
        if not require_committed:
            reporter.warn(
                "pre-commit mode: canonical state/views may differ from HEAD until the "
                "proposed snapshot is committed"
            )
        else:
            mismatches: list[str] = []
            for logical_path in control_paths:
                path = project / logical_path
                try:
                    current_content = path.read_text(encoding="utf-8")
                except OSError:
                    mismatches.append(f"{logical_path} (unreadable)")
                    continue
                committed_content = _git_read_blob(project, head_commit, logical_path)
                if committed_content is None:
                    mismatches.append(f"{logical_path} (not committed)")
                elif committed_content != current_content:
                    mismatches.append(f"{logical_path} (differs from HEAD)")

            status = _run_git(
                project,
                [
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--",
                    *control_paths,
                ],
            )
            if status is None or status.returncode != 0:
                mismatches.append("Git index/worktree status could not be inspected")
            elif status.stdout.strip() and not mismatches:
                mismatches.extend(
                    line[3:] if len(line) > 3 else line
                    for line in status.stdout.splitlines()
                )
            if mismatches:
                reporter.error(
                    "control-plane state/views must match committed HEAD exactly: "
                    + ", ".join(dict.fromkeys(mismatches))
                )
            else:
                reporter.passed(
                    "canonical state and generated views match committed HEAD exactly"
                )
    else:
        reporter.warn("repository has no baseline commit yet; create one before the first task")


def _validate_top_level(state: dict[str, Any], reporter: Reporter) -> Optional[list[Any]]:
    _require_keys(
        state,
        ("schema_version", "project_id", "adoption_level", "updated_at", "pm_control", "tasks"),
        "state root",
        reporter,
    )

    if state.get("schema_version") == SCHEMA_VERSION:
        reporter.passed(f"schema_version is {SCHEMA_VERSION}")
    else:
        reporter.error(
            f"schema_version must be {SCHEMA_VERSION!r}, got {state.get('schema_version')!r}"
        )

    if _nonempty_string(state.get("project_id")):
        reporter.passed(f"project_id is set: {state['project_id']}")
    else:
        reporter.error("project_id must be a non-empty string")

    adoption_level = state.get("adoption_level")
    if adoption_level not in {"lite", "standard", "automated"}:
        reporter.error("adoption_level must be one of: lite, standard, automated")

    if _parse_rfc3339_utc(state.get("updated_at")) is None:
        reporter.error("updated_at must be a UTC RFC3339 timestamp")

    pm_control = state.get("pm_control")
    if not isinstance(pm_control, dict):
        reporter.error("pm_control must be an object")
    else:
        _require_keys(pm_control, ("holder_id", "lease_epoch", "mode"), "pm_control", reporter)
        if not _nonempty_string(pm_control.get("holder_id")):
            reporter.error("pm_control.holder_id must be a non-empty string")
        epoch = pm_control.get("lease_epoch")
        if not _is_int(epoch) or epoch < 1:
            reporter.error("pm_control.lease_epoch must be an integer >= 1")
        if pm_control.get("mode") not in {"manual", "timed"}:
            reporter.error("pm_control.mode must be one of: manual, timed")
        if (
            adoption_level == "lite"
            and pm_control.get("mode") not in {None, "manual"}
        ):
            reporter.warn("lite adoption normally uses pm_control.mode='manual'")
        if (
            adoption_level in {"standard", "automated"}
            and pm_control.get("mode") not in {None, "timed"}
        ):
            reporter.warn(f"{adoption_level} adoption normally uses pm_control.mode='timed'")

    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        reporter.error("tasks must be an array (an empty array is valid)")
        return None
    if not tasks:
        reporter.passed("tasks is an empty array; empty projects are valid")
    return tasks


def _validate_sha(value: Any, context: str, reporter: Reporter, required: bool = False) -> bool:
    if value is None:
        if required:
            reporter.error(f"{context} is required and must be a full 40-character SHA")
            return False
        return True
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        reporter.error(f"{context} must be a full 40-character hexadecimal SHA or null")
        return False
    return True


def _run_git(project: Path, arguments: list[str]) -> Optional[subprocess.CompletedProcess[str]]:
    """Run a read-only Git query without a shell, or return None if it cannot run."""

    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
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
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(project), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
            env=environment,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _git_object_has_type(project: Path, object_spec: str, expected: str) -> Optional[bool]:
    result = _run_git(project, ["cat-file", "-t", object_spec])
    if result is None:
        return None
    return result.returncode == 0 and result.stdout.strip() == expected


def _git_commit_exists(project: Path, commit: str) -> Optional[bool]:
    return _git_object_has_type(project, commit, "commit")


def _git_blob_exists(project: Path, commit: str, logical_path: str) -> Optional[bool]:
    result = _run_git(project, ["ls-tree", "-z", commit, "--", logical_path])
    if result is None or result.returncode != 0:
        return None
    entries = [entry for entry in result.stdout.split("\0") if entry]
    if len(entries) != 1 or "\t" not in entries[0]:
        return False
    metadata, returned_path = entries[0].split("\t", 1)
    parts = metadata.split()
    return (
        returned_path == logical_path
        and len(parts) == 3
        and parts[0] in {"100644", "100755"}
        and parts[1] == "blob"
    )


def _git_read_blob(project: Path, commit: str, logical_path: str) -> Optional[str]:
    result = _run_git(project, ["cat-file", "blob", f"{commit}:{logical_path}"])
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def _git_head_commit(project: Path) -> Optional[str]:
    result = _run_git(project, ["rev-parse", "--verify", "HEAD"])
    if result is None:
        return None
    value = result.stdout.strip()
    if result.returncode == 0 and SHA_RE.fullmatch(value):
        return value
    return ""


def _git_is_ancestor(project: Path, ancestor: str, descendant: str) -> Optional[bool]:
    result = _run_git(project, ["merge-base", "--is-ancestor", ancestor, descendant])
    if result is None or result.returncode not in {0, 1}:
        return None
    return result.returncode == 0


def _matching_integration_event(
    project: Path,
    task_id: str,
    accepted_commit: str,
    integrated_commit: str,
) -> Optional[Path]:
    events_directory = project / "docs/pm/events"
    try:
        candidates = sorted(events_directory.glob("*.yaml"))
    except OSError:
        return None
    head_commit = _git_head_commit(project)
    if not head_commit:
        return None
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        relative = path.relative_to(project)
        committed_content = _git_read_blob(project, head_commit, relative.as_posix())
        if committed_content != content:
            continue
        values: dict[str, Any] = {}
        for line in content.splitlines():
            if not line or line[0].isspace() or ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                values[key] = _parse_frontmatter_scalar(raw_value)
        if (
            values.get("schema_version") == "agentdesk.state-event/v2"
            and values.get("event_type") == "CHANGE_INTEGRATED"
            and values.get("task_id") == task_id
            and values.get("from_state") == "accepted"
            and values.get("to_state") == "integrated"
            and values.get("accepted_commit") == accepted_commit
            and values.get("integrated_commit") == integrated_commit
            and values.get("equivalence_method")
            in {"patch_id", "tree", "approved_mapping"}
            and values.get("equivalence_result") == "passed"
            and _nonempty_string(values.get("equivalence_evidence_ref"))
        ):
            return relative
    return None


def _validate_dispatch(
    task: dict[str, Any], context: str, state: str, attempt: Any, reporter: Reporter
) -> Optional[str]:
    dispatch = task.get("current_dispatch")
    blocked_attempt_valid = task.get("blocked_attempt_valid")
    must_have = state in ACTIVE_DISPATCH_STATES or (
        state == "blocked" and blocked_attempt_valid is True
    )
    must_be_empty = state not in ACTIVE_DISPATCH_STATES and not (
        state == "blocked" and blocked_attempt_valid is True
    )

    if must_have and not isinstance(dispatch, dict):
        reporter.error(f"{context}.current_dispatch must be an object while state={state!r}")
        return None
    if must_be_empty and dispatch is not None:
        reporter.error(f"{context}.current_dispatch must be null while state={state!r}")
        return None
    if dispatch is None:
        return None
    if not isinstance(dispatch, dict):
        reporter.error(f"{context}.current_dispatch must be an object or null")
        return None

    _require_keys(
        dispatch,
        ("dispatch_id", "role_id", "base_commit", "branch"),
        f"{context}.current_dispatch",
        reporter,
    )
    for key in ("dispatch_id", "role_id", "branch"):
        if not _nonempty_string(dispatch.get(key)):
            reporter.error(f"{context}.current_dispatch.{key} must be a non-empty string")
    _validate_sha(
        dispatch.get("base_commit"),
        f"{context}.current_dispatch.base_commit",
        reporter,
        required=True,
    )
    if not _is_int(attempt) or attempt < 1:
        reporter.error(f"{context}.attempt must be >= 1 while a dispatch is active")
    dispatch_id = dispatch.get("dispatch_id")
    return dispatch_id if _nonempty_string(dispatch_id) else None


def _validate_model_selection(
    provider: Any,
    model_id: Any,
    tier: Any,
    deliberation_tier: Any,
    raw_capabilities: Any,
    requirement: Optional[dict[str, Any]],
    context: str,
    reporter: Reporter,
) -> Optional[list[str]]:
    if not _nonempty_string(provider):
        reporter.error(f"{context} provider must be a non-empty string")
    if not _nonempty_string(model_id):
        reporter.error(f"{context} model_id must be a non-empty string")
    if tier not in MODEL_TIER_INDEX:
        reporter.error(f"{context} tier must be one of: {', '.join(MODEL_TIERS)}")
    if deliberation_tier not in DELIBERATION_TIER_INDEX:
        reporter.error(
            f"{context} deliberation_tier must be one of: "
            f"{', '.join(DELIBERATION_TIERS)}"
        )
    capabilities = _capability_list(
        raw_capabilities, f"{context} capabilities", reporter
    )
    if requirement is not None:
        required_tier = requirement["required_tier"]
        if (
            tier in MODEL_TIER_INDEX
            and MODEL_TIER_INDEX[tier] < MODEL_TIER_INDEX[required_tier]
        ):
            reporter.error(
                f"{context} tier {tier!r} is below frozen required tier "
                f"{required_tier!r}"
            )
        required_deliberation = requirement["deliberation_tier"]
        if (
            deliberation_tier in DELIBERATION_TIER_INDEX
            and DELIBERATION_TIER_INDEX[deliberation_tier]
            < DELIBERATION_TIER_INDEX[required_deliberation]
        ):
            reporter.error(
                f"{context} deliberation_tier {deliberation_tier!r} is below "
                f"frozen requirement {required_deliberation!r}"
            )
        if capabilities is not None:
            missing = sorted(
                set(requirement["required_capabilities"]) - set(capabilities)
            )
            if missing:
                reporter.error(
                    f"{context} capabilities are missing frozen requirement(s): "
                    f"{', '.join(missing)}"
                )
    return capabilities


def _selector_environment() -> dict[str, str]:
    """Return a deterministic environment without caller-controlled Git hooks."""

    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
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


def _verify_selector_snapshot(
    project: Path,
    task: dict[str, Any],
    task_card: Optional[dict[str, Any]],
    selection: dict[str, Any],
    context: str,
    reporter: Reporter,
) -> None:
    """Prove an active snapshot is the deterministic selector's exact output."""

    if not isinstance(task_card, dict):
        reporter.error(
            f"{context} cannot replay model selection without frozen task-card frontmatter"
        )
        return
    task_card_commit = task.get("task_card_commit")
    role_id = task_card.get("role_id")
    risk = task_card.get("risk")
    task_min_tier = task_card.get("min_model_tier")
    raw_capabilities = task_card.get("required_model_capabilities")
    if not isinstance(task_card_commit, str) or SHA_RE.fullmatch(task_card_commit) is None:
        reporter.error(f"{context} cannot replay selector without a full task_card_commit")
        return
    if not _nonempty_string(role_id) or risk not in RISKS:
        reporter.error(f"{context} cannot replay selector with invalid frozen role_id/risk")
        return
    if task_min_tier != "inherit" and task_min_tier not in MODEL_TIER_INDEX:
        reporter.error(f"{context} cannot replay selector with invalid min_model_tier")
        return
    task_capabilities = _capability_list(
        raw_capabilities,
        f"{context} frozen required_model_capabilities",
        reporter,
    )
    if task_capabilities is None:
        reporter.error(
            f"{context} cannot replay selector with invalid frozen capabilities"
        )
        return

    selector = Path(__file__).resolve().with_name("select_model.py")
    if not selector.is_file() or selector.is_symlink():
        reporter.error(
            f"{context} cannot replay selector because {selector} is not a regular file"
        )
        return
    if not _nonempty_string(sys.executable):
        reporter.error(f"{context} cannot replay selector: current Python is unknown")
        return

    arguments = [
        sys.executable,
        str(selector),
        "--project",
        str(project),
        f"--task-card-commit={task_card_commit}",
        f"--role-id={role_id}",
        f"--risk={risk}",
        f"--task-min-tier={task_min_tier}",
    ]
    arguments.extend(
        f"--required-capability={capability}" for capability in task_capabilities
    )
    degradation_approval_id = selection.get("model_degradation_approval_id")
    if degradation_approval_id is not None:
        if not _nonempty_string(degradation_approval_id):
            reporter.error(
                f"{context} cannot replay selector with an invalid degradation approval ID"
            )
            return
        arguments.append(
            f"--degradation-approval-id={degradation_approval_id}"
        )

    try:
        result = subprocess.run(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_selector_environment(),
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        reporter.error(f"{context} could not execute deterministic selector: {exc}")
        return
    except subprocess.TimeoutExpired:
        reporter.error(f"{context} deterministic selector timed out")
        return

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        if len(detail) > 1000:
            detail = detail[:997] + "..."
        reporter.error(
            f"{context} deterministic selector failed with exit "
            f"{result.returncode}: {detail}"
        )
        return
    try:
        expected = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        reporter.error(
            f"{context} deterministic selector returned non-JSON stdout at "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        )
        return
    if not isinstance(expected, dict):
        reporter.error(f"{context} deterministic selector output must be an object")
        return
    expected_keys = set(MODEL_SELECTION_FIELDS)
    actual_keys = set(expected)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        reporter.error(
            f"{context} deterministic selector must return exactly nine fields "
            f"({'; '.join(details)})"
        )
        return

    snapshot_keys = set(selection)
    mismatches = [
        key for key in MODEL_SELECTION_FIELDS if selection.get(key) != expected.get(key)
    ]
    missing_snapshot_keys = sorted(expected_keys - snapshot_keys)
    extra_snapshot_keys = sorted(snapshot_keys - expected_keys)
    if mismatches or missing_snapshot_keys or extra_snapshot_keys:
        details: list[str] = []
        if mismatches:
            details.append("mismatched=" + ",".join(mismatches))
        if missing_snapshot_keys:
            details.append("missing=" + ",".join(missing_snapshot_keys))
        if extra_snapshot_keys:
            details.append("extra=" + ",".join(extra_snapshot_keys))
        reporter.error(
            f"{context} is not the deterministic selector's exact output; "
            + "; ".join(details)
        )
    else:
        reporter.passed(f"{context} exactly matches deterministic selector output")


def _run_git_bytes(
    project: Path, arguments: list[str]
) -> Optional[subprocess.CompletedProcess[bytes]]:
    """Run a read-only Git query with byte-exact output and a clean environment."""

    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(project), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_selector_environment(),
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _git_blob_bytes(project: Path, commit: str, logical_path: str) -> Optional[bytes]:
    result = _run_git_bytes(
        project, ["cat-file", "blob", f"{commit}:{logical_path}"]
    )
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def _evidence_document(
    raw: bytes, logical_path: str, context: str, reporter: Reporter
) -> Optional[dict[str, Any]]:
    if b"\r" in raw:
        reporter.error(f"{context} must use repository LF line endings: {logical_path}")
        return None
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        reporter.error(f"{context} is not valid UTF-8: {logical_path}")
        return None
    values = _yaml_subset_mapping_from_text(content, context, reporter)
    return {"path": logical_path, "values": values, "raw": raw}


def _commit_evidence_documents(
    project: Path,
    commit: str,
    directory: PurePosixPath,
    context: str,
    reporter: Reporter,
) -> list[dict[str, Any]]:
    result = _run_git_bytes(
        project,
        ["ls-tree", "-rz", "--full-tree", commit, "--", directory.as_posix()],
    )
    if result is None or result.returncode != 0:
        reporter.error(
            f"cannot index committed {context} evidence at {commit[:12]}"
        )
        return []
    documents: list[dict[str, Any]] = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = header.decode("ascii").split(" ", 2)
            logical_path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            reporter.error(
                f"cannot parse committed {context} tree entry at {commit[:12]}"
            )
            continue
        logical = PurePosixPath(logical_path)
        if logical.parent != directory or logical.suffix != ".yaml":
            continue
        if mode not in {"100644", "100755"} or object_type != "blob":
            reporter.error(
                f"committed {context} evidence must be a regular blob, got "
                f"mode={mode} type={object_type}: {logical_path}"
            )
            continue
        if SHA_RE.fullmatch(object_id) is None:
            reporter.error(f"invalid Git object ID for committed evidence: {logical_path}")
            continue
        raw = _git_blob_bytes(project, commit, logical_path)
        if raw is None:
            reporter.error(
                f"cannot read committed {context} evidence blob: "
                f"{commit}:{logical_path}"
            )
            continue
        document = _evidence_document(
            raw,
            logical_path,
            f"committed {context} evidence {logical_path}",
            reporter,
        )
        if document is not None:
            documents.append(document)
    return documents


def _worktree_evidence_documents(
    project: Path,
    directory: PurePosixPath,
    context: str,
    reporter: Reporter,
) -> list[dict[str, Any]]:
    filesystem_directory = project.joinpath(*directory.parts)
    try:
        paths = sorted(filesystem_directory.glob("*.yaml"))
    except OSError as exc:
        reporter.error(f"cannot index worktree {context} evidence: {exc}")
        return []
    documents: list[dict[str, Any]] = []
    for path in paths:
        logical_path = path.relative_to(project).as_posix()
        if path.is_symlink():
            reporter.error(f"worktree {context} evidence must not be a symlink: {logical_path}")
            continue
        if not path.is_file():
            reporter.error(
                f"worktree {context} evidence must be a regular file: {logical_path}"
            )
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            reporter.error(f"cannot read worktree {context} evidence {logical_path}: {exc}")
            continue
        document = _evidence_document(
            raw,
            logical_path,
            f"worktree {context} evidence {logical_path}",
            reporter,
        )
        if document is not None:
            documents.append(document)
    return documents


def _validate_evidence_id_uniqueness(
    event_documents: list[dict[str, Any]],
    outbox_documents: list[dict[str, Any]],
    context: str,
    reporter: Reporter,
) -> None:
    for documents, id_field, label in (
        (event_documents, "event_id", "event_id"),
        (outbox_documents, "message_id", "message_id"),
    ):
        indexed: dict[str, list[str]] = {}
        for document in documents:
            identifier = document["values"].get(id_field)
            if not _nonempty_string(identifier):
                reporter.error(
                    f"{context} evidence {document['path']} must have a non-empty {id_field}"
                )
                continue
            indexed.setdefault(identifier, []).append(document["path"])
        for identifier, paths in sorted(indexed.items()):
            if len(paths) > 1:
                reporter.error(
                    f"{context} {label} must be globally unique; {identifier!r} "
                    f"is reused by: {', '.join(paths)}"
                )
    for documents, event_type, label in (
        (event_documents, "TASK_DISPATCHED", "TASK_DISPATCHED event"),
        (outbox_documents, "task.dispatch", "task.dispatch outbox message"),
    ):
        indexed_dispatches: dict[str, list[str]] = {}
        type_field = "event_type" if documents is event_documents else "message_type"
        for document in documents:
            values = document["values"]
            if values.get(type_field) != event_type:
                continue
            dispatch_id = values.get("dispatch_id")
            if not _nonempty_string(dispatch_id):
                reporter.error(
                    f"{context} {label} {document['path']} must have a dispatch_id"
                )
                continue
            indexed_dispatches.setdefault(dispatch_id, []).append(document["path"])
        for dispatch_id, paths in sorted(indexed_dispatches.items()):
            if len(paths) > 1:
                reporter.error(
                    f"{context} dispatch_id {dispatch_id!r} is reused by multiple "
                    f"{label}s: {', '.join(paths)}"
                )
    for event_type, label in (
        ("MODEL_DEGRADATION_APPROVED", "approval_id"),
        ("MODEL_DEGRADATION_REVOKED", "revocation for approval_id"),
    ):
        indexed_approvals: dict[str, list[str]] = {}
        for document in event_documents:
            values = document["values"]
            if values.get("event_type") != event_type:
                continue
            approval_id = values.get("approval_id")
            if not _nonempty_string(approval_id):
                reporter.error(
                    f"{context} {event_type} {document['path']} must have approval_id"
                )
                continue
            indexed_approvals.setdefault(approval_id, []).append(document["path"])
        for approval_id, paths in sorted(indexed_approvals.items()):
            if len(paths) > 1:
                reporter.error(
                    f"{context} {label} {approval_id!r} must be unique; found in: "
                    + ", ".join(paths)
                )


def _commit_state_contains_dispatch(
    project: Path,
    commit: str,
    dispatch_id: str,
    context: str,
    reporter: Reporter,
) -> Optional[bool]:
    exists = _git_blob_exists(project, commit, STATE_FILE.as_posix())
    if exists is False:
        return False
    if exists is None:
        reporter.error(f"cannot inspect state blob at {commit[:12]} for {context}")
        return None
    raw = _git_blob_bytes(project, commit, STATE_FILE.as_posix())
    if raw is None:
        reporter.error(f"cannot read state blob at {commit[:12]} for {context}")
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        reporter.error(
            f"state history is not valid JSON-compatible YAML at {commit[:12]}; "
            f"cannot prove first appearance of {dispatch_id}"
        )
        return None
    tasks = value.get("tasks") if isinstance(value, dict) else None
    if not isinstance(tasks, list):
        reporter.error(
            f"state history has no tasks array at {commit[:12]}; "
            f"cannot prove first appearance of {dispatch_id}"
        )
        return None
    for raw_task in tasks:
        if not isinstance(raw_task, dict):
            continue
        dispatch = raw_task.get("current_dispatch")
        if isinstance(dispatch, dict) and dispatch.get("dispatch_id") == dispatch_id:
            return True
    return False


def _git_commit_parents(
    project: Path, commit: str, context: str, reporter: Reporter
) -> Optional[list[str]]:
    result = _run_git(project, ["rev-list", "--parents", "-n", "1", commit])
    if result is None or result.returncode != 0:
        reporter.error(f"cannot inspect Git parents for {context} at {commit[:12]}")
        return None
    parts = result.stdout.strip().split()
    if not parts or parts[0] != commit or any(SHA_RE.fullmatch(item) is None for item in parts):
        reporter.error(f"invalid Git parent metadata for {context} at {commit[:12]}")
        return None
    return parts[1:]


def _dispatch_introduction_commit(
    project: Path, dispatch_id: str, context: str, reporter: Reporter
) -> Optional[tuple[str, list[str]]]:
    result = _run_git(
        project,
        ["rev-list", "--topo-order", "HEAD", "--", STATE_FILE.as_posix()],
    )
    if result is None or result.returncode != 0:
        reporter.error(f"cannot inspect tasks.yaml history for {context}")
        return None
    candidates: list[tuple[str, list[str]]] = []
    for commit in result.stdout.splitlines():
        if SHA_RE.fullmatch(commit) is None:
            reporter.error(f"invalid commit in tasks.yaml history for {context}: {commit!r}")
            return None
        contains = _commit_state_contains_dispatch(
            project, commit, dispatch_id, context, reporter
        )
        if contains is None:
            return None
        if not contains:
            continue
        parents = _git_commit_parents(project, commit, context, reporter)
        if parents is None:
            return None
        parent_contains = False
        for parent in parents:
            contains_in_parent = _commit_state_contains_dispatch(
                project, parent, dispatch_id, context, reporter
            )
            if contains_in_parent is None:
                return None
            parent_contains = parent_contains or contains_in_parent
        if not parent_contains:
            candidates.append((commit, parents))
    if not candidates:
        return None
    if len(candidates) != 1:
        reporter.error(
            f"{context} dispatch_id {dispatch_id!r} has {len(candidates)} "
            "incomparable/reintroduced first appearances; failing closed"
        )
        return None
    commit, parents = candidates[0]
    if len(parents) > 1:
        reporter.error(
            f"{context} dispatch first appears in merge commit {commit}; "
            "atomic evidence proof is ambiguous, failing closed"
        )
        return None
    return commit, parents


def _git_path_exists(
    project: Path, commit: str, logical_path: str
) -> Optional[bool]:
    result = _run_git_bytes(project, ["cat-file", "-e", f"{commit}:{logical_path}"])
    if result is None:
        return None
    if result.returncode == 0:
        return True
    if result.returncode in {1, 128}:
        return False
    return None


def _path_introduction_commit(
    project: Path,
    tip_commit: str,
    logical_path: str,
    context: str,
    reporter: Reporter,
) -> Optional[str]:
    result = _run_git(
        project, ["rev-list", "--topo-order", tip_commit, "--", logical_path]
    )
    if result is None or result.returncode != 0:
        reporter.error(f"cannot inspect introduction history for {context} {logical_path}")
        return None
    candidates: list[tuple[str, list[str]]] = []
    for commit in result.stdout.splitlines():
        if SHA_RE.fullmatch(commit) is None:
            reporter.error(f"invalid commit in evidence history for {logical_path}")
            return None
        exists = _git_path_exists(project, commit, logical_path)
        if exists is not True:
            if exists is None:
                reporter.error(f"cannot inspect {logical_path} at {commit[:12]}")
                return None
            continue
        parents = _git_commit_parents(project, commit, context, reporter)
        if parents is None:
            return None
        existed_in_parent = False
        for parent in parents:
            parent_exists = _git_path_exists(project, parent, logical_path)
            if parent_exists is None:
                reporter.error(f"cannot inspect {logical_path} at parent {parent[:12]}")
                return None
            existed_in_parent = existed_in_parent or parent_exists
        if not existed_in_parent:
            candidates.append((commit, parents))
    if len(candidates) != 1:
        reporter.error(
            f"{context} {logical_path} has {len(candidates)} ambiguous introduction "
            "commits; failing closed"
        )
        return None
    commit, parents = candidates[0]
    if len(parents) > 1:
        reporter.error(
            f"{context} {logical_path} was introduced by merge commit {commit}; "
            "failing closed"
        )
        return None
    return commit


def _matching_documents(
    documents: list[dict[str, Any]],
    kind_field: str,
    kind_value: str,
    dispatch_id: str,
) -> list[dict[str, Any]]:
    return [
        document
        for document in documents
        if document["values"].get(kind_field) == kind_value
        and document["values"].get("dispatch_id") == dispatch_id
    ]


def _require_one_evidence_document(
    documents: list[dict[str, Any]],
    description: str,
    context: str,
    reporter: Reporter,
) -> Optional[dict[str, Any]]:
    if len(documents) != 1:
        reporter.error(
            f"{context} requires exactly one {description}, found {len(documents)}"
        )
        return None
    return documents[0]


def _validate_lease_epoch(value: Any, context: str, reporter: Reporter) -> bool:
    if not _is_int(value) or value < 1:
        reporter.error(f"{context}.lease_epoch must be an integer >= 1")
        return False
    return True


def _validate_task_dispatched_event(
    document: dict[str, Any],
    task: dict[str, Any],
    dispatch: dict[str, Any],
    outbox_raw: bytes,
    context: str,
    reporter: Reporter,
) -> None:
    values = document["values"]
    expected = {
        "schema_version": "agentdesk.state-event/v2",
        "event_type": "TASK_DISPATCHED",
        "task_id": task.get("task_id"),
        "revision": task.get("revision"),
        "attempt": task.get("attempt"),
        "dispatch_id": dispatch.get("dispatch_id"),
        "from_state": "ready",
        "to_state": "dispatched",
        "actor_role_id": "PM",
    }
    _require_keys(
        values,
        tuple(expected) + ("event_id", "lease_epoch", "occurred_at", "payload_digest"),
        f"{context} {document['path']}",
        reporter,
    )
    for key, expected_value in expected.items():
        if values.get(key) != expected_value:
            reporter.error(
                f"{context} {document['path']} field {key!r} must be "
                f"{expected_value!r}"
            )
    event_id = values.get("event_id")
    if not isinstance(event_id, str) or EVENT_ID_RE.fullmatch(event_id) is None:
        reporter.error(f"{context} {document['path']} event_id must start with EVT-")
    if _parse_rfc3339_utc(values.get("occurred_at")) is None:
        reporter.error(f"{context} {document['path']} occurred_at must be UTC RFC3339")
    _validate_lease_epoch(values.get("lease_epoch"), f"{context} {document['path']}", reporter)
    payload_digest = values.get("payload_digest")
    if not isinstance(payload_digest, str) or PAYLOAD_DIGEST_RE.fullmatch(payload_digest) is None:
        reporter.error(
            f"{context} {document['path']} payload_digest must be sha256: plus "
            "64 lowercase hex characters"
        )
    computed_digest = "sha256:" + hashlib.sha256(outbox_raw).hexdigest()
    if payload_digest != computed_digest:
        reporter.error(
            f"{context} {document['path']} payload_digest does not match the "
            "paired outbox file's raw UTF-8 LF bytes"
        )


def _validate_task_dispatch_outbox(
    document: dict[str, Any],
    event_document: dict[str, Any],
    task: dict[str, Any],
    dispatch: dict[str, Any],
    selection: dict[str, Any],
    context: str,
    reporter: Reporter,
) -> None:
    values = document["values"]
    event_values = event_document["values"]
    task_id = task.get("task_id")
    revision = task.get("revision")
    attempt = task.get("attempt")
    dispatch_id = dispatch.get("dispatch_id")
    expected = {
        "schema_version": "agentdesk.outbox-message/v2",
        "message_type": "task.dispatch",
        "event_id": event_values.get("event_id"),
        "task_id": task_id,
        "revision": revision,
        "attempt": attempt,
        "dispatch_id": dispatch_id,
        "destination_role_id": dispatch.get("role_id"),
        "dedupe_key": (
            f"{task_id}/r{revision}/a{attempt}/{dispatch_id}/task.dispatch"
        ),
    }
    _require_keys(
        values,
        tuple(expected) + ("message_id", "created_at", "payload", "model_selection"),
        f"{context} {document['path']}",
        reporter,
    )
    for key, expected_value in expected.items():
        if values.get(key) != expected_value:
            reporter.error(
                f"{context} {document['path']} field {key!r} must be "
                f"{expected_value!r}"
            )
    message_id = values.get("message_id")
    if not isinstance(message_id, str) or MESSAGE_ID_RE.fullmatch(message_id) is None:
        reporter.error(f"{context} {document['path']} message_id must start with MSG-")
    if _parse_rfc3339_utc(values.get("created_at")) is None:
        reporter.error(f"{context} {document['path']} created_at must be UTC RFC3339")

    payload = values.get("payload")
    expected_payload = {
        "task_path": task.get("task_card_path"),
        "task_card_commit": task.get("task_card_commit"),
        "base_commit": dispatch.get("base_commit"),
        "branch": dispatch.get("branch"),
        "report_path": task.get("report_path"),
    }
    if not isinstance(payload, dict):
        reporter.error(f"{context} {document['path']} payload must be an object")
    else:
        for key, expected_value in expected_payload.items():
            if payload.get(key) != expected_value:
                reporter.error(
                    f"{context} {document['path']} payload.{key} must be "
                    f"{expected_value!r}"
                )

    outbox_selection = values.get("model_selection")
    if not isinstance(outbox_selection, dict):
        reporter.error(f"{context} {document['path']} model_selection must be an object")
    else:
        if set(outbox_selection) != set(MODEL_SELECTION_FIELDS):
            reporter.error(
                f"{context} {document['path']} model_selection must contain exactly "
                "the nine selector fields"
            )
        for key in MODEL_SELECTION_FIELDS:
            if outbox_selection.get(key) != selection.get(key):
                reporter.error(
                    f"{context} {document['path']} model_selection.{key} must "
                    "exactly match the frozen dispatch selection"
                )


def _validate_degradation_approval_event(
    document: dict[str, Any],
    task: dict[str, Any],
    dispatch_event: dict[str, Any],
    selection: dict[str, Any],
    requirement: dict[str, Any],
    context: str,
    reporter: Reporter,
) -> None:
    values = document["values"]
    approval_id = selection.get("model_degradation_approval_id")
    expected = {
        "schema_version": "agentdesk.state-event/v2",
        "event_type": "MODEL_DEGRADATION_APPROVED",
        "approval_id": approval_id,
        "purpose": "model_degradation",
        "task_id": task.get("task_id"),
        "revision": task.get("revision"),
        "attempt": task.get("attempt"),
        "approver_role_id": "PM",
        "approved_selected_tier": selection.get("selected_model_tier"),
        "preferred_model_tier": requirement.get("preferred_tier"),
        "revoked_at": None,
    }
    _require_keys(
        values,
        tuple(expected)
        + ("event_id", "lease_epoch", "granted_at", "expires_at", "reason"),
        f"{context} {document['path']}",
        reporter,
    )
    for key, expected_value in expected.items():
        if values.get(key) != expected_value:
            reporter.error(
                f"{context} {document['path']} field {key!r} must be "
                f"{expected_value!r}"
            )
    event_id = values.get("event_id")
    if not isinstance(event_id, str) or EVENT_ID_RE.fullmatch(event_id) is None:
        reporter.error(f"{context} {document['path']} event_id must start with EVT-")
    if not isinstance(approval_id, str) or APPROVAL_ID_RE.fullmatch(approval_id) is None:
        reporter.error(f"{context} {document['path']} approval_id must start with APR-")
    _validate_lease_epoch(values.get("lease_epoch"), f"{context} {document['path']}", reporter)
    if not _nonempty_string(values.get("reason")):
        reporter.error(f"{context} {document['path']} reason must be non-empty")

    dispatched_at = _parse_rfc3339_utc(
        task.get("timestamps", {}).get("dispatched_at")
        if isinstance(task.get("timestamps"), dict)
        else None
    )
    granted_at = _parse_rfc3339_utc(values.get("granted_at"))
    if granted_at is None:
        reporter.error(f"{context} {document['path']} granted_at must be UTC RFC3339")
    if dispatched_at is None:
        reporter.error(f"{context} cannot validate approval time without dispatched_at")
    elif granted_at is not None and granted_at > dispatched_at:
        reporter.error(f"{context} {document['path']} granted_at must not follow dispatched_at")
    expires_at_value = values.get("expires_at")
    if expires_at_value is not None:
        expires_at = _parse_rfc3339_utc(expires_at_value)
        if expires_at is None:
            reporter.error(f"{context} {document['path']} expires_at must be UTC RFC3339 or null")
        elif dispatched_at is not None and expires_at <= dispatched_at:
            reporter.error(f"{context} {document['path']} approval expired by dispatched_at")

    approval_lease = values.get("lease_epoch")
    dispatch_lease = dispatch_event["values"].get("lease_epoch")
    if document.get("added_with_dispatch") is True and approval_lease != dispatch_lease:
        reporter.error(
            f"{context} {document['path']} was added with dispatch and must use "
            "the TASK_DISPATCHED lease_epoch"
        )


def _validate_degradation_revocation_event(
    document: dict[str, Any],
    approval_document: dict[str, Any],
    task: dict[str, Any],
    approval_id: str,
    revocation_commit: Optional[str],
    dispatched_at: Optional[datetime],
    execution_incomplete: bool,
    context: str,
    reporter: Reporter,
) -> None:
    values = document["values"]
    expected = {
        "schema_version": "agentdesk.state-event/v2",
        "event_type": "MODEL_DEGRADATION_REVOKED",
        "approval_id": approval_id,
        "task_id": task.get("task_id"),
        "revision": task.get("revision"),
        "attempt": task.get("attempt"),
        "actor_role_id": "PM",
    }
    _require_keys(
        values,
        tuple(expected) + ("event_id", "lease_epoch", "revoked_at", "reason"),
        f"{context} {document['path']}",
        reporter,
    )
    for key, expected_value in expected.items():
        if values.get(key) != expected_value:
            reporter.error(
                f"{context} {document['path']} field {key!r} must be "
                f"{expected_value!r}"
            )
    event_id = values.get("event_id")
    if not isinstance(event_id, str) or EVENT_ID_RE.fullmatch(event_id) is None:
        reporter.error(f"{context} {document['path']} event_id must start with EVT-")
    _validate_lease_epoch(values.get("lease_epoch"), f"{context} {document['path']}", reporter)
    if not _nonempty_string(values.get("reason")):
        reporter.error(f"{context} {document['path']} reason must be non-empty")
    revoked_at = _parse_rfc3339_utc(values.get("revoked_at"))
    if revoked_at is None:
        reporter.error(f"{context} {document['path']} revoked_at must be UTC RFC3339")
        return
    approval_values = approval_document["values"]
    granted_at = _parse_rfc3339_utc(approval_values.get("granted_at"))
    if granted_at is not None and revoked_at < granted_at:
        reporter.error(
            f"{context} {document['path']} revoked_at must not precede granted_at"
        )
    approval_lease = approval_values.get("lease_epoch")
    revocation_lease = values.get("lease_epoch")
    if (
        _is_int(approval_lease)
        and _is_int(revocation_lease)
        and revocation_lease < approval_lease
    ):
        reporter.error(
            f"{context} {document['path']} lease_epoch must be >= approval lease_epoch"
        )
    if revocation_commit is None:
        reporter.warn(
            f"pre-commit revocation {document['path']} has no commit yet; strict "
            "approval-descendant ancestry is not proven"
        )
    if dispatched_at is None:
        reporter.error(f"{context} cannot evaluate revocation without dispatched_at")
    elif revoked_at <= dispatched_at:
        reporter.error(
            f"{context} approval {approval_id} was revoked no later than dispatch"
        )
    elif execution_incomplete:
        reporter.error(
            f"{context} approval {approval_id} was revoked after dispatch while "
            "execution is incomplete; stop and recover the dispatch"
        )
    else:
        reporter.warn(
            f"historical model approval {approval_id} was revoked after completed "
            "execution; original dispatch-time validity is retained"
        )


def _verify_current_evidence_copy(
    committed_document: dict[str, Any],
    worktree_documents: list[dict[str, Any]],
    context: str,
    reporter: Reporter,
) -> None:
    matches = [
        document
        for document in worktree_documents
        if document["path"] == committed_document["path"]
    ]
    if len(matches) != 1:
        reporter.error(
            f"{context} committed evidence must exist once as a regular current "
            f"worktree file: {committed_document['path']}"
        )
        return
    if matches[0]["raw"] != committed_document["raw"]:
        reporter.error(
            f"{context} current worktree evidence differs from immutable Git blob: "
            f"{committed_document['path']}"
        )


def _verify_head_evidence_copy(
    original_document: dict[str, Any],
    head_documents: list[dict[str, Any]],
    context: str,
    reporter: Reporter,
) -> None:
    matches = [
        document
        for document in head_documents
        if document["path"] == original_document["path"]
    ]
    if len(matches) != 1:
        reporter.error(
            f"{context} immutable evidence must still exist once at repository HEAD: "
            f"{original_document['path']}"
        )
        return
    if matches[0]["raw"] != original_document["raw"]:
        reporter.error(
            f"{context} repository HEAD rewrites immutable evidence from its "
            f"original commit: {original_document['path']}"
        )


def _require_added_in_dispatch_commit(
    project: Path,
    document: dict[str, Any],
    parents: list[str],
    context: str,
    reporter: Reporter,
) -> None:
    for parent in parents:
        existed = _git_path_exists(project, parent, document["path"])
        if existed is None:
            reporter.error(
                f"{context} cannot prove whether {document['path']} existed before dispatch"
            )
        elif existed:
            reporter.error(
                f"{context} evidence must be newly added in the dispatch state commit, "
                f"but already existed in parent {parent[:12]}: {document['path']}"
            )


def _validate_dispatch_evidence(
    project: Path,
    task: dict[str, Any],
    task_card: Optional[dict[str, Any]],
    dispatch: dict[str, Any],
    selection: dict[str, Any],
    context: str,
    state: str,
    require_committed: bool,
    reporter: Reporter,
) -> None:
    starting_errors = reporter.errors
    dispatch_id = dispatch.get("dispatch_id")
    if not _nonempty_string(dispatch_id):
        reporter.error(f"{context} cannot validate evidence without dispatch_id")
        return
    events_directory = PurePosixPath("docs/pm/events")
    outbox_directory = PurePosixPath("docs/pm/outbox")
    work_events = _worktree_evidence_documents(
        project, events_directory, "event", reporter
    )
    work_outbox = _worktree_evidence_documents(
        project, outbox_directory, "outbox", reporter
    )
    _validate_evidence_id_uniqueness(
        work_events, work_outbox, "current worktree", reporter
    )

    history_errors = reporter.errors
    introduction = _dispatch_introduction_commit(
        project, dispatch_id, context, reporter
    )
    if introduction is None and reporter.errors > history_errors:
        return

    dispatch_commit: Optional[str] = None
    dispatch_parents: list[str] = []
    head_events: list[dict[str, Any]] = []
    if introduction is None:
        if require_committed:
            reporter.error(
                f"{context} dispatch_id {dispatch_id!r} has no committed first "
                "appearance in tasks.yaml history"
            )
            return
        source_events = work_events
        source_outbox = work_outbox
        reporter.warn(
            f"pre-commit new dispatch {dispatch_id!r}: event/outbox are structurally "
            "validatable, but atomic same-commit evidence is not yet proven"
        )
    else:
        dispatch_commit, dispatch_parents = introduction
        source_events = _commit_evidence_documents(
            project, dispatch_commit, events_directory, "event", reporter
        )
        source_outbox = _commit_evidence_documents(
            project, dispatch_commit, outbox_directory, "outbox", reporter
        )
        _validate_evidence_id_uniqueness(
            source_events,
            source_outbox,
            f"dispatch commit {dispatch_commit[:12]}",
            reporter,
        )
        head_commit = _git_head_commit(project)
        if not head_commit:
            reporter.error(f"{context} cannot index current committed evidence without HEAD")
            return
        if head_commit == dispatch_commit:
            head_events = source_events
            head_outbox = source_outbox
        else:
            head_events = _commit_evidence_documents(
                project, head_commit, events_directory, "event", reporter
            )
            head_outbox = _commit_evidence_documents(
                project, head_commit, outbox_directory, "outbox", reporter
            )
            _validate_evidence_id_uniqueness(
                head_events,
                head_outbox,
                f"repository HEAD {head_commit[:12]}",
                reporter,
            )

    event_document = _require_one_evidence_document(
        _matching_documents(
            source_events, "event_type", "TASK_DISPATCHED", dispatch_id
        ),
        "TASK_DISPATCHED event",
        context,
        reporter,
    )
    outbox_document = _require_one_evidence_document(
        _matching_documents(
            source_outbox, "message_type", "task.dispatch", dispatch_id
        ),
        "task.dispatch outbox message",
        context,
        reporter,
    )
    if event_document is None or outbox_document is None:
        return

    if dispatch_commit is not None:
        _require_added_in_dispatch_commit(
            project, event_document, dispatch_parents, context, reporter
        )
        _require_added_in_dispatch_commit(
            project, outbox_document, dispatch_parents, context, reporter
        )
        _verify_current_evidence_copy(
            event_document, work_events, context, reporter
        )
        _verify_current_evidence_copy(
            outbox_document, work_outbox, context, reporter
        )
        _verify_head_evidence_copy(
            event_document, head_events, context, reporter
        )
        _verify_head_evidence_copy(
            outbox_document, head_outbox, context, reporter
        )

    _validate_task_dispatch_outbox(
        outbox_document,
        event_document,
        task,
        dispatch,
        selection,
        context,
        reporter,
    )
    _validate_task_dispatched_event(
        event_document,
        task,
        dispatch,
        outbox_document["raw"],
        context,
        reporter,
    )

    requirement = (
        task_card.get("_model_requirement") if isinstance(task_card, dict) else None
    )
    if not isinstance(requirement, dict):
        return
    selected_tier = selection.get("selected_model_tier")
    preferred_tier = requirement.get("preferred_tier")
    needs_approval = (
        requirement.get("degradation_policy") == "require_pm_approval"
        and selected_tier in MODEL_TIER_INDEX
        and preferred_tier in MODEL_TIER_INDEX
        and MODEL_TIER_INDEX[selected_tier] < MODEL_TIER_INDEX[preferred_tier]
    )
    if not needs_approval:
        if reporter.errors == starting_errors:
            reporter.passed(f"{context} dispatch event/outbox evidence reconciled")
        return
    approval_id = selection.get("model_degradation_approval_id")
    approvals = task.get("granted_approval_ids")
    if (
        not isinstance(approval_id, str)
        or APPROVAL_ID_RE.fullmatch(approval_id) is None
        or not isinstance(approvals, list)
        or approval_id not in approvals
    ):
        reporter.error(
            f"{context} degraded require_pm_approval selection must use an APR-* "
            "ID present in granted_approval_ids"
        )
        return

    approval_candidates = [
        document
        for document in source_events
        if document["values"].get("event_type")
        == "MODEL_DEGRADATION_APPROVED"
        and document["values"].get("approval_id") == approval_id
    ]
    approval_document = _require_one_evidence_document(
        approval_candidates,
        f"MODEL_DEGRADATION_APPROVED record for {approval_id}",
        context,
        reporter,
    )
    if approval_document is None:
        return

    approval_commit: Optional[str] = None
    if dispatch_commit is not None:
        approval_commit = _path_introduction_commit(
            project,
            dispatch_commit,
            approval_document["path"],
            context,
            reporter,
        )
        if approval_commit is not None:
            ancestry = _git_is_ancestor(project, approval_commit, dispatch_commit)
            if ancestry is not True:
                reporter.error(
                    f"{context} approval commit must be the dispatch commit or its ancestor"
                )
            original_approval = _git_blob_bytes(
                project, approval_commit, approval_document["path"]
            )
            if original_approval is None or original_approval != approval_document["raw"]:
                reporter.error(
                    f"{context} approval evidence was rewritten after its "
                    f"introduction commit: {approval_document['path']}"
                )
            approval_document["added_with_dispatch"] = (
                approval_commit == dispatch_commit
            )
        _verify_current_evidence_copy(
            approval_document, work_events, context, reporter
        )
        _verify_head_evidence_copy(
            approval_document, head_events, context, reporter
        )
    else:
        head_commit = _git_head_commit(project)
        path_exists_at_head = (
            _git_path_exists(project, head_commit, approval_document["path"])
            if head_commit
            else False
        )
        approval_document["added_with_dispatch"] = not bool(path_exists_at_head)
        if head_commit and path_exists_at_head:
            approval_commit = _path_introduction_commit(
                project,
                head_commit,
                approval_document["path"],
                context,
                reporter,
            )

    dispatch_lease = event_document["values"].get("lease_epoch")
    approval_lease = approval_document["values"].get("lease_epoch")
    if (
        _is_int(dispatch_lease)
        and _is_int(approval_lease)
        and approval_lease > dispatch_lease
    ):
        reporter.error(
            f"{context} approval lease_epoch must be <= TASK_DISPATCHED lease_epoch"
        )
    _validate_degradation_approval_event(
        approval_document,
        task,
        event_document,
        selection,
        requirement,
        context,
        reporter,
    )

    execution_incomplete = state in {"dispatched", "in_progress"} or (
        state == "blocked"
        and task.get("resume_state") in {"dispatched", "in_progress"}
    )
    if dispatch_commit is not None and require_committed:
        revocation_source = head_events
        relevant_work_revocations = [
            document
            for document in work_events
            if document["values"].get("event_type")
            == "MODEL_DEGRADATION_REVOKED"
            and document["values"].get("approval_id") == approval_id
        ]
    else:
        revocation_source = work_events
        relevant_work_revocations = []
    revocations = [
        document
        for document in revocation_source
        if document["values"].get("event_type") == "MODEL_DEGRADATION_REVOKED"
        and document["values"].get("approval_id") == approval_id
    ]
    if len(revocations) > 1:
        reporter.error(f"{context} approval_id {approval_id} has multiple revocations")
    if dispatch_commit is not None and require_committed:
        committed_paths = {document["path"] for document in revocations}
        for work_revocation in relevant_work_revocations:
            if work_revocation["path"] not in committed_paths:
                reporter.error(
                    f"{context} worktree revocation must be committed before strict "
                    f"validation: {work_revocation['path']}"
                )
    dispatched_at = _parse_rfc3339_utc(
        task.get("timestamps", {}).get("dispatched_at")
        if isinstance(task.get("timestamps"), dict)
        else None
    )
    for revocation in revocations:
        revocation_commit: Optional[str] = None
        if dispatch_commit is not None and require_committed:
            head_commit = _git_head_commit(project)
            if head_commit:
                revocation_commit = _path_introduction_commit(
                    project,
                    head_commit,
                    revocation["path"],
                    context,
                    reporter,
                )
            if revocation_commit is not None:
                original_revocation = _git_blob_bytes(
                    project, revocation_commit, revocation["path"]
                )
                if original_revocation is None or original_revocation != revocation["raw"]:
                    reporter.error(
                        f"{context} revocation evidence was rewritten after its "
                        f"introduction commit: {revocation['path']}"
                    )
            _verify_current_evidence_copy(
                revocation, work_events, context, reporter
            )
            if approval_commit is not None and revocation_commit is not None:
                ancestry = _git_is_ancestor(
                    project, approval_commit, revocation_commit
                )
                if revocation_commit == approval_commit or ancestry is not True:
                    reporter.error(
                        f"{context} revocation commit must be a strict descendant "
                        "of the approval commit"
                    )
        _validate_degradation_revocation_event(
            revocation,
            approval_document,
            task,
            approval_id,
            revocation_commit,
            dispatched_at,
            execution_incomplete,
            context,
            reporter,
        )

    if reporter.errors == starting_errors:
        reporter.passed(f"{context} dispatch event/outbox evidence reconciled")


def _validate_dispatch_model_snapshot(
    project: Path,
    task: dict[str, Any],
    task_card: Optional[dict[str, Any]],
    bindings: Optional[dict[str, dict[str, Any]]],
    context: str,
    state: str,
    require_committed: bool,
    reporter: Reporter,
) -> None:
    dispatch = task.get("current_dispatch")
    if not isinstance(dispatch, dict):
        return
    dispatch_context = f"{context}.current_dispatch"
    selection = dispatch.get("model_selection")
    if not isinstance(selection, dict):
        reporter.error(f"{dispatch_context}.model_selection must be an object")
        return
    selection_context = f"{dispatch_context}.model_selection"
    execution_incomplete = state in {"dispatched", "in_progress"} or (
        state == "blocked"
        and task.get("resume_state") in {"dispatched", "in_progress"}
    )

    def runtime_binding_issue(message: str) -> None:
        if execution_incomplete:
            reporter.error(message)
        else:
            reporter.warn(f"post-execution runtime binding drift: {message}")

    _require_keys(selection, MODEL_SELECTION_FIELDS, selection_context, reporter)
    requirement = (
        task_card.get("_model_requirement") if isinstance(task_card, dict) else None
    )

    snapshot_required_tier = selection.get("required_model_tier")
    if snapshot_required_tier not in MODEL_TIER_INDEX:
        reporter.error(
            f"{selection_context}.required_model_tier must be one of: "
            f"{', '.join(MODEL_TIERS)}"
        )
    snapshot_required_capabilities = _capability_list(
        selection.get("required_model_capabilities"),
        f"{selection_context}.required_model_capabilities",
        reporter,
    )
    if requirement is not None:
        if snapshot_required_tier != requirement["required_tier"]:
            reporter.error(
                f"{selection_context}.required_model_tier must equal frozen computed "
                f"tier {requirement['required_tier']!r}"
            )
        if (
            snapshot_required_capabilities is not None
            and set(snapshot_required_capabilities)
            != set(requirement["required_capabilities"])
        ):
            reporter.error(
                f"{selection_context}.required_model_capabilities must equal the "
                "frozen role/task capability union"
            )

    selected_capabilities = _validate_model_selection(
        selection.get("selected_model_provider"),
        selection.get("selected_model_id"),
        selection.get("selected_model_tier"),
        selection.get("selected_deliberation_tier"),
        selection.get("selected_model_capabilities"),
        requirement,
        f"{selection_context}.selected_model",
        reporter,
    )
    selected_tier = selection.get("selected_model_tier")
    if (
        snapshot_required_tier in MODEL_TIER_INDEX
        and selected_tier in MODEL_TIER_INDEX
        and MODEL_TIER_INDEX[selected_tier]
        < MODEL_TIER_INDEX[snapshot_required_tier]
    ):
        reporter.error(
            f"{selection_context}.selected_model_tier is below its frozen "
            "required_model_tier"
        )
    if selected_capabilities is not None and snapshot_required_capabilities is not None:
        missing_snapshot_capabilities = sorted(
            set(snapshot_required_capabilities) - set(selected_capabilities)
        )
        if missing_snapshot_capabilities:
            reporter.error(
                f"{selection_context}.selected_model_capabilities does not cover "
                "its frozen required_model_capabilities: "
                + ", ".join(missing_snapshot_capabilities)
            )

    binding_id = selection.get("model_binding_id")
    if not _nonempty_string(binding_id):
        reporter.error(f"{selection_context}.model_binding_id must be a non-empty string")
    elif bindings is None:
        runtime_binding_issue(
            f"{selection_context}.model_binding_id cannot be verified because "
            f"{MODEL_BINDINGS_FILE.as_posix()} is unavailable"
        )
    else:
        binding = bindings.get(binding_id)
        if binding is None:
            runtime_binding_issue(
                f"{selection_context}.model_binding_id is not configured locally: "
                f"{binding_id}"
            )
        else:
            if not binding["enabled"]:
                runtime_binding_issue(
                    f"{selection_context}.model_binding_id is disabled: {binding_id}"
                )
            expected_snapshot = {
                "selected_model_provider": binding["provider"],
                "selected_model_id": binding["model_id"],
                "selected_model_tier": binding["tier"],
                "selected_deliberation_tier": binding["deliberation_tier"],
            }
            for key, expected_value in expected_snapshot.items():
                if selection.get(key) != expected_value:
                    runtime_binding_issue(
                        f"{selection_context}.{key} must match local binding "
                        f"{binding_id!r}: {expected_value!r}"
                    )
            if (
                selected_capabilities is not None
                and set(selected_capabilities) != set(binding["capabilities"])
            ):
                runtime_binding_issue(
                    f"{selection_context}.selected_model_capabilities must match "
                    f"local binding {binding_id!r}"
                )

    approval_id = selection.get("model_degradation_approval_id")
    approvals = task.get("granted_approval_ids")
    granted = set(approvals) if isinstance(approvals, list) else set()
    if approval_id is not None:
        if not _nonempty_string(approval_id):
            reporter.error(
                f"{selection_context}.model_degradation_approval_id must be null or "
                "a non-empty string"
            )
        elif approval_id not in granted:
            reporter.error(
                f"{selection_context}.model_degradation_approval_id must appear in "
                f"{context}.granted_approval_ids"
            )

    if requirement is not None and selected_tier in MODEL_TIER_INDEX:
        preferred_tier = requirement["preferred_tier"]
        degraded = MODEL_TIER_INDEX[selected_tier] < MODEL_TIER_INDEX[preferred_tier]
        if not degraded and approval_id is not None:
            reporter.error(
                f"{selection_context}.model_degradation_approval_id must be null "
                "when the selected tier is not degraded"
            )
        elif degraded:
            degradation_policy = requirement["degradation_policy"]
            if degradation_policy == "block":
                reporter.error(
                    f"{selection_context}.selected_model_tier {selected_tier!r} is below "
                    f"preferred tier {preferred_tier!r}, but degradation_policy='block'"
                )
            elif degradation_policy == "require_pm_approval" and (
                not _nonempty_string(approval_id) or approval_id not in granted
            ):
                reporter.error(
                    f"{selection_context} requires a granted PM approval to select tier "
                    f"{selected_tier!r} below preferred tier {preferred_tier!r}"
                )

    if execution_incomplete:
        _verify_selector_snapshot(
            project,
            task,
            task_card,
            selection,
            selection_context,
            reporter,
        )
    _validate_dispatch_evidence(
        project,
        task,
        task_card,
        dispatch,
        selection,
        context,
        state,
        require_committed,
        reporter,
    )


def _validate_timestamps(
    task: dict[str, Any], context: str, state: str, reporter: Reporter
) -> None:
    timestamps = task.get("timestamps")
    if not isinstance(timestamps, dict):
        reporter.error(f"{context}.timestamps must be an object")
        return
    _require_keys(timestamps, TIMESTAMP_FIELDS, f"{context}.timestamps", reporter)

    parsed: dict[str, datetime] = {}
    for key in TIMESTAMP_FIELDS:
        value = timestamps.get(key)
        if value is None:
            continue
        parsed_value = _parse_rfc3339_utc(value)
        if parsed_value is None:
            reporter.error(
                f"{context}.timestamps.{key} must be a UTC RFC3339 timestamp or null"
            )
        else:
            parsed[key] = parsed_value

    for key in ("created_at", "updated_at"):
        if timestamps.get(key) is None:
            reporter.error(f"{context}.timestamps.{key} is required")

    required_for_state = {
        "ready": "ready_at",
        "dispatched": "dispatched_at",
        "in_progress": "started_at",
        "review_ready": "delivered_at",
        "returned": "delivered_at",
        "blocked": "blocked_at",
        "accepted": "accepted_at",
        "integrated": "integrated_at",
    }
    required_timestamp = required_for_state.get(state)
    if required_timestamp and timestamps.get(required_timestamp) is None:
        reporter.error(
            f"{context}.timestamps.{required_timestamp} is required while state={state!r}"
        )
    if state in {"accepted", "integrated"} and timestamps.get("accepted_at") is None:
        reporter.error(f"{context}.timestamps.accepted_at is required after acceptance")

    delivered_state = state in {"review_ready", "returned", "accepted", "integrated"} or (
        state == "blocked" and task.get("resume_state") in {"review_ready", "accepted"}
    )
    if delivered_state and timestamps.get("delivered_at") is None:
        reporter.error(f"{context}.timestamps.delivered_at is required after delivery")
    if (
        state == "blocked"
        and task.get("resume_state") == "accepted"
        and timestamps.get("accepted_at") is None
    ):
        reporter.error(f"{context}.timestamps.accepted_at is required after acceptance")

    previous_key: Optional[str] = None
    previous_value: Optional[datetime] = None
    for key in LIFECYCLE_TIMESTAMPS:
        value = parsed.get(key)
        if value is None:
            continue
        if previous_value is not None and value < previous_value:
            reporter.error(
                f"{context}.timestamps is not monotonic: {key} precedes {previous_key}"
            )
        previous_key, previous_value = key, value

    blocked_at = parsed.get("blocked_at")
    created_at = parsed.get("created_at")
    updated_at = parsed.get("updated_at")
    if blocked_at is not None and created_at is not None and blocked_at < created_at:
        reporter.error(f"{context}.timestamps.blocked_at precedes created_at")
    if blocked_at is not None and updated_at is not None and blocked_at > updated_at:
        reporter.error(f"{context}.timestamps.blocked_at follows updated_at")


def _validate_blocked_envelope(
    task: dict[str, Any], context: str, state: str, reporter: Reporter
) -> None:
    if state == "blocked":
        for key in BLOCKED_STRING_FIELDS:
            if not _nonempty_string(task.get(key)):
                reporter.error(f"{context}.{key} must be a non-empty string while blocked")
        if _parse_rfc3339_utc(task.get("review_after")) is None:
            reporter.error(f"{context}.review_after must be a UTC RFC3339 timestamp while blocked")
        if not isinstance(task.get("blocked_attempt_valid"), bool):
            reporter.error(f"{context}.blocked_attempt_valid must be boolean while blocked")
        resume_state = task.get("resume_state")
        resumable_states = {
            "draft",
            "ready",
            "dispatched",
            "in_progress",
            "review_ready",
            "returned",
            "accepted",
        }
        if resume_state not in resumable_states:
            reporter.error(
                f"{context}.resume_state must name a resumable non-terminal state while blocked"
            )
        if task.get("blocked_attempt_valid") is True and resume_state not in ACTIVE_DISPATCH_STATES:
            reporter.error(
                f"{context}.resume_state must be an active dispatch state when "
                "blocked_attempt_valid=true"
            )
        if task.get("blocked_kind") not in BLOCKED_KINDS:
            reporter.error(
                f"{context}.blocked_kind must be one of: {', '.join(sorted(BLOCKED_KINDS))}"
            )
    else:
        for key in BLOCKED_ENVELOPE_FIELDS:
            if task.get(key) is not None:
                reporter.error(f"{context}.{key} must be null while state={state!r}")


def _parse_frontmatter_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if value in {"null", "~"}:
        return None
    if value in {"true", "false"}:
        return value == "true"
    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    if value.startswith("[") or value.startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            # Capability arrays in human-authored YAML commonly use the safe
            # flow form ``[coding, testing]``.  Accept that narrow subset while
            # keeping objects and complex YAML out of this stdlib parser.
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if not inner:
                    return []
                return [
                    _parse_frontmatter_scalar(item.strip())
                    for item in inner.split(",")
                ]
            return value
        if isinstance(parsed, (list, dict)):
            return parsed
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _yaml_subset_mapping_from_lines(
    lines: Iterable[str], context: str, reporter: Reporter
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    # Parse the small deterministic YAML subset used by the workflow: mappings
    # composed of scalar values, inline JSON arrays/objects, and indented child
    # mappings.  This intentionally is not a general YAML parser.
    stack: list[tuple[int, dict[str, Any]]] = [(-1, values)]
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            reporter.error(f"{context} indentation must use spaces")
            continue
        indentation = len(line) - len(line.lstrip(" "))
        key, raw_value = line.lstrip(" ").split(":", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None:
            continue
        while len(stack) > 1 and indentation <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if not raw_value.strip():
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indentation, child))
        else:
            parent[key] = _parse_frontmatter_scalar(raw_value)
    return values


def _yaml_subset_mapping_from_text(
    content: str, context: str, reporter: Reporter
) -> dict[str, Any]:
    return _yaml_subset_mapping_from_lines(content.splitlines(), context, reporter)


def _frontmatter_scalars_from_text(
    content: str, context: str, reporter: Reporter
) -> Optional[dict[str, Any]]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        reporter.error(f"{context} must start with YAML frontmatter")
        return None
    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration:
        reporter.error(f"{context} has no closing YAML frontmatter delimiter")
        return None
    return _yaml_subset_mapping_from_lines(
        lines[1:closing_index], f"{context} frontmatter", reporter
    )


def _read_frontmatter_scalars(
    path: Path, context: str, reporter: Reporter
) -> Optional[dict[str, Any]]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        reporter.error(f"cannot read {context}: {exc}")
        return None
    return _frontmatter_scalars_from_text(content, context, reporter)


def _active_role_ids(project: Path, reporter: Reporter) -> Optional[set[str]]:
    path = project / "docs/pm/ROLES.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        reporter.error(f"cannot read docs/pm/ROLES.md: {exc}")
        return None
    header: Optional[list[str]] = None
    active: set[str] = set()
    role_index = -1
    role_no_index = -1
    role_name_index = -1
    expected_title_index = -1
    status_index = -1
    seen_role_ids: set[str] = set()
    seen_role_nos: dict[str, str] = {}
    role_name_headers = {"role_name", "role name", "display name", "岗位名称"}
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        normalized = [cell.lower() for cell in cells]
        if (
            header is None
            and "role_no" in normalized
            and "role_id" in normalized
            and "expected_thread_title" in normalized
            and "status" in normalized
        ):
            header = cells
            role_no_index = normalized.index("role_no")
            role_index = normalized.index("role_id")
            expected_title_index = normalized.index("expected_thread_title")
            status_index = normalized.index("status")
            role_name_index = next(
                (
                    index
                    for index, name in enumerate(normalized)
                    if name in role_name_headers
                ),
                -1,
            )
            if role_name_index < 0:
                reporter.error(
                    "docs/pm/ROLES.md must contain a role_name, Role name, "
                    "Display name, or 岗位名称 column"
                )
                return None
            continue
        if header is None or max(
            role_no_index,
            role_index,
            role_name_index,
            expected_title_index,
            status_index,
        ) >= len(cells):
            continue
        if set("".join(cells)) <= {"-", ":", " "}:
            continue
        role_no = cells[role_no_index].strip("`")
        role_id = cells[role_index].strip("`")
        role_name = cells[role_name_index].strip("`")
        expected_title = cells[expected_title_index].strip("`")
        status = cells[status_index].strip("`")
        if not all((role_no, role_id, role_name, expected_title, status)):
            reporter.error(
                "every docs/pm/ROLES.md row must define role_no, role_id, "
                "role_name, expected_thread_title, and Status"
            )
            continue
        required_title = f"{role_no} . {role_name}"
        if expected_title != required_title:
            reporter.error(
                f"role {role_id} expected_thread_title must equal {required_title!r}"
            )
        if role_id in seen_role_ids:
            reporter.error(f"duplicate role_id in docs/pm/ROLES.md: {role_id}")
            continue
        if role_no in seen_role_nos:
            reporter.error(
                f"duplicate role_no in docs/pm/ROLES.md: {role_no} "
                f"({seen_role_nos[role_no]} and {role_id})"
            )
            continue
        seen_role_ids.add(role_id)
        seen_role_nos[role_no] = role_id
        if status.lower() == "active":
            active.add(role_id)
    if header is None:
        reporter.error(
            "docs/pm/ROLES.md must contain a "
            "role_no/role_id/role_name/expected_thread_title/Status table"
        )
        return None
    reporter.passed("every role has a stable role_no, role_id, and role_name")
    return active


def _model_policy_is_required(
    task: dict[str, Any], state: str, reporter: Reporter
) -> bool:
    """Every non-draft lifecycle record must use model-policy v1 fields.

    Draft records may omit model policy while they are still being authored.
    Terminal legacy records are relaxed only under the explicit migration-audit
    flag; strict validation never infers legacy status from lifecycle state.
    """

    if isinstance(task.get("current_dispatch"), dict):
        return True
    if state == "draft":
        return False
    if reporter.allow_legacy_model_evidence and state in TERMINAL_STATES:
        return False
    return True


def _frozen_role_policy(
    project: Path,
    task: dict[str, Any],
    state: str,
    current_policy: Optional[dict[str, Any]],
    cache: dict[str, Optional[dict[str, Any]]],
    context: str,
    reporter: Reporter,
) -> Optional[dict[str, Any]]:
    commit = task.get("task_card_commit")
    strict = _model_policy_is_required(task, state, reporter)
    if not isinstance(commit, str) or SHA_RE.fullmatch(commit) is None:
        return current_policy
    if commit in cache:
        policy = cache[commit]
        if policy is None:
            message = (
                f"{context}.task_card_commit has no usable frozen "
                f"{ROLE_POLICY_FILE.as_posix()}"
            )
            if strict:
                reporter.error(message)
            else:
                reporter.warn(f"legacy history: {message}")
            return current_policy
        return policy

    logical_path = ROLE_POLICY_FILE.as_posix()
    exists = _git_blob_exists(project, commit, logical_path)
    if exists is True:
        content = _git_read_blob(project, commit, logical_path)
        if content is None:
            reporter.error(
                f"cannot read frozen {logical_path} from {context}.task_card_commit"
            )
            cache[commit] = None
            return current_policy
        label = f"{logical_path}@{commit[:12]}"
        value = _parse_json_compatible_object(content, label, reporter)
        if value is None:
            cache[commit] = None
            return current_policy
        starting_errors = reporter.errors
        policy = _validate_role_policy_object(value, label, reporter)
        cache[commit] = policy
        if reporter.errors == starting_errors:
            reporter.passed(f"validated frozen role policy at {commit[:12]}")
        return policy

    message = (
        f"{context}.task_card_commit does not contain frozen {logical_path}"
        if exists is False
        else f"could not verify frozen {logical_path} for {context}.task_card_commit"
    )
    if strict:
        reporter.error(message)
    else:
        reporter.warn(f"legacy history: {message}")
    cache[commit] = None
    return current_policy


def _max_model_tier(*tiers: str) -> str:
    return max(tiers, key=lambda tier: MODEL_TIER_INDEX[tier])


def _task_card_model_requirement(
    frontmatter: dict[str, Any],
    task: dict[str, Any],
    state: str,
    policy: Optional[dict[str, Any]],
    context: str,
    reporter: Reporter,
) -> Optional[dict[str, Any]]:
    strict = _model_policy_is_required(task, state, reporter)
    tier_present = "min_model_tier" in frontmatter
    capabilities_present = "required_model_capabilities" in frontmatter
    min_model_tier = frontmatter.get("min_model_tier")

    if not tier_present:
        if strict:
            reporter.error(
                f"{context} frontmatter min_model_tier is required for this lifecycle state"
            )
        else:
            reporter.warn(
                f"legacy history: {context} frontmatter has no min_model_tier"
            )
        min_model_tier = "inherit"
    elif min_model_tier != "inherit" and min_model_tier not in MODEL_TIER_INDEX:
        reporter.error(
            f"{context} frontmatter min_model_tier must be 'inherit' or one of: "
            f"{', '.join(MODEL_TIERS)}"
        )

    raw_capabilities = frontmatter.get("required_model_capabilities")
    if not capabilities_present:
        if strict:
            reporter.error(
                f"{context} frontmatter required_model_capabilities is required"
            )
        else:
            reporter.warn(
                f"legacy history: {context} frontmatter has no "
                "required_model_capabilities"
            )
        task_capabilities: Optional[list[str]] = []
    else:
        task_capabilities = _capability_list(
            raw_capabilities,
            f"{context} frontmatter required_model_capabilities",
            reporter,
        )

    role_id = frontmatter.get("role_id")
    risk = frontmatter.get("risk")
    if policy is None:
        if strict:
            reporter.error(f"{context} has no usable frozen role/model policy")
        else:
            reporter.warn(
                f"legacy history: cannot compute model requirement for {context}"
            )
        return None
    roles = policy.get("roles")
    role_policy = roles.get(role_id) if isinstance(roles, dict) else None
    if not isinstance(role_policy, dict):
        if strict:
            reporter.error(
                f"{context} role_id {role_id!r} has no valid frozen model policy"
            )
        else:
            reporter.warn(
                f"legacy history: {context} role_id {role_id!r} has no frozen model policy"
            )
        return None
    risk_floors = policy.get("risk_floors")
    risk_floor = risk_floors.get(risk) if isinstance(risk_floors, dict) else None
    if risk_floor not in MODEL_TIER_INDEX:
        if strict:
            reporter.error(f"{context} risk {risk!r} has no valid frozen model tier floor")
        else:
            reporter.warn(
                f"legacy history: {context} risk {risk!r} has no frozen model tier floor"
            )
        return None
    minimum_tier = role_policy["minimum_tier"]
    default_tier = role_policy["default_tier"]
    if min_model_tier == "inherit" or min_model_tier not in MODEL_TIER_INDEX:
        task_floor = minimum_tier
    else:
        task_floor = min_model_tier
    required_tier = _max_model_tier(minimum_tier, task_floor, risk_floor)
    preferred_tier = _max_model_tier(default_tier, required_tier)
    capability_union = sorted(
        set(role_policy["required_capabilities"]) | set(task_capabilities or [])
    )
    return {
        "required_tier": required_tier,
        "preferred_tier": preferred_tier,
        "deliberation_tier": role_policy["deliberation_tier"],
        "required_capabilities": capability_union,
        "degradation_policy": role_policy["degradation_policy"],
    }


def _validate_task_card(
    content: str,
    task: dict[str, Any],
    context: str,
    project: Path,
    state: str,
    policy: Optional[dict[str, Any]],
    active_roles: Optional[set[str]],
    reporter: Reporter,
) -> Optional[dict[str, Any]]:
    frontmatter = _frontmatter_scalars_from_text(content, context, reporter)
    if frontmatter is None:
        return None
    expected = {
        "schema_version": "agentdesk.task-card/v2",
        "task_id": task.get("task_id"),
        "revision": task.get("revision"),
    }
    for key, expected_value in expected.items():
        if frontmatter.get(key) != expected_value:
            reporter.error(
                f"{context} frontmatter {key!r} must equal ledger value "
                f"{expected_value!r}"
            )
    task_type = frontmatter.get("type")
    if task_type is not None or state != "draft":
        if task_type not in TASK_TYPES:
            reporter.error(f"{context} frontmatter type must be a supported task type")
    priority = frontmatter.get("priority")
    if priority is not None or state != "draft":
        if priority not in PRIORITIES:
            reporter.error(f"{context} frontmatter priority must be one of P0, P1, P2, P3")
    risk = frontmatter.get("risk")
    if risk is not None or state != "draft":
        if risk not in RISKS:
            reporter.error(f"{context} frontmatter risk must be one of L0 through L4")
    if _parse_rfc3339_utc(frontmatter.get("created_at")) is None:
        reporter.error(f"{context} frontmatter created_at must be a UTC RFC3339 timestamp")

    role_id = frontmatter.get("role_id")
    if not _nonempty_string(role_id) and state != "draft":
        reporter.error(f"{context} frontmatter role_id must be a non-empty string")
    elif _nonempty_string(role_id) and state != "draft":
        if active_roles is not None and role_id not in active_roles:
            reporter.error(f"{context} frontmatter role_id is not Active: {role_id}")

    base_commit = frontmatter.get("base_commit")
    if base_commit is None and state == "draft":
        pass
    elif not isinstance(base_commit, str) or SHA_RE.fullmatch(base_commit) is None:
        reporter.error(f"{context} frontmatter base_commit must be a full Git SHA")
    else:
        base_exists = _git_commit_exists(project, base_commit)
        if base_exists is False:
            reporter.error(f"{context} frontmatter base_commit does not resolve to a Git commit")
        elif base_exists is None:
            reporter.warn(f"could not verify {context} frontmatter base_commit")

    dispatch = task.get("current_dispatch")
    if isinstance(dispatch, dict):
        if role_id != dispatch.get("role_id"):
            reporter.error(f"{context} role_id must match current_dispatch.role_id")
        if base_commit != dispatch.get("base_commit"):
            reporter.error(f"{context} base_commit must match current_dispatch.base_commit")
    frontmatter["_model_requirement"] = _task_card_model_requirement(
        frontmatter,
        task,
        state,
        policy,
        context,
        reporter,
    )
    return frontmatter


def _validate_delivery_report(
    project: Path,
    content: str,
    task: dict[str, Any],
    task_card: Optional[dict[str, Any]],
    context: str,
    state: str,
    require_committed: bool,
    reporter: Reporter,
) -> Optional[dict[str, Any]]:
    frontmatter = _frontmatter_scalars_from_text(content, context, reporter)
    if frontmatter is None:
        return None
    expected = {
        "schema_version": "agentdesk.delivery-report/v2",
        "task_id": task.get("task_id"),
        "revision": task.get("revision"),
        "attempt": task.get("attempt"),
        "implementation_commit": task.get("implementation_commit"),
        "report_path": task.get("report_path"),
    }
    dispatch = task.get("current_dispatch")
    if task_card is not None:
        expected["base_commit"] = task_card.get("base_commit")
        expected["role_id"] = task_card.get("role_id")
    if isinstance(dispatch, dict):
        expected["dispatch_id"] = dispatch.get("dispatch_id")
        expected["base_commit"] = dispatch.get("base_commit")
    for key, expected_value in expected.items():
        if frontmatter.get(key) != expected_value:
            reporter.error(
                f"{context} frontmatter {key!r} must equal ledger value "
                f"{expected_value!r}"
            )
    if frontmatter.get("delivery_status") != "completed":
        reporter.error(f"{context} frontmatter delivery_status must be 'completed'")
    for key in ("dispatch_id", "callback_id"):
        if not _nonempty_string(frontmatter.get(key)):
            reporter.error(f"{context} frontmatter {key} must be a non-empty string")
    if frontmatter.get("report_commit") is not None:
        reporter.error(
            f"{context} frontmatter report_commit must be null because a report cannot self-reference"
        )

    model_evidence_required = _model_policy_is_required(task, state, reporter)
    executor_model = frontmatter.get("executor_model")
    if executor_model is None:
        message = f"{context} frontmatter is missing executor_model evidence"
        if model_evidence_required:
            reporter.error(message)
        else:
            reporter.warn(f"legacy history: {message}")
    elif not isinstance(executor_model, dict):
        reporter.error(f"{context} frontmatter executor_model must be an object")
    else:
        missing_model_fields = [
            key for key in MODEL_SELECTION_FIELDS if key not in executor_model
        ]
        if missing_model_fields:
            message = (
                f"{context} frontmatter executor_model is missing field(s): "
                + ", ".join(missing_model_fields)
            )
            if model_evidence_required:
                reporter.error(message)
            else:
                reporter.warn(f"legacy history: {message}")
        else:
            requirement = (
                task_card.get("_model_requirement")
                if isinstance(task_card, dict)
                else None
            )
            required_tier = executor_model.get("required_model_tier")
            if required_tier not in MODEL_TIER_INDEX:
                reporter.error(
                    f"{context} executor_model.required_model_tier must be one of: "
                    f"{', '.join(MODEL_TIERS)}"
                )
            required_capabilities = _capability_list(
                executor_model.get("required_model_capabilities"),
                f"{context} executor_model.required_model_capabilities",
                reporter,
            )
            if requirement is not None:
                if required_tier != requirement["required_tier"]:
                    reporter.error(
                        f"{context} executor_model.required_model_tier must equal "
                        f"frozen computed tier {requirement['required_tier']!r}"
                    )
                if (
                    required_capabilities is not None
                    and set(required_capabilities)
                    != set(requirement["required_capabilities"])
                ):
                    reporter.error(
                        f"{context} executor_model.required_model_capabilities must "
                        "equal the frozen role/task capability union"
                    )

            selected_capabilities = _validate_model_selection(
                executor_model.get("selected_model_provider"),
                executor_model.get("selected_model_id"),
                executor_model.get("selected_model_tier"),
                executor_model.get("selected_deliberation_tier"),
                executor_model.get("selected_model_capabilities"),
                requirement,
                f"{context} executor_model.selected_model",
                reporter,
            )
            if not _nonempty_string(executor_model.get("model_binding_id")):
                reporter.error(
                    f"{context} executor_model.model_binding_id must be non-empty"
                )
            if selected_capabilities is not None and required_capabilities is not None:
                missing_capabilities = sorted(
                    set(required_capabilities) - set(selected_capabilities)
                )
                if missing_capabilities:
                    reporter.error(
                        f"{context} executor_model.selected_model_capabilities does "
                        "not cover required_model_capabilities: "
                        + ", ".join(missing_capabilities)
                    )

            approval_id = executor_model.get("model_degradation_approval_id")
            approvals = task.get("granted_approval_ids")
            granted = set(approvals) if isinstance(approvals, list) else set()
            if approval_id is not None and (
                not _nonempty_string(approval_id) or approval_id not in granted
            ):
                reporter.error(
                    f"{context} executor_model.model_degradation_approval_id must "
                    "be null or appear in granted_approval_ids"
                )
            selected_tier = executor_model.get("selected_model_tier")
            if requirement is not None and selected_tier in MODEL_TIER_INDEX:
                preferred_tier = requirement["preferred_tier"]
                degraded = (
                    MODEL_TIER_INDEX[selected_tier]
                    < MODEL_TIER_INDEX[preferred_tier]
                )
                if not degraded and approval_id is not None:
                    reporter.error(
                        f"{context} executor_model.model_degradation_approval_id "
                        "must be null when the selected tier is not degraded"
                    )
                elif degraded and requirement["degradation_policy"] == "block":
                    reporter.error(
                        f"{context} executor_model selected tier is below preferred "
                        "tier while degradation_policy='block'"
                    )
                elif degraded and requirement["degradation_policy"] == "require_pm_approval" and (
                    not _nonempty_string(approval_id) or approval_id not in granted
                ):
                    reporter.error(
                        f"{context} executor_model requires a granted PM degradation approval"
                    )

            if isinstance(dispatch, dict):
                model_selection = dispatch.get("model_selection")
                if not isinstance(model_selection, dict):
                    reporter.error(
                        f"{context} cannot compare executor_model because "
                        "current_dispatch.model_selection is missing"
                    )
                else:
                    for key in MODEL_SELECTION_FIELDS:
                        if executor_model.get(key) != model_selection.get(key):
                            reporter.error(
                                f"{context} frontmatter executor_model.{key} must "
                                f"exactly match current_dispatch.model_selection.{key}"
                            )
            else:
                report_dispatch = {
                    "dispatch_id": frontmatter.get("dispatch_id"),
                    "role_id": (
                        task_card.get("role_id")
                        if isinstance(task_card, dict)
                        else frontmatter.get("role_id")
                    ),
                    "base_commit": frontmatter.get("base_commit"),
                    "branch": frontmatter.get("branch"),
                    "model_selection": executor_model,
                }
                if not _nonempty_string(report_dispatch.get("branch")):
                    reporter.error(
                        f"{context} frontmatter branch must be non-empty for "
                        "dispatch evidence reconciliation"
                    )
                _validate_dispatch_evidence(
                    project,
                    task,
                    task_card,
                    report_dispatch,
                    executor_model,
                    context,
                    state,
                    require_committed,
                    reporter,
                )
    return frontmatter


def _validate_acceptance_record(
    path: Path,
    project: Path,
    task: dict[str, Any],
    task_card: Optional[dict[str, Any]],
    delivery_report: Optional[dict[str, Any]],
    context: str,
    state: str,
    require_committed: bool,
    reporter: Reporter,
) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        reporter.error(f"cannot read {context}: {exc}")
        return
    head_commit = _git_head_commit(project)
    logical_path = path.relative_to(project).as_posix()
    if not require_committed:
        reporter.warn(
            f"pre-commit mode: {context} is structurally checked but not yet immutable"
        )
    elif not head_commit:
        reporter.error(f"{context} cannot be immutable because the repository has no HEAD commit")
    else:
        committed_content = _git_read_blob(project, head_commit, logical_path)
        if committed_content is None:
            reporter.error(f"{context} is not committed at repository HEAD")
        elif committed_content != content:
            reporter.error(f"{context} differs from the immutable copy at repository HEAD")
        else:
            reporter.passed(f"{context} is committed and unchanged at repository HEAD")

    frontmatter = _frontmatter_scalars_from_text(content, context, reporter)
    if frontmatter is None:
        return
    expected = {
        "schema_version": "agentdesk.acceptance/v2",
        "task_id": task.get("task_id"),
        "revision": task.get("revision"),
        "attempt": task.get("attempt"),
        "implementation_commit": task.get("implementation_commit"),
        "report_commit": task.get("report_commit"),
    }
    if task_card is not None:
        expected["base_commit"] = task_card.get("base_commit")
    for key, expected_value in expected.items():
        if frontmatter.get(key) != expected_value:
            reporter.error(
                f"{context} frontmatter {key!r} must equal ledger value "
                f"{expected_value!r}"
            )

    decision = frontmatter.get("decision")
    if decision not in {"accepted", "returned", "blocked"}:
        reporter.error(f"{context} frontmatter decision must be accepted, returned, or blocked")
    expected_decision: Optional[str] = None
    if state in {"accepted", "integrated"} or (
        state == "blocked" and task.get("resume_state") == "accepted"
    ):
        expected_decision = "accepted"
    elif state == "returned":
        expected_decision = "returned"
    if expected_decision is not None and decision != expected_decision:
        reporter.error(
            f"{context} frontmatter decision must be {expected_decision!r} "
            f"while state={state!r}"
        )

    record_accepted_commit = frontmatter.get("accepted_commit")
    if decision == "accepted":
        if record_accepted_commit != task.get("accepted_commit"):
            reporter.error(
                f"{context} frontmatter accepted_commit must equal the ledger accepted_commit"
            )
        if record_accepted_commit != task.get("implementation_commit"):
            reporter.error(
                f"{context} frontmatter accepted_commit must equal implementation_commit"
            )
    elif record_accepted_commit is not None:
        reporter.error(
            f"{context} frontmatter accepted_commit must be null unless decision='accepted'"
        )

    reviewed_dispatch_id = frontmatter.get("reviewed_dispatch_id")
    if not _nonempty_string(reviewed_dispatch_id):
        reporter.error(f"{context} frontmatter reviewed_dispatch_id must be non-empty")
    elif (
        delivery_report is not None
        and reviewed_dispatch_id != delivery_report.get("dispatch_id")
    ):
        reporter.error(
            f"{context} reviewed_dispatch_id must match the immutable delivery report dispatch_id"
        )


def _validate_report_and_acceptance_paths(
    project: Path,
    task: dict[str, Any],
    task_card: Optional[dict[str, Any]],
    context: str,
    task_id: Any,
    revision: Any,
    attempt: Any,
    state: str,
    require_committed: bool,
    reporter: Reporter,
) -> None:
    report_path = task.get("report_path")
    delivery_report_frontmatter: Optional[dict[str, Any]] = None
    needs_reserved_report = state in ACTIVE_DISPATCH_STATES
    needs_existing_report = state in {"review_ready", "returned", "accepted", "integrated"}
    if state in {"cancelled", "superseded"} and task.get("report_commit") is not None:
        needs_existing_report = True
    if state == "blocked" and task.get("resume_state") in {"review_ready", "accepted"}:
        needs_existing_report = True

    if report_path is None:
        if needs_reserved_report or needs_existing_report:
            reporter.error(f"{context}.report_path must be preassigned while state={state!r}")
    else:
        report_file = _safe_relative_path(
            project,
            report_path,
            f"{context}.report_path",
            reporter,
            PurePosixPath("docs/pm/reports"),
        )
        if (
            _nonempty_string(task_id)
            and _is_int(revision)
            and _is_int(attempt)
            and attempt >= 1
        ):
            expected = f"docs/pm/reports/{task_id}-r{revision}-a{attempt}.md"
            if report_path != expected:
                reporter.error(f"{context}.report_path must be {expected!r} for this revision/attempt")
        if needs_existing_report and report_file is not None:
            report_commit = task.get("report_commit")
            if isinstance(report_commit, str) and SHA_RE.fullmatch(report_commit):
                exists_at_commit = _git_blob_exists(project, report_commit, report_path)
                if exists_at_commit is True:
                    reporter.passed(
                        f"{context}.report_path is readable from immutable report_commit"
                    )
                    report_content = _git_read_blob(project, report_commit, report_path)
                    if report_content is None:
                        reporter.error(
                            f"cannot read {context}.report_path from report_commit"
                        )
                    else:
                        delivery_report_frontmatter = _validate_delivery_report(
                            project,
                            report_content,
                            task,
                            task_card,
                            f"{context}.report_path",
                            state,
                            require_committed,
                            reporter,
                        )
                elif exists_at_commit is False:
                    reporter.error(
                        f"{context}.report_path is not a blob at report_commit: "
                        f"{report_commit}:{report_path}"
                    )
                elif report_file.is_file():
                    reporter.warn(
                        f"could not verify {context}.report_path at report_commit; "
                        "a current-worktree copy exists"
                    )
                else:
                    reporter.warn(
                        f"could not verify {context}.report_path at report_commit and "
                        "no current-worktree copy exists"
                    )

    acceptance_path = task.get("acceptance_path")
    acceptance_allowed_states = {
        "returned",
        "blocked",
        "accepted",
        "integrated",
        "cancelled",
        "superseded",
    }
    acceptance_required = state in {"returned", "accepted", "integrated"} or (
        state == "blocked" and task.get("resume_state") == "accepted"
    )
    if acceptance_path is None:
        if acceptance_required:
            reporter.error(f"{context}.acceptance_path is required while state={state!r}")
    elif state in acceptance_allowed_states:
        acceptance_file = _safe_relative_path(
            project,
            acceptance_path,
            f"{context}.acceptance_path",
            reporter,
            PurePosixPath("docs/pm/acceptances"),
        )
        if (
            _nonempty_string(task_id)
            and _is_int(revision)
            and _is_int(attempt)
            and attempt >= 1
        ):
            expected_pattern = re.compile(
                rf"docs/pm/acceptances/{re.escape(task_id)}-r{revision}-a{attempt}"
                r"-review[1-9][0-9]*\.md"
            )
            if expected_pattern.fullmatch(acceptance_path) is None:
                reporter.error(
                    f"{context}.acceptance_path must match "
                    f"docs/pm/acceptances/{task_id}-r{revision}-a{attempt}-reviewN.md"
                )
        if acceptance_file is not None:
            if not acceptance_file.is_file():
                reporter.error(f"{context}.acceptance_path does not exist: {acceptance_path}")
            else:
                _validate_acceptance_record(
                    acceptance_file,
                    project,
                    task,
                    task_card,
                    delivery_report_frontmatter,
                    f"{context}.acceptance_path",
                    state,
                    require_committed,
                    reporter,
                )
    else:
        reporter.error(f"{context}.acceptance_path must be null while state={state!r}")


def _validate_task(
    project: Path,
    raw_task: Any,
    index: int,
    seen_task_ids: set[str],
    seen_dispatch_ids: set[str],
    current_policy: Optional[dict[str, Any]],
    bindings: Optional[dict[str, dict[str, Any]]],
    frozen_policy_cache: dict[str, Optional[dict[str, Any]]],
    active_roles: Optional[set[str]],
    require_committed: bool,
    reporter: Reporter,
) -> None:
    context = f"tasks[{index}]"
    if not isinstance(raw_task, dict):
        reporter.error(f"{context} must be an object")
        return
    starting_errors = reporter.errors
    task: dict[str, Any] = raw_task
    _require_keys(task, TASK_FIELDS, context, reporter)

    task_id = task.get("task_id")
    if not isinstance(task_id, str) or TASK_ID_RE.fullmatch(task_id) is None:
        reporter.error(f"{context}.task_id must match TC- followed by at least three digits")
    elif task_id in seen_task_ids:
        reporter.error(f"duplicate task_id: {task_id}")
    else:
        seen_task_ids.add(task_id)
    task_label = task_id if isinstance(task_id, str) else f"index {index}"

    revision = task.get("revision")
    if not _is_int(revision) or revision < 1:
        reporter.error(f"{context}.revision must be an integer >= 1")
    attempt = task.get("attempt")
    if not _is_int(attempt) or attempt < 0:
        reporter.error(f"{context}.attempt must be a non-negative integer")

    state = task.get("state")
    if state not in STATES:
        reporter.error(f"{context}.state must be one of: {', '.join(STATES)}")
        state = "__invalid__"

    task_policy = _frozen_role_policy(
        project,
        task,
        state,
        current_policy,
        frozen_policy_cache,
        context,
        reporter,
    )

    card_file = _safe_relative_path(
        project,
        task.get("task_card_path"),
        f"{context}.task_card_path",
        reporter,
        PurePosixPath("docs/pm/tasks"),
    )
    if card_file is not None and not card_file.is_file():
        reporter.error(f"{context}.task_card_path does not exist: {task.get('task_card_path')}")
    if _nonempty_string(task_id) and _is_int(revision):
        expected_prefix = f"docs/pm/tasks/{task_id}-r{revision}-"
        task_card_path_value = task.get("task_card_path")
        if not (
            isinstance(task_card_path_value, str)
            and task_card_path_value.startswith(expected_prefix)
            and task_card_path_value.endswith(".md")
        ):
            reporter.error(
                f"{context}.task_card_path must match "
                f"docs/pm/tasks/{task_id}-r{revision}-<slug>.md"
            )

    valid_commit_fields: set[str] = set()
    for key in SHA_FIELDS:
        value = task.get(key)
        valid_sha = _validate_sha(value, f"{context}.{key}", reporter)
        if valid_sha and isinstance(value, str):
            valid_commit_fields.add(key)
            object_exists = _git_commit_exists(project, value)
            if object_exists is True:
                reporter.passed(f"{context}.{key} resolves to a Git commit")
            elif object_exists is False:
                reporter.error(f"{context}.{key} does not resolve to a Git commit: {value}")
            else:
                reporter.warn(f"could not verify Git object for {context}.{key}")
    if state != "draft":
        _validate_sha(
            task.get("task_card_commit"),
            f"{context}.task_card_commit",
            reporter,
            required=True,
        )
    task_card_commit = task.get("task_card_commit")
    task_card_path = task.get("task_card_path")
    task_card_frontmatter: Optional[dict[str, Any]] = None
    if (
        card_file is not None
        and "task_card_commit" in valid_commit_fields
        and isinstance(task_card_commit, str)
        and isinstance(task_card_path, str)
    ):
        card_at_commit = _git_blob_exists(project, task_card_commit, task_card_path)
        if card_at_commit is True:
            reporter.passed(f"{context}.task_card_path exists at task_card_commit")
            card_content = _git_read_blob(project, task_card_commit, task_card_path)
            if card_content is None:
                reporter.error(f"cannot read {context}.task_card_path at task_card_commit")
            else:
                task_card_frontmatter = _validate_task_card(
                    card_content,
                    task,
                    f"{context}.task_card_path",
                    project,
                    state,
                    task_policy,
                    active_roles,
                    reporter,
                )
        elif card_at_commit is False:
            reporter.error(
                f"{context}.task_card_path is not a blob at task_card_commit: "
                f"{task_card_commit}:{task_card_path}"
            )
        else:
            reporter.warn(f"could not verify {context}.task_card_path at task_card_commit")
    elif card_file is not None and card_file.is_file():
        try:
            current_card_content = card_file.read_text(encoding="utf-8")
        except OSError as exc:
            reporter.error(f"cannot read {context}.task_card_path: {exc}")
        else:
            task_card_frontmatter = _validate_task_card(
                current_card_content,
                task,
                f"{context}.task_card_path",
                project,
                state,
                task_policy,
                active_roles,
                reporter,
            )

    dispatch_id = _validate_dispatch(task, context, state, attempt, reporter)
    if dispatch_id is not None:
        if dispatch_id in seen_dispatch_ids:
            reporter.error(f"current dispatch_id is reused by multiple tasks: {dispatch_id}")
        else:
            seen_dispatch_ids.add(dispatch_id)

    approvals = task.get("granted_approval_ids")
    if not isinstance(approvals, list) or any(not _nonempty_string(item) for item in approvals):
        reporter.error(f"{context}.granted_approval_ids must be an array of non-empty strings")
    elif len(approvals) != len(set(approvals)):
        reporter.error(f"{context}.granted_approval_ids must not contain duplicates")

    _validate_dispatch_model_snapshot(
        project,
        task,
        task_card_frontmatter,
        bindings,
        context,
        state,
        require_committed,
        reporter,
    )

    delivery_state = task.get("delivery_state")
    if delivery_state not in DELIVERY_STATES:
        reporter.error(
            f"{context}.delivery_state must be one of: "
            f"{', '.join(sorted(DELIVERY_STATES))}"
        )
    integration_state = task.get("integration_state")
    if integration_state not in INTEGRATION_STATES:
        reporter.error(
            f"{context}.integration_state must be one of: "
            f"{', '.join(sorted(INTEGRATION_STATES))}"
        )

    expected_delivery_by_state = {
        "draft": "none",
        "ready": "none",
        "dispatched": "none",
        "in_progress": "working",
        "review_ready": "submitted",
        "returned": "rejected",
        "accepted": "accepted",
        "integrated": "accepted",
    }
    expected_delivery = expected_delivery_by_state.get(state)
    if state == "blocked" and task.get("resume_state") == "review_ready":
        expected_delivery = "submitted"
    if state == "blocked" and task.get("resume_state") == "accepted":
        expected_delivery = "accepted"
    if expected_delivery is not None and delivery_state != expected_delivery:
        reporter.error(
            f"{context}.delivery_state must be {expected_delivery!r} while state={state!r}"
        )
    if state != "integrated" and integration_state == "integrated":
        reporter.error(
            f"{context}.integration_state='integrated' is only valid while state='integrated'"
        )
    if state != "integrated" and task.get("integrated_commit") is not None:
        reporter.error(f"{context}.integrated_commit must be null before integration")
    if state != "blocked" and integration_state == "failed":
        reporter.error(
            f"{context}.integration_state='failed' is only valid while state='blocked'"
        )

    pre_delivery_states = {"draft", "ready", "dispatched", "in_progress"}
    if state in pre_delivery_states:
        for key in ("implementation_commit", "report_commit", "accepted_commit"):
            if task.get(key) is not None:
                reporter.error(f"{context}.{key} must be null before delivery")
    accepted_evidence_states = {"accepted", "integrated", "cancelled", "superseded"}
    if state == "blocked" and task.get("resume_state") == "accepted":
        accepted_evidence_states.add("blocked")
    if state not in accepted_evidence_states and task.get("accepted_commit") is not None:
        reporter.error(f"{context}.accepted_commit must be null before acceptance")

    _validate_blocked_envelope(task, context, state, reporter)
    _validate_timestamps(task, context, state, reporter)
    _validate_report_and_acceptance_paths(
        project,
        task,
        task_card_frontmatter,
        context,
        task_id,
        revision,
        attempt,
        state,
        require_committed,
        reporter,
    )

    delivered_states = {"review_ready", "returned", "accepted", "integrated"}
    if state == "blocked" and task.get("resume_state") in {"review_ready", "accepted"}:
        delivered_states.add("blocked")
    if state in delivered_states:
        if not _is_int(attempt) or attempt < 1:
            reporter.error(f"{context}.attempt must be >= 1 after a delivery exists")
        _validate_sha(
            task.get("implementation_commit"),
            f"{context}.implementation_commit",
            reporter,
            required=True,
        )
        _validate_sha(
            task.get("report_commit"),
            f"{context}.report_commit",
            reporter,
            required=True,
        )
        implementation_commit = task.get("implementation_commit")
        report_commit = task.get("report_commit")
        base_commit = (
            task_card_frontmatter.get("base_commit")
            if task_card_frontmatter is not None
            else None
        )
        if (
            isinstance(base_commit, str)
            and SHA_RE.fullmatch(base_commit)
            and "implementation_commit" in valid_commit_fields
            and isinstance(implementation_commit, str)
        ):
            base_contains_implementation = _git_is_ancestor(
                project, base_commit, implementation_commit
            )
            if base_contains_implementation is True:
                reporter.passed(
                    f"{context}.implementation_commit descends from the frozen base_commit"
                )
            elif base_contains_implementation is False:
                reporter.error(
                    f"{context}.implementation_commit must descend from the task-card base_commit"
                )
            else:
                reporter.warn(
                    f"could not verify frozen-base ancestry for {context}.implementation_commit"
                )
        if (
            "implementation_commit" in valid_commit_fields
            and "report_commit" in valid_commit_fields
            and isinstance(implementation_commit, str)
            and isinstance(report_commit, str)
        ):
            report_contains_implementation = _git_is_ancestor(
                project, implementation_commit, report_commit
            )
            if report_contains_implementation is True:
                reporter.passed(
                    f"{context}.report_commit contains implementation_commit"
                )
            elif report_contains_implementation is False:
                reporter.error(
                    f"{context}.implementation_commit must be an ancestor of report_commit"
                )
            else:
                reporter.warn(
                    f"could not verify commit ancestry for {context}.report_commit"
                )

    accepted_state = state in {"accepted", "integrated"} or (
        state == "blocked" and task.get("resume_state") == "accepted"
    )
    if accepted_state:
        if task.get("delivery_state") != "accepted":
            reporter.error(f"{context}.delivery_state must be 'accepted' after acceptance")
        _validate_sha(
            task.get("accepted_commit"),
            f"{context}.accepted_commit",
            reporter,
            required=True,
        )
        if task.get("accepted_commit") != task.get("implementation_commit"):
            reporter.error(
                f"{context}.accepted_commit must equal the reviewed implementation_commit"
            )

    if state == "accepted" and task.get("integration_state") not in {
        "pending",
        "not_applicable",
    }:
        reporter.error(
            f"{context}.integration_state must be 'pending' or 'not_applicable' "
            "while state='accepted'"
        )

    if state == "integrated":
        if task.get("integration_state") not in {"integrated", "not_applicable"}:
            reporter.error(
                f"{context}.integration_state must be 'integrated' or 'not_applicable' "
                "while state='integrated'"
            )
        _validate_sha(
            task.get("integrated_commit"),
            f"{context}.integrated_commit",
            reporter,
            required=True,
        )
        accepted_commit = task.get("accepted_commit")
        integrated_commit = task.get("integrated_commit")
        if (
            "accepted_commit" in valid_commit_fields
            and "integrated_commit" in valid_commit_fields
            and isinstance(accepted_commit, str)
            and isinstance(integrated_commit, str)
        ):
            integrated_contains_accepted = _git_is_ancestor(
                project, accepted_commit, integrated_commit
            )
            if integrated_contains_accepted is True:
                reporter.passed(f"{context}.integrated_commit contains accepted_commit")
            elif integrated_contains_accepted is False:
                event_path = _matching_integration_event(
                    project,
                    task_id if isinstance(task_id, str) else "",
                    accepted_commit,
                    integrated_commit,
                )
                if event_path is None:
                    reporter.error(
                        f"{context}.integrated_commit does not contain accepted_commit and "
                        "no matching CHANGE_INTEGRATED event records equivalence evidence"
                    )
                else:
                    reporter.passed(
                        f"{context}.integrated_commit has committed structured "
                        f"equivalence evidence in {event_path.as_posix()}"
                    )
            else:
                reporter.warn(
                    f"could not verify integration ancestry for {context}.integrated_commit"
                )

    if state in TERMINAL_STATES and task.get("current_dispatch") is not None:
        # The dispatch validator also reports this; retain this branch only as a
        # documented invariant without producing a duplicate diagnostic.
        pass

    if state != "__invalid__" and reporter.errors == starting_errors:
        reporter.passed(f"validated task {task_label} ({state})")


def validate(
    project: Path,
    require_committed: bool = True,
    allow_legacy_model_evidence: bool = False,
) -> Reporter:
    reporter = Reporter(
        allow_legacy_model_evidence=allow_legacy_model_evidence
    )
    if not project.exists():
        reporter.error(f"project path does not exist: {project}")
        return reporter
    if not project.is_dir():
        reporter.error(f"project path is not a directory: {project}")
        return reporter
    project = project.resolve()
    _validate_project_layout(project, reporter)
    _validate_runtime_privacy(project, reporter)
    _validate_git_worktree(project, reporter, require_committed)
    current_policy = _load_role_policy(project, reporter)
    bindings = _load_model_bindings(project, reporter)
    active_roles = _active_role_ids(project, reporter)
    if active_roles is not None and current_policy is not None:
        policy_roles = current_policy.get("roles")
        configured_roles = set(policy_roles) if isinstance(policy_roles, dict) else set()
        missing_role_policies = sorted(active_roles - configured_roles)
        if missing_role_policies:
            reporter.error(
                f"every Active role in docs/pm/ROLES.md must have a valid entry in "
                f"{ROLE_POLICY_FILE.as_posix()}; missing: "
                + ", ".join(missing_role_policies)
            )
        else:
            reporter.passed("every Active role has a current role/model policy")

    state_path = project / STATE_FILE
    if not state_path.is_file():
        return reporter
    state = _load_state(state_path, reporter)
    if state is None:
        return reporter
    tasks = _validate_top_level(state, reporter)
    if tasks is None:
        return reporter

    seen_task_ids: set[str] = set()
    seen_dispatch_ids: set[str] = set()
    frozen_policy_cache: dict[str, Optional[dict[str, Any]]] = {}
    for index, task in enumerate(tasks):
        _validate_task(
            project,
            task,
            index,
            seen_task_ids,
            seen_dispatch_ids,
            current_policy,
            bindings,
            frozen_policy_cache,
            active_roles,
            require_committed,
            reporter,
        )
    return reporter


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an AgentDesk task-card project (stdlib only)."
    )
    parser.add_argument(
        "--pre-commit",
        action="store_true",
        help=(
            "validate a proposed control-plane snapshot before committing it; "
            "final validation should omit this flag"
        ),
    )
    parser.add_argument(
        "--project",
        default=".",
        help="project root to validate (default: current directory)",
    )
    parser.add_argument(
        "--allow-legacy-model-evidence",
        action="store_true",
        help=(
            "migration-audit only: downgrade missing frozen model evidence on "
            "legacy terminal tasks to warnings; the result is not a strict pass "
            "and must not drive automatic acceptance"
        ),
    )
    parser.add_argument(
        "--require-runtime",
        action="store_true",
        help=(
            "after repository validation, require verified runtime route "
            "attestations and transport receipts for active task states"
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    reporter = validate(
        Path(args.project).expanduser(),
        require_committed=not args.pre_commit,
        allow_legacy_model_evidence=args.allow_legacy_model_evidence,
    )
    reporter.summary()
    runtime_failed = False
    if args.require_runtime:
        runtime_script = Path(__file__).resolve().with_name("validate_runtime.py")
        runtime_result = subprocess.run(
            [
                sys.executable,
                str(runtime_script),
                "--project",
                str(Path(args.project).expanduser()),
                "--check-active",
            ],
            check=False,
        )
        runtime_failed = runtime_result.returncode != 0
    return 1 if reporter.errors or runtime_failed else 0


if __name__ == "__main__":
    sys.exit(main())
