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


def _load_json_object(path: Path, label: str) -> Dict[str, Any]:
    if path.is_symlink():
        raise CompatibilityError(
            "unsafe_symlink", f"{label} must not be a symbolic link: {path}"
        )
    if not path.is_file():
        raise CompatibilityError("missing_file", f"{label} does not exist: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CompatibilityError(
            "invalid_utf8", f"{label} is not valid UTF-8: {path}"
        ) from exc
    except OSError as exc:
        raise CompatibilityError(
            "read_error", f"cannot read {label} at {path}: {exc}"
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


def _load_project_lock(path: Path, manifest: ReleaseManifest) -> ProjectLock:
    value = _load_json_object(path, "project lock")
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

    return ProjectLock(
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
    state = _load_json_object(state_path, "canonical task state")
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


def check_compatibility(
    *,
    project: Path,
    operation: str,
    release_manifest_path: Path,
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
                project_lock = _load_project_lock(lock_path, manifest)
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
                    elif operation == "write" and active_attempts:
                        try:
                            current_lock_bytes = lock_path.read_bytes()
                        except OSError as exc:
                            raise CompatibilityError(
                                "read_error",
                                f"cannot read project lock bytes at "
                                f"{lock_path}: {exc}",
                            ) from exc
                        _verify_active_attempt_locks(
                            project,
                            active_attempts,
                            current_lock_bytes,
                        )
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
        "--json",
        action="store_true",
        help="emit one JSON compatibility result",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    result = check_compatibility(
        project=Path(args.project),
        operation=args.operation,
        release_manifest_path=Path(args.release_manifest),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        _print_human(result)
    return 0 if result["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
