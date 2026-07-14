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
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


IGNORE_ENTRY = ".agentdesk/runtime/"
TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "assets" / "project-template"
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
    if destination.exists():
        _emit("EXISTS", relative_path)
        return True

    _emit("CREATED", relative_path, "dry-run" if dry_run else None)
    if not dry_run:
        destination.mkdir(parents=True, exist_ok=False)
    return True


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

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as output:
            output.write(content)
    except FileExistsError:
        # Preserve the no-overwrite guarantee even if another process created
        # the destination between the existence check and the exclusive open.
        _report_existing_file(relative_path, destination)


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
            with destination.open("xb") as output:
                output.write(f"{IGNORE_ENTRY}\n".encode("utf-8"))
        return

    content = destination.read_bytes()
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

    separator = b"" if not content or content.endswith((b"\n", b"\r")) else b"\n"
    with destination.open("ab") as output:
        output.write(separator + encoded_entry + b"\n")


def _sorted_directories(paths: Iterable[Path]) -> list[Path]:
    """Return unique directories with parents before their descendants."""

    return sorted(set(paths), key=lambda path: (len(path.parts), path.as_posix()))


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
    root = _git_root(project)
    resolved_project = project.resolve()
    if root is not None:
        if root != resolved_project:
            raise ProjectArgumentError(
                f"project must be the Git worktree root; detected parent repository: {root}"
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


def initialize(
    project: Path,
    project_id: str,
    mode: str,
    pm_holder_id: str,
    init_git: bool,
    dry_run: bool,
) -> None:
    """Initialize ``project`` according to the requested adoption mode."""

    project_id = _validate_replacement_value("--project-id", project_id)
    pm_holder_id = _validate_replacement_value("--pm-holder-id", pm_holder_id)
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

    template = _load_template(TEMPLATE_ROOT, replacements)

    if project.exists() and not project.is_dir():
        raise ProjectArgumentError(f"project path is not a directory: {project}")
    if project.is_symlink():
        raise ProjectArgumentError(f"project path must not be a symlink: {project}")
    _preflight_git_location(project)
    if not project.exists() and not dry_run:
        project.mkdir(parents=True, exist_ok=False)

    all_directories = _sorted_directories(
        (*template.directories, *REQUIRED_DIRECTORIES)
    )
    for relative_path in all_directories:
        _ensure_directory(project, relative_path, dry_run)

    for relative_path, content in template.files:
        _create_template_file(project, relative_path, content, dry_run)

    _ensure_gitignore(project, dry_run)
    _ensure_git_repository(project, init_git, dry_run)


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; return zero on a completed or dry-run initialization."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        # ``absolute`` normalizes the location without resolving the final
        # component, so initialize() can still reject a symlink project root.
        project = Path(args.project).expanduser().absolute()
        project_id = args.project_id if args.project_id is not None else project.name
        initialize(
            project=project,
            project_id=project_id,
            mode=args.mode,
            pm_holder_id=args.pm_holder_id,
            init_git=args.init_git,
            dry_run=args.dry_run,
        )
    except InitializationError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR initialization failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
