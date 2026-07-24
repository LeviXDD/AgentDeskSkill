#!/usr/bin/env python3
"""Build one immutable, content-addressed AgentDesk Skill release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence


RELEASE_MANIFEST_SCHEMA = "agentdesk.release-manifest/v1"
BUNDLE_MANIFEST_SCHEMA = "agentdesk.immutable-bundle-manifest/v1"
DIGEST_SCOPE = "all-files-with-canonical-release-manifest"
RELEASE_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
VOLATILE_NAMES = frozenset({".DS_Store", "__pycache__"})
VOLATILE_SUFFIXES = frozenset({".pyc", ".pyo"})


class BuildError(Exception):
    """Raised when a source tree cannot become a trustworthy release."""


def _run_git(repository: Path, arguments: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repository),
                *arguments,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            env={
                key: value
                for key, value in os.environ.items()
                if not key.startswith("GIT_")
            }
            | {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
            },
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"cannot inspect Git source: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise BuildError(f"Git inspection failed: {detail}")
    return result.stdout


def _run_git_bytes(repository: Path, arguments: Sequence[str]) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repository),
                *arguments,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            env={
                key: value
                for key, value in os.environ.items()
                if not key.startswith("GIT_")
            }
            | {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
            },
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"cannot read Git source object: {exc}") from exc
    if result.returncode != 0:
        detail = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
            or "unknown Git error"
        )
        raise BuildError(f"Git object read failed: {detail}")
    return result.stdout


def _reject_symlink_ancestors(path: Path, label: str) -> None:
    """Reject every existing symlink component in one lexical path."""

    candidate = path.expanduser().absolute()
    for component in reversed((candidate, *candidate.parents)):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BuildError(
                f"cannot inspect {label} path component {component}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise BuildError(
                f"{label} path must not contain symbolic links: {component}"
            )


def _resolve_future_path(path: Path, label: str) -> Path:
    """Resolve an absent path through its nearest existing parent safely."""

    candidate = path.expanduser().absolute()
    _reject_symlink_ancestors(candidate, label)
    missing: list[str] = []
    existing = candidate
    while not existing.exists():
        if existing == existing.parent:
            raise BuildError(f"{label} has no existing filesystem ancestor")
        missing.append(existing.name)
        existing = existing.parent
    if not existing.is_dir():
        raise BuildError(
            f"{label} existing ancestor is not a directory: {existing}"
        )
    try:
        resolved = existing.resolve(strict=True)
    except OSError as exc:
        raise BuildError(f"cannot resolve {label}: {exc}") from exc
    return resolved.joinpath(*reversed(missing))


def _tracked_source_files(
    repository: Path, relative_source: Path, source_commit: str
) -> dict[str, tuple[str, str]]:
    output = _run_git(
        repository,
        (
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            source_commit,
            "--",
            relative_source.as_posix(),
        ),
    )
    prefix = relative_source.as_posix().rstrip("/") + "/"
    tracked: dict[str, tuple[str, str]] = {}
    for raw_entry in output.split("\0"):
        if not raw_entry:
            continue
        if "\t" not in raw_entry:
            raise BuildError(f"Git returned malformed tree entry: {raw_entry!r}")
        metadata, repository_path = raw_entry.split("\t", 1)
        metadata_parts = metadata.split()
        if len(metadata_parts) != 3:
            raise BuildError(f"Git returned malformed tree metadata: {metadata!r}")
        mode, object_type, object_id = metadata_parts
        if not repository_path.startswith(prefix):
            raise BuildError(
                "Git returned a tracked path outside the Skill source: "
                f"{repository_path}"
            )
        relative = repository_path[len(prefix) :]
        if (
            not relative
            or relative.startswith("/")
            or ".." in Path(relative).parts
        ):
            raise BuildError(f"Git returned an unsafe Skill path: {relative!r}")
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise BuildError(
                "Skill source may contain only regular Git blobs with mode "
                f"100644 or 100755: {relative} ({mode} {object_type})"
            )
        if relative in tracked:
            raise BuildError(f"Git returned duplicate Skill path: {relative}")
        tracked[relative] = (mode, object_id)
    if not tracked:
        raise BuildError("Skill source contains no Git-tracked files")
    return tracked


def _validate_source(
    source: Path,
) -> tuple[Path, str, dict[str, tuple[str, str]]]:
    if source.is_symlink() or not source.is_dir():
        raise BuildError(f"source must be a regular Skill directory: {source}")
    repository_text = _run_git(source, ("rev-parse", "--show-toplevel")).strip()
    repository = Path(repository_text).resolve()
    try:
        relative_source = source.resolve().relative_to(repository)
    except ValueError as exc:
        raise BuildError("source must be inside its detected Git repository") from exc

    head = _run_git(repository, ("rev-parse", "HEAD")).strip().lower()
    if COMMIT_RE.fullmatch(head) is None:
        raise BuildError("Git HEAD is not a full 40-hex commit")
    dirty = _run_git(
        repository,
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            relative_source.as_posix(),
        ),
    )
    if dirty.strip():
        raise BuildError(
            "Skill source has tracked or untracked changes; commit the exact "
            "release contents before packaging"
        )

    actual_files: set[str] = set()
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if path.is_symlink():
            raise BuildError(f"source contains a symlink: {relative.as_posix()}")
        if not path.is_dir() and not path.is_file():
            raise BuildError(
                f"source contains an unsupported entry: {relative.as_posix()}"
            )
        if (
            any(part in VOLATILE_NAMES for part in relative.parts)
            or path.suffix in VOLATILE_SUFFIXES
        ):
            raise BuildError(
                f"source contains a volatile release file: {relative.as_posix()}"
            )
        if path.is_file():
            actual_files.add(relative.as_posix())

    tracked_files = _tracked_source_files(repository, relative_source, head)
    untracked_files = sorted(actual_files - set(tracked_files))
    missing_files = sorted(set(tracked_files) - actual_files)
    if untracked_files or missing_files:
        details: list[str] = []
        if untracked_files:
            details.append(
                "untracked/ignored file(s): " + ", ".join(untracked_files)
            )
        if missing_files:
            details.append("missing tracked file(s): " + ", ".join(missing_files))
        raise BuildError(
            "Skill source files must exactly equal the Git-tracked file set; "
            + "; ".join(details)
        )
    return repository, head, tracked_files


def _materialize_git_tree(
    repository: Path,
    tracked_files: dict[str, tuple[str, str]],
    destination: Path,
) -> None:
    """Write the exact committed blobs, never filtered worktree bytes."""

    for relative, (mode, object_id) in sorted(tracked_files.items()):
        path = destination.joinpath(*Path(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            _run_git_bytes(repository, ("cat-file", "blob", object_id))
        )
        path.chmod(0o755 if mode == "100755" else 0o644)


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"missing regular release manifest: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read release manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise BuildError("release manifest must contain one JSON object")
    if value.get("schema_version") != RELEASE_MANIFEST_SCHEMA:
        raise BuildError(
            f"release manifest schema must be {RELEASE_MANIFEST_SCHEMA}"
        )
    if value.get("digest_scope") != DIGEST_SCOPE:
        raise BuildError(f"release manifest digest_scope must be {DIGEST_SCOPE}")
    return value


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
        raise BuildError(
            f"release manifest cannot be serialized as canonical JSON: {exc}"
        ) from exc


def _bundle_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise BuildError(
                f"release contains a symlink: {path.relative_to(root).as_posix()}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise BuildError(
                "release contains an unsupported entry: "
                f"{path.relative_to(root).as_posix()}"
            )
        relative = path.relative_to(root)
        if (
            any(part in VOLATILE_NAMES for part in relative.parts)
            or path.suffix in VOLATILE_SUFFIXES
        ):
            raise BuildError(
                f"release contains a volatile file: {relative.as_posix()}"
            )
        yield path


def bundle_digest(root: Path) -> tuple[str, list[dict[str, Any]]]:
    """Digest every file, canonicalizing only the manifest's digest field."""

    aggregate = hashlib.sha256()
    entries: list[dict[str, Any]] = []
    manifest_path = root / "release-manifest.json"
    manifest = _load_manifest(manifest_path)
    for path in _bundle_files(root):
        data = (
            _canonical_manifest_bytes(manifest)
            if path == manifest_path
            else path.read_bytes()
        )
        relative = path.relative_to(root).as_posix()
        file_digest = hashlib.sha256(data).hexdigest()
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(file_digest.encode("ascii"))
        aggregate.update(b"\n")
        entries.append(
            {
                "path": relative,
                "bytes": len(data),
                "sha256": file_digest,
            }
        )
    return aggregate.hexdigest(), entries


def _make_read_only(root: Path) -> None:
    files = [path for path in root.rglob("*") if path.is_file()]
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for path in files:
        current = stat.S_IMODE(path.stat().st_mode)
        path.chmod(0o555 if current & 0o111 else 0o444)
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555)
    root.chmod(0o555)


def _verify_read_only_release(
    root: Path,
    *,
    skill_root: Path,
    tracked_files: dict[str, tuple[str, str]],
) -> None:
    """Recheck immutability and executable modes before publication."""

    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"release contains a symlink after sealing: {path}")
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            raise BuildError(f"cannot inspect sealed release path {path}: {exc}") from exc
        if mode & 0o222:
            raise BuildError(f"sealed release path remains writable: {path}")
    for relative, (git_mode, _) in tracked_files.items():
        path = skill_root.joinpath(*Path(relative).parts)
        try:
            executable = bool(stat.S_IMODE(path.stat().st_mode) & 0o111)
        except OSError as exc:
            raise BuildError(
                f"cannot inspect packaged Git mode for {relative}: {exc}"
            ) from exc
        expected_executable = git_mode == "100755"
        if executable != expected_executable:
            raise BuildError(
                f"packaged executable mode changed for {relative}: "
                f"expected {git_mode}"
            )


def _validate_packaged_manifest(skill_root: Path) -> None:
    checker = skill_root / "scripts" / "check_compatibility.py"
    if checker.is_symlink() or not checker.is_file():
        raise BuildError("release is missing scripts/check_compatibility.py")
    with tempfile.TemporaryDirectory(prefix="agentdesk-release-validation-") as temp:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(checker),
                    "--project",
                    temp,
                    "--operation",
                    "read",
                    "--json",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BuildError(f"cannot validate packaged release: {exc}") from exc
    if result.returncode != 0:
        detail = result.stdout.strip() or result.stderr.strip() or "unknown error"
        raise BuildError(f"packaged release compatibility check failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BuildError(
            "packaged release compatibility checker did not emit JSON"
        ) from exc
    release = payload.get("release") if isinstance(payload, dict) else None
    if (
        not isinstance(release, dict)
        or release.get("skill_release")
        != json.loads(
            (skill_root / "release-manifest.json").read_text(encoding="utf-8")
        ).get("skill_release")
        or release.get("identity_verified") is not True
    ):
        raise BuildError(
            "packaged release checker did not attest the verified release identity"
        )


def _remove_tree(root: Path) -> None:
    if root.is_symlink():
        root.unlink()
        return
    root.chmod(0o700)
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        path.chmod(0o700 if path.is_dir() else 0o600)
    shutil.rmtree(root)


def _path_identity(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    return metadata.st_dev, metadata.st_ino


def _has_path_identity(path: Path, identity: tuple[int, int]) -> bool:
    try:
        return _path_identity(path) == identity
    except OSError:
        return False


def build_release(
    *,
    source: Path,
    destination: Path,
    release_id: str,
) -> dict[str, Any]:
    source = source.expanduser().absolute()
    requested_destination = destination.expanduser().absolute()
    if RELEASE_ID_RE.fullmatch(release_id) is None:
        raise BuildError(
            "release ID must use letters, digits, dots, underscores, or hyphens"
        )
    _reject_symlink_ancestors(source, "source")
    repository, source_commit, tracked_files = _validate_source(source)
    destination = _resolve_future_path(requested_destination, "destination")
    try:
        source_root = source.resolve(strict=True)
        if os.path.commonpath((str(source_root), str(destination))) == str(
            source_root
        ):
            raise BuildError("destination must not be inside the Skill source")
    except ValueError as exc:
        raise BuildError("source and destination cannot be compared safely") from exc
    if destination.exists() or destination.is_symlink():
        raise BuildError(f"destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(destination, "destination")
    safe_parent = destination.parent.resolve(strict=True)
    destination = safe_parent / destination.name
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{release_id}.", dir=str(destination.parent))
    )
    temporary_identity = _path_identity(temporary)
    installed_by_us = False
    try:
        skill_destination = temporary / "skills" / "agentdesk"
        skill_destination.parent.mkdir(parents=True)
        skill_destination.mkdir()
        _materialize_git_tree(repository, tracked_files, skill_destination)
        copied_files = {
            path.relative_to(skill_destination).as_posix()
            for path in _bundle_files(skill_destination)
        }
        if copied_files != set(tracked_files):
            raise BuildError(
                "copied release files changed after source validation; "
                "refusing a non-Git release snapshot"
            )
        checked_repository, checked_head, checked_files = _validate_source(source)
        if (
            checked_repository != repository
            or checked_head != source_commit
            or checked_files != tracked_files
        ):
            raise BuildError("Skill Git source changed while packaging")

        manifest_path = skill_destination / "release-manifest.json"
        manifest = _load_manifest(manifest_path)
        manifest.update(
            {
                "skill_release": release_id,
                "release_status": "stable",
                "source_commit": source_commit,
                "bundle_sha256": None,
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        digest, entries = bundle_digest(skill_destination)
        manifest["bundle_sha256"] = digest
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        verified_digest, verified_entries = bundle_digest(skill_destination)
        if verified_digest != digest or verified_entries != entries:
            raise BuildError("release contents changed while computing the bundle digest")
        bundle_manifest = {
            "schema_version": BUNDLE_MANIFEST_SCHEMA,
            "release_id": release_id,
            "source_commit": source_commit,
            "bundle": {
                "root": "skills/agentdesk",
                "digest_scope": DIGEST_SCOPE,
                "sha256": digest,
                "file_count": len(entries),
                "files": entries,
            },
        }
        (temporary / "bundle-manifest.json").write_text(
            json.dumps(bundle_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        _make_read_only(temporary)
        sealed_digest, sealed_entries = bundle_digest(skill_destination)
        if sealed_digest != digest or sealed_entries != entries:
            raise BuildError(
                "sealed release contents changed before publication"
            )
        _verify_read_only_release(
            temporary,
            skill_root=skill_destination,
            tracked_files=tracked_files,
        )
        _validate_packaged_manifest(skill_destination)

        if destination.exists() or destination.is_symlink():
            raise BuildError(f"destination appeared while packaging: {destination}")
        # Darwin refuses to rename a directory whose own mode is 0555.  The
        # complete tree is sealed and verified above; briefly restore only the
        # release root's owner-write bit for the same-filesystem rename.  All
        # files and descendant directories remain read-only throughout.
        temporary.chmod(0o755)
        os.rename(temporary, destination)
        installed_by_us = True
        if not _has_path_identity(destination, temporary_identity):
            raise BuildError(
                "published destination identity changed before final sealing"
            )
        destination.chmod(0o555)
        return bundle_manifest
    except Exception:
        if _has_path_identity(temporary, temporary_identity):
            _remove_tree(temporary)
        if (
            installed_by_us
            and _has_path_identity(destination, temporary_identity)
            and destination.is_dir()
            and not destination.is_symlink()
        ):
            _remove_tree(destination)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build an immutable AgentDesk release from a clean committed Skill tree."
        )
    )
    parser.add_argument("--source", required=True, help="source skills/agentdesk path")
    parser.add_argument("--destination", required=True, help="new release directory")
    parser.add_argument("--release-id", required=True, help="immutable release identifier")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = build_release(
            source=Path(args.source),
            destination=Path(args.destination),
            release_id=args.release_id,
        )
    except (BuildError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "release_id": result["release_id"],
                "source_commit": result["source_commit"],
                "bundle_sha256": result["bundle"]["sha256"],
                "file_count": result["bundle"]["file_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
