#!/usr/bin/env python3
"""Explicitly pin one legacy-unpinned AgentDesk project to this release.

This command is intentionally narrower than ``init_project.py``.  It accepts
only an existing, clean AgentDesk Git worktree whose canonical state has no
active attempt and whose version lock is absent.  A dry run is always
read-only.  Apply mode additionally requires a verified Stable Skill bundle
and an explicit acknowledgement before it exclusively creates the lock and
asks the same bundle's initializer to fill only missing template files, then
regenerates the derived project views.
"""

import argparse
import errno
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


SKILL_ROOT = Path(__file__).resolve().parent.parent
ADOPTER = Path(__file__).resolve()
RELEASE_MANIFEST = SKILL_ROOT / "release-manifest.json"
LOCK_TEMPLATE = (
    SKILL_ROOT
    / "assets"
    / "project-template"
    / "docs"
    / "pm"
    / "AGENTDESK.lock.json"
)
INITIALIZER = SKILL_ROOT / "scripts" / "init_project.py"
RENDERER = SKILL_ROOT / "scripts" / "render_views.py"
VALIDATOR = SKILL_ROOT / "scripts" / "validate_project.py"
COMPATIBILITY_CHECKER = SKILL_ROOT / "scripts" / "check_compatibility.py"
PROJECT_TEMPLATE = SKILL_ROOT / "assets" / "project-template"
LOCK_FILE = Path("docs/pm/AGENTDESK.lock.json")
STATE_FILE = Path("docs/pm/state/tasks.yaml")
IGNORE_ENTRY = ".agentdesk/runtime/"
GENERATED_VIEW_FILES = (
    Path("docs/pm/BOARD.md"),
    Path("docs/pm/STATUS.md"),
    Path("docs/pm/CONSULTATIONS.md"),
)
PUBLIC_CONTROL_PLANE_PROBES = (
    Path("docs/pm/tasks/TC-000-r1-agentdesk-trackability-probe.md"),
    Path("docs/pm/reports/TC-000-r1-a1.md"),
    Path("docs/pm/acceptances/TC-000-r1-a1.md"),
    Path("docs/pm/events/EVT-AGENTDESK-TRACKABILITY-PROBE.yaml"),
    Path("docs/pm/outbox/MSG-AGENTDESK-TRACKABILITY-PROBE.yaml"),
    Path("docs/pm/consultations/C000/consultation.yaml"),
)
AGENTDESK_MARKERS = (
    STATE_FILE,
    Path("docs/pm/BOARD.md"),
    Path("docs/pm/CHECKS.yaml"),
    Path("docs/pm/CONSULTATION-POLICY.yaml"),
    Path("docs/pm/CONSULTATIONS.md"),
    Path("docs/pm/DECISIONS.md"),
    Path("docs/pm/PM-PLAYBOOK.md"),
    Path("docs/pm/ROLE-POLICIES.yaml"),
    Path("docs/pm/ROLES.md"),
    Path("docs/pm/STATUS.md"),
    Path("docs/pm/state"),
    Path("docs/pm/tasks"),
    Path("docs/pm/reports"),
    Path("docs/pm/acceptances"),
    Path("docs/pm/consultations"),
    Path("docs/pm/events"),
    Path("docs/pm/outbox"),
    Path(".agentdesk"),
    Path(".agentdesk/runtime"),
    Path(".agentdesk/runtime/consultations.yaml"),
    Path(".agentdesk/runtime/model-bindings.yaml"),
    Path(".agentdesk/runtime/routes.yaml"),
    Path(".agentdesk/runtime/transport-receipts.yaml"),
)

RESULT_SCHEMA = "agentdesk.legacy-adoption-result/v2"
RELEASE_MANIFEST_SCHEMA = "agentdesk.release-manifest/v1"
PROJECT_LOCK_SCHEMA = "agentdesk.project-lock/v1"
SCHEMA_SET_ID = "agentdesk.schema-set/v1"
DIGEST_SCOPE = "all-files-with-canonical-release-manifest"
ADOPTION_LEVELS = ("lite", "standard", "automated")
TASK_STATES = frozenset(
    (
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
)
ACTIVE_STATES = frozenset(("dispatched", "in_progress", "review_ready"))
TASK_ID_RE = re.compile(r"^TC-[0-9]{3,}$")
SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BUNDLE_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class AdoptionError(Exception):
    """A deterministic, user-actionable adoption failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DuplicateKeyError(ValueError):
    """Raised when JSON contains a key whose earlier value would be lost."""


class NonFiniteNumberError(ValueError):
    """Raised when JSON uses NaN or infinity, which strict JSON forbids."""


def _reject_duplicate_keys(
    pairs: Iterable[Tuple[str, Any]]
) -> Dict[str, Any]:
    value = {}  # type: Dict[str, Any]
    for key, item in pairs:
        if key in value:
            raise DuplicateKeyError("duplicate JSON key {!r}".format(key))
        value[key] = item
    return value


def _reject_non_finite_number(value: str) -> None:
    raise NonFiniteNumberError(
        "non-finite JSON number {!r} is not allowed".format(value)
    )


def _load_json_object_and_bytes(
    path: Path, label: str
) -> Tuple[Dict[str, Any], bytes]:
    if path.is_symlink():
        raise AdoptionError(
            "unsafe_symlink",
            "{} must not be a symbolic link: {}".format(label, path),
        )
    if not path.is_file():
        raise AdoptionError(
            "missing_file", "{} does not exist: {}".format(label, path)
        )
    try:
        raw_bytes = path.read_bytes()
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdoptionError(
            "invalid_utf8", "{} is not valid UTF-8: {}".format(label, path)
        ) from exc
    except OSError as exc:
        raise AdoptionError(
            "read_error", "cannot read {} at {}: {}".format(label, path, exc)
        ) from exc
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_number,
        )
    except (
        json.JSONDecodeError,
        DuplicateKeyError,
        NonFiniteNumberError,
    ) as exc:
        if isinstance(exc, json.JSONDecodeError):
            detail = "line {}, column {}: {}".format(
                exc.lineno, exc.colno, exc.msg
            )
        else:
            detail = str(exc)
        raise AdoptionError(
            "invalid_json",
            "{} must be strict JSON ({}): {}".format(label, detail, path),
        ) from exc
    if not isinstance(value, dict):
        raise AdoptionError(
            "invalid_object",
            "{} must contain one top-level object: {}".format(label, path),
        )
    return value, raw_bytes


def _load_json_object(path: Path, label: str) -> Dict[str, Any]:
    value, _raw_bytes = _load_json_object_and_bytes(path, label)
    return value


def _nonempty_string(
    value: Any, label: str, code: str = "invalid_state"
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdoptionError(
            code, "{} must be a non-empty string".format(label)
        )
    return value


def _template_safe_string(
    value: Any, label: str, code: str = "invalid_state"
) -> str:
    result = _nonempty_string(value, label, code=code)
    if any(ord(character) < 0x20 for character in result):
        raise AdoptionError(
            code,
            "{} must be a one-line printable string".format(label),
        )
    if '"' in result or "\\" in result:
        raise AdoptionError(
            code,
            "{} cannot contain a double quote or backslash".format(label),
        )
    return result


def _object(value: Any, label: str, code: str = "invalid_manifest") -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise AdoptionError(code, "{} must be an object".format(label))
    return value


def _load_manifest(path: Path) -> Dict[str, Any]:
    manifest = _load_json_object(path, "release manifest")
    if manifest.get("schema_version") != RELEASE_MANIFEST_SCHEMA:
        raise AdoptionError(
            "invalid_manifest",
            "release manifest schema_version must be {!r}".format(
                RELEASE_MANIFEST_SCHEMA
            ),
        )
    _template_safe_string(
        manifest.get("skill_release"),
        "release manifest skill_release",
        code="invalid_manifest",
    )
    release_status = manifest.get("release_status")
    if not isinstance(release_status, str) or not release_status:
        raise AdoptionError(
            "invalid_manifest",
            "release manifest release_status must be a non-empty string",
        )
    source_commit = manifest.get("source_commit")
    if source_commit is not None and (
        not isinstance(source_commit, str)
        or SOURCE_COMMIT_RE.fullmatch(source_commit) is None
    ):
        raise AdoptionError(
            "invalid_manifest",
            "release manifest source_commit must be null or lowercase 40-hex",
        )
    bundle_digest = manifest.get("bundle_sha256")
    if bundle_digest is not None and (
        not isinstance(bundle_digest, str)
        or BUNDLE_DIGEST_RE.fullmatch(bundle_digest) is None
    ):
        raise AdoptionError(
            "invalid_manifest",
            "release manifest bundle_sha256 must be null or lowercase 64-hex",
        )
    if manifest.get("digest_scope") != DIGEST_SCOPE:
        raise AdoptionError(
            "invalid_manifest",
            "release manifest digest_scope must be {!r}".format(DIGEST_SCOPE),
        )
    _nonempty_string(
        manifest.get("protocol_version"),
        "release manifest protocol_version",
        code="invalid_manifest",
    )
    schema_set = _object(
        manifest.get("schema_set"), "release manifest schema_set"
    )
    if schema_set.get("id") != SCHEMA_SET_ID:
        raise AdoptionError(
            "invalid_manifest",
            "release manifest schema_set.id must be {!r}".format(
                SCHEMA_SET_ID
            ),
        )
    schemas = _object(
        schema_set.get("schemas"), "release manifest schema_set.schemas"
    )
    if not schemas:
        raise AdoptionError(
            "invalid_manifest",
            "release manifest schema_set.schemas must not be empty",
        )
    for name, version in schemas.items():
        _nonempty_string(
            name,
            "release manifest schema component",
            code="invalid_manifest",
        )
        _nonempty_string(
            version,
            "release manifest schema {!r}".format(name),
            code="invalid_manifest",
        )
    _object(
        manifest.get("runtime_adapter"),
        "release manifest runtime_adapter",
    )
    compatibility = _object(
        manifest.get("compatibility"),
        "release manifest compatibility",
    )
    _object(
        compatibility.get("reader"),
        "release manifest compatibility.reader",
    )
    _object(
        compatibility.get("writer"),
        "release manifest compatibility.writer",
    )
    return manifest


def _canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    normalized = dict(manifest)
    normalized["bundle_sha256"] = None
    return (
        json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _compute_bundle_digest(
    bundle_root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
) -> str:
    bundle_root = bundle_root.absolute()
    manifest_path = manifest_path.absolute()
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise AdoptionError(
            "unsafe_bundle",
            "Skill bundle root must be a regular directory: {}".format(
                bundle_root
            ),
        )
    try:
        manifest_relative = manifest_path.relative_to(bundle_root)
    except ValueError as exc:
        raise AdoptionError(
            "unsafe_bundle", "release manifest must be inside the Skill bundle"
        ) from exc

    entries = []  # type: List[Tuple[str, str]]
    try:
        paths = sorted(
            bundle_root.rglob("*"),
            key=lambda item: item.relative_to(bundle_root).as_posix(),
        )
        for path in paths:
            relative = path.relative_to(bundle_root)
            if path.is_symlink():
                raise AdoptionError(
                    "unsafe_bundle",
                    "Skill bundle must not contain symbolic links: {}".format(
                        relative.as_posix()
                    ),
                )
            if path.is_dir():
                continue
            if not path.is_file():
                raise AdoptionError(
                    "unsafe_bundle",
                    "Skill bundle contains an unsupported entry: {}".format(
                        relative.as_posix()
                    ),
                )
            if relative == manifest_relative:
                content = _canonical_manifest_bytes(manifest)
            else:
                content = path.read_bytes()
            entries.append(
                (
                    relative.as_posix(),
                    hashlib.sha256(content).hexdigest(),
                )
            )
    except AdoptionError:
        raise
    except OSError as exc:
        raise AdoptionError(
            "bundle_read_error", "cannot hash Skill bundle: {}".format(exc)
        ) from exc

    aggregate = hashlib.sha256()
    for relative, file_digest in entries:
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(file_digest.encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def _writable_bundle_paths(bundle_root: Path) -> List[str]:
    writable: List[str] = []
    try:
        for path in (bundle_root, *bundle_root.rglob("*")):
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                continue
            if stat.S_IMODE(mode) & 0o222:
                writable.append(
                    "."
                    if path == bundle_root
                    else path.relative_to(bundle_root).as_posix()
                )
    except OSError as exc:
        raise AdoptionError(
            "bundle_read_error",
            "cannot inspect Skill bundle modes: {}".format(exc),
        ) from exc
    return writable


def _stable_release_problem(
    manifest: Mapping[str, Any],
    actual_digest: str,
    writable_paths: Sequence[str],
) -> Optional[Dict[str, str]]:
    if manifest.get("release_status") != "stable":
        return {
            "code": "release_not_stable",
            "message": "apply requires release_status='stable'",
        }
    source_commit = manifest.get("source_commit")
    if (
        not isinstance(source_commit, str)
        or SOURCE_COMMIT_RE.fullmatch(source_commit) is None
    ):
        return {
            "code": "release_identity_unverified",
            "message": "apply requires a lowercase 40-hex source_commit",
        }
    declared_digest = manifest.get("bundle_sha256")
    if (
        not isinstance(declared_digest, str)
        or BUNDLE_DIGEST_RE.fullmatch(declared_digest) is None
    ):
        return {
            "code": "release_identity_unverified",
            "message": "apply requires a lowercase 64-hex bundle_sha256",
        }
    if declared_digest != actual_digest:
        return {
            "code": "bundle_digest_mismatch",
            "message": (
                "recomputed Skill bundle digest does not match the release "
                "manifest"
            ),
        }
    if writable_paths:
        return {
            "code": "mutable_bundle",
            "message": (
                "apply requires a read-only immutable Skill bundle; writable "
                "path(s): {}".format(", ".join(writable_paths[:8]))
            ),
        }
    return None


def _assert_safe_relative_path(
    project: Path, relative_path: Path, label: str
) -> None:
    current = project
    for index, part in enumerate(relative_path.parts):
        current = current / part
        if current.is_symlink():
            raise AdoptionError(
                "unsafe_symlink",
                "{} contains a symbolic-link component: {}".format(
                    label, current
                ),
            )
        if (
            index < len(relative_path.parts) - 1
            and current.exists()
            and not current.is_dir()
        ):
            raise AdoptionError(
                "unsafe_path",
                "{} has a non-directory parent component: {}".format(
                    label, current
                ),
            )


def _inspect_markers(project: Path) -> List[str]:
    markers = []  # type: List[str]
    for relative in AGENTDESK_MARKERS:
        _assert_safe_relative_path(project, relative, "AgentDesk marker")
        path = project / relative
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                raise AdoptionError(
                    "unsafe_symlink",
                    "AgentDesk marker must not be a symlink: {}".format(path),
                )
            markers.append(relative.as_posix())
    if not markers:
        raise AdoptionError(
            "not_agentdesk_project",
            "project has no recognized AgentDesk markers",
        )
    if STATE_FILE.as_posix() not in markers:
        raise AdoptionError(
            "missing_canonical_state",
            "{} is required to adopt a legacy project".format(
                STATE_FILE.as_posix()
            ),
        )
    return markers


def _inspect_state(
    project: Path, manifest: Mapping[str, Any]
) -> Dict[str, Any]:
    path = project / STATE_FILE
    state, state_bytes = _load_json_object_and_bytes(
        path, "canonical task state"
    )
    schemas = _object(
        _object(
            manifest.get("schema_set"), "release manifest schema_set"
        ).get("schemas"),
        "release manifest schema_set.schemas",
    )
    expected_tasks_schema = schemas.get("tasks")
    if state.get("schema_version") != expected_tasks_schema:
        raise AdoptionError(
            "unsupported_state_schema",
            "canonical task state schema_version must equal this release's "
            "tasks schema {!r}".format(expected_tasks_schema),
        )

    project_id = _template_safe_string(
        state.get("project_id"), "canonical task state project_id"
    )
    adoption_level = state.get("adoption_level")
    if adoption_level not in ADOPTION_LEVELS:
        raise AdoptionError(
            "invalid_state",
            "canonical task state adoption_level must be one of: {}".format(
                ", ".join(ADOPTION_LEVELS)
            ),
        )
    pm_control = _object(
        state.get("pm_control"),
        "canonical task state pm_control",
        code="invalid_state",
    )
    holder_id = _template_safe_string(
        pm_control.get("holder_id"),
        "canonical task state pm_control.holder_id",
    )
    updated_at = _template_safe_string(
        state.get("updated_at"), "canonical task state updated_at"
    )
    updated_candidate = (
        updated_at[:-1] + "+00:00"
        if updated_at.endswith("Z")
        else updated_at
    )
    try:
        parsed_updated_at = datetime.fromisoformat(updated_candidate)
    except ValueError:
        parsed_updated_at = None
    if (
        parsed_updated_at is None
        or parsed_updated_at.tzinfo is None
        or parsed_updated_at.utcoffset() != timedelta(0)
    ):
        raise AdoptionError(
            "invalid_state",
            "canonical task state updated_at must be a UTC RFC3339 timestamp",
        )
    pm_mode = _template_safe_string(
        pm_control.get("mode"),
        "canonical task state pm_control.mode",
    )
    if pm_mode not in {"manual", "timed"}:
        raise AdoptionError(
            "invalid_state",
            "canonical task state pm_control.mode must be manual or timed",
        )
    lease_epoch = pm_control.get("lease_epoch")
    if (
        not isinstance(lease_epoch, int)
        or isinstance(lease_epoch, bool)
        or lease_epoch < 1
    ):
        raise AdoptionError(
            "invalid_state",
            "canonical task state pm_control.lease_epoch must be an "
            "integer >= 1",
        )

    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        raise AdoptionError(
            "invalid_state", "canonical task state tasks must be an array"
        )
    active = []  # type: List[str]
    for index, task in enumerate(tasks):
        context = "canonical task state tasks[{}]".format(index)
        if not isinstance(task, dict):
            raise AdoptionError(
                "invalid_state", "{} must be an object".format(context)
            )
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or TASK_ID_RE.fullmatch(task_id) is None:
            raise AdoptionError(
                "invalid_state",
                "{}.task_id must match TC- followed by at least three "
                "digits".format(context),
            )
        state_name = task.get("state")
        if state_name not in TASK_STATES:
            raise AdoptionError(
                "invalid_state",
                "{}.state must be one of: {}".format(
                    context, ", ".join(sorted(TASK_STATES))
                ),
            )
        attempt = task.get("attempt")
        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 0
        ):
            raise AdoptionError(
                "invalid_state",
                "{}.attempt must be a non-negative integer".format(context),
            )
        dispatch = task.get("current_dispatch")
        if dispatch is not None and not isinstance(dispatch, dict):
            raise AdoptionError(
                "invalid_state",
                "{}.current_dispatch must be an object or null".format(
                    context
                ),
            )
        blocked_attempt_valid = task.get("blocked_attempt_valid")
        if blocked_attempt_valid is not None and not isinstance(
            blocked_attempt_valid, bool
        ):
            raise AdoptionError(
                "invalid_state",
                "{}.blocked_attempt_valid must be boolean or null".format(
                    context
                ),
            )
        if state_name == "blocked" and not isinstance(
            blocked_attempt_valid, bool
        ):
            raise AdoptionError(
                "invalid_state",
                "{}.blocked_attempt_valid must be boolean while blocked".format(
                    context
                ),
            )
        # Adoption needs only a conservative proof that no attempt may be
        # active.  Any dispatch envelope, even one that is structurally stale,
        # is therefore classified as active and blocked before migration.
        is_active = (
            dispatch is not None
            or state_name in ACTIVE_STATES
            or (
                state_name == "blocked"
                and blocked_attempt_valid is True
            )
        )
        if is_active:
            active.append(task_id)
    if active:
        raise AdoptionError(
            "active_attempts",
            "legacy adoption is blocked while attempts are active: {}".format(
                ", ".join(active)
            ),
        )

    return {
        "project_id": project_id,
        "adoption_level": adoption_level,
        "pm_holder_id": holder_id,
        "updated_at": updated_at,
        "pm_mode": pm_mode,
        "state_sha256": hashlib.sha256(state_bytes).hexdigest(),
        "task_count": len(tasks),
    }


def _sanitized_git_environment() -> Dict[str, str]:
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


def _isolated_python_command(
    script: Path, arguments: Sequence[str]
) -> List[str]:
    """Run a bundled script without user-site or environment code injection."""

    return [sys.executable, "-I", str(script)] + list(arguments)


def _run_git(
    project: Path, arguments: Sequence[str], allow_failure: bool = False
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project),
            ]
            + list(arguments),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            env=_sanitized_git_environment(),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise AdoptionError(
            "git_inspection_failed",
            "cannot inspect project Git state: {}".format(exc),
        ) from exc
    if result.returncode != 0 and not allow_failure:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or "unknown Git error"
        )
        raise AdoptionError(
            "git_inspection_failed",
            "Git inspection failed: {}".format(detail),
        )
    return result


def _inspect_git(project: Path) -> Dict[str, str]:
    root_result = _run_git(
        project, ("rev-parse", "--show-toplevel"), allow_failure=True
    )
    if root_result.returncode != 0:
        raise AdoptionError(
            "not_git_repository",
            "project must be an existing Git worktree root",
        )
    root_text = root_result.stdout.strip()
    if not root_text:
        raise AdoptionError(
            "not_git_repository", "Git did not report a worktree root"
        )
    root = Path(root_text).resolve()
    if root != project.resolve():
        raise AdoptionError(
            "not_git_root",
            "project must be the Git worktree root; detected {}".format(root),
        )
    git_entry = project / ".git"
    if git_entry.is_symlink():
        raise AdoptionError(
            "unsafe_symlink", ".git must not be a symbolic link"
        )

    try:
        head_result = _run_git(
            project,
            ("rev-parse", "--verify", "HEAD^{commit}"),
            allow_failure=True,
        )
    except AdoptionError as exc:
        raise AdoptionError(
            "project_changed_after_lock",
            "project HEAD could not be safely revalidated after lock "
            "creation: {}".format(exc.message),
        ) from exc
    head = head_result.stdout.strip().lower()
    if (
        head_result.returncode != 0
        or SOURCE_COMMIT_RE.fullmatch(head) is None
    ):
        raise AdoptionError(
            "missing_head",
            "project must have a real baseline commit before adoption",
        )

    tracked_state = _run_git(
        project,
        ("ls-files", "--error-unmatch", "--", STATE_FILE.as_posix()),
        allow_failure=True,
    )
    if tracked_state.returncode != 0:
        raise AdoptionError(
            "state_not_tracked",
            "{} must be tracked before adoption".format(STATE_FILE.as_posix()),
        )

    status = _run_git(
        project, ("status", "--porcelain=v1", "--untracked-files=all")
    ).stdout
    if status:
        raise AdoptionError(
            "dirty_worktree",
            "project worktree must be clean before adoption",
        )
    return {"head": head}


def _render_lock(
    manifest: Mapping[str, Any], project_id: str
) -> bytes:
    if LOCK_TEMPLATE.is_symlink() or not LOCK_TEMPLATE.is_file():
        raise AdoptionError(
            "invalid_template",
            "version-lock template must be a regular file: {}".format(
                LOCK_TEMPLATE
            ),
        )
    try:
        text = LOCK_TEMPLATE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AdoptionError(
            "invalid_template",
            "cannot read version-lock template: {}".format(exc),
        ) from exc

    replacements = (
        (
            '"{{PROJECT_ID}}"',
            json.dumps(project_id, ensure_ascii=False),
        ),
        (
            '"{{AGENTDESK_SKILL_RELEASE}}"',
            json.dumps(manifest.get("skill_release"), ensure_ascii=False),
        ),
        (
            "{{AGENTDESK_SOURCE_COMMIT_JSON}}",
            json.dumps(manifest.get("source_commit")),
        ),
        (
            "{{AGENTDESK_BUNDLE_SHA256_JSON}}",
            json.dumps(manifest.get("bundle_sha256")),
        ),
    )
    for placeholder, value in replacements:
        if text.count(placeholder) != 1:
            raise AdoptionError(
                "invalid_template",
                "version-lock template must contain exactly one {!r}".format(
                    placeholder
                ),
            )
        text = text.replace(placeholder, value)
    if "{{" in text or "}}" in text:
        raise AdoptionError(
            "invalid_template",
            "version-lock template contains an unresolved placeholder",
        )
    try:
        lock = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_number,
        )
    except (
        json.JSONDecodeError,
        DuplicateKeyError,
        NonFiniteNumberError,
    ) as exc:
        raise AdoptionError(
            "invalid_template",
            "rendered version lock is not strict JSON: {}".format(exc),
        ) from exc
    if not isinstance(lock, dict):
        raise AdoptionError(
            "invalid_template", "rendered version lock must be an object"
        )

    compatibility = _object(
        manifest.get("compatibility"),
        "release manifest compatibility",
    )
    expected = (
        ("schema_version", lock.get("schema_version"), PROJECT_LOCK_SCHEMA),
        ("project_id", lock.get("project_id"), project_id),
        (
            "skill",
            lock.get("skill"),
            {
                "release": manifest.get("skill_release"),
                "source_commit": manifest.get("source_commit"),
                "bundle_sha256": manifest.get("bundle_sha256"),
            },
        ),
        (
            "protocol",
            lock.get("protocol"),
            {"version": manifest.get("protocol_version")},
        ),
        (
            "schemas",
            lock.get("schemas"),
            _object(
                manifest.get("schema_set"),
                "release manifest schema_set",
            ).get("schemas"),
        ),
        (
            "runtime_adapter",
            lock.get("runtime_adapter"),
            manifest.get("runtime_adapter"),
        ),
        (
            "operation_compatibility",
            lock.get("operation_compatibility"),
            {
                "reader": compatibility.get("reader"),
                "writer": compatibility.get("writer"),
            },
        ),
    )
    mismatches = [
        name for name, actual, wanted in expected if actual != wanted
    ]
    if mismatches:
        raise AdoptionError(
            "invalid_template",
            "version-lock template does not exactly match the release "
            "manifest: {}".format(", ".join(mismatches)),
        )
    return text.encode("utf-8")


def _assert_legacy_unpinned(project: Path) -> None:
    _assert_safe_relative_path(project, LOCK_FILE, "project version lock")
    lock_path = project / LOCK_FILE
    if lock_path.is_symlink():
        raise AdoptionError(
            "unsafe_symlink",
            "{} must not be a symbolic link".format(LOCK_FILE.as_posix()),
        )
    if lock_path.exists():
        raise AdoptionError(
            "already_pinned",
            "{} already exists; this command only adopts legacy-unpinned "
            "projects".format(LOCK_FILE.as_posix()),
        )


def _initializer_command(
    project: Path, state_summary: Mapping[str, Any]
) -> List[str]:
    return _isolated_python_command(
        INITIALIZER,
        [
        "--project",
        str(project),
        "--project-id",
        str(state_summary["project_id"]),
        "--mode",
        str(state_summary["adoption_level"]),
        "--pm-holder-id",
        str(state_summary["pm_holder_id"]),
        "--migration-prepare",
        ],
    )


def _renderer_command(project: Path) -> List[str]:
    return _isolated_python_command(
        RENDERER,
        [
            "--project",
            str(project),
            "--migration-prepare",
        ],
    )


def _renderer_check_command(project: Path) -> List[str]:
    return _isolated_python_command(
        RENDERER,
        [
            "--project",
            str(project),
            "--check",
        ],
    )


def _precommit_validator_command(project: Path) -> List[str]:
    return _isolated_python_command(
        VALIDATOR,
        [
            "--project",
            str(project),
            "--pre-commit",
            "--migration-prepare",
        ],
    )


def _writer_check_command(project: Path) -> List[str]:
    return _isolated_python_command(
        COMPATIBILITY_CHECKER,
        [
            "--project",
            str(project),
            "--operation",
            "write",
            "--require-clean",
            "--json",
        ],
    )


def _strict_validator_command(project: Path) -> List[str]:
    return _isolated_python_command(
        VALIDATOR,
        [
            "--project",
            str(project),
        ],
    )


def _resume_command(
    project: Path, expected_head: str, expected_state_sha256: str
) -> List[str]:
    return _isolated_python_command(
        ADOPTER,
        [
            "--project",
            str(project),
            "--resume-after-lock",
            "--expected-head",
            expected_head,
            "--expected-state-sha256",
            expected_state_sha256,
            "--json",
        ],
    )


def _verify_prepared_command(
    project: Path, expected_head: str, expected_state_sha256: str
) -> List[str]:
    return _isolated_python_command(
        ADOPTER,
        [
            "--project",
            str(project),
            "--verify-prepared",
            "--expected-head",
            expected_head,
            "--expected-state-sha256",
            expected_state_sha256,
            "--json",
        ],
    )


def _template_file_paths() -> Tuple[Path, ...]:
    if PROJECT_TEMPLATE.is_symlink() or not PROJECT_TEMPLATE.is_dir():
        raise AdoptionError(
            "invalid_template",
            "project template must be a regular directory: {}".format(
                PROJECT_TEMPLATE
            ),
        )
    result = []  # type: List[Path]
    try:
        for path in sorted(
            PROJECT_TEMPLATE.rglob("*"),
            key=lambda item: item.relative_to(PROJECT_TEMPLATE).as_posix(),
        ):
            relative = path.relative_to(PROJECT_TEMPLATE)
            if path.is_symlink():
                raise AdoptionError(
                    "invalid_template",
                    "project template must not contain symlinks: {}".format(
                        relative.as_posix()
                    ),
                )
            if path.is_file():
                result.append(relative)
            elif not path.is_dir():
                raise AdoptionError(
                    "invalid_template",
                    "project template contains an unsupported entry: {}".format(
                        relative.as_posix()
                    ),
                )
    except AdoptionError:
        raise
    except OSError as exc:
        raise AdoptionError(
            "invalid_template",
            "cannot inspect project template: {}".format(exc),
        ) from exc
    return tuple(result)


def _require_public_control_plane_trackable(project: Path) -> None:
    candidates = sorted(
        {
            path.as_posix()
            for path in _template_file_paths()
            if path.parts[:2] != (".agentdesk", "runtime")
        }
        | {Path(".gitignore").as_posix()}
        | {path.as_posix() for path in PUBLIC_CONTROL_PLANE_PROBES}
    )
    ignored = []  # type: List[str]
    for repository_path in candidates:
        result = _run_git(
            project,
            (
                "check-ignore",
                "--quiet",
                "--no-index",
                "--",
                repository_path,
            ),
            allow_failure=True,
        )
        if result.returncode == 0:
            ignored.append(repository_path)
        elif result.returncode != 1:
            detail = (
                result.stderr.strip()
                or result.stdout.strip()
                or "unknown Git error"
            )
            raise AdoptionError(
                "git_inspection_failed",
                "cannot inspect public control-plane ignore rules: {}".format(
                    detail
                ),
            )
    if ignored:
        raise AdoptionError(
            "public_control_plane_ignored",
            "effective Git ignore rules exclude versioned AgentDesk "
            "control-plane paths; update and commit the repository ignore "
            "rules before initialization or adoption: {}".format(
                ", ".join(ignored[:12])
            ),
        )


def _planned_allowed_changes(project: Path) -> Tuple[str, ...]:
    allowed = {
        LOCK_FILE.as_posix(),
        *(path.as_posix() for path in GENERATED_VIEW_FILES),
    }  # type: Set[str]
    gitignore_path = project / ".gitignore"
    if gitignore_path.is_symlink() or (
        gitignore_path.exists() and not gitignore_path.is_file()
    ):
        raise AdoptionError(
            "invalid_gitignore",
            ".gitignore must be an ordinary file when present",
        )
    try:
        gitignore = (
            gitignore_path.read_bytes() if gitignore_path.exists() else None
        )
    except OSError as exc:
        raise AdoptionError(
            "read_error", "cannot inspect .gitignore: {}".format(exc)
        ) from exc
    encoded_entry = IGNORE_ENTRY.encode("utf-8")
    if gitignore is None or not any(
        line.strip() == encoded_entry for line in gitignore.splitlines()
    ):
        allowed.add(Path(".gitignore").as_posix())
    for relative in _template_file_paths():
        path = project / relative
        if not path.exists() and not path.is_symlink():
            allowed.add(relative.as_posix())
    return tuple(sorted(allowed))


def _tree_entry_at_commit(
    project: Path, commit: str, relative_path: Path
) -> Optional[Tuple[str, str, str]]:
    repository_path = relative_path.as_posix()
    result = _run_git(
        project,
        ("ls-tree", "-z", commit, "--", repository_path),
        allow_failure=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise AdoptionError(
            "git_inspection_failed",
            "cannot inspect {} at {}: {}".format(
                repository_path, commit, detail
            ),
        )
    entries = [entry for entry in result.stdout.split("\0") if entry]
    if not entries:
        return None
    if len(entries) != 1 or "\t" not in entries[0]:
        raise AdoptionError(
            "invalid_baseline_path",
            "{} at {} is not one unambiguous Git tree entry".format(
                repository_path, commit
            ),
        )
    metadata, returned_path = entries[0].split("\t", 1)
    parts = metadata.split()
    if len(parts) != 3 or returned_path != repository_path:
        raise AdoptionError(
            "invalid_baseline_path",
            "{} at {} has an invalid Git tree entry".format(
                repository_path, commit
            ),
        )
    return parts[0], parts[1], parts[2]


def _file_at_commit(
    project: Path, commit: str, relative_path: Path
) -> Optional[bytes]:
    entry = _tree_entry_at_commit(project, commit, relative_path)
    if entry is None:
        return None
    mode, object_type, object_id = entry
    if mode not in {"100644", "100755"} or object_type != "blob":
        raise AdoptionError(
            "invalid_baseline_path",
            "{} at {} must be an ordinary Git blob".format(
                relative_path.as_posix(), commit
            ),
        )
    result = _run_git(
        project,
        ("cat-file", "blob", object_id),
        allow_failure=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise AdoptionError(
            "git_inspection_failed",
            "cannot read {} at {}: {}".format(
                relative_path.as_posix(), commit, detail
            ),
        )
    return result.stdout.encode("utf-8", errors="surrogateescape")


def _planned_changes_at_head(
    project: Path, expected_head: str
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Reconstruct the original allowlist and mandatory additions from Git."""

    allowed = {
        LOCK_FILE.as_posix(),
        *(path.as_posix() for path in GENERATED_VIEW_FILES),
    }  # type: Set[str]
    required = {LOCK_FILE.as_posix()}  # type: Set[str]
    for relative in _template_file_paths():
        if _tree_entry_at_commit(project, expected_head, relative) is None:
            allowed.add(relative.as_posix())
            if relative.parts[:2] != (".agentdesk", "runtime"):
                required.add(relative.as_posix())

    gitignore = _file_at_commit(project, expected_head, Path(".gitignore"))
    encoded_entry = IGNORE_ENTRY.encode("utf-8")
    if gitignore is None or not any(
        line.strip() == encoded_entry for line in gitignore.splitlines()
    ):
        allowed.add(Path(".gitignore").as_posix())
        required.add(Path(".gitignore").as_posix())
    return tuple(sorted(allowed)), tuple(sorted(required))


def _expected_static_template_content(
    relative: Path,
    state: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> bytes:
    source = PROJECT_TEMPLATE / relative
    if source.is_symlink() or not source.is_file():
        raise AdoptionError(
            "invalid_template",
            "expected template must be a regular file: {}".format(relative),
        )
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AdoptionError(
            "invalid_template",
            "cannot read expected template {}: {}".format(relative, exc),
        ) from exc
    replacements = {
        "{{PROJECT_ID}}": str(state["project_id"]),
        "{{MODE}}": str(state["adoption_level"]),
        "{{DATE}}": str(state["updated_at"]),
        "{{PM_HOLDER_ID}}": str(state["pm_holder_id"]),
        "{{LEASE_MODE}}": str(state["pm_mode"]),
        "{{AGENTDESK_SKILL_RELEASE}}": str(
            manifest.get("skill_release")
        ),
        "{{AGENTDESK_SOURCE_COMMIT_JSON}}": json.dumps(
            manifest.get("source_commit")
        ),
        "{{AGENTDESK_BUNDLE_SHA256_JSON}}": json.dumps(
            manifest.get("bundle_sha256")
        ),
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    if "{{" in text or "}}" in text:
        raise AdoptionError(
            "unverifiable_template_content",
            "cannot deterministically reconstruct prepared template "
            "content for {}".format(relative.as_posix()),
        )
    return text.encode("utf-8")


def _require_project_head(project: Path) -> str:
    root_result = _run_git(
        project, ("rev-parse", "--show-toplevel"), allow_failure=True
    )
    if root_result.returncode != 0:
        raise AdoptionError(
            "not_git_repository",
            "project must be an existing Git worktree root",
        )
    root_text = root_result.stdout.strip()
    if not root_text or Path(root_text).resolve() != project.resolve():
        raise AdoptionError(
            "not_git_root", "project must be the Git worktree root"
        )
    head_result = _run_git(
        project,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        allow_failure=True,
    )
    head = head_result.stdout.strip().lower()
    if (
        head_result.returncode != 0
        or SOURCE_COMMIT_RE.fullmatch(head) is None
    ):
        raise AdoptionError(
            "missing_head", "project must have a valid Git HEAD commit"
        )
    return head


def _resume_preflight(
    project: Path, expected_head: str, expected_state_sha256: str
) -> Dict[str, Any]:
    if SOURCE_COMMIT_RE.fullmatch(expected_head) is None:
        raise AdoptionError(
            "invalid_expected_head",
            "--expected-head must be a lowercase 40-hex commit",
        )
    if re.fullmatch(r"[0-9a-f]{64}", expected_state_sha256) is None:
        raise AdoptionError(
            "invalid_expected_state",
            "--expected-state-sha256 must be a lowercase 64-hex digest",
        )
    if project.is_symlink() or not project.is_dir():
        raise AdoptionError(
            "invalid_project",
            "project must be an existing non-symlink directory",
        )
    manifest = _load_manifest(RELEASE_MANIFEST)
    actual_digest = _compute_bundle_digest(
        SKILL_ROOT, RELEASE_MANIFEST, manifest
    )
    stable_problem = _stable_release_problem(
        manifest, actual_digest, _writable_bundle_paths(SKILL_ROOT)
    )
    if stable_problem is not None:
        raise AdoptionError(
            stable_problem["code"], stable_problem["message"]
        )
    head = _require_project_head(project)
    _require_public_control_plane_trackable(project)
    if head != expected_head:
        raise AdoptionError(
            "recovery_head_changed",
            "project HEAD no longer matches the failed adoption baseline",
        )
    if _tree_entry_at_commit(project, expected_head, LOCK_FILE) is not None:
        raise AdoptionError(
            "recovery_lock_already_committed",
            "the baseline already contains the project lock; use "
            "--verify-prepared instead of recovery",
        )
    state = _inspect_state(project, manifest)
    if state["state_sha256"] != expected_state_sha256:
        raise AdoptionError(
            "recovery_state_changed",
            "canonical task state no longer matches the failed adoption "
            "snapshot",
        )
    lock_content = _render_lock(manifest, state["project_id"])
    if _read_lock_safely(project) != lock_content:
        raise AdoptionError(
            "recovery_lock_changed",
            "retained project lock bytes do not match this exact release",
        )
    allowed, _required = _planned_changes_at_head(project, expected_head)
    _assert_scoped_worktree_changes(project, allowed)
    return {
        "manifest": manifest,
        "actual_bundle_sha256": actual_digest,
        "state": state,
        "git": {"head": expected_head},
        "markers": _inspect_markers(project),
        "lock_content": lock_content,
        "stable_problem": None,
        "allowed_change_paths": allowed,
    }


def _nul_paths(value: str) -> Set[str]:
    return {item for item in value.split("\0") if item}


def _assert_scoped_worktree_changes(
    project: Path, allowed_paths: Sequence[str]
) -> None:
    try:
        staged = _nul_paths(
            _run_git(
                project,
                ("diff", "--cached", "--name-only", "-z", "--no-renames"),
            ).stdout
        )
        changed = _nul_paths(
            _run_git(
                project,
                ("diff", "--name-only", "-z", "--no-renames"),
            ).stdout
        )
        changed.update(
            _nul_paths(
                _run_git(
                    project,
                    ("ls-files", "--others", "--exclude-standard", "-z"),
                ).stdout
            )
        )
    except AdoptionError as exc:
        raise AdoptionError(
            "project_changed_after_lock",
            "cannot verify the scoped Git diff after lock creation: "
            "{}".format(exc.message),
        ) from exc
    if staged:
        raise AdoptionError(
            "project_changed_after_lock",
            "the adoption process never stages files, but the Git index "
            "changed during adoption: {}".format(
                ", ".join(sorted(staged)[:12])
            ),
        )

    unexpected = changed.difference(allowed_paths)
    if unexpected:
        raise AdoptionError(
            "project_changed_after_lock",
            "a path outside the reviewed legacy-adoption change set changed: "
            "{}".format(", ".join(sorted(unexpected)[:12])),
        )


def _preflight(project: Path) -> Dict[str, Any]:
    if project.is_symlink():
        raise AdoptionError(
            "unsafe_symlink", "project root must not be a symbolic link"
        )
    if not project.is_dir():
        raise AdoptionError(
            "invalid_project",
            "project must be an existing directory: {}".format(project),
        )
    _assert_legacy_unpinned(project)
    markers = _inspect_markers(project)
    manifest = _load_manifest(RELEASE_MANIFEST)
    actual_digest = _compute_bundle_digest(
        SKILL_ROOT, RELEASE_MANIFEST, manifest
    )
    writable_paths = _writable_bundle_paths(SKILL_ROOT)
    state_summary = _inspect_state(project, manifest)
    git_summary = _inspect_git(project)
    _require_public_control_plane_trackable(project)
    lock_content = _render_lock(manifest, state_summary["project_id"])
    stable_problem = _stable_release_problem(
        manifest, actual_digest, writable_paths
    )
    allowed_change_paths = _planned_allowed_changes(project)
    return {
        "manifest": manifest,
        "actual_bundle_sha256": actual_digest,
        "state": state_summary,
        "git": git_summary,
        "markers": markers,
        "lock_content": lock_content,
        "stable_problem": stable_problem,
        "allowed_change_paths": allowed_change_paths,
    }


def _result_base(
    project: Path, action: str, preflight: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    result = {
        "schema_version": RESULT_SCHEMA,
        "action": action,
        "project": str(project),
        "status": "unknown",
        "complete": False,
        "lock_created": False,
        "warnings": [],
        "errors": [],
    }  # type: Dict[str, Any]
    if preflight is not None:
        manifest = preflight["manifest"]
        state = preflight["state"]
        lock_content = preflight["lock_content"]
        result.update(
            {
                "project_id": state["project_id"],
                "adoption_level": state["adoption_level"],
                "project_head": preflight["git"]["head"],
                "state_sha256": state["state_sha256"],
                "markers": list(preflight["markers"]),
                "release": {
                    "skill_release": manifest.get("skill_release"),
                    "release_status": manifest.get("release_status"),
                    "source_commit": manifest.get("source_commit"),
                    "bundle_sha256": manifest.get("bundle_sha256"),
                    "actual_bundle_sha256": preflight[
                        "actual_bundle_sha256"
                    ],
                    "apply_eligible": preflight["stable_problem"] is None,
                },
                "lock": {
                    "path": str(project / LOCK_FILE),
                    "sha256": hashlib.sha256(lock_content).hexdigest(),
                },
                "allowed_change_paths": list(
                    preflight["allowed_change_paths"]
                ),
                "plan": [
                    {
                        "operation": "create",
                        "path": LOCK_FILE.as_posix(),
                        "exclusive": True,
                    },
                    {
                        "operation": "initialize_missing",
                        "command": _initializer_command(project, state),
                        "overwrite_existing": False,
                    },
                    {
                        "operation": "render_views",
                        "command": _renderer_command(project),
                    },
                    {
                        "operation": "check_views",
                        "command": _renderer_check_command(project),
                    },
                    {
                        "operation": "validate_precommit",
                        "command": _precommit_validator_command(project),
                    },
                ],
            }
        )
    return result


def _revalidate_before_write(
    project: Path, preflight: Mapping[str, Any]
) -> None:
    _assert_legacy_unpinned(project)
    git_summary = _inspect_git(project)
    _require_public_control_plane_trackable(project)
    if git_summary["head"] != preflight["git"]["head"]:
        raise AdoptionError(
            "project_changed",
            "project HEAD changed after preflight; rerun the command",
        )
    state_summary = _inspect_state(project, preflight["manifest"])
    for field in (
        "project_id",
        "adoption_level",
        "pm_holder_id",
        "state_sha256",
    ):
        if state_summary[field] != preflight["state"][field]:
            raise AdoptionError(
                "project_changed",
                "canonical task state changed after preflight; rerun the "
                "command",
            )


def _revalidate_after_lock(
    project: Path, preflight: Mapping[str, Any]
) -> None:
    try:
        lock_content = _read_lock_safely(project)
    except AdoptionError as exc:
        raise AdoptionError(
            "project_changed_after_lock",
            "cannot safely re-read the retained project version lock: "
            "{}".format(exc.message),
        ) from exc
    if lock_content != preflight["lock_content"]:
        raise AdoptionError(
            "project_changed_after_lock",
            "the retained project version lock changed during adoption",
        )

    try:
        head_result = _run_git(
            project,
            ("rev-parse", "--verify", "HEAD^{commit}"),
            allow_failure=True,
        )
    except AdoptionError as exc:
        raise AdoptionError(
            "project_changed_after_lock",
            "project HEAD could not be safely revalidated after lock "
            "creation: {}".format(exc.message),
        ) from exc
    head = head_result.stdout.strip().lower()
    if (
        head_result.returncode != 0
        or SOURCE_COMMIT_RE.fullmatch(head) is None
        or head != preflight["git"]["head"]
    ):
        raise AdoptionError(
            "project_changed_after_lock",
            "project HEAD changed during adoption; keep the lock and stop",
        )
    _require_public_control_plane_trackable(project)

    try:
        state_summary = _inspect_state(project, preflight["manifest"])
    except AdoptionError as exc:
        raise AdoptionError(
            "project_changed_after_lock",
            "canonical task state became invalid or active during adoption: "
            "{}".format(exc.message),
        ) from exc
    for field in (
        "project_id",
        "adoption_level",
        "pm_holder_id",
        "state_sha256",
    ):
        if state_summary[field] != preflight["state"][field]:
            raise AdoptionError(
                "project_changed_after_lock",
                "canonical task state changed during adoption; keep the "
                "lock and stop",
            )

    _assert_scoped_worktree_changes(
        project, preflight["allowed_change_paths"]
    )


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_project_pm_directories(project: Path) -> Tuple[int, int, int]:
    flags = _directory_open_flags()
    opened = []  # type: List[int]
    try:
        root_fd = os.open(str(project), flags)
        opened.append(root_fd)
        docs_fd = os.open("docs", flags, dir_fd=root_fd)
        opened.append(docs_fd)
        pm_fd = os.open("pm", flags, dir_fd=docs_fd)
        opened.append(pm_fd)
        return root_fd, docs_fd, pm_fd
    except OSError as exc:
        for descriptor in reversed(opened):
            os.close(descriptor)
        raise AdoptionError(
            "unsafe_lock_parent",
            "cannot safely open project/docs/pm without following symlinks: "
            "{}".format(exc),
        ) from exc


def _close_descriptors(descriptors: Sequence[int]) -> None:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _same_inode(descriptor: int, path: Path) -> bool:
    try:
        opened = os.fstat(descriptor)
        current = os.stat(str(path), follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISDIR(opened.st_mode)
        and stat.S_ISDIR(current.st_mode)
        and opened.st_dev == current.st_dev
        and opened.st_ino == current.st_ino
    )


def _verify_open_project_path(
    project: Path, descriptors: Sequence[int]
) -> None:
    root_fd, docs_fd, pm_fd = descriptors
    checks = (
        (root_fd, project),
        (docs_fd, project / "docs"),
        (pm_fd, project / "docs" / "pm"),
    )
    if not all(_same_inode(descriptor, path) for descriptor, path in checks):
        raise AdoptionError(
            "project_path_changed",
            "project/docs/pm changed while the adoption lock path was open",
        )


def _read_all(descriptor: int) -> bytes:
    chunks = []  # type: List[bytes]
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_lock_safely(project: Path) -> bytes:
    descriptors = _open_project_pm_directories(project)
    lock_fd = None  # type: Optional[int]
    try:
        _verify_open_project_path(project, descriptors)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        lock_fd = os.open(
            LOCK_FILE.name,
            flags,
            dir_fd=descriptors[-1],
        )
        metadata = os.fstat(lock_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise AdoptionError(
                "unsafe_lock",
                "project version lock must be a regular file",
            )
        content = _read_all(lock_fd)
        _verify_open_project_path(project, descriptors)
        return content
    except OSError as exc:
        raise AdoptionError(
            "unsafe_lock",
            "cannot safely read project version lock: {}".format(exc),
        ) from exc
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        _close_descriptors(descriptors)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short write while creating version lock")
        view = view[written:]


def _unlink_owned_entry(
    directory_fd: int, name: str, expected: os.stat_result
) -> None:
    try:
        actual = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        actual.st_dev == expected.st_dev
        and actual.st_ino == expected.st_ino
        and stat.S_ISREG(actual.st_mode)
    ):
        os.unlink(name, dir_fd=directory_fd)


def _create_lock(project: Path, content: bytes) -> None:
    descriptors = _open_project_pm_directories(project)
    pm_fd = descriptors[-1]
    temporary_name = ".AGENTDESK.lock.{}.tmp".format(
        secrets.token_hex(12)
    )
    temporary_fd = None  # type: Optional[int]
    temporary_stat = None  # type: Optional[os.stat_result]
    published = False
    try:
        _verify_open_project_path(project, descriptors)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        temporary_fd = os.open(
            temporary_name,
            flags,
            0o644,
            dir_fd=pm_fd,
        )
        temporary_stat = os.fstat(temporary_fd)
        if not stat.S_ISREG(temporary_stat.st_mode):
            raise AdoptionError(
                "lock_create_failed",
                "temporary version lock is not a regular file",
            )
        _write_all(temporary_fd, content)
        os.fsync(temporary_fd)
        _verify_open_project_path(project, descriptors)
        try:
            os.link(
                temporary_name,
                LOCK_FILE.name,
                src_dir_fd=pm_fd,
                dst_dir_fd=pm_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise AdoptionError(
                "lock_race",
                "version lock appeared during adoption; no file was "
                "overwritten",
            ) from exc
        published = True
        _verify_open_project_path(project, descriptors)
        os.fsync(pm_fd)
    except AdoptionError:
        raise
    except OSError as exc:
        raise AdoptionError(
            "lock_create_failed",
            "cannot atomically publish version lock: {}".format(exc),
        ) from exc
    finally:
        if temporary_fd is not None:
            try:
                os.close(temporary_fd)
            except OSError:
                pass
        if temporary_stat is not None:
            try:
                _unlink_owned_entry(pm_fd, temporary_name, temporary_stat)
                os.fsync(pm_fd)
            except OSError:
                pass
        if published and not _same_inode(
            pm_fd, project / "docs" / "pm"
        ):
            try:
                if temporary_stat is not None:
                    _unlink_owned_entry(
                        pm_fd, LOCK_FILE.name, temporary_stat
                    )
                    os.fsync(pm_fd)
            except OSError:
                pass
        _close_descriptors(descriptors)


def _record_incomplete(
    result: Dict[str, Any],
    *,
    code: str,
    message: str,
    commands: Sequence[Sequence[str]],
    instruction: str,
    initializer_completed: bool = False,
) -> None:
    # Never expose the underlying tools as an authoritative recovery chain:
    # they do not carry the original HEAD/state/diff fence.  The single
    # adopter resume command reconstructs and revalidates that fence first.
    del commands, instruction
    recovery_commands = [
        _resume_command(
            Path(str(result["project"])),
            str(result["project_head"]),
            str(result["state_sha256"]),
        )
    ]
    result["status"] = "incomplete"
    result["complete"] = False
    result["errors"].append({"code": code, "message": message})
    recovery = {
        "lock_retained": True,
        "initializer_completed": initializer_completed,
        "commands": recovery_commands,
        "instruction": (
            "Resolve the reported failure, then run only the recorded "
            "same-bundle fenced resume command. It will reject any changed "
            "baseline HEAD, canonical state, lock bytes, index, or path "
            "outside the original allowlist."
        ),
    }  # type: Dict[str, Any]
    if recovery_commands:
        recovery["command"] = recovery_commands[0]
    if len(recovery_commands) > 1:
        recovery["next_command"] = recovery_commands[1]
    result["recovery"] = recovery


def adopt(
    project: Path,
    action: str,
    acknowledged: bool,
    expected_head: Optional[str] = None,
    expected_state_sha256: Optional[str] = None,
) -> Tuple[int, Dict[str, Any]]:
    if action == "resume":
        if expected_head is None or expected_state_sha256 is None:
            raise AdoptionError(
                "missing_recovery_fence",
                "resume requires --expected-head and "
                "--expected-state-sha256",
            )
        preflight = _resume_preflight(
            project, expected_head, expected_state_sha256
        )
    else:
        preflight = _preflight(project)
    result = _result_base(project, action, preflight)
    stable_problem = preflight["stable_problem"]

    if action == "dry-run":
        result["status"] = "plan"
        if stable_problem is not None:
            result["warnings"].append(stable_problem)
        return 0, result

    if action == "apply" and not acknowledged:
        raise AdoptionError(
            "acknowledgement_required",
            "--apply requires --acknowledge-legacy-unpinned",
        )
    if stable_problem is not None:
        raise AdoptionError(
            stable_problem["code"], stable_problem["message"]
        )
    if INITIALIZER.is_symlink() or not INITIALIZER.is_file():
        raise AdoptionError(
            "missing_initializer",
            "same-bundle initializer must be a regular file: {}".format(
                INITIALIZER
            ),
        )
    if RENDERER.is_symlink() or not RENDERER.is_file():
        raise AdoptionError(
            "missing_renderer",
            "same-bundle renderer must be a regular file: {}".format(
                RENDERER
            ),
        )
    if VALIDATOR.is_symlink() or not VALIDATOR.is_file():
        raise AdoptionError(
            "missing_validator",
            "same-bundle validator must be a regular file: {}".format(
                VALIDATOR
            ),
        )
    if (
        COMPATIBILITY_CHECKER.is_symlink()
        or not COMPATIBILITY_CHECKER.is_file()
    ):
        raise AdoptionError(
            "missing_compatibility_checker",
            "same-bundle compatibility checker must be a regular file: "
            "{}".format(COMPATIBILITY_CHECKER),
        )

    if action == "apply":
        _revalidate_before_write(project, preflight)
    initializer_command = _initializer_command(
        project, preflight["state"]
    )
    render_command = _renderer_command(project)
    view_check_command = _renderer_check_command(project)
    validator_command = _precommit_validator_command(project)
    full_recovery = (
        initializer_command,
        render_command,
        view_check_command,
        validator_command,
    )
    if action == "apply":
        try:
            _create_lock(project, preflight["lock_content"])
        except AdoptionError as exc:
            retained = False
            try:
                retained = (
                    _read_lock_safely(project) == preflight["lock_content"]
                )
            except AdoptionError:
                retained = False
            if not retained:
                raise
            result["lock_created"] = True
            _record_incomplete(
                result,
                code=exc.code,
                message=(
                    "the complete version lock was retained, but its atomic "
                    "publication could not be durably confirmed: {}".format(
                        exc.message
                    )
                ),
                commands=full_recovery,
                instruction=(
                    "Keep the exact retained lock and use fenced recovery."
                ),
            )
            return 1, result
        result["lock_created"] = True
    else:
        result["lock_created"] = True
        result["resumed"] = True

    try:
        _revalidate_after_lock(project, preflight)
    except AdoptionError as exc:
        _record_incomplete(
            result,
            code=exc.code,
            message=exc.message,
            commands=full_recovery,
            instruction=(
                "Freeze the project and inspect the retained lock and "
                "scoped diff. After resolving the concurrent change, run "
                "the recorded isolated same-bundle migration preparation "
                "commands in order."
            ),
        )
        return 1, result

    try:
        initialized = subprocess.run(
            initializer_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            env=_sanitized_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_incomplete(
            result,
            code="initializer_failed_after_lock",
            message=(
                "version lock was retained, but the initializer could not "
                "complete: {}".format(exc)
            ),
            commands=full_recovery,
            instruction=(
                "Resolve the initializer failure, then rerun this exact "
                "same-bundle initializer, renderer, view check, and "
                "pre-commit validator sequence. Do not recreate or edit the "
                "lock."
            ),
        )
        return 1, result

    result["initializer"] = {
        "returncode": initialized.returncode,
        "stdout": initialized.stdout.splitlines(),
        "stderr": initialized.stderr.splitlines(),
    }
    if initialized.returncode != 0:
        detail = (
            initialized.stderr.strip()
            or initialized.stdout.strip()
            or "initializer exited non-zero"
        )
        _record_incomplete(
            result,
            code="initializer_failed_after_lock",
            message=(
                "version lock was retained, but the initializer did not "
                "complete: {}".format(detail)
            ),
            commands=full_recovery,
            instruction=(
                "Resolve the reported path or template conflict, then rerun "
                "the recorded same-bundle initializer, renderer, view check, "
                "and pre-commit validator sequence. Do not recreate or edit "
                "the lock."
            ),
        )
        return 1, result

    try:
        _revalidate_after_lock(project, preflight)
    except AdoptionError as exc:
        _record_incomplete(
            result,
            code=exc.code,
            message=exc.message,
            commands=(render_command, view_check_command, validator_command),
            instruction=(
                "Freeze the project and inspect the retained lock and state. "
                "After resolving the concurrent change, rerun the recorded "
                "same-bundle renderer, view check, and pre-commit validator "
                "sequence."
            ),
            initializer_completed=True,
        )
        return 1, result

    try:
        rendered = subprocess.run(
            render_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            env=_sanitized_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_incomplete(
            result,
            code="renderer_failed_after_lock",
            message=(
                "version lock was retained and initialization completed, "
                "but derived views could not be regenerated: {}".format(exc)
            ),
            commands=(render_command, view_check_command, validator_command),
            instruction=(
                "Resolve the renderer failure, then rerun this exact "
                "same-bundle renderer, view check, and pre-commit validator "
                "sequence. Do not recreate or edit the lock."
            ),
            initializer_completed=True,
        )
        return 1, result

    result["renderer"] = {
        "returncode": rendered.returncode,
        "stdout": rendered.stdout.splitlines(),
        "stderr": rendered.stderr.splitlines(),
    }
    if rendered.returncode != 0:
        detail = (
            rendered.stderr.strip()
            or rendered.stdout.strip()
            or "renderer exited non-zero"
        )
        _record_incomplete(
            result,
            code="renderer_failed_after_lock",
            message=(
                "version lock was retained and initialization completed, "
                "but derived views were not regenerated: {}".format(detail)
            ),
            commands=(render_command, view_check_command, validator_command),
            instruction=(
                "Resolve the renderer failure, then rerun this exact "
                "same-bundle renderer, view check, and pre-commit validator "
                "sequence. Do not recreate or edit the lock."
            ),
            initializer_completed=True,
        )
        return 1, result

    try:
        _revalidate_after_lock(project, preflight)
    except AdoptionError as exc:
        _record_incomplete(
            result,
            code=exc.code,
            message=exc.message,
            commands=(render_command, view_check_command, validator_command),
            instruction=(
                "Freeze the project and inspect the retained lock and state. "
                "After resolving the concurrent change, rerun the recorded "
                "same-bundle renderer, view check, and pre-commit validator "
                "sequence."
            ),
            initializer_completed=True,
        )
        return 1, result

    try:
        checked = subprocess.run(
            view_check_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            env=_sanitized_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_incomplete(
            result,
            code="renderer_check_failed_after_lock",
            message=(
                "version lock and regenerated views were retained, but the "
                "view check could not complete: {}".format(exc)
            ),
            commands=(render_command, view_check_command, validator_command),
            instruction=(
                "Resolve the view-check failure, then rerun the recorded "
                "same-bundle renderer, view check, and pre-commit validator "
                "sequence. Do not recreate or edit the lock."
            ),
            initializer_completed=True,
        )
        return 1, result

    result["view_check"] = {
        "returncode": checked.returncode,
        "stdout": checked.stdout.splitlines(),
        "stderr": checked.stderr.splitlines(),
    }
    if checked.returncode != 0:
        detail = (
            checked.stderr.strip()
            or checked.stdout.strip()
            or "renderer check exited non-zero"
        )
        _record_incomplete(
            result,
            code="renderer_check_failed_after_lock",
            message=(
                "version lock and regenerated views were retained, but the "
                "view check failed: {}".format(detail)
            ),
            commands=(render_command, view_check_command, validator_command),
            instruction=(
                "Resolve the view drift, then rerun the recorded same-bundle "
                "renderer, view check, and pre-commit validator sequence. Do "
                "not recreate or edit the lock."
            ),
            initializer_completed=True,
        )
        return 1, result

    try:
        _revalidate_after_lock(project, preflight)
    except AdoptionError as exc:
        _record_incomplete(
            result,
            code=exc.code,
            message=exc.message,
            commands=(render_command, view_check_command, validator_command),
            instruction=(
                "Freeze the project and inspect the retained lock and state. "
                "After resolving the concurrent change, rerun the recorded "
                "same-bundle renderer, view check, and pre-commit validator "
                "sequence."
            ),
            initializer_completed=True,
        )
        return 1, result

    try:
        validated = subprocess.run(
            validator_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            env=_sanitized_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_incomplete(
            result,
            code="precommit_validation_failed_after_lock",
            message=(
                "version lock and regenerated views were retained, but "
                "pre-commit validation could not complete: {}".format(exc)
            ),
            commands=(validator_command,),
            instruction=(
                "Resolve the validation failure, then rerun the recorded "
                "same-bundle pre-commit validator. Do not recreate or edit "
                "the lock."
            ),
            initializer_completed=True,
        )
        return 1, result

    result["precommit_validation"] = {
        "returncode": validated.returncode,
        "stdout": validated.stdout.splitlines(),
        "stderr": validated.stderr.splitlines(),
    }
    if validated.returncode != 0:
        detail = (
            validated.stderr.strip()
            or validated.stdout.strip()
            or "pre-commit validator exited non-zero"
        )
        _record_incomplete(
            result,
            code="precommit_validation_failed_after_lock",
            message=(
                "version lock and regenerated views were retained, but "
                "pre-commit validation failed: {}".format(detail)
            ),
            commands=(validator_command,),
            instruction=(
                "Resolve the validation failure, then rerun the recorded "
                "same-bundle pre-commit validator. Do not recreate or edit "
                "the lock."
            ),
            initializer_completed=True,
        )
        return 1, result

    try:
        _revalidate_after_lock(project, preflight)
    except AdoptionError as exc:
        _record_incomplete(
            result,
            code=exc.code,
            message=exc.message,
            commands=(render_command, view_check_command, validator_command),
            instruction=(
                "Freeze the project and inspect the retained lock and state. "
                "After resolving the concurrent change, rerun the recorded "
                "same-bundle renderer, view check, and pre-commit validator "
                "sequence."
            ),
            initializer_completed=True,
        )
        return 1, result

    result["status"] = "prepared"
    result["prepared"] = True
    result["complete"] = False
    result["commit_required"] = True
    result["strict_validation_pending"] = True
    result["post_commit_verification"] = {
        "commands": [
            _verify_prepared_command(
                project,
                str(result["project_head"]),
                str(result["state_sha256"]),
            ),
        ],
        "instruction": (
            "Review and create exactly one scoped adoption commit, then run "
            "the recorded same-bundle verifier. It binds the commit parent, "
            "changed paths, clean worktree, exact lock, views, and strict "
            "validation; only a durable result unfreezes business writers."
        ),
    }
    return 0, result


def _verify_prepared_fence(
    project: Path, expected_head: str, expected_state_sha256: str
) -> Dict[str, Any]:
    if SOURCE_COMMIT_RE.fullmatch(expected_head) is None:
        raise AdoptionError(
            "invalid_expected_head",
            "--expected-head must be a lowercase 40-hex commit",
        )
    if re.fullmatch(r"[0-9a-f]{64}", expected_state_sha256) is None:
        raise AdoptionError(
            "invalid_expected_state",
            "--expected-state-sha256 must be a lowercase 64-hex digest",
        )
    if project.is_symlink() or not project.is_dir():
        raise AdoptionError(
            "invalid_project",
            "project must be an existing non-symlink directory",
        )

    manifest = _load_manifest(RELEASE_MANIFEST)
    actual_digest = _compute_bundle_digest(
        SKILL_ROOT, RELEASE_MANIFEST, manifest
    )
    stable_problem = _stable_release_problem(
        manifest, actual_digest, _writable_bundle_paths(SKILL_ROOT)
    )
    if stable_problem is not None:
        raise AdoptionError(
            stable_problem["code"], stable_problem["message"]
        )
    state = _inspect_state(project, manifest)
    if state["state_sha256"] != expected_state_sha256:
        raise AdoptionError(
            "prepared_state_changed",
            "canonical task state differs from the prepared adoption "
            "snapshot",
        )
    lock_content = _render_lock(manifest, state["project_id"])
    if _read_lock_safely(project) != lock_content:
        raise AdoptionError(
            "prepared_lock_changed",
            "project lock bytes differ from the prepared exact release",
        )

    head = _require_project_head(project)
    _require_public_control_plane_trackable(project)
    parents = _run_git(
        project, ("rev-list", "--parents", "-n", "1", head)
    ).stdout.strip().split()
    if parents != [head, expected_head]:
        raise AdoptionError(
            "prepared_commit_shape_invalid",
            "durable adoption requires exactly one non-merge commit whose "
            "only parent is the recorded pre-adoption HEAD",
        )

    allowed, required = _planned_changes_at_head(project, expected_head)
    new_initialization = (
        _tree_entry_at_commit(project, expected_head, STATE_FILE) is None
    )
    diff = _run_git(
        project,
        (
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-z",
            "--no-renames",
            expected_head,
            head,
        ),
    ).stdout
    changed = _nul_paths(diff)
    if not changed:
        raise AdoptionError(
            "prepared_commit_empty",
            "the scoped adoption commit contains no changed paths",
        )
    unexpected = changed.difference(allowed)
    if unexpected:
        raise AdoptionError(
            "prepared_commit_out_of_scope",
            "the adoption commit contains path(s) outside the original "
            "allowlist: {}".format(", ".join(sorted(unexpected)[:12])),
        )
    missing = set(required).difference(changed)
    if missing:
        raise AdoptionError(
            "prepared_commit_incomplete",
            "the adoption commit omitted required prepared path(s): {}".format(
                ", ".join(sorted(missing)[:12])
            ),
        )

    for repository_path in sorted(changed):
        relative = Path(repository_path)
        baseline_entry = _tree_entry_at_commit(
            project, expected_head, relative
        )
        committed_entry = _tree_entry_at_commit(project, head, relative)
        if committed_entry is None:
            raise AdoptionError(
                "prepared_commit_deleted_path",
                "the adoption commit deleted allowed path {}".format(
                    repository_path
                ),
            )
        committed_mode, committed_type, _committed_id = committed_entry
        if (
            committed_mode not in {"100644", "100755"}
            or committed_type != "blob"
        ):
            raise AdoptionError(
                "prepared_commit_path_not_blob",
                "the adoption commit must store {} as an ordinary blob".format(
                    repository_path
                ),
            )
        if baseline_entry is None:
            if committed_mode != "100644":
                raise AdoptionError(
                    "prepared_commit_mode_mismatch",
                    "new prepared path {} must use mode 100644".format(
                        repository_path
                    ),
                )
        elif committed_mode != baseline_entry[0]:
            raise AdoptionError(
                "prepared_commit_mode_mismatch",
                "prepared path {} changed its Git mode".format(
                    repository_path
                ),
            )

    baseline_gitignore = _file_at_commit(
        project, expected_head, Path(".gitignore")
    )
    encoded_entry = IGNORE_ENTRY.encode("utf-8")
    needs_gitignore = baseline_gitignore is None or not any(
        line.strip() == encoded_entry
        for line in baseline_gitignore.splitlines()
    )
    if needs_gitignore:
        separator = (
            b""
            if not baseline_gitignore
            or baseline_gitignore.endswith((b"\n", b"\r"))
            else b"\n"
        )
        expected_gitignore = (
            (baseline_gitignore or b"")
            + separator
            + encoded_entry
            + b"\n"
        )
        committed_gitignore = _file_at_commit(
            project, head, Path(".gitignore")
        )
        if new_initialization and (
            committed_gitignore is None
            or not any(
                line.strip() == encoded_entry
                for line in committed_gitignore.splitlines()
            )
        ):
            raise AdoptionError(
                "prepared_content_mismatch",
                ".gitignore must retain the AgentDesk runtime ignore entry",
            )
        if (
            not new_initialization
            and committed_gitignore != expected_gitignore
        ):
            raise AdoptionError(
                "prepared_content_mismatch",
                ".gitignore must contain exactly the baseline bytes plus "
                "the required AgentDesk runtime ignore entry",
            )

    if not new_initialization:
        generated_paths = set(GENERATED_VIEW_FILES)
        for relative in _template_file_paths():
            if (
                relative == LOCK_FILE
                or relative in generated_paths
                or relative.parts[:2] == (".agentdesk", "runtime")
                or _tree_entry_at_commit(
                    project, expected_head, relative
                )
                is not None
            ):
                continue
            committed_content = _file_at_commit(project, head, relative)
            expected_content = _expected_static_template_content(
                relative, state, manifest
            )
            if committed_content != expected_content:
                raise AdoptionError(
                    "prepared_content_mismatch",
                    "prepared template path {} does not match the exact "
                    "same-bundle rendered bytes".format(
                        relative.as_posix()
                    ),
                )

    status = _run_git(
        project, ("status", "--porcelain=v1", "--untracked-files=all")
    ).stdout
    if status:
        raise AdoptionError(
            "prepared_worktree_not_clean",
            "durable adoption requires a clean tracked and untracked "
            "worktree",
        )
    return {
        "manifest": manifest,
        "actual_bundle_sha256": actual_digest,
        "state": state,
        "head": head,
        "base_head": expected_head,
        "allowed_change_paths": list(allowed),
        "required_change_paths": list(required),
        "committed_change_paths": sorted(changed),
        "preparation_kind": (
            "new-initialization"
            if new_initialization
            else "legacy-adoption"
        ),
    }


def verify_prepared(
    project: Path, expected_head: str, expected_state_sha256: str
) -> Tuple[int, Dict[str, Any]]:
    fence = _verify_prepared_fence(
        project, expected_head, expected_state_sha256
    )
    manifest = fence["manifest"]
    state = fence["state"]
    result = _result_base(project, "verify-prepared")
    result.update(
        {
            "project_id": state["project_id"],
            "adoption_level": state["adoption_level"],
            "project_head": expected_head,
            "committed_head": fence["head"],
            "state_sha256": expected_state_sha256,
            "release": {
                "skill_release": manifest.get("skill_release"),
                "release_status": manifest.get("release_status"),
                "source_commit": manifest.get("source_commit"),
                "bundle_sha256": manifest.get("bundle_sha256"),
                "actual_bundle_sha256": fence["actual_bundle_sha256"],
                "apply_eligible": True,
            },
            "lock": {
                "path": str(project / LOCK_FILE),
                "sha256": hashlib.sha256(
                    _render_lock(manifest, state["project_id"])
                ).hexdigest(),
            },
            "allowed_change_paths": fence["allowed_change_paths"],
            "required_change_paths": fence["required_change_paths"],
            "committed_change_paths": fence["committed_change_paths"],
            "preparation_kind": fence["preparation_kind"],
        }
    )
    commands = (
        ("writer", _writer_check_command(project)),
        ("view_check", _renderer_check_command(project)),
        ("strict_validation", _strict_validator_command(project)),
    )
    for label, command in commands:
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                env=_sanitized_git_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["status"] = "verification_failed"
            result["errors"].append(
                {
                    "code": "{}_failed".format(label),
                    "message": "cannot run {} gate: {}".format(label, exc),
                }
            )
            return 1, result
        result[label] = {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.splitlines(),
            "stderr": completed.stderr.splitlines(),
        }
        if completed.returncode != 0:
            detail = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "{} exited non-zero".format(label)
            )
            result["status"] = "verification_failed"
            result["errors"].append(
                {
                    "code": "{}_failed".format(label),
                    "message": detail,
                }
            )
            return 1, result
        _verify_prepared_fence(
            project, expected_head, expected_state_sha256
        )

    result["status"] = "durable"
    result["complete"] = True
    result["durable"] = True
    result["commit_required"] = False
    return 0, result


def _print_human(result: Mapping[str, Any], stream: Any = sys.stdout) -> None:
    print("STATUS {}".format(result["status"]), file=stream)
    print("ACTION {}".format(result["action"]), file=stream)
    print("PROJECT {}".format(result["project"]), file=stream)
    if "project_id" in result:
        print("PROJECT_ID {}".format(result["project_id"]), file=stream)
        print(
            "ADOPTION_LEVEL {}".format(result["adoption_level"]), file=stream
        )
        print(
            "RELEASE {} ({})".format(
                result["release"]["skill_release"],
                result["release"]["release_status"],
            ),
            file=stream,
        )
        print(
            "APPLY_ELIGIBLE {}".format(
                str(result["release"]["apply_eligible"]).lower()
            ),
            file=stream,
        )
        print("LOCK {}".format(result["lock"]["path"]), file=stream)
        for item in result.get("plan", []):
            if item["operation"] == "create":
                print(
                    "PLAN CREATE {} (exclusive)".format(item["path"]),
                    file=stream,
                )
            elif item["operation"] == "initialize_missing":
                print(
                    "PLAN INITIALIZE_MISSING (no overwrite)",
                    file=stream,
                )
            elif item["operation"] == "render_views":
                print("PLAN RENDER_VIEWS", file=stream)
            elif item["operation"] == "check_views":
                print("PLAN CHECK_VIEWS", file=stream)
            elif item["operation"] == "validate_precommit":
                print("PLAN VALIDATE_PRECOMMIT", file=stream)
    if result.get("prepared"):
        print("PREPARED true", file=stream)
        print(
            "COMMIT_REQUIRED {}".format(
                str(result.get("commit_required", False)).lower()
            ),
            file=stream,
        )
        print(
            "STRICT_VALIDATION_PENDING {}".format(
                str(
                    result.get("strict_validation_pending", False)
                ).lower()
            ),
            file=stream,
        )
        print(
            "POST_COMMIT_VERIFICATION {} command(s)".format(
                len(
                    result.get("post_commit_verification", {}).get(
                        "commands", []
                    )
                )
            ),
            file=stream,
        )
        for command in result.get("post_commit_verification", {}).get(
            "commands", []
        ):
            print(
                "POST_COMMIT_COMMAND {}".format(shlex.join(command)),
                file=stream,
            )
    if result.get("durable"):
        print("DURABLE true", file=stream)
    for warning in result.get("warnings", []):
        print(
            "WARNING {} {}".format(warning["code"], warning["message"]),
            file=stream,
        )
    for error in result.get("errors", []):
        print(
            "ERROR {} {}".format(error["code"], error["message"]),
            file=stream,
        )
    if result.get("recovery"):
        print(
            "RECOVERY {}".format(result["recovery"]["instruction"]),
            file=stream,
        )
        for command in result["recovery"].get("commands", []):
            print(
                "RECOVERY_COMMAND {}".format(shlex.join(command)),
                file=stream,
            )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Explicitly pin a clean legacy-unpinned AgentDesk project to "
            "this exact Skill release."
        )
    )
    parser.add_argument(
        "--project",
        required=True,
        help="existing AgentDesk Git worktree root to inspect or adopt",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--dry-run",
        action="store_true",
        help="print the deterministic adoption plan without writing",
    )
    action.add_argument(
        "--apply",
        action="store_true",
        help=(
            "prepare one scoped adoption snapshot; ordinary writers remain "
            "blocked until the exact lock is committed"
        ),
    )
    action.add_argument(
        "--resume-after-lock",
        action="store_true",
        help=(
            "resume a failed prepared adoption while revalidating its "
            "recorded baseline fence"
        ),
    )
    action.add_argument(
        "--verify-prepared",
        action="store_true",
        help=(
            "verify one committed prepared adoption and return durable only "
            "when its commit scope and all gates pass"
        ),
    )
    parser.add_argument(
        "--expected-head",
        help="recorded pre-adoption 40-hex HEAD for resume or verification",
    )
    parser.add_argument(
        "--expected-state-sha256",
        help=(
            "recorded canonical task-state SHA-256 for resume or "
            "verification"
        ),
    )
    parser.add_argument(
        "--acknowledge-legacy-unpinned",
        action="store_true",
        help=(
            "explicitly acknowledge that historical Skill identity cannot be "
            "proven; required with --apply"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit one stable JSON result object",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.dry_run and args.acknowledge_legacy_unpinned:
        parser.error(
            "--acknowledge-legacy-unpinned is valid only with --apply"
        )
    if (
        args.acknowledge_legacy_unpinned
        and not args.apply
    ):
        parser.error(
            "--acknowledge-legacy-unpinned is valid only with --apply"
        )
    fenced_action = args.resume_after_lock or args.verify_prepared
    if fenced_action and (
        args.expected_head is None
        or args.expected_state_sha256 is None
    ):
        parser.error(
            "--resume-after-lock and --verify-prepared require "
            "--expected-head and --expected-state-sha256"
        )
    if not fenced_action and (
        args.expected_head is not None
        or args.expected_state_sha256 is not None
    ):
        parser.error(
            "--expected-head and --expected-state-sha256 are valid only "
            "with --resume-after-lock or --verify-prepared"
        )

    # ``absolute`` preserves a final symlink so preflight can reject it.
    project = Path(args.project).expanduser().absolute()
    if args.apply:
        action = "apply"
    elif args.resume_after_lock:
        action = "resume"
    elif args.verify_prepared:
        action = "verify-prepared"
    else:
        action = "dry-run"
    try:
        if action == "verify-prepared":
            code, result = verify_prepared(
                project=project,
                expected_head=str(args.expected_head),
                expected_state_sha256=str(args.expected_state_sha256),
            )
        else:
            code, result = adopt(
                project=project,
                action=action,
                acknowledged=args.acknowledge_legacy_unpinned,
                expected_head=args.expected_head,
                expected_state_sha256=args.expected_state_sha256,
            )
    except AdoptionError as exc:
        result = _result_base(project, action)
        result["status"] = "error"
        result["errors"].append({"code": exc.code, "message": exc.message})
        code = 2
    except OSError as exc:
        result = _result_base(project, action)
        result["status"] = "error"
        result["errors"].append(
            {
                "code": "unexpected_io_error",
                "message": "adoption failed safely: {}".format(exc),
            }
        )
        code = 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        _print_human(result, sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
