from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "agentdesk"
SCRIPT = SKILL_ROOT / "scripts" / "adopt_legacy_project.py"
STATE_FILE = Path("docs/pm/state/tasks.yaml")
LOCK_FILE = Path("docs/pm/AGENTDESK.lock.json")
SOURCE_COMMIT = "7" * 40
ADOPTER_SPEC = importlib.util.spec_from_file_location(
    "agentdesk_adopt_legacy_project", SCRIPT
)
if ADOPTER_SPEC is None or ADOPTER_SPEC.loader is None:
    raise RuntimeError("cannot load legacy adopter")
ADOPTER = importlib.util.module_from_spec(ADOPTER_SPEC)
ADOPTER_SPEC.loader.exec_module(ADOPTER)


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


def replace_tool_with_state_mutating_wrapper(
    tool: Path, task_id: str
) -> None:
    implementation = tool.with_name(
        "_{}_before_state_mutation.py".format(tool.stem)
    )
    shutil.copy2(tool, implementation)
    tool.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import json",
                "import runpy",
                "import sys",
                "from pathlib import Path",
                "",
                "try:",
                "    runpy.run_path(",
                "        str(Path(__file__).with_name({})),"
                .format(json.dumps(implementation.name)),
                "        run_name='__main__',",
                "    )",
                "except SystemExit as exc:",
                "    if exc.code not in (None, 0):",
                "        raise",
                "",
                "project = Path(sys.argv[sys.argv.index('--project') + 1])",
                "state_path = project / 'docs/pm/state/tasks.yaml'",
                "state = json.loads(state_path.read_text(encoding='utf-8'))",
                "state['tasks'].append(",
                "    {",
                "        'task_id': {},".format(json.dumps(task_id)),
                "        'state': 'integrated',",
                "        'attempt': 0,",
                "        'current_dispatch': None,",
                "        'blocked_attempt_valid': None,",
                "    }",
                ")",
                "state_path.write_text(",
                "    json.dumps(state, ensure_ascii=False, indent=2) + '\\n',",
                "    encoding='utf-8',",
                ")",
                "raise SystemExit(0)",
                "",
            )
        ),
        encoding="utf-8",
    )


def replace_initializer_with_unrelated_file_wrapper(tool: Path) -> None:
    implementation = tool.with_name("_init_project_before_unrelated_file.py")
    shutil.copy2(tool, implementation)
    tool.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import runpy",
                "import sys",
                "from pathlib import Path",
                "",
                "try:",
                "    runpy.run_path(",
                "        str(Path(__file__).with_name({})),"
                .format(json.dumps(implementation.name)),
                "        run_name='__main__',",
                "    )",
                "except SystemExit as exc:",
                "    if exc.code not in (None, 0):",
                "        raise",
                "",
                "project = Path(sys.argv[sys.argv.index('--project') + 1])",
                "(project / 'unrelated-concurrent.txt').write_text(",
                "    'not part of the adoption plan\\n',",
                "    encoding='utf-8',",
                ")",
                "raise SystemExit(0)",
                "",
            )
        ),
        encoding="utf-8",
    )


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
        mutating_initializer: bool = False,
        unrelated_file_initializer: bool = False,
        failing_renderer: bool = False,
        failing_renderer_check: bool = False,
        mutating_renderer: bool = False,
        failing_validator: bool = False,
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
        if mutating_initializer:
            replace_tool_with_state_mutating_wrapper(
                bundle / "scripts" / "init_project.py",
                "TC-INIT-MUTATION",
            )
        if unrelated_file_initializer:
            replace_initializer_with_unrelated_file_wrapper(
                bundle / "scripts" / "init_project.py"
            )
        if failing_renderer:
            renderer = bundle / "scripts" / "render_views.py"
            renderer.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "print('intentional renderer failure', file=sys.stderr)\n"
                "raise SystemExit(8)\n",
                encoding="utf-8",
            )
        if failing_renderer_check:
            renderer = bundle / "scripts" / "render_views.py"
            implementation = bundle / "scripts" / "_render_views_impl.py"
            shutil.copy2(renderer, implementation)
            renderer.write_text(
                "#!/usr/bin/env python3\n"
                "import runpy\n"
                "import sys\n"
                "from pathlib import Path\n"
                "if '--check' in sys.argv:\n"
                "    print('intentional renderer check failure', "
                "file=sys.stderr)\n"
                "    raise SystemExit(8)\n"
                "runpy.run_path(\n"
                "    str(Path(__file__).with_name('_render_views_impl.py')),\n"
                "    run_name='__main__',\n"
                ")\n",
                encoding="utf-8",
            )
        if mutating_renderer:
            replace_tool_with_state_mutating_wrapper(
                bundle / "scripts" / "render_views.py",
                "TC-RENDER-MUTATION",
            )
        if failing_validator:
            validator = bundle / "scripts" / "validate_project.py"
            validator.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "print('intentional validator failure', file=sys.stderr)\n"
                "raise SystemExit(7)\n",
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
        isolated: bool = False,
        environment: Optional[Dict[str, str]] = None,
    ) -> Tuple[subprocess.CompletedProcess[str], Dict[str, Any]]:
        command = [sys.executable]
        if isolated:
            command.append("-I")
        command.extend(
            [
                str(script),
                "--project",
                str(project),
                "--apply" if apply else "--dry-run",
                "--json",
            ]
        )
        if acknowledge:
            command.append("--acknowledge-legacy-unpinned")
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=150,
            env=environment,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover
            self.fail(
                "adoption command did not emit JSON: {}\nstdout={!r}\n"
                "stderr={!r}".format(exc, result.stdout, result.stderr)
            )
        return result, payload

    def run_compatibility(
        self, bundle: Path, project: Path, operation: str
    ) -> Tuple[subprocess.CompletedProcess[str], Dict[str, Any]]:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(bundle / "scripts" / "check_compatibility.py"),
                "--project",
                str(project),
                "--operation",
                operation,
                "--json",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover
            self.fail(
                "compatibility command did not emit JSON: {}\nstdout={!r}\n"
                "stderr={!r}".format(exc, result.stdout, result.stderr)
            )
        return result, payload

    def run_json_command(
        self, command: list[str]
    ) -> Tuple[subprocess.CompletedProcess[str], Dict[str, Any]]:
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
                "command did not emit JSON: {}\nstdout={!r}\nstderr={!r}".format(
                    exc, result.stdout, result.stderr
                )
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
            "--resume-after-lock",
            "--verify-prepared",
            "--expected-head",
            "--expected-state-sha256",
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

    def test_unknown_task_state_is_rejected_before_lock_creation(self) -> None:
        bundle = self.make_stable_bundle("invalid-task-state-bundle")
        project = self.make_project(
            "invalid-task-state-project",
            state=self.state(
                tasks=[
                    {
                        "task_id": "TC-900",
                        "state": "bogus",
                        "attempt": 0,
                        "current_dispatch": None,
                        "blocked_attempt_valid": None,
                    }
                ]
            ),
        )
        before = snapshot_worktree(project)

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )

        self.assertEqual(2, result.returncode, payload)
        self.assert_error(payload, "invalid_state")
        self.assertFalse((project / LOCK_FILE).exists())
        self.assertEqual(before, snapshot_worktree(project))

    def test_ignored_public_control_plane_is_rejected_before_lock(self) -> None:
        bundle = self.make_stable_bundle("ignored-public-paths-bundle")
        project = self.make_project("ignored-public-paths-project")
        (project / ".gitignore").write_text(
            "AGENTS.md\ndocs/pm/\n.agentdesk/runtime/\n",
            encoding="utf-8",
        )
        added = self.run_git(project, "add", ".gitignore")
        self.assertEqual(0, added.returncode, added.stderr)
        committed = self.run_git(
            project, "commit", "-m", "ignore future control plane"
        )
        self.assertEqual(0, committed.returncode, committed.stderr)
        before = snapshot_worktree(project)

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )

        self.assertEqual(2, result.returncode, payload)
        self.assert_error(payload, "public_control_plane_ignored")
        self.assertFalse((project / LOCK_FILE).exists())
        self.assertEqual(before, snapshot_worktree(project))

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
        board = project / "docs/pm/BOARD.md"
        status = project / "docs/pm/STATUS.md"
        board.write_text("stale legacy board\n", encoding="utf-8")
        status.write_text("stale legacy status\n", encoding="utf-8")
        added = self.run_git(
            project,
            "add",
            "AGENTS.md",
            "docs/pm/BOARD.md",
            "docs/pm/STATUS.md",
        )
        self.assertEqual(0, added.returncode, added.stderr)
        committed = self.run_git(
            project, "commit", "-m", "preserve user instructions"
        )
        self.assertEqual(0, committed.returncode, committed.stderr)

        before = snapshot_worktree(project)
        planned, plan = self.run_adoption(project, script=script)
        self.assertEqual(0, planned.returncode, plan)
        self.assertTrue(plan["release"]["apply_eligible"])
        self.assertEqual(
            [
                "create",
                "initialize_missing",
                "render_views",
                "check_views",
                "validate_precommit",
            ],
            [item["operation"] for item in plan["plan"]],
        )
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
        self.assertEqual("prepared", payload["status"])
        self.assertTrue(payload["prepared"])
        self.assertFalse(payload["complete"])
        self.assertTrue(payload["commit_required"])
        self.assertTrue(payload["strict_validation_pending"])
        self.assertTrue(payload["lock_created"])
        self.assertEqual(0, payload["renderer"]["returncode"])
        self.assertEqual(0, payload["view_check"]["returncode"])
        self.assertEqual(0, payload["precommit_validation"]["returncode"])
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
        self.assertNotEqual(
            "stale legacy board\n", board.read_text(encoding="utf-8")
        )
        self.assertNotEqual(
            "stale legacy status\n", status.read_text(encoding="utf-8")
        )
        self.assertTrue((project / "docs/pm/CHECKS.yaml").is_file())
        rendered = subprocess.run(
            [
                sys.executable,
                str(bundle / "scripts" / "render_views.py"),
                "--project",
                str(project),
                "--check",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, rendered.returncode, rendered.stdout)

        after = snapshot_worktree(project)
        repeated, repeated_payload = self.run_adoption(
            project, script=script, apply=True, acknowledge=True
        )
        self.assertEqual(2, repeated.returncode, repeated_payload)
        self.assert_error(repeated_payload, "already_pinned")
        self.assertEqual(after, snapshot_worktree(project))

    def test_prepared_scoped_commit_verifies_as_durable(self) -> None:
        bundle = self.make_stable_bundle("durable-bundle")
        project = self.make_project("durable-project")
        applied, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )
        self.assertEqual(0, applied.returncode, payload)
        self.assertEqual("prepared", payload["status"])
        self.assertEqual(
            1, len(payload["post_commit_verification"]["commands"])
        )
        verify_command = payload["post_commit_verification"]["commands"][0]
        self.assertIn("--verify-prepared", verify_command)

        added = self.run_git(project, "add", "-A")
        self.assertEqual(0, added.returncode, added.stderr)
        committed = self.run_git(
            project, "commit", "-m", "adopt AgentDesk release"
        )
        self.assertEqual(0, committed.returncode, committed.stderr)

        verified, verification = self.run_json_command(verify_command)
        self.assertEqual(0, verified.returncode, verification)
        self.assertEqual("durable", verification["status"])
        self.assertTrue(verification["complete"])
        self.assertTrue(verification["durable"])
        self.assertEqual(0, verification["writer"]["returncode"])
        self.assertEqual(0, verification["view_check"]["returncode"])
        self.assertEqual(
            0, verification["strict_validation"]["returncode"]
        )
        human_command = [
            item for item in verify_command if item != "--json"
        ]
        human = subprocess.run(
            human_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=150,
        )
        self.assertEqual(0, human.returncode, human.stdout)
        self.assertIn("STATUS durable", human.stdout)
        self.assertIn("DURABLE true", human.stdout)

    def test_prepared_commit_rejects_unrelated_business_path(self) -> None:
        bundle = self.make_stable_bundle("overbroad-bundle")
        project = self.make_project("overbroad-project")
        applied, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )
        self.assertEqual(0, applied.returncode, payload)
        (project / "unrelated-business-change.txt").write_text(
            "must not ride with migration\n", encoding="utf-8"
        )
        added = self.run_git(project, "add", "-A")
        self.assertEqual(0, added.returncode, added.stderr)
        committed = self.run_git(
            project, "commit", "-m", "overbroad adoption"
        )
        self.assertEqual(0, committed.returncode, committed.stderr)

        verified, verification = self.run_json_command(
            payload["post_commit_verification"]["commands"][0]
        )
        self.assertEqual(2, verified.returncode, verification)
        self.assert_error(verification, "prepared_commit_out_of_scope")

    def test_prepared_commit_binds_allowed_path_contents(self) -> None:
        bundle = self.make_stable_bundle("content-fence-bundle")
        cases = (
            (
                "gitignore-extra",
                Path(".gitignore"),
                "unrelated-secret.txt\n",
            ),
            (
                "template-extra",
                Path("docs/pm/CHECKS.yaml"),
                "\n# unrelated content\n",
            ),
        )
        for name, relative, suffix in cases:
            with self.subTest(name=name):
                project = self.make_project(name)
                applied, payload = self.run_adoption(
                    project,
                    script=(
                        bundle / "scripts" / "adopt_legacy_project.py"
                    ),
                    apply=True,
                    acknowledge=True,
                )
                self.assertEqual(0, applied.returncode, payload)
                path = project / relative
                path.write_text(
                    path.read_text(encoding="utf-8") + suffix,
                    encoding="utf-8",
                )
                added = self.run_git(project, "add", "-A")
                self.assertEqual(0, added.returncode, added.stderr)
                committed = self.run_git(
                    project, "commit", "-m", "tamper allowed content"
                )
                self.assertEqual(0, committed.returncode, committed.stderr)

                verified, verification = self.run_json_command(
                    payload["post_commit_verification"]["commands"][0]
                )
                self.assertEqual(2, verified.returncode, verification)
                self.assert_error(
                    verification, "prepared_content_mismatch"
                )

    def test_recovery_rejects_changed_head_and_unplanned_path(self) -> None:
        bundle = self.make_stable_bundle(
            "recovery-fence-bundle", failing_initializer=True
        )

        path_project = self.make_project("recovery-unplanned-path")
        failed, payload = self.run_adoption(
            path_project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )
        self.assertEqual(1, failed.returncode, payload)
        (path_project / "outside-plan.txt").write_text(
            "concurrent change\n", encoding="utf-8"
        )
        resumed, recovery = self.run_json_command(
            payload["recovery"]["command"]
        )
        self.assertEqual(2, resumed.returncode, recovery)
        self.assert_error(recovery, "project_changed_after_lock")

        head_project = self.make_project("recovery-changed-head")
        failed, payload = self.run_adoption(
            head_project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )
        self.assertEqual(1, failed.returncode, payload)
        (head_project / "baseline-moved.txt").write_text(
            "new commit\n", encoding="utf-8"
        )
        added = self.run_git(head_project, "add", "baseline-moved.txt")
        self.assertEqual(0, added.returncode, added.stderr)
        committed = self.run_git(
            head_project, "commit", "-m", "move baseline"
        )
        self.assertEqual(0, committed.returncode, committed.stderr)
        resumed, recovery = self.run_json_command(
            payload["recovery"]["command"]
        )
        self.assertEqual(2, resumed.returncode, recovery)
        self.assert_error(recovery, "recovery_head_changed")

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
            str((bundle / "scripts" / "adopt_legacy_project.py").resolve()),
            payload["recovery"]["command"],
        )
        self.assertIn(
            "--resume-after-lock",
            payload["recovery"]["command"],
        )

    def test_human_failure_output_prints_fenced_recovery_command(self) -> None:
        bundle = self.make_stable_bundle(
            "human-recovery-bundle", failing_initializer=True
        )
        project = self.make_project("human-recovery-project")
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(bundle / "scripts" / "adopt_legacy_project.py"),
                "--project",
                str(project),
                "--apply",
                "--acknowledge-legacy-unpinned",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=150,
        )
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("STATUS incomplete", result.stdout)
        self.assertIn("RECOVERY_COMMAND", result.stdout)
        self.assertIn("--resume-after-lock", result.stdout)

    def test_renderer_failure_retains_lock_and_reports_recovery(self) -> None:
        bundle = self.make_stable_bundle(
            "failing-renderer-bundle", failing_renderer=True
        )
        project = self.make_project("renderer-failure")

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
        self.assert_error(payload, "renderer_failed_after_lock")
        self.assertTrue((project / LOCK_FILE).is_file())
        self.assertTrue(payload["recovery"]["lock_retained"])
        self.assertTrue(payload["recovery"]["initializer_completed"])
        self.assertIn(
            str((bundle / "scripts" / "adopt_legacy_project.py").resolve()),
            payload["recovery"]["command"],
        )

    def test_renderer_check_failure_is_incomplete(self) -> None:
        bundle = self.make_stable_bundle(
            "failing-renderer-check-bundle",
            failing_renderer_check=True,
        )
        project = self.make_project("renderer-check-failure")

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )

        self.assertEqual(1, result.returncode, payload)
        self.assertEqual("incomplete", payload["status"])
        self.assertFalse(payload["complete"])
        self.assert_error(payload, "renderer_check_failed_after_lock")
        self.assertTrue((project / LOCK_FILE).is_file())
        self.assertEqual(0, payload["renderer"]["returncode"])
        self.assertEqual(8, payload["view_check"]["returncode"])
        self.assertEqual(1, len(payload["recovery"]["commands"]))
        self.assertIn(
            "--resume-after-lock", payload["recovery"]["commands"][0]
        )

    def test_precommit_validation_failure_is_incomplete(self) -> None:
        bundle = self.make_stable_bundle(
            "failing-validator-bundle", failing_validator=True
        )
        project = self.make_project("validator-failure")

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )

        self.assertEqual(1, result.returncode, payload)
        self.assertEqual("incomplete", payload["status"])
        self.assertFalse(payload["complete"])
        self.assert_error(
            payload, "precommit_validation_failed_after_lock"
        )
        self.assertTrue((project / LOCK_FILE).is_file())
        self.assertEqual(0, payload["renderer"]["returncode"])
        self.assertEqual(0, payload["view_check"]["returncode"])
        self.assertEqual(7, payload["precommit_validation"]["returncode"])

    def test_initializer_success_with_state_mutation_is_fenced(self) -> None:
        bundle = self.make_stable_bundle(
            "mutating-initializer-bundle",
            mutating_initializer=True,
        )
        project = self.make_project("mutating-initializer")

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )

        self.assertEqual(1, result.returncode, payload)
        self.assertEqual("incomplete", payload["status"])
        self.assertFalse(payload["complete"])
        self.assertTrue(payload["lock_created"])
        self.assert_error(payload, "project_changed_after_lock")
        self.assertEqual(0, payload["initializer"]["returncode"])
        self.assertNotIn("renderer", payload)
        self.assertNotIn("view_check", payload)
        self.assertNotIn("precommit_validation", payload)
        state = json.loads(
            (project / STATE_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["TC-INIT-MUTATION"],
            [task["task_id"] for task in state["tasks"]],
        )

    def test_renderer_success_with_state_mutation_is_fenced(self) -> None:
        bundle = self.make_stable_bundle(
            "mutating-renderer-bundle",
            mutating_renderer=True,
        )
        project = self.make_project("mutating-renderer")

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )

        self.assertEqual(1, result.returncode, payload)
        self.assertEqual("incomplete", payload["status"])
        self.assertFalse(payload["complete"])
        self.assertTrue(payload["lock_created"])
        self.assert_error(payload, "project_changed_after_lock")
        self.assertEqual(0, payload["initializer"]["returncode"])
        self.assertEqual(0, payload["renderer"]["returncode"])
        self.assertNotIn("view_check", payload)
        self.assertNotIn("precommit_validation", payload)
        state = json.loads(
            (project / STATE_FILE).read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["TC-RENDER-MUTATION"],
            [task["task_id"] for task in state["tasks"]],
        )

    def test_partial_lock_write_never_publishes_final_path(self) -> None:
        project = self.root / "partial-lock-write"
        pm_directory = project / "docs" / "pm"
        pm_directory.mkdir(parents=True)
        real_write = ADOPTER.os.write
        calls = 0

        def partial_then_fail(descriptor: int, value: bytes) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(descriptor, value[:7])
            raise OSError(28, "intentional no space left")

        with mock.patch.object(
            ADOPTER.os, "write", side_effect=partial_then_fail
        ):
            with self.assertRaises(ADOPTER.AdoptionError) as raised:
                ADOPTER._create_lock(project, b'{"complete": true}\n')

        self.assertEqual("lock_create_failed", raised.exception.code)
        self.assertFalse((project / LOCK_FILE).exists())
        self.assertEqual(
            [],
            list(pm_directory.glob(".AGENTDESK.lock.*.tmp")),
        )

    def test_lock_parent_swap_cannot_escape_project(self) -> None:
        project = self.root / "lock-parent-swap"
        pm_directory = project / "docs" / "pm"
        pm_directory.mkdir(parents=True)
        held_directory = project / "docs" / "pm-held"
        outside = self.root / "outside-lock-target"
        outside.mkdir()
        original_verify = ADOPTER._verify_open_project_path
        calls = 0

        def swap_before_publish(
            project_path: Path, descriptors: Tuple[int, int, int]
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                pm_directory.rename(held_directory)
                pm_directory.symlink_to(outside, target_is_directory=True)
            original_verify(project_path, descriptors)

        with mock.patch.object(
            ADOPTER,
            "_verify_open_project_path",
            side_effect=swap_before_publish,
        ):
            with self.assertRaises(ADOPTER.AdoptionError) as raised:
                ADOPTER._create_lock(project, b'{"complete": true}\n')

        self.assertEqual("project_path_changed", raised.exception.code)
        self.assertFalse((outside / LOCK_FILE.name).exists())
        self.assertFalse((held_directory / LOCK_FILE.name).exists())
        self.assertEqual(
            [],
            list(held_directory.glob(".AGENTDESK.lock.*.tmp")),
        )

    def test_initializer_unrelated_file_is_fenced_after_lock(self) -> None:
        bundle = self.make_stable_bundle(
            "unrelated-file-initializer-bundle",
            unrelated_file_initializer=True,
        )
        project = self.make_project("unrelated-file-initializer")

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )

        self.assertEqual(1, result.returncode, payload)
        self.assertEqual("incomplete", payload["status"])
        self.assertFalse(payload["complete"])
        self.assertTrue(payload["lock_created"])
        self.assertEqual(0, payload["initializer"]["returncode"])
        self.assert_error(payload, "project_changed_after_lock")
        self.assertNotIn("renderer", payload)
        self.assertNotIn("view_check", payload)
        self.assertNotIn("precommit_validation", payload)

    def test_pythonpath_sitecustomize_is_not_executed_by_adoption_chain(
        self,
    ) -> None:
        bundle = self.make_stable_bundle("isolated-python-bundle")
        project = self.make_project("isolated-python-project")
        injection_root = self.root / "pythonpath-injection"
        injection_root.mkdir()
        marker = self.root / "pythonpath-injection-executed"
        (injection_root / "sitecustomize.py").write_text(
            "\n".join(
                (
                    "import os",
                    "from pathlib import Path",
                    "marker = os.environ.get('AGENTDESK_TEST_INJECTION_MARKER')",
                    "if marker:",
                    "    Path(marker).write_text(",
                    "        'sitecustomize executed\\n', encoding='utf-8'",
                    "    )",
                    "",
                )
            ),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(injection_root)
        environment["AGENTDESK_TEST_INJECTION_MARKER"] = str(marker)

        result, payload = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
            isolated=True,
            environment=environment,
        )

        self.assertEqual(0, result.returncode, payload)
        self.assertEqual("prepared", payload["status"])
        self.assertFalse(
            marker.exists(),
            "verified same-bundle commands executed PYTHONPATH sitecustomize",
        )

    def test_prepared_lock_blocks_write_but_permits_migrate(self) -> None:
        bundle = self.make_stable_bundle("prepared-compatibility-bundle")
        project = self.make_project("prepared-compatibility-project")

        applied, adoption = self.run_adoption(
            project,
            script=bundle / "scripts" / "adopt_legacy_project.py",
            apply=True,
            acknowledge=True,
        )
        self.assertEqual(0, applied.returncode, adoption)
        self.assertEqual("prepared", adoption["status"])
        self.assertFalse(adoption["complete"])

        writer, writer_payload = self.run_compatibility(
            bundle, project, "write"
        )
        self.assertNotEqual(0, writer.returncode, writer_payload)
        self.assertFalse(writer_payload["compatible"], writer_payload)

        migrator, migrator_payload = self.run_compatibility(
            bundle, project, "migrate"
        )
        self.assertEqual(0, migrator.returncode, migrator_payload)
        self.assertTrue(migrator_payload["compatible"], migrator_payload)


if __name__ == "__main__":
    unittest.main()
