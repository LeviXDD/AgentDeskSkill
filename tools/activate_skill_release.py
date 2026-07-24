#!/usr/bin/env python3
"""Verify and atomically activate one immutable AgentDesk Skill release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence


RELEASE_MANIFEST_SCHEMA = "agentdesk.release-manifest/v1"
DIGEST_SCOPE = "all-files-with-canonical-release-manifest"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
VOLATILE_NAMES = frozenset({".DS_Store", "__pycache__"})
VOLATILE_SUFFIXES = frozenset({".pyc", ".pyo"})


class ActivationError(Exception):
    """Raised when a release or install target is unsafe to activate."""


class DuplicateKeyError(ValueError):
    """Raised when JSON shadowing would discard signed information."""


def _reject_duplicate_keys(
    pairs: Iterable[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKeyError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ActivationError(f"{label} must be a regular file: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        DuplicateKeyError,
    ) as exc:
        raise ActivationError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ActivationError(f"{label} must contain one JSON object")
    return value


def _reject_symlink_ancestors(path: Path, label: str) -> None:
    """Reject every existing symlink component in one lexical path."""

    candidate = path.expanduser().absolute()
    for component in reversed((candidate, *candidate.parents)):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ActivationError(
                f"cannot inspect {label} path component {component}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise ActivationError(
                f"{label} path must not contain symbolic links: {component}"
            )


def _resolve_future_directory(path: Path, label: str) -> Path:
    candidate = path.expanduser().absolute()
    _reject_symlink_ancestors(candidate, label)
    missing: list[str] = []
    existing = candidate
    while not existing.exists():
        if existing == existing.parent:
            raise ActivationError(
                f"{label} has no existing filesystem ancestor"
            )
        missing.append(existing.name)
        existing = existing.parent
    if not existing.is_dir():
        raise ActivationError(
            f"{label} existing ancestor is not a directory: {existing}"
        )
    try:
        resolved = existing.resolve(strict=True)
    except OSError as exc:
        raise ActivationError(f"cannot resolve {label}: {exc}") from exc
    return resolved.joinpath(*reversed(missing))


def _bundle_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ActivationError(
                f"release contains a symlink: {path.relative_to(root).as_posix()}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise ActivationError(
                "release contains an unsupported entry: "
                f"{path.relative_to(root).as_posix()}"
            )
        relative = path.relative_to(root)
        if (
            any(part in VOLATILE_NAMES for part in relative.parts)
            or path.suffix in VOLATILE_SUFFIXES
        ):
            raise ActivationError(
                f"release contains a volatile file: {relative.as_posix()}"
            )
        yield path


def _canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    normalized = dict(manifest)
    normalized["bundle_sha256"] = None
    try:
        return (
            json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise ActivationError(
            f"release manifest cannot be serialized as canonical JSON: {exc}"
        ) from exc


def _bundle_digest(root: Path, manifest: dict[str, Any]) -> str:
    aggregate = hashlib.sha256()
    manifest_path = root / "release-manifest.json"
    for path in _bundle_files(root):
        relative = path.relative_to(root).as_posix()
        effective_bytes = (
            _canonical_manifest_bytes(manifest)
            if path == manifest_path
            else path.read_bytes()
        )
        file_digest = hashlib.sha256(effective_bytes).hexdigest()
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(file_digest.encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def _load_and_verify_release(
    release_directory: Path, expected_release: str | None
) -> tuple[Path, dict[str, Any]]:
    if release_directory.is_symlink() or not release_directory.is_dir():
        raise ActivationError(
            f"release directory must be a regular directory: {release_directory}"
        )
    skill_root = release_directory / "skills" / "agentdesk"
    if skill_root.is_symlink() or not skill_root.is_dir():
        raise ActivationError(f"release has no regular Skill root: {skill_root}")
    manifest_path = skill_root / "release-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ActivationError(f"release has no regular manifest: {manifest_path}")
    manifest = _load_json_object(manifest_path, "release manifest")
    if manifest.get("schema_version") != RELEASE_MANIFEST_SCHEMA:
        raise ActivationError(
            f"release manifest schema must be {RELEASE_MANIFEST_SCHEMA}"
        )
    release_id = manifest.get("skill_release")
    if not isinstance(release_id, str) or not release_id:
        raise ActivationError("release manifest skill_release must be non-empty")
    if expected_release is not None and release_id != expected_release:
        raise ActivationError(
            f"release ID mismatch: expected {expected_release!r}, got {release_id!r}"
        )
    if manifest.get("release_status") != "stable":
        raise ActivationError("only a stable release may be globally activated")
    source_commit = manifest.get("source_commit")
    digest = manifest.get("bundle_sha256")
    if not isinstance(source_commit, str) or COMMIT_RE.fullmatch(source_commit) is None:
        raise ActivationError("release source_commit is not a lowercase 40-hex SHA")
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise ActivationError("release bundle_sha256 is not a lowercase 64-hex digest")
    if manifest.get("digest_scope") != DIGEST_SCOPE:
        raise ActivationError(f"release digest_scope must be {DIGEST_SCOPE}")
    actual_digest = _bundle_digest(skill_root, manifest)
    if actual_digest != digest:
        raise ActivationError(
            f"release bundle digest mismatch: expected {digest}, got {actual_digest}"
        )

    for path in (release_directory, *release_directory.rglob("*")):
        if path.is_symlink():
            raise ActivationError(f"release contains a symlink: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o222:
            raise ActivationError(f"release path is writable: {path}")
    return skill_root, manifest


def _checker_environment() -> dict[str, str]:
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
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


def _validate_packaged_checker(
    skill_root: Path, manifest: dict[str, Any]
) -> None:
    checker = skill_root / "scripts" / "check_compatibility.py"
    if checker.is_symlink() or not checker.is_file():
        raise ActivationError(
            "release is missing regular scripts/check_compatibility.py"
        )
    with tempfile.TemporaryDirectory(
        prefix="agentdesk-activation-validation-"
    ) as project:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(checker),
                    "--project",
                    project,
                    "--operation",
                    "read",
                    "--json",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=_checker_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ActivationError(
                f"cannot run packaged compatibility checker: {exc}"
            ) from exc
    if result.returncode != 0:
        detail = result.stdout.strip() or result.stderr.strip() or "unknown error"
        raise ActivationError(
            f"packaged compatibility checker rejected its release: {detail}"
        )
    try:
        payload = json.loads(
            result.stdout, object_pairs_hook=_reject_duplicate_keys
        )
    except (json.JSONDecodeError, DuplicateKeyError) as exc:
        raise ActivationError(
            f"packaged compatibility checker emitted invalid JSON: {exc}"
        ) from exc
    release = payload.get("release") if isinstance(payload, dict) else None
    if (
        not isinstance(release, dict)
        or release.get("skill_release") != manifest["skill_release"]
        or release.get("source_commit") != manifest["source_commit"]
        or release.get("bundle_sha256") != manifest["bundle_sha256"]
        or release.get("identity_verified") is not True
    ):
        raise ActivationError(
            "packaged compatibility checker did not attest the exact "
            "verified release identity"
        )


def _path_identity(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    return metadata.st_dev, metadata.st_ino


def _restore_entry_no_clobber(backup: Path, destination: Path) -> bool:
    """Restore one preserved entry only when the destination remains absent."""

    try:
        if backup.is_symlink():
            destination.symlink_to(
                os.readlink(backup),
                target_is_directory=True,
            )
        elif backup.is_file():
            os.link(backup, destination, follow_symlinks=False)
        else:
            return False
    except (FileExistsError, OSError):
        return False
    backup.unlink()
    return True


def _switch_install_link(
    install_link: Path,
    skill_root: Path,
    previous_target: str | None,
) -> None:
    """Create or replace the install link without overwriting a raced entry."""

    if previous_target is None:
        try:
            install_link.symlink_to(skill_root, target_is_directory=True)
        except OSError as exc:
            raise ActivationError(
                "install target appeared during activation; no raced entry "
                "was overwritten"
            ) from exc
        return

    expected_identity = _path_identity(install_link)
    descriptor, backup_name = tempfile.mkstemp(
        prefix=f".{install_link.name}.previous.",
        dir=str(install_link.parent),
    )
    os.close(descriptor)
    backup = Path(backup_name)
    placeholder_identity = _path_identity(backup)
    moved_previous = False
    try:
        if (
            not install_link.is_symlink()
            or _path_identity(install_link) != expected_identity
            or os.readlink(install_link) != previous_target
        ):
            raise ActivationError(
                "install target changed before activation; no entry was "
                "overwritten"
            )
        os.replace(install_link, backup)
        moved_previous = True
        if (
            not backup.is_symlink()
            or _path_identity(backup) != expected_identity
            or os.readlink(backup) != previous_target
        ):
            _restore_entry_no_clobber(backup, install_link)
            raise ActivationError(
                "install target changed during activation; the raced entry "
                "was preserved"
            )
        try:
            install_link.symlink_to(skill_root, target_is_directory=True)
        except OSError as exc:
            restored = _restore_entry_no_clobber(backup, install_link)
            recovery = (
                "previous link restored"
                if restored
                else f"previous link preserved at {backup}"
            )
            raise ActivationError(
                "install target appeared during activation; no raced entry "
                f"was overwritten ({recovery})"
            ) from exc
        backup.unlink()
        moved_previous = False
    finally:
        if not moved_previous:
            try:
                if (
                    backup.exists()
                    and not backup.is_symlink()
                    and _path_identity(backup) == placeholder_identity
                ):
                    backup.unlink()
            except OSError:
                pass


def activate(
    *,
    release_directory: Path,
    install_link: Path,
    expected_release: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    release_directory = release_directory.expanduser().absolute()
    requested_install_link = install_link.expanduser().absolute()
    _reject_symlink_ancestors(release_directory, "release directory")
    release_directory = release_directory.resolve(strict=True)
    install_parent = _resolve_future_directory(
        requested_install_link.parent, "install parent"
    )
    install_link = install_parent / requested_install_link.name
    skill_root, manifest = _load_and_verify_release(
        release_directory, expected_release
    )
    _validate_packaged_checker(skill_root, manifest)

    previous_target: str | None = None
    if install_link.is_symlink():
        previous_target = os.readlink(install_link)
    elif install_link.exists():
        raise ActivationError(
            f"install target exists and is not a symlink: {install_link}"
        )

    result = {
        "release_id": manifest["skill_release"],
        "source_commit": manifest["source_commit"],
        "bundle_sha256": manifest["bundle_sha256"],
        "skill_root": str(skill_root),
        "install_link": str(install_link),
        "previous_target": previous_target,
        "activated": not dry_run,
    }
    if dry_run:
        return result

    install_link.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(install_link.parent, "install parent")
    resolved_parent = install_link.parent.resolve(strict=True)
    install_link = resolved_parent / install_link.name
    _switch_install_link(install_link, skill_root, previous_target)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify and atomically switch the global AgentDesk Skill link."
    )
    parser.add_argument("--release-directory", required=True)
    parser.add_argument(
        "--install-link",
        default="~/.codex/skills/agentdesk",
        help="global AgentDesk Skill symlink",
    )
    parser.add_argument("--expected-release")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify and report without switching the install link",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = activate(
            release_directory=Path(args.release_directory),
            install_link=Path(args.install_link),
            expected_release=args.expected_release,
            dry_run=args.dry_run,
        )
    except (ActivationError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
