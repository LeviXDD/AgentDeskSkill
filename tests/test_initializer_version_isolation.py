from __future__ import annotations

import json
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "agentdesk"
INITIALIZER = SKILL_ROOT / "scripts" / "init_project.py"
LOCK_TEMPLATE = (
    SKILL_ROOT
    / "assets"
    / "project-template"
    / "docs"
    / "pm"
    / "AGENTDESK.lock.json"
)
MANIFEST = json.loads(
    (SKILL_ROOT / "release-manifest.json").read_text(encoding="utf-8")
)
INITIALIZER_SPEC = importlib.util.spec_from_file_location(
    "agentdesk_init_project_test", INITIALIZER
)
if INITIALIZER_SPEC is None or INITIALIZER_SPEC.loader is None:
    raise RuntimeError("cannot load initializer")
INITIALIZER_MODULE = importlib.util.module_from_spec(INITIALIZER_SPEC)
sys.modules[INITIALIZER_SPEC.name] = INITIALIZER_MODULE
INITIALIZER_SPEC.loader.exec_module(INITIALIZER_MODULE)


def run_initializer(
    project: Path,
    *extra: str,
    initializer: Path = INITIALIZER,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(initializer),
            "--project",
            str(project),
            "--project-id",
            "version-test",
            "--mode",
            "standard",
            *extra,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )


def snapshot(root: Path) -> Dict[str, Tuple[str, bytes]]:
    if not root.exists():
        return {}
    result: Dict[str, Tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", str(path.readlink()).encode("utf-8"))
        elif path.is_dir():
            result[relative] = ("directory", b"")
        else:
            result[relative] = ("file", path.read_bytes())
    return result


def rendered_lock(project_id: str = "version-test") -> bytes:
    text = LOCK_TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{PROJECT_ID}}": project_id,
        "{{AGENTDESK_SKILL_RELEASE}}": MANIFEST["skill_release"],
        "{{AGENTDESK_SOURCE_COMMIT_JSON}}": json.dumps(
            MANIFEST["source_commit"]
        ),
        "{{AGENTDESK_BUNDLE_SHA256_JSON}}": json.dumps(
            MANIFEST["bundle_sha256"]
        ),
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    json.loads(text)
    return text.encode("utf-8")


class InitializerVersionIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agentdesk-init-version-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_unverified_candidate_requires_explicit_disposable_flag(self) -> None:
        project = self.root / "new"
        result = run_initializer(project)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("unverified development release identity", result.stdout)
        self.assertFalse(project.exists())

        allowed = run_initializer(
            project, "--allow-development-release", "--init-git"
        )
        self.assertEqual(0, allowed.returncode, allowed.stdout)
        self.assertEqual(
            rendered_lock(),
            (project / "docs/pm/AGENTDESK.lock.json").read_bytes(),
        )

    def test_unknown_release_status_is_never_an_initialization_override(self) -> None:
        for release_status in ("candidate", "recovery", "developmnt"):
            with self.subTest(release_status=release_status):
                copied_skill = self.root / release_status / "agentdesk"
                copied_skill.parent.mkdir(parents=True)
                shutil.copytree(SKILL_ROOT, copied_skill)
                manifest_path = copied_skill / "release-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["release_status"] = release_status
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                project = self.root / f"project-{release_status}"

                result = run_initializer(
                    project,
                    "--allow-development-release",
                    initializer=copied_skill / "scripts" / "init_project.py",
                )

                self.assertEqual(2, result.returncode, result.stdout)
                self.assertIn(
                    "release_status must be exactly 'development' or 'stable'",
                    result.stdout,
                )
                self.assertFalse(project.exists())

    def test_legacy_marker_without_lock_is_zero_write_even_in_dry_run(self) -> None:
        markers = (
            (Path("docs/pm/state/tasks.yaml"), False),
            (Path("docs/pm/BOARD.md"), False),
            (Path("docs/pm/STATUS.md"), False),
            (Path("docs/pm/ROLES.md"), False),
            (Path("docs/pm/CHECKS.yaml"), False),
            (Path("docs/pm/DECISIONS.md"), False),
            (Path("docs/pm/PM-PLAYBOOK.md"), False),
            (Path("docs/pm/ROLE-POLICIES.yaml"), False),
            (Path("docs/pm/CONSULTATION-POLICY.yaml"), False),
            (Path("docs/pm/CONSULTATIONS.md"), False),
            (Path("docs/pm/state"), True),
            (Path("docs/pm/tasks"), True),
            (Path("docs/pm/reports"), True),
            (Path("docs/pm/acceptances"), True),
            (Path("docs/pm/consultations"), True),
            (Path("docs/pm/events"), True),
            (Path("docs/pm/outbox"), True),
            (Path(".agentdesk"), True),
            (Path(".agentdesk/runtime"), True),
            (Path(".agentdesk/runtime/routes.yaml"), False),
            (Path(".agentdesk/runtime/consultations.yaml"), False),
            (Path(".agentdesk/runtime/model-bindings.yaml"), False),
            (Path(".agentdesk/runtime/transport-receipts.yaml"), False),
        )
        for index, (marker, is_directory) in enumerate(markers):
            with self.subTest(marker=marker):
                project = self.root / f"legacy-marker-{index}"
                marker_path = project / marker
                if is_directory:
                    marker_path.mkdir(parents=True)
                else:
                    marker_path.parent.mkdir(parents=True)
                    marker_path.write_text("legacy\n", encoding="utf-8")
                before = snapshot(project)

                dry_run = run_initializer(project, "--dry-run")
                self.assertEqual(2, dry_run.returncode, dry_run.stdout)
                self.assertIn("legacy-unpinned", dry_run.stdout)
                self.assertEqual(before, snapshot(project))

                actual = run_initializer(
                    project, "--allow-development-release"
                )
                self.assertEqual(2, actual.returncode, actual.stdout)
                self.assertIn("legacy-unpinned", actual.stdout)
                self.assertEqual(before, snapshot(project))

    def test_ordinary_agents_file_is_not_a_legacy_marker(self) -> None:
        project = self.root / "ordinary"
        project.mkdir()
        agents = project / "AGENTS.md"
        agents.write_text("user-owned\n", encoding="utf-8")

        result = run_initializer(
            project, "--allow-development-release", "--init-git"
        )

        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual("user-owned\n", agents.read_text(encoding="utf-8"))
        self.assertTrue((project / "docs/pm/AGENTDESK.lock.json").is_file())

    def test_non_git_project_requires_explicit_init_before_any_write(self) -> None:
        project = self.root / "non-git-existing"
        project.mkdir()
        readme = project / "README.md"
        readme.write_text("business files\n", encoding="utf-8")
        before = snapshot(project)

        result = run_initializer(
            project, "--allow-development-release"
        )

        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("--init-git", result.stdout)
        self.assertIn("no files were changed", result.stdout)
        self.assertEqual(before, snapshot(project))

    def test_symlinked_git_authority_is_rejected_before_any_write(self) -> None:
        authority = self.root / "git-authority"
        authority.mkdir()
        initialized = subprocess.run(
            ["git", "-C", str(authority), "init", "-b", "main"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, initialized.returncode, initialized.stdout)
        project = self.root / "symlinked-git-project"
        project.mkdir()
        (project / "README.md").write_text(
            "business files\n", encoding="utf-8"
        )
        (project / ".git").symlink_to(
            authority / ".git", target_is_directory=True
        )
        before = snapshot(project)

        result = run_initializer(
            project, "--allow-development-release"
        )

        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn(
            ".git authority must not be a symbolic link", result.stdout
        )
        self.assertEqual(before, snapshot(project))

    def test_linked_worktree_git_file_remains_supported(self) -> None:
        authority = self.root / "linked-authority"
        authority.mkdir()
        (authority / "README.md").write_text(
            "baseline\n", encoding="utf-8"
        )
        commands = (
            ("git", "init", "-b", "main"),
            ("git", "add", "README.md"),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "baseline",
            ),
        )
        for command in commands:
            result = subprocess.run(
                list(command),
                cwd=authority,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stdout)
        project = self.root / "linked-project"
        linked = subprocess.run(
            [
                "git",
                "-C",
                str(authority),
                "worktree",
                "add",
                "--detach",
                str(project),
                "HEAD",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, linked.returncode, linked.stdout)
        self.assertTrue((project / ".git").is_file())
        self.assertFalse((project / ".git").is_symlink())

        result = run_initializer(
            project, "--allow-development-release"
        )

        self.assertEqual(0, result.returncode, result.stdout)
        self.assertTrue(
            (project / "docs/pm/AGENTDESK.lock.json").is_file()
        )

    def test_mismatched_existing_lock_blocks_before_any_write(self) -> None:
        project = self.root / "mismatch"
        lock_path = project / "docs/pm/AGENTDESK.lock.json"
        lock_path.parent.mkdir(parents=True)
        value = json.loads(rendered_lock())
        value["skill"]["release"] = "another-release"
        lock_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        before = snapshot(project)

        result = run_initializer(
            project, "--dry-run", "--allow-development-release"
        )

        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("does not exactly match", result.stdout)
        self.assertEqual(before, snapshot(project))

    def test_development_override_cannot_resume_existing_lock(self) -> None:
        project = self.root / "interrupted"
        lock_path = project / "docs/pm/AGENTDESK.lock.json"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_bytes(rendered_lock())
        before = snapshot(project)

        result = run_initializer(project, "--allow-development-release")

        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("limited to creating a fresh disposable", result.stdout)
        self.assertEqual(rendered_lock(), lock_path.read_bytes())
        self.assertEqual(before, snapshot(project))

    def test_partial_authority_write_never_publishes_final_path(self) -> None:
        destination = self.root / "atomic" / "AGENTDESK.lock.json"
        destination.parent.mkdir()
        real_write = INITIALIZER_MODULE.os.write
        calls = 0

        def partial_then_fail(descriptor: int, value: bytes) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(descriptor, value[:5])
            raise OSError(28, "intentional no space left")

        with mock.patch.object(
            INITIALIZER_MODULE.os,
            "write",
            side_effect=partial_then_fail,
        ):
            with self.assertRaises(OSError):
                INITIALIZER_MODULE._atomic_exclusive_create(
                    destination.parent,
                    Path("AGENTDESK.lock.json"),
                    b'{"complete": true}\n',
                )

        self.assertFalse(destination.exists())
        self.assertEqual([], list(destination.parent.glob(".*.tmp")))

    def test_authority_parent_symlink_swap_cannot_escape_project(self) -> None:
        project = self.root / "parent-swap"
        pm_directory = project / "docs" / "pm"
        pm_directory.mkdir(parents=True)
        held_docs = project / "docs-held"
        outside_docs = self.root / "outside-docs"
        (outside_docs / "pm").mkdir(parents=True)
        real_open = INITIALIZER_MODULE.os.open
        swapped = False

        def swap_before_open(
            path: object,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: Optional[int] = None,
        ) -> int:
            nonlocal swapped
            if path == "docs" and dir_fd is not None and not swapped:
                swapped = True
                (project / "docs").rename(held_docs)
                (project / "docs").symlink_to(
                    outside_docs, target_is_directory=True
                )
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(
            INITIALIZER_MODULE.os, "open", side_effect=swap_before_open
        ):
            with self.assertRaises(OSError):
                INITIALIZER_MODULE._atomic_exclusive_create(
                    project,
                    Path("docs/pm/AGENTDESK.lock.json"),
                    b'{"complete": true}\n',
                )

        self.assertFalse(
            (outside_docs / "pm" / "AGENTDESK.lock.json").exists()
        )
        self.assertFalse(
            (held_docs / "pm" / "AGENTDESK.lock.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
