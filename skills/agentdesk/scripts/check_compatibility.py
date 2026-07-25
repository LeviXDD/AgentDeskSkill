#!/usr/bin/env python3
"""Check whether this AgentDesk release may operate on a project.

The project lock and release manifest are JSON-compatible YAML (strict JSON),
so the checker has no third-party dependencies.  Read-only operations may
continue with an explicit warning when a legacy project is unpinned or the
release identity is not yet verifiable.  Mutating operations fail closed
unless the lock exactly matches a content-addressed release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


LOCK_FILE = Path("docs/pm/AGENTDESK.lock.json")
STATE_FILE = Path("docs/pm/state/tasks.yaml")
RELEASE_MANIFEST_FILE = (
    Path(__file__).resolve().parent.parent / "release-manifest.json"
)
RESULT_SCHEMA = "agentdesk.compatibility-result/v1"
RELEASE_MANIFEST_SCHEMA = "agentdesk.release-manifest/v1"
PROJECT_LOCK_SCHEMA = "agentdesk.project-lock/v1"
SCHEMA_SET_ID = "agentdesk.schema-set/v1"
BUNDLE_DIGEST_SCOPE = "all-files-with-canonical-release-manifest"
BUNDLE_DIGEST_ALGORITHM = (
    "sha256(sorted(relative-posix-path + NUL + "
    "sha256(file-bytes; release-manifest.json uses canonical JSON with "
    "bundle_sha256=null) + LF))"
)
OPERATIONS = ("read", "validate", "write", "migrate")
READ_ONLY_OPERATIONS = frozenset(("read", "validate"))
WRITE_OPERATIONS = frozenset(("write", "migrate"))

SCHEMA_VERSION_RE = re.compile(
    r"^(?P<family>[a-z0-9][a-z0-9._-]*)/v(?P<major>[1-9][0-9]*)$"
)
SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BUNDLE_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
TASK_STATE_SCHEMA = "agentdesk.tasks/v2"
TASK_STATES = frozenset(
    {
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
    }
)
ACTIVE_DISPATCH_STATES = frozenset(
    {"dispatched", "in_progress", "review_ready"}
)


class CompatibilityError(Exception):
    """A deterministic compatibility or input error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DuplicateKeyError(ValueError):
    """Raised when JSON repeats a key and would otherwise lose information."""


@dataclass(frozen=True)
class OperationCompatibility:
    protocol_versions: Tuple[str, ...]
    schema_versions: Tuple[str, ...]
    runtime_adapter_contracts: Tuple[str, ...]


@dataclass(frozen=True)
class ReleaseManifest:
    path: Path
    skill_release: str
    release_status: str
    source_commit: Optional[str]
    bundle_sha256: Optional[str]
    digest_scope: str
    protocol_version: str
    schema_set_id: str
    schemas: Mapping[str, str]
    runtime_adapter_id: str
    runtime_adapter_contract: str
    runtime_schemas: Mapping[str, str]
    project_lock_schemas: Tuple[str, ...]
    reader: OperationCompatibility
    writer: OperationCompatibility
    requires_exact_release: bool
    requires_verified_bundle: bool

    @property
    def identity_declared(self) -> bool:
        return (
            self.source_commit is not None
            and SOURCE_COMMIT_RE.fullmatch(self.source_commit) is not None
            and self.bundle_sha256 is not None
            and BUNDLE_DIGEST_RE.fullmatch(self.bundle_sha256) is not None
        )


@dataclass(frozen=True)
class ProjectLock:
    path: Path
    schema_version: str
    project_id: str
    skill_release: str
    source_commit: Optional[str]
    bundle_sha256: Optional[str]
    protocol_version: str
    schemas: Mapping[str, str]
    runtime_adapter_id: str
    runtime_adapter_contract: str
    runtime_schemas: Mapping[str, str]
    reader: OperationCompatibility
    writer: OperationCompatibility
    requires_exact_release: Any
    requires_verified_bundle: Any

    @property
    def identity_declared(self) -> bool:
        return (
            self.source_commit is not None
            and SOURCE_COMMIT_RE.fullmatch(self.source_commit) is not None
            and self.bundle_sha256 is not None
            and BUNDLE_DIGEST_RE.fullmatch(self.bundle_sha256) is not None
        )


def _reject_duplicate_keys(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKeyError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _parse_json_object_bytes(
    raw_bytes: bytes, path: Path, label: str
) -> Dict[str, Any]:
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompatibilityError(
            "invalid_utf8", f"{label} is not valid UTF-8: {path}"
        ) from exc
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, DuplicateKeyError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            detail = (
                f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
            )
        else:
            detail = str(exc)
        raise CompatibilityError(
            "invalid_json",
            f"{label} must be JSON-compatible YAML ({detail}): {path}",
        ) from exc
    if not isinstance(value, dict):
        raise CompatibilityError(
            "invalid_object", f"{label} must contain a top-level object: {path}"
        )
    return value


def _load_json_object_and_bytes(
    path: Path, label: str
) -> Tuple[Dict[str, Any], bytes]:
    if path.is_symlink():
        raise CompatibilityError(
            "unsafe_symlink", f"{label} must not be a symbolic link: {path}"
        )
    if not path.is_file():
        raise CompatibilityError("missing_file", f"{label} does not exist: {path}")
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise CompatibilityError(
            "read_error", f"cannot read {label} at {path}: {exc}"
        ) from exc
    return _parse_json_object_bytes(raw_bytes, path, label), raw_bytes


def _load_json_object(path: Path, label: str) -> Dict[str, Any]:
    value, _raw_bytes = _load_json_object_and_bytes(path, label)
    return value


def _read_project_regular_file(
    project: Path, relative_path: Path, label: str
) -> bytes:
    """Read one control-plane file without following any path symlink."""

    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    file_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        file_flags |= os.O_NOFOLLOW

    opened: List[int] = []
    file_descriptor: Optional[int] = None
    try:
        current_descriptor = os.open(str(project), directory_flags)
        opened.append(current_descriptor)
        for component in relative_path.parts[:-1]:
            current_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=current_descriptor,
            )
            opened.append(current_descriptor)
        file_descriptor = os.open(
            relative_path.name,
            file_flags,
            dir_fd=current_descriptor,
        )
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CompatibilityError(
                "unsafe_project_path",
                f"{label} must be an ordinary regular file: "
                f"{project / relative_path}",
            )
        chunks: List[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except CompatibilityError:
        raise
    except FileNotFoundError as exc:
        raise CompatibilityError(
            "missing_file",
            f"{label} does not exist: {project / relative_path}",
        ) from exc
    except OSError as exc:
        code = "unsafe_symlink" if exc.errno in {getattr(os, "ELOOP", 40)} else "read_error"
        raise CompatibilityError(
            code,
            f"cannot safely read {label} at {project / relative_path}: {exc}",
        ) from exc
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _require_safe_project_path(project: Path, relative: Path, label: str) -> None:
    """Reject symlinks at the project root or below it for control-plane paths."""

    current = project
    components = (Path("."), *relative.parts)
    for component in components:
        if component != Path("."):
            current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise CompatibilityError(
                "path_inspection_failed",
                f"cannot inspect {label} path component {current}: {exc}",
            ) from exc
        if stat.S_ISLNK(mode):
            raise CompatibilityError(
                "unsafe_symlink",
                f"{label} path must not contain symbolic links: {current}",
            )


def _writable_bundle_paths(bundle_root: Path) -> List[str]:
    paths = [bundle_root]
    try:
        paths.extend(bundle_root.rglob("*"))
        writable: List[str] = []
        for path in paths:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                continue
            if stat.S_IMODE(mode) & 0o222:
                writable.append(
                    "."
                    if path == bundle_root
                    else path.relative_to(bundle_root).as_posix()
                )
        return writable
    except OSError as exc:
        raise CompatibilityError(
            "bundle_read_error", f"cannot inspect Skill bundle modes: {exc}"
        ) from exc


def _git_environment() -> Dict[str, str]:
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


def _run_git_bytes(project: Path, arguments: Sequence[str], label: str) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project),
                *arguments,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            env=_git_environment(),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise CompatibilityError(
            "git_inspection_failed", f"cannot inspect {label}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
            or "unknown Git error"
        )
        raise CompatibilityError(
            "git_inspection_failed", f"cannot inspect {label}: {detail}"
        )
    return result.stdout


def _run_git_probe(
    project: Path, arguments: Sequence[str], label: str
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project),
                *arguments,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            env=_git_environment(),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise CompatibilityError(
            "writer_git_inspection_failed",
            f"cannot inspect {label} for Writer authorization: {exc}",
        ) from exc


def _verify_mutating_git_boundary(
    project: Path, *, require_repository: bool
) -> None:
    """Bind mutating authorization to this exact project worktree root."""

    git_entry = project / ".git"
    try:
        before = git_entry.lstat()
    except FileNotFoundError:
        before = None
    except OSError as exc:
        raise CompatibilityError(
            "project_git_inspection_failed",
            f"cannot inspect project Git authority at {git_entry}: {exc}",
        ) from exc
    if before is not None and stat.S_ISLNK(before.st_mode):
        raise CompatibilityError(
            "unsafe_project_git",
            f"mutating operations refuse a symbolic-link Git authority: "
            f"{git_entry}",
        )

    root_result = _run_git_probe(
        project,
        ("rev-parse", "--show-toplevel"),
        "project Git worktree root",
    )
    if root_result.returncode != 0:
        if require_repository:
            raise CompatibilityError(
                "writer_git_repository_missing",
                "write requires the project to be an exact Git worktree root "
                "with a committed AgentDesk project lock",
            )
        if before is not None:
            raise CompatibilityError(
                "project_git_authority_invalid",
                f"cannot authorize migration through the Git authority at "
                f"{git_entry}",
            )
        return

    root_text = root_result.stdout.strip()
    try:
        reported_root = Path(os.fsdecode(root_text)).resolve(strict=True)
        expected_root = project.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise CompatibilityError(
            "project_git_inspection_failed",
            f"cannot resolve the project Git worktree root: {exc}",
        ) from exc
    if not root_text or reported_root != expected_root:
        raise CompatibilityError(
            "project_git_root_mismatch",
            "mutating operations require --project to be the exact Git "
            f"worktree root; Git reported {os.fsdecode(root_text)!r}",
        )

    try:
        after = git_entry.lstat()
    except FileNotFoundError:
        after = None
    except OSError as exc:
        raise CompatibilityError(
            "project_git_inspection_failed",
            f"cannot re-inspect project Git authority at {git_entry}: {exc}",
        ) from exc
    if after is not None and stat.S_ISLNK(after.st_mode):
        raise CompatibilityError(
            "unsafe_project_git",
            f"mutating operations refuse a symbolic-link Git authority: "
            f"{git_entry}",
        )
    before_identity = (
        None
        if before is None
        else (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode))
    )
    after_identity = (
        None
        if after is None
        else (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode))
    )
    if before_identity != after_identity:
        raise CompatibilityError(
            "project_git_authority_changed",
            "project .git authority changed while mutating authorization was "
            "being verified; retry from a stable checkout",
        )


def compute_bundle_digest(bundle_root: Path, manifest_path: Path) -> str:
    """Return the stable digest for one Skill bundle.

    ``BUNDLE_DIGEST_ALGORITHM`` includes every relative POSIX path and its
    effective file bytes.  The release manifest participates as canonical
    JSON with only ``bundle_sha256`` normalized to null, avoiding a
    self-reference while protecting every other release identity field.
    Permissions, mtimes, ownership, and directory metadata are excluded so
    the result is stable across installations.
    """

    bundle_root = bundle_root.absolute()
    manifest_path = manifest_path.absolute()
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise CompatibilityError(
            "unsafe_bundle",
            f"bundle root must be a regular directory: {bundle_root}",
        )
    try:
        manifest_relative = manifest_path.relative_to(bundle_root)
    except ValueError as exc:
        raise CompatibilityError(
            "unsafe_bundle",
            f"release manifest must be inside bundle root: {manifest_path}",
        ) from exc

    entries: List[Tuple[str, str]] = []
    try:
        paths = sorted(
            bundle_root.rglob("*"),
            key=lambda item: item.relative_to(bundle_root).as_posix(),
        )
        for path in paths:
            relative = path.relative_to(bundle_root)
            if path.is_symlink():
                raise CompatibilityError(
                    "unsafe_bundle",
                    "bundle must not contain symbolic links: "
                    f"{relative.as_posix()}",
                )
            if path.is_dir():
                continue
            if not path.is_file():
                raise CompatibilityError(
                    "unsafe_bundle",
                    "bundle contains an unsupported entry: "
                    f"{relative.as_posix()}",
                )
            if relative == manifest_relative:
                manifest = _load_json_object(
                    path, "release manifest for bundle digest"
                )
                manifest["bundle_sha256"] = None
                try:
                    effective_bytes = (
                        json.dumps(
                            manifest,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode("utf-8")
                        + b"\n"
                    )
                except (TypeError, ValueError) as exc:
                    raise CompatibilityError(
                        "invalid_manifest",
                        "release manifest cannot be serialized as canonical JSON: "
                        f"{exc}",
                    ) from exc
            else:
                effective_bytes = path.read_bytes()
            file_digest = hashlib.sha256(effective_bytes).hexdigest()
            entries.append((relative.as_posix(), file_digest))
    except CompatibilityError:
        raise
    except OSError as exc:
        raise CompatibilityError(
            "bundle_read_error", f"cannot hash Skill bundle: {exc}"
        ) from exc

    aggregate = hashlib.sha256()
    for relative_path, file_digest in entries:
        aggregate.update(relative_path.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(file_digest.encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def _object(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise CompatibilityError("invalid_shape", f"{label} must be an object")
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompatibilityError(
            "invalid_field", f"{label} must be a non-empty string"
        )
    return value


def _optional_digest(
    value: Any, label: str, pattern: "re.Pattern[str]"
) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CompatibilityError(
            "invalid_identity",
            f"{label} must be null or lowercase hexadecimal matching "
            f"{pattern.pattern}",
        )
    return value


def _schema_version(value: Any, label: str) -> str:
    candidate = _nonempty_string(value, label)
    if SCHEMA_VERSION_RE.fullmatch(candidate) is None:
        raise CompatibilityError(
            "invalid_schema_version",
            f"{label} must use the '<family>/v<major>' form",
        )
    return candidate


def _schema_map(value: Any, label: str) -> Dict[str, str]:
    source = _object(value, label)
    if not source:
        raise CompatibilityError(
            "invalid_schema_set", f"{label} must not be empty"
        )
    result: Dict[str, str] = {}
    for component, schema_value in source.items():
        component_name = _nonempty_string(component, f"{label} component")
        result[component_name] = _schema_version(
            schema_value, f"{label}.{component_name}"
        )
    return result


def _string_array(
    value: Any, label: str, *, schemas: bool = False
) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CompatibilityError(
            "invalid_compatibility", f"{label} must be a non-empty array"
        )
    result: List[str] = []
    for index, item in enumerate(value):
        if schemas:
            parsed = _schema_version(item, f"{label}[{index}]")
        else:
            parsed = _nonempty_string(item, f"{label}[{index}]")
        if parsed in result:
            raise CompatibilityError(
                "duplicate_compatibility",
                f"{label} must not contain duplicate value {parsed!r}",
            )
        result.append(parsed)
    return tuple(result)


def _operation_compatibility(
    value: Any, label: str
) -> OperationCompatibility:
    source = _object(value, label)
    return OperationCompatibility(
        protocol_versions=_string_array(
            source.get("protocol_versions"),
            f"{label}.protocol_versions",
            schemas=True,
        ),
        schema_versions=_string_array(
            source.get("schema_versions"),
            f"{label}.schema_versions",
            schemas=True,
        ),
        runtime_adapter_contracts=_string_array(
            source.get("runtime_adapter_contracts"),
            f"{label}.runtime_adapter_contracts",
            schemas=True,
        ),
    )


def _require_known_major(
    actual: str, supported: Iterable[str], label: str
) -> None:
    match = SCHEMA_VERSION_RE.fullmatch(actual)
    if match is None:  # already guarded by _schema_version
        raise CompatibilityError(
            "invalid_schema_version", f"{label} has an invalid schema version"
        )
    supported_versions = tuple(supported)
    supported_majors = {
        (
            supported_match.group("family"),
            supported_match.group("major"),
        )
        for item in supported_versions
        for supported_match in [SCHEMA_VERSION_RE.fullmatch(item)]
        if supported_match is not None
    }
    key = (match.group("family"), match.group("major"))
    if key not in supported_majors:
        raise CompatibilityError(
            "unknown_schema_major",
            f"{label} uses unsupported schema major {actual!r}",
        )
    if actual not in supported_versions:
        raise CompatibilityError(
            "unsupported_schema_version",
            f"{label} uses unsupported schema version {actual!r}",
        )


def _ensure_current_values_are_declared(
    compatibility: OperationCompatibility,
    *,
    protocol_version: str,
    schemas: Mapping[str, str],
    runtime_contract: str,
    runtime_schemas: Mapping[str, str],
    label: str,
    exact: bool,
) -> None:
    expected_protocols = {protocol_version}
    expected_schemas = set(schemas.values()) | set(runtime_schemas.values())
    expected_contracts = {runtime_contract}
    actual_protocols = set(compatibility.protocol_versions)
    actual_schemas = set(compatibility.schema_versions)
    actual_contracts = set(compatibility.runtime_adapter_contracts)

    comparisons = (
        ("protocol_versions", actual_protocols, expected_protocols),
        ("schema_versions", actual_schemas, expected_schemas),
        ("runtime_adapter_contracts", actual_contracts, expected_contracts),
    )
    for field, actual, expected in comparisons:
        valid = actual == expected if exact else expected.issubset(actual)
        if not valid:
            relation = "exactly equal" if exact else "include"
            raise CompatibilityError(
                "incomplete_compatibility",
                f"{label}.{field} must {relation} the project's current values; "
                f"expected {sorted(expected)!r}, got {sorted(actual)!r}",
            )


def _load_release_manifest(path: Path) -> ReleaseManifest:
    value = _load_json_object(path, "release manifest")
    schema_version = _schema_version(
        value.get("schema_version"), "release manifest schema_version"
    )
    _require_known_major(
        schema_version, (RELEASE_MANIFEST_SCHEMA,), "release manifest"
    )

    schema_set = _object(value.get("schema_set"), "release manifest schema_set")
    schema_set_id = _schema_version(
        schema_set.get("id"), "release manifest schema_set.id"
    )
    _require_known_major(schema_set_id, (SCHEMA_SET_ID,), "release manifest schema set")
    schemas = _schema_map(
        schema_set.get("schemas"), "release manifest schema_set.schemas"
    )

    runtime_adapter = _object(
        value.get("runtime_adapter"), "release manifest runtime_adapter"
    )
    runtime_schemas = _schema_map(
        runtime_adapter.get("runtime_schemas"),
        "release manifest runtime_adapter.runtime_schemas",
    )
    for component, schema_version_value in runtime_schemas.items():
        if schemas.get(component) != schema_version_value:
            raise CompatibilityError(
                "runtime_schema_mismatch",
                "release manifest runtime schema "
                f"{component!r} must match schema_set.schemas",
            )

    compatibility = _object(
        value.get("compatibility"), "release manifest compatibility"
    )
    reader = _operation_compatibility(
        compatibility.get("reader"), "release manifest compatibility.reader"
    )
    writer_value = _object(
        compatibility.get("writer"), "release manifest compatibility.writer"
    )
    writer = _operation_compatibility(
        writer_value, "release manifest compatibility.writer"
    )
    requires_exact_release = writer_value.get("requires_exact_release")
    requires_verified_bundle = writer_value.get("requires_verified_bundle")
    if requires_exact_release is not True or requires_verified_bundle is not True:
        raise CompatibilityError(
            "unsafe_writer_policy",
            "release manifest writer policy must require exact release and "
            "verified bundle",
        )

    project_lock_schemas = _string_array(
        compatibility.get("project_lock_schemas"),
        "release manifest compatibility.project_lock_schemas",
        schemas=True,
    )
    for supported_lock_schema in project_lock_schemas:
        _require_known_major(
            supported_lock_schema,
            (PROJECT_LOCK_SCHEMA,),
            "release manifest project lock compatibility",
        )

    protocol_version = _schema_version(
        value.get("protocol_version"), "release manifest protocol_version"
    )
    runtime_adapter_contract = _schema_version(
        runtime_adapter.get("contract_version"),
        "release manifest runtime_adapter.contract_version",
    )
    _ensure_current_values_are_declared(
        reader,
        protocol_version=protocol_version,
        schemas=schemas,
        runtime_contract=runtime_adapter_contract,
        runtime_schemas=runtime_schemas,
        label="release manifest compatibility.reader",
        exact=False,
    )
    _ensure_current_values_are_declared(
        writer,
        protocol_version=protocol_version,
        schemas=schemas,
        runtime_contract=runtime_adapter_contract,
        runtime_schemas=runtime_schemas,
        label="release manifest compatibility.writer",
        exact=False,
    )

    digest_scope = _nonempty_string(
        value.get("digest_scope"), "release manifest digest_scope"
    )
    if digest_scope != BUNDLE_DIGEST_SCOPE:
        raise CompatibilityError(
            "unsupported_digest_scope",
            "release manifest digest_scope must be "
            f"{BUNDLE_DIGEST_SCOPE!r}, got {digest_scope!r}",
        )

    return ReleaseManifest(
        path=path,
        skill_release=_nonempty_string(
            value.get("skill_release"), "release manifest skill_release"
        ),
        release_status=_nonempty_string(
            value.get("release_status"), "release manifest release_status"
        ),
        source_commit=_optional_digest(
            value.get("source_commit"),
            "release manifest source_commit",
            SOURCE_COMMIT_RE,
        ),
        bundle_sha256=_optional_digest(
            value.get("bundle_sha256"),
            "release manifest bundle_sha256",
            BUNDLE_DIGEST_RE,
        ),
        digest_scope=digest_scope,
        protocol_version=protocol_version,
        schema_set_id=schema_set_id,
        schemas=schemas,
        runtime_adapter_id=_nonempty_string(
            runtime_adapter.get("id"), "release manifest runtime_adapter.id"
        ),
        runtime_adapter_contract=runtime_adapter_contract,
        runtime_schemas=runtime_schemas,
        project_lock_schemas=project_lock_schemas,
        reader=reader,
        writer=writer,
        requires_exact_release=requires_exact_release,
        requires_verified_bundle=requires_verified_bundle,
    )


def _load_project_lock(
    project: Path, manifest: ReleaseManifest
) -> Tuple[ProjectLock, bytes]:
    path = project / LOCK_FILE
    raw_bytes = _read_project_regular_file(project, LOCK_FILE, "project lock")
    value = _parse_json_object_bytes(raw_bytes, path, "project lock")
    schema_version = _schema_version(
        value.get("schema_version"), "project lock schema_version"
    )
    _require_known_major(
        schema_version, manifest.project_lock_schemas, "project lock"
    )

    skill = _object(value.get("skill"), "project lock skill")
    protocol = _object(value.get("protocol"), "project lock protocol")
    schemas = _schema_map(value.get("schemas"), "project lock schemas")
    runtime_adapter = _object(
        value.get("runtime_adapter"), "project lock runtime_adapter"
    )
    runtime_schemas = _schema_map(
        runtime_adapter.get("runtime_schemas"),
        "project lock runtime_adapter.runtime_schemas",
    )
    operation_compatibility = _object(
        value.get("operation_compatibility"),
        "project lock operation_compatibility",
    )
    reader = _operation_compatibility(
        operation_compatibility.get("reader"),
        "project lock operation_compatibility.reader",
    )
    writer_value = _object(
        operation_compatibility.get("writer"),
        "project lock operation_compatibility.writer",
    )
    writer = _operation_compatibility(
        writer_value, "project lock operation_compatibility.writer"
    )

    protocol_version = _schema_version(
        protocol.get("version"), "project lock protocol.version"
    )
    runtime_adapter_contract = _schema_version(
        runtime_adapter.get("contract_version"),
        "project lock runtime_adapter.contract_version",
    )
    _ensure_current_values_are_declared(
        reader,
        protocol_version=protocol_version,
        schemas=schemas,
        runtime_contract=runtime_adapter_contract,
        runtime_schemas=runtime_schemas,
        label="project lock operation_compatibility.reader",
        exact=False,
    )
    _ensure_current_values_are_declared(
        writer,
        protocol_version=protocol_version,
        schemas=schemas,
        runtime_contract=runtime_adapter_contract,
        runtime_schemas=runtime_schemas,
        label="project lock operation_compatibility.writer",
        exact=True,
    )

    project_lock = ProjectLock(
        path=path,
        schema_version=schema_version,
        project_id=_nonempty_string(
            value.get("project_id"), "project lock project_id"
        ),
        skill_release=_nonempty_string(
            skill.get("release"), "project lock skill.release"
        ),
        source_commit=_optional_digest(
            skill.get("source_commit"),
            "project lock skill.source_commit",
            SOURCE_COMMIT_RE,
        ),
        bundle_sha256=_optional_digest(
            skill.get("bundle_sha256"),
            "project lock skill.bundle_sha256",
            BUNDLE_DIGEST_RE,
        ),
        protocol_version=protocol_version,
        schemas=schemas,
        runtime_adapter_id=_nonempty_string(
            runtime_adapter.get("id"), "project lock runtime_adapter.id"
        ),
        runtime_adapter_contract=runtime_adapter_contract,
        runtime_schemas=runtime_schemas,
        reader=reader,
        writer=writer,
        requires_exact_release=writer_value.get("requires_exact_release"),
        requires_verified_bundle=writer_value.get("requires_verified_bundle"),
    )
    return project_lock, raw_bytes


def _check_operation_support(
    project_lock: ProjectLock,
    manifest: ReleaseManifest,
    operation: str,
) -> None:
    if operation in READ_ONLY_OPERATIONS:
        lock_compatibility = project_lock.reader
        release_compatibility = manifest.reader
    else:
        lock_compatibility = project_lock.writer
        release_compatibility = manifest.writer

    for value in lock_compatibility.protocol_versions:
        _require_known_major(
            value,
            release_compatibility.protocol_versions,
            "project lock protocol compatibility",
        )
    for value in lock_compatibility.schema_versions:
        _require_known_major(
            value,
            release_compatibility.schema_versions,
            "project lock schema compatibility",
        )
    for value in lock_compatibility.runtime_adapter_contracts:
        _require_known_major(
            value,
            release_compatibility.runtime_adapter_contracts,
            "project lock runtime adapter compatibility",
        )

    if project_lock.runtime_adapter_id != manifest.runtime_adapter_id:
        raise CompatibilityError(
            "runtime_adapter_mismatch",
            "project lock runtime adapter "
            f"{project_lock.runtime_adapter_id!r} is not supported by release "
            f"adapter {manifest.runtime_adapter_id!r}",
        )


def _identity_differences(
    project_lock: ProjectLock, manifest: ReleaseManifest
) -> List[str]:
    def operation_identity(
        compatibility: OperationCompatibility,
        *,
        requires_exact_release: Any = None,
        requires_verified_bundle: Any = None,
        include_writer_policy: bool,
    ) -> Dict[str, Any]:
        identity: Dict[str, Any] = {
            "protocol_versions": sorted(compatibility.protocol_versions),
            "schema_versions": sorted(compatibility.schema_versions),
            "runtime_adapter_contracts": sorted(
                compatibility.runtime_adapter_contracts
            ),
        }
        if include_writer_policy:
            identity["requires_exact_release"] = requires_exact_release
            identity["requires_verified_bundle"] = requires_verified_bundle
        return identity

    comparisons = (
        ("skill.release", project_lock.skill_release, manifest.skill_release),
        ("skill.source_commit", project_lock.source_commit, manifest.source_commit),
        ("skill.bundle_sha256", project_lock.bundle_sha256, manifest.bundle_sha256),
        ("protocol.version", project_lock.protocol_version, manifest.protocol_version),
        ("schemas", dict(project_lock.schemas), dict(manifest.schemas)),
        (
            "runtime_adapter.id",
            project_lock.runtime_adapter_id,
            manifest.runtime_adapter_id,
        ),
        (
            "runtime_adapter.contract_version",
            project_lock.runtime_adapter_contract,
            manifest.runtime_adapter_contract,
        ),
        (
            "runtime_adapter.runtime_schemas",
            dict(project_lock.runtime_schemas),
            dict(manifest.runtime_schemas),
        ),
        (
            "operation_compatibility.reader",
            operation_identity(
                project_lock.reader,
                include_writer_policy=False,
            ),
            operation_identity(
                manifest.reader,
                include_writer_policy=False,
            ),
        ),
        (
            "operation_compatibility.writer",
            operation_identity(
                project_lock.writer,
                requires_exact_release=project_lock.requires_exact_release,
                requires_verified_bundle=project_lock.requires_verified_bundle,
                include_writer_policy=True,
            ),
            operation_identity(
                manifest.writer,
                requires_exact_release=manifest.requires_exact_release,
                requires_verified_bundle=manifest.requires_verified_bundle,
                include_writer_policy=True,
            ),
        ),
    )
    return [
        label
        for label, project_value, release_value in comparisons
        if project_value != release_value
    ]


def _message(code: str, text: str) -> Dict[str, str]:
    return {"code": code, "message": text}


def _load_project_state(project: Path) -> Dict[str, Any]:
    state_path = project / STATE_FILE
    _require_safe_project_path(project, STATE_FILE, "canonical task state")
    state_bytes = _read_project_regular_file(
        project, STATE_FILE, "canonical task state"
    )
    state = _parse_json_object_bytes(
        state_bytes, state_path, "canonical task state"
    )
    if state.get("schema_version") != TASK_STATE_SCHEMA:
        raise CompatibilityError(
            "invalid_task_state",
            f"{STATE_FILE.as_posix()} schema_version must be exactly "
            f"{TASK_STATE_SCHEMA!r}",
        )
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        raise CompatibilityError(
            "invalid_task_state",
            f"{STATE_FILE.as_posix()} tasks must be an array",
        )
    project_id = state.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise CompatibilityError(
            "invalid_task_state",
            f"{STATE_FILE.as_posix()} project_id must be a non-empty string",
        )
    return state


def _active_attempts(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    tasks = state["tasks"]
    active: List[Dict[str, Any]] = []
    for index, task in enumerate(tasks):
        context = f"{STATE_FILE.as_posix()} tasks[{index}]"
        if not isinstance(task, dict):
            raise CompatibilityError(
                "invalid_task_state",
                f"{context} must be an object",
            )
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise CompatibilityError(
                "invalid_task_state",
                f"{context}.task_id must be a non-empty string",
            )
        state_name = task.get("state")
        if state_name not in TASK_STATES:
            raise CompatibilityError(
                "invalid_task_state",
                f"{context}.state must be one of "
                + ", ".join(sorted(TASK_STATES)),
            )
        attempt = task.get("attempt")
        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 0
        ):
            raise CompatibilityError(
                "invalid_task_state",
                f"{context}.attempt must be a non-negative integer",
            )
        blocked_attempt_valid = task.get("blocked_attempt_valid")
        if blocked_attempt_valid is not None and not isinstance(
            blocked_attempt_valid, bool
        ):
            raise CompatibilityError(
                "invalid_task_state",
                f"{context}.blocked_attempt_valid must be boolean or null",
            )
        if state_name == "blocked" and not isinstance(
            blocked_attempt_valid, bool
        ):
            raise CompatibilityError(
                "invalid_task_state",
                f"{context}.blocked_attempt_valid must be boolean while blocked",
            )

        dispatch = task.get("current_dispatch")
        if dispatch is not None and not isinstance(dispatch, dict):
            raise CompatibilityError(
                "invalid_task_state",
                f"{context}.current_dispatch must be an object or null",
            )
        is_active = state_name in ACTIVE_DISPATCH_STATES or (
            state_name == "blocked" and blocked_attempt_valid is True
        )
        if is_active:
            if attempt < 1:
                raise CompatibilityError(
                    "invalid_task_state",
                    f"{context}.attempt must be at least 1 for an active attempt",
                )
            if not isinstance(dispatch, dict):
                raise CompatibilityError(
                    "invalid_task_state",
                    f"{context}.current_dispatch must be an object for an "
                    "active attempt",
                )
            dispatch_id = dispatch.get("dispatch_id")
            if not isinstance(dispatch_id, str) or not dispatch_id.strip():
                raise CompatibilityError(
                    "invalid_task_state",
                    f"{context}.current_dispatch.dispatch_id must be a "
                    "non-empty string",
                )
        elif dispatch is not None:
            raise CompatibilityError(
                "invalid_task_state",
                f"{context}.current_dispatch must be null while state is "
                f"{state_name!r}",
            )
        if is_active:
            active.append(task)
    return active


def _active_attempt_labels(active_tasks: Sequence[Mapping[str, Any]]) -> List[str]:
    labels: List[str] = []
    for index, task in enumerate(active_tasks):
        task_id = task.get("task_id")
        labels.append(
            task_id
            if isinstance(task_id, str) and task_id
            else f"active_tasks[{index}]"
        )
    return labels


def _verify_active_attempt_locks(
    project: Path,
    active_tasks: Sequence[Mapping[str, Any]],
    current_lock_bytes: bytes,
    writer_head: str,
) -> None:
    verified_commits: set[str] = set()
    lock_path = LOCK_FILE.as_posix()
    for task in active_tasks:
        task_id = task.get("task_id")
        label = task_id if isinstance(task_id, str) and task_id else "<unknown-task>"
        commit = task.get("task_card_commit")
        if not isinstance(commit, str) or SOURCE_COMMIT_RE.fullmatch(commit) is None:
            raise CompatibilityError(
                "active_attempt_version_unverified",
                f"active task {label} must have a full 40-hex task_card_commit",
            )
        if commit in verified_commits:
            continue
        commit_check = _run_git_probe(
            project,
            ("cat-file", "-e", f"{commit}^{{commit}}"),
            f"task_card_commit {commit}",
        )
        if commit_check.returncode != 0:
            raise CompatibilityError(
                "active_attempt_version_unverified",
                f"active task {label} task_card_commit must identify a "
                "readable Git commit object",
            )
        ancestry = _run_git_probe(
            project,
            ("merge-base", "--is-ancestor", commit, writer_head),
            f"task_card_commit ancestry for active task {label}",
        )
        if ancestry.returncode == 1:
            raise CompatibilityError(
                "active_attempt_commit_unreachable",
                f"active task {label} task_card_commit must be an ancestor "
                "of the Writer-verified project HEAD",
            )
        if ancestry.returncode != 0:
            raise CompatibilityError(
                "active_attempt_version_unverified",
                f"cannot verify active task {label} task_card_commit ancestry "
                "against the Writer-verified project HEAD",
            )
        tree_entry = _run_git_bytes(
            project,
            ("ls-tree", "-z", commit, "--", lock_path),
            f"project lock at task_card_commit {commit}",
        )
        entries = [entry for entry in tree_entry.split(b"\0") if entry]
        if len(entries) != 1 or b"\t" not in entries[0]:
            raise CompatibilityError(
                "active_attempt_lock_missing",
                f"task_card_commit {commit} must contain {lock_path} as one "
                "ordinary Git blob",
            )
        metadata, returned_path = entries[0].split(b"\t", 1)
        parts = metadata.split()
        if (
            len(parts) != 3
            or parts[0] not in {b"100644", b"100755"}
            or parts[1] != b"blob"
            or returned_path != lock_path.encode("utf-8")
        ):
            raise CompatibilityError(
                "active_attempt_lock_not_blob",
                f"task_card_commit {commit} must contain {lock_path} as one "
                "ordinary Git blob",
            )
        committed_lock = _run_git_bytes(
            project,
            ("show", f"{commit}:{lock_path}"),
            f"project lock bytes at task_card_commit {commit}",
        )
        if committed_lock != current_lock_bytes:
            raise CompatibilityError(
                "active_attempt_lock_mismatch",
                f"active task {label} was dispatched under a different "
                "AgentDesk project lock",
            )
        verified_commits.add(commit)
    final_head = _run_git_probe(
        project,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        "project HEAD after active-attempt verification",
    )
    if (
        final_head.returncode != 0
        or final_head.stdout.decode("ascii", errors="ignore").strip().lower()
        != writer_head.lower()
    ):
        raise CompatibilityError(
            "writer_head_changed",
            "project HEAD changed while verifying active-attempt ancestry; "
            "retry from a stable checkout",
        )


def _verify_writer_lock_at_head(
    project: Path, current_lock_bytes: bytes
) -> Dict[str, str]:
    lock_path = LOCK_FILE.as_posix()
    _verify_mutating_git_boundary(project, require_repository=True)

    head_result = _run_git_probe(
        project,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        "project HEAD",
    )
    head = head_result.stdout.strip().lower()
    if (
        head_result.returncode != 0
        or SOURCE_COMMIT_RE.fullmatch(
            head.decode("ascii", errors="ignore")
        )
        is None
    ):
        raise CompatibilityError(
            "writer_head_missing",
            "write requires a valid Git HEAD commit containing the current "
            "AgentDesk project lock",
        )
    head_text = head.decode("ascii")

    tree_result = _run_git_probe(
        project,
        ("ls-tree", "-z", head_text, "--", lock_path),
        f"{lock_path} at project HEAD",
    )
    if tree_result.returncode != 0:
        detail = (
            tree_result.stderr.decode("utf-8", errors="replace").strip()
            or tree_result.stdout.decode("utf-8", errors="replace").strip()
            or "unknown Git error"
        )
        raise CompatibilityError(
            "writer_git_inspection_failed",
            f"cannot inspect {lock_path} at project HEAD: {detail}",
        )
    entries = [entry for entry in tree_result.stdout.split(b"\0") if entry]
    if not entries:
        raise CompatibilityError(
            "writer_lock_missing_from_head",
            f"write requires project HEAD to contain {lock_path} as one "
            "ordinary Git blob",
        )
    if len(entries) != 1 or b"\t" not in entries[0]:
        raise CompatibilityError(
            "writer_lock_not_blob",
            f"write requires project HEAD to contain {lock_path} as one "
            "ordinary Git blob",
        )
    metadata, returned_path = entries[0].split(b"\t", 1)
    parts = metadata.split()
    if (
        len(parts) != 3
        or parts[0] not in {b"100644", b"100755"}
        or parts[1] != b"blob"
        or returned_path != lock_path.encode("utf-8")
    ):
        raise CompatibilityError(
            "writer_lock_not_blob",
            f"write requires project HEAD to contain {lock_path} as one "
            "ordinary Git blob",
        )

    blob_id = parts[2].decode("ascii", errors="strict")
    blob_result = _run_git_probe(
        project,
        ("cat-file", "blob", blob_id),
        f"{lock_path} blob at project HEAD",
    )
    if blob_result.returncode != 0:
        detail = (
            blob_result.stderr.decode("utf-8", errors="replace").strip()
            or blob_result.stdout.decode("utf-8", errors="replace").strip()
            or "unknown Git error"
        )
        raise CompatibilityError(
            "writer_git_inspection_failed",
            f"cannot read {lock_path} blob at project HEAD: {detail}",
        )
    if blob_result.stdout != current_lock_bytes:
        raise CompatibilityError(
            "writer_lock_bytes_mismatch",
            f"write requires the raw bytes of {lock_path} in project HEAD "
            "to exactly match the worktree file",
        )

    try:
        final_lock_bytes = _read_project_regular_file(
            project, LOCK_FILE, "project lock"
        )
    except CompatibilityError as exc:
        raise CompatibilityError(
            "writer_lock_read_failed",
            f"cannot re-read worktree project lock after Writer "
            f"verification: {exc.message}",
        ) from exc
    if final_lock_bytes != current_lock_bytes:
        raise CompatibilityError(
            "writer_lock_changed",
            "worktree project lock changed while Writer authorization was "
            "being verified; retry from a stable checkout",
        )

    final_head_result = _run_git_probe(
        project,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        "project HEAD after lock verification",
    )
    if (
        final_head_result.returncode != 0
        or final_head_result.stdout.strip().lower() != head
    ):
        raise CompatibilityError(
            "writer_head_changed",
            "project HEAD changed while verifying the committed AgentDesk "
            "project lock; retry from a stable checkout",
        )
    return {"head": head_text, "blob": blob_id}


def _verify_clean_worktree(project: Path) -> None:
    status = _run_git_probe(
        project,
        ("status", "--porcelain=v1", "--untracked-files=all"),
        "project worktree cleanliness",
    )
    if status.returncode != 0:
        detail = (
            status.stderr.decode("utf-8", errors="replace").strip()
            or status.stdout.decode("utf-8", errors="replace").strip()
            or "unknown Git error"
        )
        raise CompatibilityError(
            "writer_git_inspection_failed",
            f"cannot inspect project worktree cleanliness: {detail}",
        )
    if status.stdout:
        raise CompatibilityError(
            "writer_worktree_not_clean",
            "this Writer verification requires a clean Git worktree with no "
            "tracked or untracked adoption changes",
        )


def check_compatibility(
    *,
    project: Path,
    operation: str,
    release_manifest_path: Path,
    require_clean: bool = False,
) -> Dict[str, Any]:
    project = project.expanduser().absolute()
    release_manifest_path = release_manifest_path.expanduser().absolute()
    lock_path = project / LOCK_FILE
    warnings: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []
    release_summary: Dict[str, Any] = {}
    lock_summary: Dict[str, Any] = {
        "path": str(lock_path),
        "state": "unknown",
    }

    external_mutating_manifest = (
        operation in WRITE_OPERATIONS
        and release_manifest_path.resolve(strict=False)
        != RELEASE_MANIFEST_FILE.resolve(strict=False)
    )
    if external_mutating_manifest:
        errors.append(
            _message(
                "external_release_manifest_forbidden",
                f"{operation} must use the release manifest beside this "
                "checker; --release-manifest is read-only and may be used only "
                "with read or validate",
            )
        )

    if not project.is_dir():
        errors.append(
            _message("invalid_project", f"project is not a directory: {project}")
        )
    elif not external_mutating_manifest:
        try:
            manifest = _load_release_manifest(release_manifest_path)
            actual_bundle_sha256: Optional[str] = None
            bundle_verification_error: Optional[CompatibilityError] = None
            try:
                actual_bundle_sha256 = compute_bundle_digest(
                    release_manifest_path.parent, release_manifest_path
                )
            except CompatibilityError as exc:
                bundle_verification_error = exc
            bundle_verified = (
                bundle_verification_error is None
                and manifest.bundle_sha256 is not None
                and actual_bundle_sha256 == manifest.bundle_sha256
            )
            release_identity_verified = (
                manifest.source_commit is not None and bundle_verified
            )
            if operation in WRITE_OPERATIONS:
                writable_paths = _writable_bundle_paths(
                    release_manifest_path.parent
                )
                if writable_paths:
                    preview = ", ".join(writable_paths[:8])
                    if len(writable_paths) > 8:
                        preview += f", ... (+{len(writable_paths) - 8} more)"
                    errors.append(
                        _message(
                            "mutable_bundle",
                            f"{operation} requires a read-only immutable Skill "
                            f"bundle; writable path(s): {preview}",
                        )
                    )
            release_summary = {
                "path": str(release_manifest_path),
                "skill_release": manifest.skill_release,
                "release_status": manifest.release_status,
                "source_commit": manifest.source_commit,
                "bundle_sha256": manifest.bundle_sha256,
                "actual_bundle_sha256": actual_bundle_sha256,
                "bundle_digest_scope": manifest.digest_scope,
                "bundle_digest_algorithm": BUNDLE_DIGEST_ALGORITHM,
                "bundle_verified": bundle_verified,
                "schema_set_id": manifest.schema_set_id,
                "identity_declared": manifest.identity_declared,
                "identity_verified": release_identity_verified,
            }
            if not release_identity_verified:
                message = (
                    "release identity is not verified by both a 40-hex source "
                    "commit and a matching recomputed bundle digest"
                )
                if operation in READ_ONLY_OPERATIONS:
                    warnings.append(
                        _message("release_identity_unverified", message)
                    )
                else:
                    errors.append(
                        _message(
                            "release_identity_unverified",
                            f"{operation} requires a release manifest with a "
                            "40-hex source commit and matching recomputed bundle "
                            "digest",
                        )
                    )
            if (
                operation in WRITE_OPERATIONS
                and manifest.release_status != "stable"
            ):
                errors.append(
                    _message(
                        "release_status_not_stable",
                        f"{operation} requires release_status 'stable', got "
                        f"{manifest.release_status!r}",
                    )
                )
            if bundle_verification_error is not None:
                item = _message(
                    bundle_verification_error.code,
                    bundle_verification_error.message,
                )
                if operation in READ_ONLY_OPERATIONS:
                    warnings.append(item)
                else:
                    errors.append(item)
            elif (
                manifest.bundle_sha256 is not None
                and actual_bundle_sha256 != manifest.bundle_sha256
            ):
                item = _message(
                    "bundle_digest_mismatch",
                    "recomputed Skill bundle digest does not match release "
                    f"manifest: expected {manifest.bundle_sha256}, "
                    f"got {actual_bundle_sha256}",
                )
                if operation in READ_ONLY_OPERATIONS:
                    warnings.append(item)
                else:
                    errors.append(item)

            _require_safe_project_path(project, LOCK_FILE, "project lock")
            if not lock_path.exists() and not lock_path.is_symlink():
                lock_summary["state"] = "legacy-unpinned"
                if operation in READ_ONLY_OPERATIONS:
                    warnings.append(
                        _message(
                            "legacy_unpinned",
                            "project has no docs/pm/AGENTDESK.lock.json; "
                            "read-only operation may continue, but its historical "
                            "Skill identity is not proven",
                        )
                    )
                else:
                    errors.append(
                        _message(
                            "legacy_unpinned",
                            "project has no docs/pm/AGENTDESK.lock.json; "
                            f"{operation} is blocked until an explicit migration "
                            "pins a verified release",
                        )
                    )
            else:
                project_lock, project_lock_bytes = _load_project_lock(
                    project, manifest
                )
                differences = _identity_differences(project_lock, manifest)
                project_identity_verified = (
                    project_lock.identity_declared
                    and release_identity_verified
                    and not differences
                )
                lock_summary.update(
                    {
                        "state": "pinned",
                        "schema_version": project_lock.schema_version,
                        "project_id": project_lock.project_id,
                        "skill_release": project_lock.skill_release,
                        "source_commit": project_lock.source_commit,
                        "bundle_sha256": project_lock.bundle_sha256,
                        "identity_declared": project_lock.identity_declared,
                        "identity_verified": project_identity_verified,
                    }
                )
                _check_operation_support(project_lock, manifest, operation)

                if operation in READ_ONLY_OPERATIONS:
                    if differences:
                        warnings.append(
                            _message(
                                "release_identity_mismatch",
                                "read-only compatibility is declared, but the "
                                "project lock differs from this release in: "
                                + ", ".join(differences),
                            )
                        )
                    if not project_lock.identity_declared:
                        warnings.append(
                            _message(
                                "project_identity_unverified",
                                "project lock has no verified 40-hex source commit "
                                "and 64-hex bundle digest",
                            )
                        )
                else:
                    if not project_lock.identity_declared:
                        errors.append(
                            _message(
                                "project_identity_unverified",
                                f"{operation} requires a project lock with a "
                                "40-hex source commit and 64-hex bundle digest",
                            )
                        )
                    if differences:
                        errors.append(
                            _message(
                                "release_identity_mismatch",
                                f"{operation} requires an exact project lock; "
                                "mismatched fields: " + ", ".join(differences),
                            )
                        )
                if operation in WRITE_OPERATIONS:
                    state = _load_project_state(project)
                    if state.get("project_id") != project_lock.project_id:
                        errors.append(
                            _message(
                                "project_id_mismatch",
                                f"{LOCK_FILE.as_posix()} project_id "
                                f"{project_lock.project_id!r} does not match "
                                f"{STATE_FILE.as_posix()} project_id "
                                f"{state.get('project_id')!r}",
                            )
                        )
                    active_attempts = _active_attempts(state)
                    if operation == "migrate" and active_attempts:
                        errors.append(
                            _message(
                                "active_attempts",
                                "migration is blocked while attempts are active: "
                                + ", ".join(
                                    _active_attempt_labels(active_attempts)
                                ),
                            )
                        )
                    elif operation == "migrate":
                        _verify_mutating_git_boundary(
                            project, require_repository=False
                        )
                    elif operation == "write":
                        committed_lock = _verify_writer_lock_at_head(
                            project, project_lock_bytes
                        )
                        if active_attempts:
                            _verify_active_attempt_locks(
                                project,
                                active_attempts,
                                project_lock_bytes,
                                committed_lock["head"],
                            )
                        lock_summary.update(
                            {
                                "committed_at_head": True,
                                "head_commit": committed_lock["head"],
                                "head_blob": committed_lock["blob"],
                            }
                        )
                        if require_clean:
                            _verify_clean_worktree(project)
                            lock_summary["worktree_clean"] = True
        except CompatibilityError as exc:
            errors.append(_message(exc.code, exc.message))

    if errors:
        status = "incompatible"
        compatible = False
    elif warnings:
        status = "warning"
        compatible = True
    else:
        status = "compatible"
        compatible = True
    return {
        "schema_version": RESULT_SCHEMA,
        "operation": operation,
        "project": str(project),
        "compatible": compatible,
        "status": status,
        "release": release_summary,
        "project_lock": lock_summary,
        "warnings": warnings,
        "errors": errors,
    }


def _print_human(result: Mapping[str, Any]) -> None:
    for warning in result["warnings"]:
        print(f"WARN {warning['code']}: {warning['message']}")
    for error in result["errors"]:
        print(f"ERROR {error['code']}: {error['message']}")
    status = str(result["status"]).upper()
    print(
        f"{status} operation={result['operation']} "
        f"project={result['project']}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check a project's AgentDesk version lock before read, validation, "
            "write, or migration operations."
        )
    )
    parser.add_argument(
        "--project",
        required=True,
        help="AgentDesk project root containing docs/pm/AGENTDESK.lock.json",
    )
    parser.add_argument(
        "--operation",
        required=True,
        choices=OPERATIONS,
        help="Operation to authorize: read, validate, write, or migrate",
    )
    parser.add_argument(
        "--release-manifest",
        default=str(RELEASE_MANIFEST_FILE),
        help=(
            "alternate release manifest for read/validate comparison only "
            "(default: <skill-root>/release-manifest.json; write/migrate must "
            "use that bundled default)"
        ),
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help=(
            "with --operation write, also require a clean tracked and "
            "untracked worktree (for post-commit activation verification)"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit one JSON compatibility result",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.require_clean and args.operation != "write":
        parser.error("--require-clean is valid only with --operation write")
    result = check_compatibility(
        project=Path(args.project),
        operation=args.operation,
        release_manifest_path=Path(args.release_manifest),
        require_clean=args.require_clean,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        _print_human(result)
    return 0 if result["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
