from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "agentdesk"
SCRIPT = SKILL_ROOT / "scripts" / "adopt_legacy_project.py"
STATE_FILE = Path("docs/pm/state/tasks.yaml")
LOCK_FILE = Path("docs/pm/AGENTDESK.lock.json")
SOURCE_COMMIT = "7" * 40


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def calculate_bundle_digest(bundle_root: Path) -> str:
    manifest_path = bundle_root / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_sha256"] = None
    canonical_manifest = (
        json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    entries = []
    for path in sorted(
        bundle_root.rglob("*"),
        key=lambda item: item.relative_to(bundle_root).as_posix(),
    ):
        if path.is_dir():
            continue
        relative = path.relative_to(bundle_root).as_posix()
        content = canonical_manifest if path == manifest_path else path.read_bytes()
        entries.append((relative, hashlib.sha256(content).hexdigest()))
    aggregate = hashlib.sha256()
    for relative, file_digest in entries:
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(file_digest.encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def snapshot_worktree(root: Path) -> Dict[str, Tuple[str, bytes]]:
    result: Dict[str, Tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative_path = path.relative_to(root)
        if relative_path.parts and relative_path.parts[0] == ".git":
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            result[relative] = (
                "symlink",
                os.readlink(str(path)).encode("utf-8"),
            )
        elif path.is_dir():
            result[relative] = ("directory", b"")
        else:
            result[relative] = ("file", path.read_bytes())
    return result


def make_tree_read_only(root: Path) -> None:
    files = [path for path in root.rglob("*") if path.is_file()]
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for path in files:
        path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555)
    root.chmod(0o555)


def make_tree_writable(root: Path) -> None:
    if not root.exists():
        return
    root.chmod(0o755)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)


class LegacyAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agentdesk-legacy-adoption-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_git(
        self, project: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=AgentDesk Tests",
                "-c",
                "user.email=agentdesk-tests@example.invalid",
                "-C",
                str(project),
                *arguments,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )

    def state(
        self,
        *,
        project_id: str = "legacy-project",
        adoption_level: str = "standard",
        tasks: Optional[list[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return {
            "schema_version": "agentdesk.tasks/v2",
            "project_id": project_id,
            "adoption_level": adoption_level,
            "updated_at": "2026-07-25T00:00:00Z",
            "pm_control": {
                "holder_id": "pm-legacy",
                "lease_epoch": 3,
                "mode": "manual" if adoption_level == "lite" else "timed",
            },
            "tasks": tasks or [],
        }

    def make_project(
        self,
        name: str,
        *,
        state: Optional[Dict[str, Any]] = None,
        commit: bool = True,
    ) -> Path:
        project = self.root / name
        project.mkdir()
        initialized = subprocess.run(
            ["git", "init", "-b", "main", str(project)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, initialized.returncode, initialized.stderr)
        write_json(project / STATE_FILE, state or self.state())
        if commit:
            added = self.run_git(project, "add", STATE_FILE.as_posix())
            self.assertEqual(0, added.returncode, added.stderr)
            committed = self.run_git(
                project, "commit", "-m", "legacy AgentDesk baseline"
            )
            self.assertEqual(0, committed.returncode, committed.stderr)
        return project

    def make_stable_bundle(
        self,
        name: str = "stable-bundle",
        *,
        failing_initializer: bool = False,
        read_only: bool = True,
    ) -> Path:
        bundle = self.root / name
        shutil.copytree(SKILL_ROOT, bundle)
        if failing_initializer:
            initializer = bundle / "scripts" / "init_project.py"
            initializer.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "print('intentional initializer failure', file=sys.stderr)\n"
                "raise SystemExit(9)\n",
                encoding="utf-8",
            )
        manifest_path = bundle / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "skill_release": "0.2.0-test",
                "release_status": "stable",
                "source_commit": SOURCE_COMMIT,
                "bundle_sha256": None,
                "digest_scope": (
                    "all-files-with-canonical-release-manifest"
                ),
            }
        )
        write_json(manifest_path, manifest)
        manifest["bundle_sha256"] = calculate_bundle_digest(bundle)
        write_json(manifest_path, manifest)
        self.assertEqual(
            manifest["bundle_sha256"], calculate_bundle_digest(bundle)
        )
        if read_only:
            self.addCleanup(make_tree_writable, bundle)
            make_tree_read_only(bundle)
        return bundle

    def run_adoption(
        self,
        project: Path,
        *,
        script: Path = SCRIPT,
        apply: bool = False,
        acknowledge: bool = False,
    ) -> Tuple[subprocess.CompletedProcess[str], Dict[str, Any]]:
        command = [
            sys.executable,
            str(script),
            "--project",
            str(project),
            "--apply" if apply else "--dry-run",
            "--json",
        ]
        if acknowledge:
            command.append("--acknowledge-legacy-unpinned")
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=150,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover
            self.fail(
                "adoption command did not emit JSON: {}\nstdout={!r}\n"
                "stderr={!r}".format(exc, result.stdout, result.stderr)
            )
        return result, payload

    def assert_error(self, payload: Dict[str, Any], code: str) -> None:
        self.assertIn(
            code,
            [item["code"] for item in payload["errors"]],
            payload,
        )

    def test_help_lists_explicit_interface(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout)
        for option in (
            "--project",
            "--dry-run",
            "--apply",
            "--acknowledge-legacy-unpinned",
            "--json",
        ):
            self.assertIn(option, result.stdout)

    def test_development_dry_run_is_a_zero_write_plan(self) -> None:
        project = self.make_project("dev-dry-run")
        before = snapshot_worktree(project)

        result, payload = self.run_adoption(project)

        self.assertEqual(0, result.returncode, payload)
        self.assertEqual("plan", payload["status"])
        self.assertFalse(payload["complete"])
        self.assertFalse(payload["lock_created"])
        self.assertFalse(payload["release"]["apply_eligible"])
        self.assertEqual("legacy-project", payload["project_id"])
        self.assertEqual("standard", payload["adoption_level"])
        self.assertIn(
            "release_not_stable",
            [item["code"] for item in payload["warnings"]],
        )
        self.assertEqual(before, snapshot_worktree(project))
        self.assertFalse((project / LOCK_FILE).exists())

    def test_development_bundle_cannot_apply(self) -> None:
        project = self.make_project("dev-apply")
        before = snapshot_worktree(project)

        result, payload = self.run_adoption(
            project, apply=True, acknowledge=True
        )

        self.assertEqual(2, result.returncode, payload)
        self.assert_error(payload, "release_not_stable")
        self.assertEqual(before, snapshot_worktree(project))
        self.assertFalse((project / LOCK_FILE).exists())

    def test_all_active_attempt_forms_block_adoption(self) -> None:
        cases = (
            (
                "dispatched",
                {
                    "task_id": "TC-001",
                    "state": "dispatched",
                    "attempt": 1,
                    "current_dispatch": None,
                    "blocked_attempt_valid": None,
                },
            ),
            (
                "in-progress",
                {
                    "task_id": "TC-002",
                    "state": "in_progress",
                    "attempt": 1,
                    "current_dispatch": None,
                    "blocked_attempt_valid": None,
                },
            ),
            (
                "review-ready",
                {
                    "task_id": "TC-003",
                    "state": "review_ready",
                    "attempt": 1,
                    "current_dispatch": None,
                    "blocked_attempt_valid": None,
                },
            ),
            (
                "dispatch-envelope",
                {
                    "task_id": "TC-004",
                    "state": "ready",
                    "attempt": 1,
                    "current_dispatch": {"dispatch_id": "DSP-004"},
                    "blocked_attempt_valid": None,
                },
            ),
            (
                "blocked-valid",
                {
                    "task_id": "TC-005",
                    "state": "blocked",
                    "attempt": 1,
                    "current_dispatch": None,
                    "blocked_attempt_valid": True,
                },
            ),
        )
        for name, task in cases:
            with self.subTest(name=name):
                project = self.make_project(
                    "active-{}".format(name),
                    state=self.state(tasks=[task]),
                )
                before = snapshot_worktree(project)

                result, payload = self.run_adoption(project)

                self.assertEqual(2, result.returncode, payload)
                self.assert_error(payload, "active_attempts")
                self.assertEqual(before, snapshot_worktree(project))

    def test_dirty_worktree_is_rejected(self) -> None:
        project = self.make_project("dirty")
        dirty = project / "unrelated.txt"
        dirty.write_text("user work\n", encoding="utf-8")
        before = snapshot_worktree(project)

        result, payload = self.run_adoption(project)

        self.assertEqual(2, result.returncode, payload)
        self.assert_error(payload, "dirty_worktree")
        self.assertEqual(before, snapshot_worktree(project))

    def test_symlink_and_malformed_state_are_rejected(self) -> None:
        symlink_project = self.make_project("symlink-state")
        state_path = symlink_project / STATE_FILE
        target = symlink_project / "legacy-state.json"
        state_path.replace(target)
        state_path.symlink_to(target.name)
        added = self.run_git(symlink_project, "add", "-A")
        self.assertEqual(0, added.returncode, added.stderr)
        committed = self.run_git(
            symlink_project, "commit", "-m", "make state a symlink"
        )
        self.assertEqual(0, committed.returncode, committed.stderr)
        result, payload = self.run_adoption(symlink_project)
        self.assertEqual(2, result.returncode, payload)
        self.assert_error(payload, "unsafe_symlink")

        malformed_project = self.make_project("malformed-state")
        malformed_path = malformed_project / STATE_FILE
        malformed_path.write_text('{"project_id":', encoding="utf-8")
        added = self.run_git(malformed_project, "add", STATE_FILE.as_posix())
        self.assertEqual(0, added.returncode, added.stderr)
        committed = self.run_git(
            malformed_project, "commit", "-m", "malformed legacy state"
        )
        self.assertEqual(0, committed.returncode, committed.stderr)
        result, payload = self.run_adoption(malformed_project)
        self.assertEqual(2, result.returncode, payload)
        self.assert_error(payload, "invalid_json")

    def test_git_root_head_and_clean_baseline_are_required(self) -> None:
        no_head = self.root / "no-head"
        no_head.mkdir()
        initialized = subprocess.run(
            ["git", "init", "-b", "main", str(no_head)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, initialized.returncode, initialized.stderr)
        write_json(no_head / STATE_FILE, self.state())
        result, payload = self.run_adoption(no_head)
        self.assertEqual(2, result.returncode, payload)
        # The untracked state also makes the worktree dirty, but the missing
        # baseline commit is the earlier, more useful diagnosis.
        self.assert_error(payload, "missing_head")

        root = self.make_project("parent-root")
        nested = root / "nested"
        nested.mkdir()
        write_json(nested / STATE_FILE, self.state())
        result, payload = self.run_adoption(nested)
        self.assertEqual(2, result.returncode, payload)
        self.assert_error(payload, "not_git_root")

    def test_verified_stable_bundle_adopts_and_second_run_refuses(self) -> None:
        bundle = self.make_stable_bundle()
        script = bundle / "scripts" / "adopt_legacy_project.py"
        project = self.make_project(
            "stable-project",
            state=self.state(
                project_id="project-from-state", adoption_level="lite"
            ),
        )
        agents = project / "AGENTS.md"
        agents.write_text("user-owned instructions\n", encoding="utf-8")
        added = self.run_git(project, "add", "AGENTS.md")
        self.assertEqual(0, added.returncode, added.stderr)
        committed = self.run_git(
            project, "commit", "-m", "preserve user instructions"
        )
        self.assertEqual(0, committed.returncode, committed.stderr)

        before = snapshot_worktree(project)
        planned, plan = self.run_adoption(project, script=script)
        self.assertEqual(0, planned.returncode, plan)
        self.assertTrue(plan["release"]["apply_eligible"])
        self.assertEqual(before, snapshot_worktree(project))

        missing_ack, missing_ack_payload = self.run_adoption(
            project, script=script, apply=True
        )
        self.assertEqual(2, missing_ack.returncode, missing_ack_payload)
        self.assert_error(
            missing_ack_payload, "acknowledgement_required"
        )
        self.assertEqual(before, snapshot_worktree(project))

        applied, payload = self.run_adoption(
            project, script=script, apply=True, acknowledge=True
        )
        self.assertEqual(0, applied.returncode, payload)
        self.assertEqual("adopted", payload["status"])
        self.assertTrue(payload["complete"])
        self.assertTrue(payload["lock_created"])
        lock = json.loads((project / LOCK_FILE).read_text(encoding="utf-8"))
        manifest = json.loads(
            (bundle / "release-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual("project-from-state", lock["project_id"])
        self.assertEqual("0.2.0-test", lock["skill"]["release"])
        self.assertEqual(SOURCE_COMMIT, lock["skill"]["source_commit"])
        self.assertEqual(
            manifest["bundle_sha256"], lock["skill"]["bundle_sha256"]
        )
        self.assertEqual(
            "lite",
            json.loads(
                (project / STATE_FILE).read_text(encoding="utf-8")
            )["adoption_level"],
        )
        self.assertEqual(
            "user-owned instructions\n", agents.read_text(encoding="utf-8")
        )
        self.assertTrue((project / "docs/pm/CHECKS.yaml").is_file())

        after = snapshot_worktree(project)
        repeated, repeated_payload = self.run_adoption(
            project, script=script, apply=True, acknowledge=True
        )
        self.assertEqual(2, repeated.returncode, repeated_payload)
        self.assert_error(repeated_payload, "already_pinned")
        self.assertEqual(after, snapshot_worktree(project))

    def test_mutable_stable_bundle_cannot_apply(self) -> None:
        bundle = self.make_stable_bundle("mutable-bundle", read_only=False)
        project = self.make_project("mutable-bundle-project")
        before = snapshot_worktree(project)

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )

        self.assertEqual(2, result.returncode, payload)
        self.assert_error(payload, "mutable_bundle")
        self.assertEqual(before, snapshot_worktree(project))

    def test_initializer_failure_retains_lock_and_reports_recovery(self) -> None:
        bundle = self.make_stable_bundle(
            "failing-bundle", failing_initializer=True
        )
        project = self.make_project("initializer-failure")

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )

        self.assertEqual(1, result.returncode, payload)
        self.assertEqual("incomplete", payload["status"])
        self.assertTrue(payload["lock_created"])
        self.assertFalse(payload["complete"])
        self.assert_error(payload, "initializer_failed_after_lock")
        self.assertTrue((project / LOCK_FILE).is_file())
        self.assertTrue(payload["recovery"]["lock_retained"])
        self.assertIn(
            str((bundle / "scripts" / "init_project.py").resolve()),
            payload["recovery"]["command"],
        )


if __name__ == "__main__":
    unittest.main()
