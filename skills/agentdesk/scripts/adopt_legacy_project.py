#!/usr/bin/env python3
"""Explicitly pin one legacy-unpinned AgentDesk project to this release.

This command is intentionally narrower than ``init_project.py``.  It accepts
only an existing, clean AgentDesk Git worktree whose canonical state has no
active attempt and whose version lock is absent.  A dry run is always
read-only.  Apply mode additionally requires a verified Stable Skill bundle
and an explicit acknowledgement before it exclusively creates the lock and
asks the same bundle's initializer to fill only missing template files.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SKILL_ROOT = Path(__file__).resolve().parent.parent
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
LOCK_FILE = Path("docs/pm/AGENTDESK.lock.json")
STATE_FILE = Path("docs/pm/state/tasks.yaml")
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

RESULT_SCHEMA = "agentdesk.legacy-adoption-result/v1"
RELEASE_MANIFEST_SCHEMA = "agentdesk.release-manifest/v1"
PROJECT_LOCK_SCHEMA = "agentdesk.project-lock/v1"
SCHEMA_SET_ID = "agentdesk.schema-set/v1"
DIGEST_SCOPE = "all-files-with-canonical-release-manifest"
ADOPTION_LEVELS = ("lite", "standard", "automated")
ACTIVE_STATES = frozenset(("dispatched", "in_progress", "review_ready"))
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


def _load_json_object(path: Path, label: str) -> Dict[str, Any]:
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
        raw = path.read_text(encoding="utf-8")
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
    state = _load_json_object(path, "canonical task state")
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
        state_name = _nonempty_string(
            task.get("state"), "{}.state".format(context)
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
        is_active = (
            dispatch is not None
            or state_name in ACTIVE_STATES
            or (
                state_name == "blocked"
                and blocked_attempt_valid is not False
                and attempt > 0
            )
        )
        if is_active:
            task_id = task.get("task_id")
            if isinstance(task_id, str) and task_id:
                active.append(task_id)
            else:
                active.append("tasks[{}]".format(index))
    if active:
        raise AdoptionError(
            "active_attempts",
            "legacy adoption is blocked while attempts are active: {}".format(
                ", ".join(active)
            ),
        )

    try:
        state_bytes = path.read_bytes()
    except OSError as exc:
        raise AdoptionError(
            "read_error", "cannot reread canonical task state: {}".format(exc)
        ) from exc
    return {
        "project_id": project_id,
        "adoption_level": adoption_level,
        "pm_holder_id": holder_id,
        "state_sha256": hashlib.sha256(state_bytes).hexdigest(),
        "task_count": len(tasks),
    }


def _sanitized_git_environment() -> Dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
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
    return [
        sys.executable,
        str(INITIALIZER),
        "--project",
        str(project),
        "--project-id",
        str(state_summary["project_id"]),
        "--mode",
        str(state_summary["adoption_level"]),
        "--pm-holder-id",
        str(state_summary["pm_holder_id"]),
    ]


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
    lock_content = _render_lock(manifest, state_summary["project_id"])
    stable_problem = _stable_release_problem(
        manifest, actual_digest, writable_paths
    )
    return {
        "manifest": manifest,
        "actual_bundle_sha256": actual_digest,
        "state": state_summary,
        "git": git_summary,
        "markers": markers,
        "lock_content": lock_content,
        "stable_problem": stable_problem,
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
                ],
            }
        )
    return result


def _revalidate_before_write(
    project: Path, preflight: Mapping[str, Any]
) -> None:
    _assert_legacy_unpinned(project)
    git_summary = _inspect_git(project)
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


def _create_lock(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise AdoptionError(
            "lock_race",
            "version lock appeared during adoption; no file was overwritten",
        ) from exc
    except OSError as exc:
        raise AdoptionError(
            "lock_create_failed",
            "cannot exclusively create version lock: {}".format(exc),
        ) from exc


def adopt(
    project: Path, action: str, acknowledged: bool
) -> Tuple[int, Dict[str, Any]]:
    preflight = _preflight(project)
    result = _result_base(project, action, preflight)
    stable_problem = preflight["stable_problem"]

    if action == "dry-run":
        result["status"] = "plan"
        if stable_problem is not None:
            result["warnings"].append(stable_problem)
        return 0, result

    if not acknowledged:
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

    _revalidate_before_write(project, preflight)
    lock_path = project / LOCK_FILE
    _create_lock(lock_path, preflight["lock_content"])
    result["lock_created"] = True

    command = _initializer_command(project, preflight["state"])
    try:
        initialized = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            env=_sanitized_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["status"] = "incomplete"
        result["errors"].append(
            {
                "code": "initializer_failed_after_lock",
                "message": (
                    "version lock was retained, but the initializer could not "
                    "complete: {}".format(exc)
                ),
            }
        )
        result["recovery"] = {
            "lock_retained": True,
            "command": command,
            "instruction": (
                "Resolve the initializer failure, then rerun this exact "
                "same-bundle initializer command. Do not recreate or edit "
                "the lock."
            ),
        }
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
        result["status"] = "incomplete"
        result["errors"].append(
            {
                "code": "initializer_failed_after_lock",
                "message": (
                    "version lock was retained, but the initializer did not "
                    "complete: {}".format(detail)
                ),
            }
        )
        result["recovery"] = {
            "lock_retained": True,
            "command": command,
            "instruction": (
                "Resolve the reported path or template conflict, then rerun "
                "this exact same-bundle initializer command. Do not recreate "
                "or edit the lock."
            ),
        }
        return 1, result

    result["status"] = "adopted"
    result["complete"] = True
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
            else:
                print(
                    "PLAN INITIALIZE_MISSING (no overwrite)",
                    file=stream,
                )
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
        help="create the version lock and fill only missing template files",
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

    # ``absolute`` preserves a final symlink so preflight can reject it.
    project = Path(args.project).expanduser().absolute()
    action = "apply" if args.apply else "dry-run"
    try:
        code, result = adopt(
            project=project,
            action=action,
            acknowledged=args.acknowledge_legacy_unpinned,
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
