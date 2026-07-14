#!/usr/bin/env python3
"""Select a runtime model binding for an AgentDesk task dispatch.

Both input files are JSON-compatible YAML so this command can keep a
zero-dependency Python 3.9 contract.  Repository policy defines hard,
vendor-neutral requirements; the gitignored runtime file maps those
requirements to models available on the current machine.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Sequence


ROLE_POLICY_FILE = Path("docs/pm/ROLE-POLICIES.yaml")
MODEL_BINDINGS_FILE = Path(".agentdesk/runtime/model-bindings.yaml")
ROLE_POLICY_SCHEMA_VERSION = "agentdesk.role-policies/v1"
MODEL_BINDINGS_SCHEMA_VERSION = "agentdesk.model-bindings/v1"

MODEL_TIERS = ("basic", "standard", "advanced", "expert")
MODEL_TIER_INDEX = {tier: index for index, tier in enumerate(MODEL_TIERS)}
DELIBERATION_TIERS = ("efficient", "balanced", "deep")
DELIBERATION_TIER_INDEX = {
    tier: index for index, tier in enumerate(DELIBERATION_TIERS)
}
RISKS = ("L0", "L1", "L2", "L3", "L4")
DEGRADATION_POLICIES = (
    "block",
    "require_pm_approval",
    "allow_to_minimum",
)
FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
APPROVAL_ID_RE = re.compile(r"^APR-.+")


class SelectionError(Exception):
    """A clear, user-correctable configuration or selection error."""


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_rfc3339_utc(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _parse_json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SelectionError(
            f"{label} must be JSON-compatible YAML; "
            f"parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise SelectionError(f"{label} must contain a top-level object")
    return value


def _load_worktree_json_object(
    project: Path, relative_path: Path, label: str
) -> dict[str, Any]:
    current = project
    parts = relative_path.parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            raise SelectionError(f"{label} does not exist: {current}") from exc
        except OSError as exc:
            raise SelectionError(f"cannot inspect {label} path {current}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise SelectionError(
                f"{label} path must not contain symlinks: {current}"
            )
        if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise SelectionError(
                f"{label} path component is not a directory: {current}"
            )
        if index == len(parts) - 1 and not stat.S_ISREG(metadata.st_mode):
            raise SelectionError(f"{label} must be a regular file: {current}")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(current, flags)
    except OSError as exc:
        raise SelectionError(f"cannot open {label} at {current}: {exc}") from exc
    try:
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise SelectionError(f"{label} must be a regular file: {current}")
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                raw = handle.read()
        except UnicodeDecodeError as exc:
            raise SelectionError(f"{label} is not valid UTF-8: {current}") from exc
        except OSError as exc:
            raise SelectionError(f"cannot read {label} at {current}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _parse_json_object(raw, label)


def _git_error_detail(stderr: bytes) -> str:
    detail = stderr.decode("utf-8", errors="replace").strip()
    return detail or "git returned no diagnostic"


def _run_git(project: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
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
            env=environment,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SelectionError(
            "git executable was not found; it is required with "
            "--task-card-commit"
        ) from exc
    except OSError as exc:
        raise SelectionError(f"could not execute git: {exc}") from exc


def _load_policy_at_commit(project: Path, commit: str) -> dict[str, Any]:
    if not FULL_SHA_RE.fullmatch(commit):
        raise SelectionError(
            "--task-card-commit must be a full 40-character hexadecimal SHA"
        )

    commit_check = _run_git(project, ["cat-file", "-e", f"{commit}^{{commit}}"])
    if commit_check.returncode != 0:
        raise SelectionError(
            f"task-card commit {commit} is not a readable Git commit in "
            f"{project}: {_git_error_detail(commit_check.stderr)}"
        )

    repository_path = ROLE_POLICY_FILE.as_posix()
    object_spec = f"{commit}:{repository_path}"
    tree_entry = _run_git(
        project, ["ls-tree", "-z", commit, "--", repository_path]
    )
    if tree_entry.returncode != 0:
        raise SelectionError(
            f"role policy path {repository_path!r} is not readable at "
            f"task-card commit {commit}: {_git_error_detail(tree_entry.stderr)}"
        )
    entries = [entry for entry in tree_entry.stdout.split(b"\0") if entry]
    valid_regular_blob = False
    if len(entries) == 1 and b"\t" in entries[0]:
        metadata, returned_path = entries[0].split(b"\t", 1)
        parts = metadata.split()
        valid_regular_blob = (
            returned_path.decode("utf-8", errors="surrogateescape") == repository_path
            and len(parts) == 3
            and parts[0] in {b"100644", b"100755"}
            and parts[1] == b"blob"
        )
    if not valid_regular_blob:
        raise SelectionError(
            f"role policy path {repository_path!r} at task-card commit "
            f"{commit} must be a regular Git blob, not a symlink or submodule"
        )

    blob = _run_git(project, ["show", object_spec])
    if blob.returncode != 0:
        raise SelectionError(
            f"could not read role policy blob {repository_path!r} at "
            f"task-card commit {commit}: {_git_error_detail(blob.stderr)}"
        )
    try:
        raw = blob.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SelectionError(
            f"role policy path {repository_path!r} at task-card commit "
            f"{commit} is not valid UTF-8"
        ) from exc
    return _parse_json_object(
        raw,
        f"role policy at {commit}:{repository_path}",
    )


def _require_schema(
    value: dict[str, Any], expected: str, label: str
) -> None:
    actual = value.get("schema_version")
    if actual != expected:
        raise SelectionError(
            f"{label}.schema_version must be {expected!r}, got {actual!r}"
        )


def _require_exact_order(
    value: Any, expected: Sequence[str], context: str
) -> None:
    if value != list(expected):
        raise SelectionError(f"{context} must be {list(expected)!r}")


def _required_value(
    value: dict[str, Any], key: str, context: str
) -> Any:
    if key not in value:
        raise SelectionError(f"{context} is missing required field {key!r}")
    return value[key]


def _require_tier(value: Any, context: str) -> str:
    if not isinstance(value, str) or value not in MODEL_TIER_INDEX:
        raise SelectionError(
            f"{context} must be one of {', '.join(MODEL_TIERS)}, got {value!r}"
        )
    return value


def _require_deliberation_tier(value: Any, context: str) -> str:
    if not isinstance(value, str) or value not in DELIBERATION_TIER_INDEX:
        raise SelectionError(
            f"{context} must be one of {', '.join(DELIBERATION_TIERS)}, "
            f"got {value!r}"
        )
    return value


def _require_capabilities(value: Any, context: str) -> set[str]:
    if not isinstance(value, list):
        raise SelectionError(f"{context} must be an array of non-empty strings")
    capabilities: set[str] = set()
    for index, item in enumerate(value):
        if not _nonempty_string(item):
            raise SelectionError(
                f"{context}[{index}] must be a non-empty string"
            )
        normalized = item.strip()
        if normalized in capabilities:
            raise SelectionError(
                f"{context} contains duplicate capability {normalized!r}"
            )
        capabilities.add(normalized)
    return capabilities


def _max_tier(*tiers: str) -> str:
    return max(tiers, key=MODEL_TIER_INDEX.__getitem__)


def _policy_requirements(
    policy: dict[str, Any],
    role_id: str,
    risk: str,
    task_min_tier: str,
    task_capabilities: set[str],
) -> tuple[str, str, str, set[str], str]:
    _require_schema(policy, ROLE_POLICY_SCHEMA_VERSION, "role policy")
    _require_exact_order(
        policy.get("tier_order"), MODEL_TIERS, "role policy.tier_order"
    )
    deliberation_order = _required_value(
        policy, "deliberation_tier_order", "role policy"
    )
    _require_exact_order(
        deliberation_order,
        DELIBERATION_TIERS,
        "role policy.deliberation_tier_order",
    )

    risk_floors = policy.get("risk_floors")
    if not isinstance(risk_floors, dict):
        raise SelectionError("role policy.risk_floors must be an object")
    missing_risks = [item for item in RISKS if item not in risk_floors]
    if missing_risks:
        raise SelectionError(
            "role policy.risk_floors is missing: " + ", ".join(missing_risks)
        )
    validated_risk_floors = {
        item: _require_tier(
            risk_floors[item], f"role policy.risk_floors.{item}"
        )
        for item in RISKS
    }
    for lower_risk, higher_risk in zip(RISKS, RISKS[1:]):
        if (
            MODEL_TIER_INDEX[validated_risk_floors[higher_risk]]
            < MODEL_TIER_INDEX[validated_risk_floors[lower_risk]]
        ):
            raise SelectionError(
                "role policy.risk_floors must not decrease: "
                f"{higher_risk} is below {lower_risk}"
            )
    risk_floor = validated_risk_floors[risk]

    roles = policy.get("roles")
    if not isinstance(roles, dict):
        raise SelectionError("role policy.roles must be an object")
    role = roles.get(role_id)
    if not isinstance(role, dict):
        available = ", ".join(sorted(str(key) for key in roles)) or "<none>"
        raise SelectionError(
            f"role {role_id!r} has no policy; available roles: {available}"
        )

    context = f"role policy.roles.{role_id}"
    minimum_tier = _require_tier(
        role.get("minimum_tier"), f"{context}.minimum_tier"
    )
    default_tier = _require_tier(
        role.get("default_tier"), f"{context}.default_tier"
    )
    if MODEL_TIER_INDEX[default_tier] < MODEL_TIER_INDEX[minimum_tier]:
        raise SelectionError(
            f"{context}.default_tier must not be below minimum_tier"
        )

    role_deliberation = _require_deliberation_tier(
        _required_value(role, "deliberation_tier", context),
        f"{context}.deliberation_tier",
    )
    role_capabilities = _require_capabilities(
        role.get("required_capabilities"),
        f"{context}.required_capabilities",
    )
    degradation_policy = role.get("degradation_policy")
    if degradation_policy not in DEGRADATION_POLICIES:
        raise SelectionError(
            f"{context}.degradation_policy must be one of "
            f"{', '.join(DEGRADATION_POLICIES)}, got {degradation_policy!r}"
        )

    task_floor = minimum_tier
    if task_min_tier != "inherit":
        task_floor = _require_tier(task_min_tier, "--task-min-tier")
    required_tier = _max_tier(minimum_tier, task_floor, risk_floor)
    preferred_tier = _max_tier(default_tier, required_tier)
    required_capabilities = role_capabilities | task_capabilities
    return (
        required_tier,
        preferred_tier,
        role_deliberation,
        required_capabilities,
        degradation_policy,
    )


def _validated_bindings(
    document: dict[str, Any]
) -> list[dict[str, Any]]:
    _require_schema(document, MODEL_BINDINGS_SCHEMA_VERSION, "model bindings")
    if "updated_at" not in document:
        raise SelectionError("model bindings is missing required field 'updated_at'")
    updated_at = document.get("updated_at")
    if updated_at is not None and not _is_rfc3339_utc(updated_at):
        raise SelectionError(
            "model bindings.updated_at must be a UTC RFC3339 timestamp or null"
        )
    raw_bindings = document.get("bindings")
    if not isinstance(raw_bindings, dict):
        raise SelectionError("model bindings.bindings must be an object")

    bindings: list[dict[str, Any]] = []
    for binding_id in sorted(raw_bindings):
        if not _nonempty_string(binding_id):
            raise SelectionError("model binding IDs must be non-empty strings")
        if binding_id != binding_id.strip():
            raise SelectionError(
                f"model binding ID {binding_id!r} must not contain outer whitespace"
            )
        raw = raw_bindings[binding_id]
        context = f"model bindings.bindings.{binding_id}"
        if not isinstance(raw, dict):
            raise SelectionError(f"{context} must be an object")

        provider = raw.get("provider")
        model_id = raw.get("model_id")
        if not _nonempty_string(provider):
            raise SelectionError(f"{context}.provider must be a non-empty string")
        if not _nonempty_string(model_id):
            raise SelectionError(f"{context}.model_id must be a non-empty string")
        if provider != provider.strip():
            raise SelectionError(f"{context}.provider must not contain outer whitespace")
        if model_id != model_id.strip():
            raise SelectionError(f"{context}.model_id must not contain outer whitespace")
        tier = _require_tier(raw.get("tier"), f"{context}.tier")
        deliberation_tier = _require_deliberation_tier(
            _required_value(raw, "deliberation_tier", context),
            f"{context}.deliberation_tier",
        )
        capabilities = _require_capabilities(
            raw.get("capabilities"), f"{context}.capabilities"
        )
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            raise SelectionError(f"{context}.enabled must be true or false")

        bindings.append(
            {
                "binding_id": binding_id.strip(),
                "provider": provider,
                "model_id": model_id,
                "tier": tier,
                "deliberation_tier": deliberation_tier,
                "capabilities": capabilities,
                "enabled": enabled,
            }
        )
    return bindings


def _rejection_reasons(
    binding: dict[str, Any],
    required_tier: str,
    required_deliberation: str,
    required_capabilities: set[str],
) -> list[str]:
    reasons: list[str] = []
    if not binding["enabled"]:
        reasons.append("disabled")
    if MODEL_TIER_INDEX[binding["tier"]] < MODEL_TIER_INDEX[required_tier]:
        reasons.append(f"tier {binding['tier']} < {required_tier}")
    if (
        DELIBERATION_TIER_INDEX[binding["deliberation_tier"]]
        < DELIBERATION_TIER_INDEX[required_deliberation]
    ):
        reasons.append(
            "deliberation "
            f"{binding['deliberation_tier']} < {required_deliberation}"
        )
    missing = sorted(required_capabilities - binding["capabilities"])
    if missing:
        reasons.append("missing capabilities " + ",".join(missing))
    return reasons


def select_binding(
    policy: dict[str, Any],
    bindings_document: dict[str, Any],
    role_id: str,
    risk: str,
    task_min_tier: str,
    task_capabilities: set[str],
    degradation_approval_id: Optional[str],
) -> dict[str, Any]:
    (
        required_tier,
        preferred_tier,
        required_deliberation,
        required_capabilities,
        degradation_policy,
    ) = _policy_requirements(
        policy,
        role_id,
        risk,
        task_min_tier,
        task_capabilities,
    )
    bindings = _validated_bindings(bindings_document)

    eligible: list[dict[str, Any]] = []
    rejected: list[str] = []
    for binding in bindings:
        reasons = _rejection_reasons(
            binding,
            required_tier,
            required_deliberation,
            required_capabilities,
        )
        if reasons:
            rejected.append(
                f"{binding['binding_id']} ({'; '.join(reasons)})"
            )
        else:
            eligible.append(binding)

    if not eligible:
        detail = "; ".join(rejected) if rejected else "no bindings configured"
        caps = ",".join(sorted(required_capabilities)) or "<none>"
        raise SelectionError(
            "no enabled model binding satisfies hard requirements "
            f"tier>={required_tier}, deliberation>={required_deliberation}, "
            f"capabilities=[{caps}]; candidates: {detail}"
        )

    preferred_index = MODEL_TIER_INDEX[preferred_tier]
    at_or_above_preferred = [
        binding
        for binding in eligible
        if MODEL_TIER_INDEX[binding["tier"]] >= preferred_index
    ]
    degraded = False
    if at_or_above_preferred:
        selected = min(
            at_or_above_preferred,
            key=lambda binding: (
                MODEL_TIER_INDEX[binding["tier"]],
                binding["binding_id"],
            ),
        )
    else:
        degraded = True
        if degradation_policy == "block":
            available = ", ".join(
                f"{item['binding_id']}:{item['tier']}"
                for item in sorted(eligible, key=lambda item: item["binding_id"])
            )
            raise SelectionError(
                f"role {role_id!r} requires preferred tier {preferred_tier}; "
                "degradation_policy=block forbids selecting eligible lower-tier "
                f"bindings ({available})"
            )
        if degradation_policy == "require_pm_approval":
            if not _nonempty_string(degradation_approval_id):
                raise SelectionError(
                    f"role {role_id!r} requires --degradation-approval-id to "
                    f"select below preferred tier {preferred_tier}"
                )
            if APPROVAL_ID_RE.fullmatch(degradation_approval_id.strip()) is None:
                raise SelectionError(
                    "--degradation-approval-id must use the APR-* namespace"
                )
        selected = min(
            eligible,
            key=lambda binding: (
                -MODEL_TIER_INDEX[binding["tier"]],
                binding["binding_id"],
            ),
        )

    approval_snapshot: Optional[str] = None
    if degraded and _nonempty_string(degradation_approval_id):
        approval_snapshot = degradation_approval_id.strip()

    return {
        "required_model_tier": required_tier,
        "required_model_capabilities": sorted(required_capabilities),
        "model_binding_id": selected["binding_id"],
        "selected_model_provider": selected["provider"],
        "selected_model_id": selected["model_id"],
        "selected_model_tier": selected["tier"],
        "selected_deliberation_tier": selected["deliberation_tier"],
        "selected_model_capabilities": sorted(selected["capabilities"]),
        "model_degradation_approval_id": approval_snapshot,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Select an enabled local model binding that satisfies repository "
            "role policy and task requirements."
        )
    )
    parser.add_argument(
        "--project",
        default=".",
        help="project root (default: current directory)",
    )
    parser.add_argument(
        "--task-card-commit",
        metavar="SHA",
        help=(
            "full 40-character task-card commit SHA; read the frozen role "
            "policy from that commit instead of the working tree"
        ),
    )
    parser.add_argument("--role-id", required=True, help="role policy ID")
    parser.add_argument("--risk", required=True, choices=RISKS, help="task risk")
    parser.add_argument(
        "--task-min-tier",
        default="inherit",
        choices=("inherit",) + MODEL_TIERS,
        help="task-specific minimum model tier (default: inherit)",
    )
    parser.add_argument(
        "--required-capability",
        action="append",
        default=[],
        metavar="CAPABILITY",
        help="additional hard capability; repeat for multiple values",
    )
    parser.add_argument(
        "--degradation-approval-id",
        help="PM approval ID required by approved below-preferred selection",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        project = Path(args.project).expanduser()
        if not project.exists():
            raise SelectionError(f"project path does not exist: {project}")
        if not project.is_dir():
            raise SelectionError(f"project path is not a directory: {project}")
        project = project.resolve()

        task_capabilities: set[str] = set()
        for capability in args.required_capability:
            if not _nonempty_string(capability):
                raise SelectionError(
                    "--required-capability must be a non-empty string"
                )
            task_capabilities.add(capability.strip())
        if args.degradation_approval_id is not None and not _nonempty_string(
            args.degradation_approval_id
        ):
            raise SelectionError(
                "--degradation-approval-id must be a non-empty string"
            )
        if (
            args.degradation_approval_id is not None
            and APPROVAL_ID_RE.fullmatch(args.degradation_approval_id.strip()) is None
        ):
            raise SelectionError(
                "--degradation-approval-id must use the APR-* namespace"
            )

        if args.task_card_commit is None:
            policy = _load_worktree_json_object(
                project, ROLE_POLICY_FILE, "role policy"
            )
        else:
            policy = _load_policy_at_commit(project, args.task_card_commit)
        bindings = _load_worktree_json_object(
            project, MODEL_BINDINGS_FILE, "model bindings"
        )
        result = select_binding(
            policy=policy,
            bindings_document=bindings,
            role_id=args.role_id,
            risk=args.risk,
            task_min_tier=args.task_min_tier,
            task_capabilities=task_capabilities,
            degradation_approval_id=args.degradation_approval_id,
        )
    except SelectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
