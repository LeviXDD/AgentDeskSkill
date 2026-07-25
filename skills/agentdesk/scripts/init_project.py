#!/usr/bin/env python3
"""Initialize a project for the task-card multi-agent workflow.

The initializer copies the bundled project template, renders its documented
placeholders, creates the workflow's mutable directories, and ensures that
runtime state is ignored by Git.  It is deliberately conservative: existing
files are never replaced, and an existing ``AGENTS.md`` is always reported as
requiring a manual merge.

The command is safe to run repeatedly.  ``--dry-run`` performs the same
validation and reports the actions that would be taken without writing to the
project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


IGNORE_ENTRY = ".agentdesk/runtime/"
SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "project-template"
RELEASE_MANIFEST = SKILL_ROOT / "release-manifest.json"
COMPATIBILITY_CHECKER = SKILL_ROOT / "scripts" / "check_compatibility.py"
PROJECT_LOCK_FILE = Path("docs/pm/AGENTDESK.lock.json")
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
RELEASE_MANIFEST_SCHEMA = "agentdesk.release-manifest/v1"
PROJECT_LOCK_SCHEMA = "agentdesk.project-lock/v1"
SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BUNDLE_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_DIRECTORIES = (
    Path("docs/pm/state"),
    Path("docs/pm/tasks"),
    Path("docs/pm/reports"),
    Path("docs/pm/acceptances"),
    Path("docs/pm/consultations"),
    Path("docs/pm/events"),
    Path("docs/pm/outbox"),
    Path(".agentdesk/runtime"),
    Path(".agentdesk/runtime/inbox"),
)


class InitializationError(Exception):
    """Base class for expected initialization failures."""


class TemplateError(InitializationError):
    """Raised when the bundled template is missing or malformed."""


class ProjectArgumentError(InitializationError):
    """Raised when a project argument cannot identify a usable project root."""


@dataclass(frozen=True)
class RenderedTemplate:
    """A fully validated, in-memory representation of the project template."""

    directories: tuple[Path, ...]
    files: tuple[tuple[Path, bytes], ...]


def _load_release_manifest(manifest_path: Path) -> dict[str, object]:
    """Load the candidate bundle identity without trusting caller input."""

    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise TemplateError(
            f"release manifest must be a regular file: {manifest_path}"
        )
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplateError(f"cannot read release manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise TemplateError("release manifest must contain one JSON object")
    if value.get("schema_version") != RELEASE_MANIFEST_SCHEMA:
        raise TemplateError(
            "release manifest schema_version must be "
            f"{RELEASE_MANIFEST_SCHEMA!r}"
        )
    release_status = value.get("release_status")
    if release_status not in {"development", "stable"}:
        raise TemplateError(
            "release manifest release_status must be exactly 'development' "
            "or 'stable'"
        )

    release = value.get("skill_release")
    if not isinstance(release, str):
        raise TemplateError("release manifest skill_release must be a string")
    _validate_replacement_value("release manifest skill_release", release)

    source_commit = value.get("source_commit")
    if source_commit is not None and (
        not isinstance(source_commit, str)
        or SOURCE_COMMIT_RE.fullmatch(source_commit) is None
    ):
        raise TemplateError(
            "release manifest source_commit must be null or a lowercase 40-hex SHA"
        )

    bundle_digest = value.get("bundle_sha256")
    if bundle_digest is not None and (
        not isinstance(bundle_digest, str)
        or BUNDLE_DIGEST_RE.fullmatch(bundle_digest) is None
    ):
        raise TemplateError(
            "release manifest bundle_sha256 must be null or a lowercase 64-hex digest"
        )

    return value


def _release_replacements(manifest: Mapping[str, object]) -> dict[str, str]:
    """Return JSON-safe project-template replacements for one manifest."""

    source_commit = manifest.get("source_commit")
    bundle_digest = manifest.get("bundle_sha256")
    return {
        "{{AGENTDESK_SKILL_RELEASE}}": str(manifest["skill_release"]),
        "{{AGENTDESK_SOURCE_COMMIT_JSON}}": json.dumps(source_commit),
        "{{AGENTDESK_BUNDLE_SHA256_JSON}}": json.dumps(bundle_digest),
    }


def _compute_release_bundle_digest(skill_root: Path) -> str:
    """Use the same canonical-manifest digest as check_compatibility.py."""

    manifest_path = skill_root / "release-manifest.json"
    manifest = _load_release_manifest(manifest_path)
    normalized_manifest = dict(manifest)
    normalized_manifest["bundle_sha256"] = None
    try:
        canonical_manifest = (
            json.dumps(
                normalized_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise TemplateError(
            f"release manifest cannot be serialized as canonical JSON: {exc}"
        ) from exc
    entries: list[tuple[str, str]] = []
    try:
        for path in sorted(
            skill_root.rglob("*"),
            key=lambda item: item.relative_to(skill_root).as_posix(),
        ):
            relative = path.relative_to(skill_root)
            if path.is_symlink():
                raise TemplateError(
                    f"Skill bundle must not contain symlinks: {relative.as_posix()}"
                )
            if path.is_dir():
                continue
            if not path.is_file():
                raise TemplateError(
                    "Skill bundle contains an unsupported entry: "
                    f"{relative.as_posix()}"
                )
            effective_bytes = (
                canonical_manifest if path == manifest_path else path.read_bytes()
            )
            entries.append(
                (relative.as_posix(), hashlib.sha256(effective_bytes).hexdigest())
            )
    except OSError as exc:
        raise TemplateError(f"cannot verify Skill bundle digest: {exc}") from exc
    aggregate = hashlib.sha256()
    for relative, digest in entries:
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def _existing_agentdesk_markers(project: Path) -> tuple[Path, ...]:
    return tuple(
        marker
        for marker in AGENTDESK_MARKERS
        if (project / marker).exists() or (project / marker).is_symlink()
    )


def _load_existing_project_lock(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ProjectArgumentError(
            f"{PROJECT_LOCK_FILE.as_posix()} must be a regular JSON file"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectArgumentError(
            f"cannot read {PROJECT_LOCK_FILE.as_posix()}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ProjectArgumentError(
            f"{PROJECT_LOCK_FILE.as_posix()} must contain one JSON object"
        )
    return value


def _require_matching_project_lock(
    project: Path,
    project_id: str,
    manifest: Mapping[str, object],
) -> None:
    """Allow initializer replay only when the existing lock is exact."""

    lock = _load_existing_project_lock(project / PROJECT_LOCK_FILE)
    expected_compatibility = manifest.get("compatibility")
    expected_operation_compatibility = (
        {
            "reader": expected_compatibility.get("reader"),
            "writer": expected_compatibility.get("writer"),
        }
        if isinstance(expected_compatibility, dict)
        else None
    )
    checks = (
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
            (
                manifest.get("schema_set", {}).get("schemas")
                if isinstance(manifest.get("schema_set"), dict)
                else None
            ),
        ),
        ("runtime_adapter", lock.get("runtime_adapter"), manifest.get("runtime_adapter")),
        (
            "operation_compatibility",
            lock.get("operation_compatibility"),
            expected_operation_compatibility,
        ),
    )
    mismatches = [name for name, actual, expected in checks if actual != expected]
    if mismatches:
        raise ProjectArgumentError(
            f"{PROJECT_LOCK_FILE.as_posix()} does not exactly match this Skill "
            f"bundle ({', '.join(mismatches)}); no files were changed"
        )


def _emit(status: str, relative_path: Path, detail: str | None = None) -> None:
    """Print one stable, machine-readable-enough action line."""

    label = relative_path.as_posix() or "."
    suffix = f" ({detail})" if detail else ""
    print(f"{status} {label}{suffix}")


def _validate_replacement_value(option: str, value: str) -> str:
    """Reject values that would turn a one-line template field into many lines."""

    if not value.strip():
        raise ProjectArgumentError(f"{option} must not be empty")
    if any(ord(character) < 0x20 for character in value):
        raise ProjectArgumentError(f"{option} must be a single-line printable value")
    if '"' in value or "\\" in value:
        raise ProjectArgumentError(
            f"{option} must not contain a double quote or backslash because it is "
            "embedded in JSON-compatible project state"
        )
    return value


def _load_template(
    template_root: Path, replacements: Mapping[str, str]
) -> RenderedTemplate:
    """Validate and render every template file before making project changes."""

    if not template_root.exists():
        raise TemplateError(f"template directory does not exist: {template_root}")
    if template_root.is_symlink() or not template_root.is_dir():
        raise TemplateError(f"template path is not a regular directory: {template_root}")

    directories: list[Path] = []
    rendered_files: list[tuple[Path, bytes]] = []

    try:
        entries = sorted(
            template_root.rglob("*"),
            key=lambda item: item.relative_to(template_root).as_posix(),
        )
        for source in entries:
            relative_path = source.relative_to(template_root)
            if source.is_symlink():
                raise TemplateError(f"template symlinks are not supported: {relative_path}")
            if source.is_dir():
                directories.append(relative_path)
                continue
            if not source.is_file():
                raise TemplateError(f"unsupported template entry: {relative_path}")

            try:
                text = source.read_bytes().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TemplateError(
                    f"template file is not UTF-8 text: {relative_path}"
                ) from exc

            for placeholder, value in replacements.items():
                text = text.replace(placeholder, value)
            rendered_files.append((relative_path, text.encode("utf-8")))
    except OSError as exc:
        raise TemplateError(f"cannot read template: {exc}") from exc

    template_file_paths = {relative_path for relative_path, _ in rendered_files}
    for required_directory in REQUIRED_DIRECTORIES:
        if required_directory in template_file_paths:
            raise TemplateError(
                "template file conflicts with required directory: "
                f"{required_directory.as_posix()}"
            )

    return RenderedTemplate(tuple(directories), tuple(rendered_files))


def _blocking_component(project: Path, relative_path: Path) -> Path | None:
    """Return the first existing non-directory or symlink in a relative path."""

    current = project
    traversed = Path()
    for part in relative_path.parts:
        current /= part
        traversed /= part
        if current.is_symlink():
            return traversed
        if current.exists() and not current.is_dir():
            return traversed
    return None


def _ensure_directory(project: Path, relative_path: Path, dry_run: bool) -> bool:
    """Create a directory if possible, returning whether children may be added."""

    destination = project / relative_path
    blocker = _blocking_component(project, relative_path)
    if blocker is not None:
        _emit(
            "MANUAL_MERGE",
            relative_path,
            f"path is blocked by {blocker.as_posix()}",
        )
        return False
    if dry_run:
        if destination.exists():
            _emit("EXISTS", relative_path)
        else:
            _emit("CREATED", relative_path, "dry-run")
        return True

    try:
        created = _ensure_directory_safely(project, relative_path)
    except OSError as exc:
        _emit(
            "MANUAL_MERGE",
            relative_path,
            f"cannot safely create directory: {exc}",
        )
        return False
    _emit("CREATED" if created else "EXISTS", relative_path)
    return True


def _ensure_directory_safely(project: Path, relative_path: Path) -> bool:
    """Create a directory tree with no-follow traversal from project root."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    opened: list[tuple[int, Path]] = []
    descriptor = os.open(str(project), flags)
    opened.append((descriptor, project))
    created_target = False
    current_path = project
    try:
        for index, component in enumerate(relative_path.parts):
            created = False
            try:
                os.mkdir(component, 0o755, dir_fd=descriptor)
                created = True
            except FileExistsError:
                pass
            next_descriptor = os.open(
                component, flags, dir_fd=descriptor
            )
            current_path = current_path / component
            opened.append((next_descriptor, current_path))
            descriptor = next_descriptor
            if created and index == len(relative_path.parts) - 1:
                created_target = True
            if not all(
                _same_directory_inode(item, path)
                for item, path in opened
            ):
                raise OSError(
                    "project path changed during directory creation"
                )
        return created_target
    finally:
        for item, _path in reversed(opened):
            try:
                os.close(item)
            except OSError:
                pass


def _report_existing_file(relative_path: Path, destination: Path) -> None:
    """Report an existing destination without changing it."""

    if relative_path == Path("AGENTS.md"):
        _emit("MANUAL_MERGE", relative_path, "existing AGENTS.md was not replaced")
    elif destination.is_symlink() or not destination.is_file():
        _emit("MANUAL_MERGE", relative_path, "destination is not a regular file")
    else:
        _emit("EXISTS", relative_path)


def _create_template_file(
    project: Path, relative_path: Path, content: bytes, dry_run: bool
) -> None:
    """Create one rendered template file using exclusive-create semantics."""

    destination = project / relative_path
    if destination.exists() or destination.is_symlink():
        if (
            relative_path == Path("AGENTS.md")
            and destination.is_file()
            and not destination.is_symlink()
        ):
            try:
                if destination.read_bytes() == content:
                    _emit("EXISTS", relative_path)
                    return
            except OSError:
                pass
        _report_existing_file(relative_path, destination)
        return

    blocker = _blocking_component(project, relative_path.parent)
    if blocker is not None:
        _emit(
            "MANUAL_MERGE",
            relative_path,
            f"parent path is blocked by {blocker.as_posix()}",
        )
        return

    _emit("CREATED", relative_path, "dry-run" if dry_run else None)
    if dry_run:
        return

    try:
        _atomic_exclusive_create(project, relative_path, content)
    except FileExistsError:
        # Preserve the no-overwrite guarantee even if another process created
        # the destination between the existence check and the exclusive open.
        _report_existing_file(relative_path, destination)


def _same_directory_inode(descriptor: int, path: Path) -> bool:
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


def _read_project_regular_file(
    project: Path, relative_path: Path
) -> bytes:
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    file_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        file_flags |= os.O_NOFOLLOW
    opened: list[tuple[int, Path]] = []
    descriptor = os.open(str(project), directory_flags)
    opened.append((descriptor, project))
    file_descriptor: Optional[int] = None
    current_path = project
    try:
        for component in relative_path.parts[:-1]:
            descriptor = os.open(
                component, directory_flags, dir_fd=descriptor
            )
            current_path = current_path / component
            opened.append((descriptor, current_path))
        file_descriptor = os.open(
            relative_path.name, file_flags, dir_fd=descriptor
        )
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ProjectArgumentError(
                f"{relative_path.as_posix()} must be an ordinary file"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        if not all(
            _same_directory_inode(item, path)
            for item, path in opened
        ):
            raise ProjectArgumentError(
                "project path changed while reading "
                f"{relative_path.as_posix()}"
            )
        return b"".join(chunks)
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for item, _path in reversed(opened):
            os.close(item)


def _atomic_exclusive_create(
    project: Path, relative_path: Path, content: bytes
) -> None:
    """Publish one complete file without exposing partial bytes or symlinks."""

    destination = project / relative_path
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    opened_directories: list[tuple[int, Path]] = []
    current_descriptor = os.open(str(project), directory_flags)
    opened_directories.append((current_descriptor, project))
    try:
        for component in relative_path.parts[:-1]:
            current_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=current_descriptor,
            )
            opened_directories.append(
                (
                    current_descriptor,
                    project.joinpath(
                        *relative_path.parts[
                            : len(opened_directories)
                        ]
                    ),
                )
            )
    except OSError:
        for descriptor, _path in reversed(opened_directories):
            os.close(descriptor)
        raise
    parent_descriptor = current_descriptor

    def verify_directories() -> None:
        if not all(
            _same_directory_inode(descriptor, path)
            for descriptor, path in opened_directories
        ):
            raise ProjectArgumentError(
                "project path changed while publishing a template file"
            )

    temporary_name = ".{}.{}.tmp".format(
        destination.name, secrets.token_hex(12)
    )
    temporary_descriptor: Optional[int] = None
    temporary_stat: Optional[os.stat_result] = None
    published = False
    try:
        verify_directories()
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            file_flags,
            0o644,
            dir_fd=parent_descriptor,
        )
        temporary_stat = os.fstat(temporary_descriptor)
        if not stat.S_ISREG(temporary_stat.st_mode):
            raise OSError("temporary authority file is not regular")
        view = memoryview(content)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise OSError("short write while creating authority file")
            view = view[written:]
        os.fsync(temporary_descriptor)
        verify_directories()
        os.link(
            temporary_name,
            destination.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        published = True
        verify_directories()
        os.fsync(parent_descriptor)
    finally:
        if temporary_descriptor is not None:
            try:
                os.close(temporary_descriptor)
            except OSError:
                pass
        if (
            published
            and temporary_stat is not None
            and not all(
                _same_directory_inode(descriptor, path)
                for descriptor, path in opened_directories
            )
        ):
            try:
                current = os.stat(
                    destination.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    current.st_dev == temporary_stat.st_dev
                    and current.st_ino == temporary_stat.st_ino
                ):
                    os.unlink(
                        destination.name, dir_fd=parent_descriptor
                    )
                    os.fsync(parent_descriptor)
            except (FileNotFoundError, OSError):
                pass
        if temporary_stat is not None:
            try:
                current = os.stat(
                    temporary_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    current.st_dev == temporary_stat.st_dev
                    and current.st_ino == temporary_stat.st_ino
                ):
                    os.unlink(temporary_name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
            except (FileNotFoundError, OSError):
                pass
        for descriptor, _path in reversed(opened_directories):
            os.close(descriptor)


def _ensure_gitignore(project: Path, dry_run: bool) -> None:
    """Ensure the runtime directory has one exact ignore entry."""

    relative_path = Path(".gitignore")
    destination = project / relative_path

    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        _emit(
            "MANUAL_MERGE",
            relative_path,
            f"add {IGNORE_ENTRY} to the existing non-regular path",
        )
        return

    if not destination.exists():
        _emit("CREATED", relative_path, "dry-run" if dry_run else None)
        if not dry_run:
            try:
                _atomic_exclusive_create(
                    project,
                    relative_path,
                    f"{IGNORE_ENTRY}\n".encode("utf-8"),
                )
            except FileExistsError:
                _report_existing_file(relative_path, destination)
        return

    if dry_run:
        content = destination.read_bytes()
    else:
        content = _append_gitignore_safely(project, check_only=True)
    encoded_entry = IGNORE_ENTRY.encode("utf-8")
    if any(line.strip() == encoded_entry for line in content.splitlines()):
        _emit("EXISTS", relative_path, IGNORE_ENTRY)
        return

    _emit(
        "CREATED",
        relative_path,
        f"{'would append' if dry_run else 'appended'} {IGNORE_ENTRY}",
    )
    if dry_run:
        return

    _append_gitignore_safely(project, check_only=False)


def _append_gitignore_safely(project: Path, *, check_only: bool) -> bytes:
    """Read or append the ignore entry through a no-follow project fd."""

    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    root_descriptor = os.open(str(project), directory_flags)
    file_descriptor: Optional[int] = None
    try:
        if not _same_directory_inode(root_descriptor, project):
            raise ProjectArgumentError(
                "project path changed while updating .gitignore"
            )
        file_flags = os.O_RDONLY if check_only else os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        file_descriptor = os.open(
            ".gitignore", file_flags, dir_fd=root_descriptor
        )
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ProjectArgumentError(
                ".gitignore must remain an ordinary file"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        encoded_entry = IGNORE_ENTRY.encode("utf-8")
        if (
            not check_only
            and not any(
                line.strip() == encoded_entry
                for line in content.splitlines()
            )
        ):
            separator = (
                b""
                if not content or content.endswith((b"\n", b"\r"))
                else b"\n"
            )
            os.lseek(file_descriptor, 0, os.SEEK_END)
            view = memoryview(separator + encoded_entry + b"\n")
            while view:
                written = os.write(file_descriptor, view)
                if written <= 0:
                    raise OSError("short write while updating .gitignore")
                view = view[written:]
            os.fsync(file_descriptor)
        current = os.stat(
            ".gitignore",
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if (
            current.st_dev != metadata.st_dev
            or current.st_ino != metadata.st_ino
            or not _same_directory_inode(root_descriptor, project)
        ):
            raise ProjectArgumentError(
                ".gitignore changed while it was being updated"
            )
        return content
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        os.close(root_descriptor)


def _sorted_directories(paths: Iterable[Path]) -> list[Path]:
    """Return unique directories with parents before their descendants."""

    return sorted(set(paths), key=lambda path: (len(path.parts), path.as_posix()))


def _git_authority_identity(
    project: Path,
) -> Optional[tuple[int, int, int]]:
    """Return a stable identity for an ordinary .git directory or file."""

    if not project.exists():
        return None
    git_entry = project / ".git"
    try:
        metadata = git_entry.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProjectArgumentError(
            f"cannot inspect project Git authority at {git_entry}: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ProjectArgumentError(
            f"project .git authority must not be a symbolic link: {git_entry}"
        )
    if not (
        stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISREG(metadata.st_mode)
    ):
        raise ProjectArgumentError(
            f"project .git authority must be an ordinary directory or linked-"
            f"worktree file: {git_entry}"
        )
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
    )


def _git_root(project: Path) -> Path | None:
    if not project.is_dir():
        return None
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project),
                "rev-parse",
                "--show-toplevel",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
            env=_sanitized_git_environment(),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectArgumentError(f"cannot inspect Git repository: {exc}") from exc
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _ensure_git_repository(project: Path, init_git: bool, dry_run: bool) -> None:
    before = _git_authority_identity(project)
    root = _git_root(project)
    resolved_project = project.resolve()
    if root is not None:
        if root != resolved_project:
            raise ProjectArgumentError(
                f"project must be the Git worktree root; detected parent repository: {root}"
            )
        if _git_authority_identity(project) != before:
            raise ProjectArgumentError(
                "project .git authority changed during initialization preflight"
            )
        _emit("EXISTS", Path(".git"), "project is already a Git worktree")
        return
    if not init_git:
        _emit("WARNING", Path(".git"), "not a Git worktree; rerun with --init-git")
        return
    _emit("WOULD_CREATE" if dry_run else "CREATED", Path(".git"), "Git worktree")
    if dry_run:
        return
    try:
        result = subprocess.run(
            ["git", "init", "-b", "main", str(project)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
            env=_sanitized_git_environment(),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise InitializationError(f"cannot initialize Git repository: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise InitializationError(f"git init failed: {detail}")
    if _git_authority_identity(project) is None:
        raise InitializationError(
            "git init returned success without an ordinary .git authority"
        )


def _sanitized_git_environment() -> dict[str, str]:
    """Ignore caller-controlled Git routing, object, config, and replace state."""

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


def _isolated_python_environment() -> dict[str, str]:
    """Remove caller-controlled Python and Git routing from child tools."""

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


def _require_existing_lock_operation(project: Path, operation: str) -> None:
    """Run the same-bundle compatibility gate before initializer replay."""

    if (
        COMPATIBILITY_CHECKER.is_symlink()
        or not COMPATIBILITY_CHECKER.is_file()
    ):
        raise TemplateError(
            "same-bundle compatibility checker must be a regular file: "
            f"{COMPATIBILITY_CHECKER}"
        )
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(COMPATIBILITY_CHECKER),
                "--project",
                str(project),
                "--operation",
                operation,
                "--json",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
            env=_isolated_python_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectArgumentError(
            f"cannot run {operation} compatibility gate: {exc}"
        ) from exc
    if result.returncode == 0:
        return
    detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(payload, dict):
            messages = [
                str(item.get("message", "")).strip()
                for item in payload.get("errors", [])
                if isinstance(item, dict)
            ]
            detail = "; ".join(message for message in messages if message) or detail
    raise ProjectArgumentError(
        f"initializer {operation} compatibility gate failed: {detail}; "
        "no files were changed"
    )


def _writable_bundle_paths(root: Path) -> list[str]:
    writable: list[str] = []
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            raise TemplateError(f"Skill bundle must not contain symlinks: {path}")
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            raise TemplateError(f"cannot inspect Skill bundle mode: {exc}") from exc
        if mode & 0o222:
            writable.append(
                "." if path == root else path.relative_to(root).as_posix()
            )
    return writable


def _git_paths(project: Path, arguments: Sequence[str]) -> set[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project),
                *arguments,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=_sanitized_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectArgumentError(
            f"cannot inspect interrupted initialization: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
            or "unknown Git error"
        )
        raise ProjectArgumentError(
            f"cannot inspect interrupted initialization: {detail}"
        )
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    }


def _require_lock_only_bootstrap_resume(
    project: Path,
    expected_lock: bytes,
    template: RenderedTemplate,
    init_git: bool,
) -> None:
    """Allow only the narrow crash window after a complete first lock."""

    try:
        actual_lock = _read_project_regular_file(
            project, PROJECT_LOCK_FILE
        )
    except OSError as exc:
        raise ProjectArgumentError(
            f"cannot read interrupted project lock: {exc}"
        ) from exc
    if actual_lock != expected_lock:
        raise ProjectArgumentError(
            "interrupted bootstrap lock bytes do not exactly match this "
            "Skill bundle; no files were changed"
        )
    writable = _writable_bundle_paths(SKILL_ROOT)
    if writable:
        preview = ", ".join(writable[:8])
        raise ProjectArgumentError(
            "interrupted bootstrap resume requires an immutable read-only "
            f"Skill bundle; writable path(s): {preview}"
        )

    allowed = {
        relative.as_posix() for relative, _content in template.files
    }
    allowed.add(Path(".gitignore").as_posix())
    root = _git_root(project)
    if root is None:
        if not init_git:
            raise ProjectArgumentError(
                "lock-only bootstrap recovery outside Git requires "
                "--init-git and may contain only initializer-owned paths"
            )
        actual_files = {
            path.relative_to(project).as_posix()
            for path in project.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        unexpected = actual_files.difference(allowed)
    else:
        if root != project.resolve():
            raise ProjectArgumentError(
                f"project must be the Git worktree root; detected {root}"
            )
        if not _baseline_has_no_agentdesk_markers(project):
            raise ProjectArgumentError(
                "interrupted bootstrap recovery requires a baseline with "
                "no committed AgentDesk markers; use explicit legacy "
                "adoption instead"
            )
        staged = _git_paths(
            project,
            ("diff", "--cached", "--name-only", "-z", "--no-renames"),
        )
        if staged:
            raise ProjectArgumentError(
                "interrupted initializer never stages files; clear the "
                "index before bootstrap recovery"
            )
        changed = _git_paths(
            project,
            ("diff", "--name-only", "-z", "--no-renames"),
        )
        changed.update(
            _git_paths(
                project,
                ("ls-files", "--others", "--exclude-standard", "-z"),
            )
        )
        unexpected = changed.difference(allowed)
        head = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project),
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=_sanitized_git_environment(),
        )
        if head.returncode == 0:
            committed_lock = subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "-C",
                    str(project),
                    "cat-file",
                    "-e",
                    f"HEAD:{PROJECT_LOCK_FILE.as_posix()}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
                env=_sanitized_git_environment(),
            )
            if committed_lock.returncode == 0:
                raise ProjectArgumentError(
                    "lock-only bootstrap recovery requires an uncommitted "
                    "first lock; use normal initializer replay instead"
                )
    if unexpected:
        raise ProjectArgumentError(
            "interrupted bootstrap contains path(s) outside the initializer "
            "scope: " + ", ".join(sorted(unexpected)[:12])
        )


def _baseline_has_no_agentdesk_markers(project: Path) -> bool:
    root = _git_root(project)
    if root is None:
        return True
    head = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(project),
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
        env=_sanitized_git_environment(),
    )
    if head.returncode != 0:
        return True
    for marker in AGENTDESK_MARKERS:
        entry = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project),
                "ls-tree",
                "-z",
                "HEAD",
                "--",
                marker.as_posix(),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=_sanitized_git_environment(),
        )
        if entry.returncode != 0 or entry.stdout:
            return False
    return True


def _preflight_git_location(project: Path) -> None:
    """Reject a project path nested under another Git worktree before writing."""

    probe = project
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    root = _git_root(probe)
    if root is not None and root != project.resolve():
        raise ProjectArgumentError(
            f"project must be the Git worktree root; detected parent repository: {root}"
        )


def _current_project_head(project: Path) -> Optional[str]:
    root = _git_root(project)
    if root is None or root != project.resolve():
        return None
    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(project),
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
        env=_sanitized_git_environment(),
    )
    head = result.stdout.strip().lower()
    if result.returncode == 0 and SOURCE_COMMIT_RE.fullmatch(head):
        return head
    return None


PUBLIC_CONTROL_PLANE_PROBES = (
    Path("docs/pm/tasks/TC-000-r1-agentdesk-trackability-probe.md"),
    Path("docs/pm/reports/TC-000-r1-a1.md"),
    Path("docs/pm/acceptances/TC-000-r1-a1.md"),
    Path("docs/pm/events/EVT-AGENTDESK-TRACKABILITY-PROBE.yaml"),
    Path("docs/pm/outbox/MSG-AGENTDESK-TRACKABILITY-PROBE.yaml"),
    Path("docs/pm/consultations/C000/consultation.yaml"),
)


def _repository_template_paths(template: RenderedTemplate) -> list[str]:
    return sorted(
        {
            relative.as_posix()
            for relative, _content in template.files
            if relative.parts[:2] != (".agentdesk", "runtime")
        }
        | {Path(".gitignore").as_posix()}
    )


def _require_public_control_plane_trackable(
    project: Path, template: RenderedTemplate
) -> None:
    """Reject effective ignore rules that hide versioned project evidence."""

    root = _git_root(project)
    if root is None:
        return
    if root != project.resolve():
        raise ProjectArgumentError(
            f"project must be the Git worktree root; detected {root}"
        )
    candidates = sorted(
        set(_repository_template_paths(template))
        | {path.as_posix() for path in PUBLIC_CONTROL_PLANE_PROBES}
    )
    ignored: list[str] = []
    for repository_path in candidates:
        try:
            result = subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "-C",
                    str(project),
                    "check-ignore",
                    "--quiet",
                    "--no-index",
                    "--",
                    repository_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
                env=_sanitized_git_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InitializationError(
                f"cannot inspect public control-plane ignore rules: {exc}"
            ) from exc
        if result.returncode == 0:
            ignored.append(repository_path)
        elif result.returncode != 1:
            detail = (
                result.stderr.decode("utf-8", errors="replace").strip()
                or result.stdout.decode("utf-8", errors="replace").strip()
                or "unknown Git error"
            )
            raise InitializationError(
                "cannot inspect public control-plane ignore rules: "
                f"{detail}"
            )
    if ignored:
        raise ProjectArgumentError(
            "effective Git ignore rules exclude versioned AgentDesk "
            "control-plane paths; update the repository ignore rules before "
            "initialization: " + ", ".join(ignored[:12])
        )


def _repository_commit_required(
    project: Path, template: RenderedTemplate
) -> bool:
    root = _git_root(project)
    if root is None or root != project.resolve():
        return True
    head = _current_project_head(project)
    if head is None:
        return True
    repository_paths = _repository_template_paths(template)
    try:
        status = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--",
                *repository_paths,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=_sanitized_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InitializationError(
            f"cannot inspect prepared initialization diff: {exc}"
        ) from exc
    if status.returncode != 0:
        detail = (
            status.stderr.decode("utf-8", errors="replace").strip()
            or status.stdout.decode("utf-8", errors="replace").strip()
            or "unknown Git error"
        )
        raise InitializationError(
            f"cannot inspect prepared initialization diff: {detail}"
        )
    if status.stdout:
        return True

    # An untracked path hidden by an existing repository ignore rule does not
    # appear in ``git status``.  Prove every initializer-owned repository file
    # is an ordinary blob at the same HEAD and that its raw bytes equal the
    # worktree file before reporting the no-op replay as committed.
    try:
        tree = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(project),
                "ls-tree",
                "-z",
                head,
                "--",
                *repository_paths,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=_sanitized_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InitializationError(
            f"cannot inspect committed initialization files: {exc}"
        ) from exc
    if tree.returncode != 0:
        detail = (
            tree.stderr.decode("utf-8", errors="replace").strip()
            or tree.stdout.decode("utf-8", errors="replace").strip()
            or "unknown Git error"
        )
        raise InitializationError(
            f"cannot inspect committed initialization files: {detail}"
        )
    committed: dict[str, str] = {}
    for entry in (item for item in tree.stdout.split(b"\0") if item):
        if b"\t" not in entry:
            return True
        metadata, raw_path = entry.split(b"\t", 1)
        parts = metadata.split()
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if (
            len(parts) != 3
            or parts[0] not in {b"100644", b"100755"}
            or parts[1] != b"blob"
            or path in committed
        ):
            return True
        committed[path] = parts[2].decode("ascii", errors="strict")
    if set(committed) != set(repository_paths):
        return True

    for path in repository_paths:
        try:
            worktree_bytes = _read_project_regular_file(
                project, Path(path)
            )
            blob = subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "-C",
                    str(project),
                    "cat-file",
                    "blob",
                    committed[path],
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
                env=_sanitized_git_environment(),
            )
        except (
            OSError,
            ProjectArgumentError,
            subprocess.TimeoutExpired,
        ):
            return True
        if blob.returncode != 0 or blob.stdout != worktree_bytes:
            return True

    final_head = _current_project_head(project)
    if final_head != head:
        raise InitializationError(
            "project HEAD changed while verifying committed initialization "
            "files; retry from a stable checkout"
        )
    return False


def initialize(
    project: Path,
    project_id: str,
    mode: str,
    pm_holder_id: str,
    init_git: bool,
    dry_run: bool,
    allow_development_release: bool = False,
    migration_prepare: bool = False,
) -> dict[str, Any]:
    """Initialize ``project`` according to the requested adoption mode."""

    project_id = _validate_replacement_value("--project-id", project_id)
    pm_holder_id = _validate_replacement_value("--pm-holder-id", pm_holder_id)
    manifest = _load_release_manifest(RELEASE_MANIFEST)
    actual_bundle_digest = _compute_release_bundle_digest(SKILL_ROOT)
    declared_bundle_digest = manifest.get("bundle_sha256")
    if (
        declared_bundle_digest is not None
        and declared_bundle_digest != actual_bundle_digest
    ):
        raise TemplateError(
            "Skill bundle digest does not match release-manifest.json; "
            "no project files were changed"
        )
    lease_mode = "manual" if mode == "lite" else "timed"
    replacements = {
        "{{PROJECT_ID}}": project_id,
        "{{DATE}}": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "{{MODE}}": mode,
        "{{PM_HOLDER_ID}}": pm_holder_id,
        "{{LEASE_MODE}}": lease_mode,
    }
    replacements.update(_release_replacements(manifest))

    template = _load_template(TEMPLATE_ROOT, replacements)
    lock_templates = [
        (relative_path, content)
        for relative_path, content in template.files
        if relative_path == PROJECT_LOCK_FILE
    ]
    if len(lock_templates) != 1:
        raise TemplateError(
            "project template must contain exactly one "
            f"{PROJECT_LOCK_FILE.as_posix()}"
        )
    lock_relative, lock_content = lock_templates[0]

    if project.exists() and not project.is_dir():
        raise ProjectArgumentError(f"project path is not a directory: {project}")
    if project.is_symlink():
        raise ProjectArgumentError(f"project path must not be a symlink: {project}")
    git_authority_before = _git_authority_identity(project)
    _preflight_git_location(project)
    baseline_head = _current_project_head(project)

    markers = _existing_agentdesk_markers(project)
    lock_path = project / PROJECT_LOCK_FILE
    lock_exists = lock_path.exists() or lock_path.is_symlink()
    if markers and not lock_exists:
        marker_list = ", ".join(path.as_posix() for path in markers)
        _emit(
            "MANUAL_MERGE",
            PROJECT_LOCK_FILE,
            f"legacy-unpinned AgentDesk markers: {marker_list}",
        )
        raise ProjectArgumentError(
            "legacy-unpinned AgentDesk project requires an explicit version "
            "migration; no files were changed"
        )
    if lock_exists:
        _require_matching_project_lock(project, project_id, manifest)

    release_verified = (
        SOURCE_COMMIT_RE.fullmatch(str(manifest.get("source_commit") or ""))
        is not None
        and BUNDLE_DIGEST_RE.fullmatch(str(manifest.get("bundle_sha256") or ""))
        is not None
        and manifest.get("bundle_sha256") == actual_bundle_digest
        and manifest.get("release_status") == "stable"
    )
    development_override = (
        manifest.get("release_status") == "development"
        and allow_development_release
    )
    if (
        not dry_run
        and development_override
        and (markers or lock_exists)
    ):
        raise ProjectArgumentError(
            "--allow-development-release is limited to creating a fresh "
            "disposable test project; it cannot replay an existing "
            "AgentDesk control plane or version lock"
        )
    if not dry_run and not release_verified:
        if not development_override:
            raise ProjectArgumentError(
                "this Skill bundle has an unverified development release identity; "
                "no files were changed. Use a packaged stable release, or pass "
                "--allow-development-release only for a disposable test project."
            )
    if (
        not dry_run
        and _git_root(project) is None
        and not init_git
    ):
        raise ProjectArgumentError(
            "project is not an existing Git worktree; no files were changed. "
            "Obtain explicit approval to introduce version control, then rerun "
            "with --init-git."
        )

    if lock_exists and not dry_run and not development_override:
        # ``migrate`` proves there are no active attempts.  Normal replay must
        # additionally prove that the exact raw lock is already committed at
        # HEAD; explicit migration preparation is the only uncommitted path.
        state_path = project / STATE_FILE
        state_exists = state_path.exists() or state_path.is_symlink()
        if not state_exists:
            _require_lock_only_bootstrap_resume(
                project, lock_content, template, init_git
            )
        else:
            _require_existing_lock_operation(project, "migrate")
            if (
                not migration_prepare
                and _baseline_has_no_agentdesk_markers(project)
            ):
                _require_lock_only_bootstrap_resume(
                    project, lock_content, template, init_git
                )
            elif not migration_prepare:
                _require_existing_lock_operation(project, "write")

    if not project.exists() and not dry_run:
        project.mkdir(parents=True, exist_ok=False)

    _ensure_git_repository(project, init_git, dry_run)
    _require_public_control_plane_trackable(project, template)

    all_directories = _sorted_directories(
        (*template.directories, *REQUIRED_DIRECTORIES)
    )
    lock_parent_directories = (
        Path("docs"),
        Path("docs/pm"),
    )
    for relative_path in lock_parent_directories:
        _ensure_directory(project, relative_path, dry_run)

    _create_template_file(project, lock_relative, lock_content, dry_run)

    for relative_path in all_directories:
        if relative_path in lock_parent_directories:
            continue
        _ensure_directory(project, relative_path, dry_run)

    for relative_path, content in template.files:
        if relative_path == PROJECT_LOCK_FILE:
            continue
        _create_template_file(project, relative_path, content, dry_run)

    _ensure_gitignore(project, dry_run)
    _require_public_control_plane_trackable(project, template)
    git_authority_after = _git_authority_identity(project)
    if (
        not dry_run
        and git_authority_before is not None
        and git_authority_after != git_authority_before
    ):
        raise ProjectArgumentError(
            "project .git authority changed while initialization was being "
            "prepared; retry from a stable checkout"
        )
    state_sha256: Optional[str] = None
    if not dry_run:
        try:
            state_sha256 = hashlib.sha256(
                _read_project_regular_file(project, STATE_FILE)
            ).hexdigest()
        except OSError as exc:
            raise InitializationError(
                f"cannot read prepared canonical state: {exc}"
            ) from exc
    commit_required = (
        False
        if dry_run
        else _repository_commit_required(project, template)
    )
    return {
        "baseline_head": baseline_head,
        "state_sha256": state_sha256,
        "commit_required": commit_required,
    }


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""

    parser = argparse.ArgumentParser(
        description="Initialize a task-card multi-agent development project.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--project",
        default=".",
        help="project directory to initialize",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="stable project identifier (defaults to the project directory name)",
    )
    parser.add_argument(
        "--mode",
        choices=("lite", "standard", "automated"),
        default="standard",
        help="workflow adoption level",
    )
    parser.add_argument(
        "--pm-holder-id",
        default="pm-session-bootstrap",
        help="initial PM lease holder identifier",
    )
    parser.add_argument(
        "--init-git",
        action="store_true",
        help="initialize a new main-branch Git worktree when none exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report actions without writing anything",
    )
    parser.add_argument(
        "--allow-development-release",
        action="store_true",
        help=(
            "allow an unverified development manifest only for a disposable "
            "test project; never use this to adopt or migrate an existing project"
        ),
    )
    parser.add_argument(
        "--migration-prepare",
        action="store_true",
        help=(
            "resume an explicitly approved initialization/adoption while its "
            "exact stable project lock is not yet committed; requires no "
            "active attempts"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; return zero on a prepared or dry-run initialization."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        # ``absolute`` normalizes the location without resolving the final
        # component, so initialize() can still reject a symlink project root.
        project = Path(args.project).expanduser().absolute()
        project_id = args.project_id if args.project_id is not None else project.name
        initialization = initialize(
            project=project,
            project_id=project_id,
            mode=args.mode,
            pm_holder_id=args.pm_holder_id,
            init_git=args.init_git,
            dry_run=args.dry_run,
            allow_development_release=args.allow_development_release,
            migration_prepare=args.migration_prepare,
        )
    except InitializationError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR initialization failed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("STATUS plan")
        print("COMPLETE false")
    else:
        commit_required = bool(initialization["commit_required"])
        print("STATUS prepared" if commit_required else "STATUS committed")
        print("COMPLETE false")
        print(
            "COMMIT_REQUIRED {}".format(
                str(commit_required).lower()
            )
        )
        baseline_head = initialization.get("baseline_head")
        state_sha256 = initialization.get("state_sha256")
        if (
            commit_required
            and baseline_head is not None
            and state_sha256 is not None
        ):
            command = [
                sys.executable,
                "-I",
                str(
                    SKILL_ROOT
                    / "scripts"
                    / "adopt_legacy_project.py"
                ),
                "--project",
                str(project),
                "--verify-prepared",
                "--expected-head",
                baseline_head,
                "--expected-state-sha256",
                state_sha256,
                "--json",
            ]
            print(f"PROJECT_HEAD {baseline_head}")
            print(f"STATE_SHA256 {state_sha256}")
            print(
                "POST_COMMIT_COMMAND {}".format(shlex.join(command))
            )
        elif not commit_required:
            print("DURABLE_VERIFICATION_REQUIRED true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
