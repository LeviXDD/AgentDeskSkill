from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_SKILL = REPOSITORY_ROOT / "skills" / "agentdesk"
BUILD_TOOL = REPOSITORY_ROOT / "tools" / "build_skill_release.py"
ACTIVATE_TOOL = REPOSITORY_ROOT / "tools" / "activate_skill_release.py"


def run(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )


class ReleaseToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agentdesk-release-tools-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self._make_tree_writable)

    def _make_tree_writable(self) -> None:
        if not self.root.exists():
            return
        for path in sorted(
            self.root.rglob("*"), key=lambda item: len(item.parts), reverse=True
        ):
            if path.is_symlink():
                continue
            try:
                path.chmod(0o700 if path.is_dir() else 0o600)
            except OSError:
                pass
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def _create_clean_source_repository(self) -> Path:
        repository = self.root / "source-repository"
        skill = repository / "skills" / "agentdesk"
        skill.parent.mkdir(parents=True)
        shutil.copytree(SOURCE_SKILL, skill)
        commands = (
            ("git", "init", "-b", "main"),
            ("git", "add", "skills/agentdesk"),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "test release source",
            ),
        )
        for command in commands:
            result = run(*command, cwd=repository)
            self.assertEqual(0, result.returncode, result.stdout)
        return skill

    def _load_build_tool(self) -> ModuleType:
        spec = importlib.util.spec_from_file_location(
            "agentdesk_build_skill_release_test", BUILD_TOOL
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _load_activate_tool(self) -> ModuleType:
        spec = importlib.util.spec_from_file_location(
            "agentdesk_activate_skill_release_test", ACTIVATE_TOOL
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_build_activate_and_detect_tampering(self) -> None:
        source = self._create_clean_source_repository()
        release = self.root / "releases" / "v0.2.0-beta"
        build = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(release),
            "--release-id",
            "v0.2.0-beta",
        )
        self.assertEqual(0, build.returncode, build.stdout)
        build_result = json.loads(build.stdout)
        self.assertEqual("v0.2.0-beta", build_result["release_id"])

        skill_root = release / "skills" / "agentdesk"
        manifest = json.loads(
            (skill_root / "release-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual("stable", manifest["release_status"])
        self.assertEqual("v0.2.0-beta", manifest["skill_release"])
        self.assertEqual(build_result["source_commit"], manifest["source_commit"])
        self.assertEqual(build_result["bundle_sha256"], manifest["bundle_sha256"])
        self.assertEqual(0, stat.S_IMODE(release.stat().st_mode) & 0o222)

        project = self.root / "project"
        initialized = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(project),
            "--project-id",
            "release-tool-test",
            "--mode",
            "standard",
            "--init-git",
        )
        self.assertEqual(0, initialized.returncode, initialized.stdout)
        uncommitted_replay = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(project),
            "--project-id",
            "release-tool-test",
            "--mode",
            "standard",
        )
        self.assertEqual(
            0, uncommitted_replay.returncode, uncommitted_replay.stdout
        )
        self.assertIn("STATUS prepared", uncommitted_replay.stdout)
        self.assertIn("COMMIT_REQUIRED true", uncommitted_replay.stdout)
        prepared_replay = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(project),
            "--project-id",
            "release-tool-test",
            "--mode",
            "standard",
            "--migration-prepare",
        )
        self.assertEqual(0, prepared_replay.returncode, prepared_replay.stdout)
        agents_path = project / "AGENTS.md"
        agents_path.write_text(
            agents_path.read_text(encoding="utf-8")
            + "\n## Project-specific instructions\n\n"
            + "Run the repository test suite before delivery.\n",
            encoding="utf-8",
        )
        for command in (
            ("git", "add", "."),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "initialize locked project",
            ),
        ):
            result = run(*command, cwd=project)
            self.assertEqual(0, result.returncode, result.stdout)
        compatible = run(
            sys.executable,
            str(skill_root / "scripts" / "check_compatibility.py"),
            "--project",
            str(project),
            "--operation",
            "write",
            "--json",
        )
        self.assertEqual(0, compatible.returncode, compatible.stdout)
        self.assertEqual("compatible", json.loads(compatible.stdout)["status"])
        committed_replay = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(project),
            "--project-id",
            "release-tool-test",
            "--mode",
            "standard",
        )
        self.assertEqual(0, committed_replay.returncode, committed_replay.stdout)
        self.assertIn("STATUS committed", committed_replay.stdout)
        self.assertIn("COMMIT_REQUIRED false", committed_replay.stdout)
        self.assertNotIn("POST_COMMIT_COMMAND ", committed_replay.stdout)

        bindings_path = project / ".agentdesk/runtime/model-bindings.yaml"
        bindings_path.write_text(
            json.dumps(
                {
                    "schema_version": "agentdesk.model-bindings/v1",
                    "updated_at": None,
                    "bindings": {
                        "test-standard": {
                            "provider": "test-provider",
                            "model_id": "test-model",
                            "tier": "standard",
                            "deliberation_tier": "balanced",
                            "capabilities": ["coding", "testing"],
                            "enabled": True,
                        }
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        head = run("git", "rev-parse", "HEAD", cwd=project)
        self.assertEqual(0, head.returncode, head.stdout)
        task_card_commit = head.stdout.strip()

        selected = run(
            sys.executable,
            str(skill_root / "scripts" / "select_model.py"),
            "--project",
            str(project),
            "--task-card-commit",
            task_card_commit,
            "--role-id",
            "DEV",
            "--risk",
            "L0",
            "--task-min-tier",
            "inherit",
        )
        self.assertEqual(0, selected.returncode, selected.stdout)
        self.assertEqual("test-standard", json.loads(selected.stdout)["model_binding_id"])

        tree = run("git", "rev-parse", "HEAD^{tree}", cwd=project)
        self.assertEqual(0, tree.returncode, tree.stdout)
        orphan = run(
            "git",
            "-c",
            "user.name=AgentDesk Test",
            "-c",
            "user.email=agentdesk@example.invalid",
            "commit-tree",
            tree.stdout.strip(),
            cwd=project,
        )
        self.assertEqual(0, orphan.returncode, orphan.stdout)
        unreachable_selection = run(
            sys.executable,
            str(skill_root / "scripts" / "select_model.py"),
            "--project",
            str(project),
            "--task-card-commit",
            orphan.stdout.strip(),
            "--role-id",
            "DEV",
            "--risk",
            "L0",
            "--task-min-tier",
            "inherit",
        )
        self.assertEqual(
            1, unreachable_selection.returncode, unreachable_selection.stdout
        )
        self.assertIn(
            "must be an ancestor", unreachable_selection.stdout
        )

        lock_path = project / "docs/pm/AGENTDESK.lock.json"
        same_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock_path.write_text(
            json.dumps(
                same_lock,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        reformatted = run("git", "add", lock_path.relative_to(project).as_posix(), cwd=project)
        self.assertEqual(0, reformatted.returncode, reformatted.stdout)
        reformatted = run(
            "git",
            "-c",
            "user.name=AgentDesk Test",
            "-c",
            "user.email=agentdesk@example.invalid",
            "commit",
            "-m",
            "reformat exact project lock",
            cwd=project,
        )
        self.assertEqual(0, reformatted.returncode, reformatted.stdout)
        raw_mismatch = run(
            sys.executable,
            str(skill_root / "scripts" / "select_model.py"),
            "--project",
            str(project),
            "--task-card-commit",
            task_card_commit,
            "--role-id",
            "DEV",
            "--risk",
            "L0",
            "--task-min-tier",
            "inherit",
        )
        self.assertEqual(1, raw_mismatch.returncode, raw_mismatch.stdout)
        self.assertIn("exact raw lock blob", raw_mismatch.stdout)

        changed_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        changed_lock["skill"]["release"] = "crossed-release"
        lock_path.write_text(
            json.dumps(changed_lock, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        blocked_selection = run(
            sys.executable,
            str(skill_root / "scripts" / "select_model.py"),
            "--project",
            str(project),
            "--task-card-commit",
            task_card_commit,
            "--role-id",
            "DEV",
            "--risk",
            "L0",
            "--task-min-tier",
            "inherit",
        )
        self.assertEqual(1, blocked_selection.returncode, blocked_selection.stdout)
        self.assertIn("dispatch version gate failed", blocked_selection.stdout)

        install_link = self.root / "install" / "agentdesk"
        dry_run = run(
            sys.executable,
            str(ACTIVATE_TOOL),
            "--release-directory",
            str(release),
            "--install-link",
            str(install_link),
            "--expected-release",
            "v0.2.0-beta",
            "--dry-run",
        )
        self.assertEqual(0, dry_run.returncode, dry_run.stdout)
        self.assertFalse(install_link.exists())

        activated = run(
            sys.executable,
            str(ACTIVATE_TOOL),
            "--release-directory",
            str(release),
            "--install-link",
            str(install_link),
            "--expected-release",
            "v0.2.0-beta",
        )
        self.assertEqual(0, activated.returncode, activated.stdout)
        self.assertTrue(install_link.is_symlink())
        self.assertEqual(str(skill_root), os.readlink(install_link))

        payload = skill_root / "SKILL.md"
        payload.chmod(0o644)
        payload.write_text(
            payload.read_text(encoding="utf-8") + "\n# tampered\n",
            encoding="utf-8",
        )
        rejected = run(
            sys.executable,
            str(ACTIVATE_TOOL),
            "--release-directory",
            str(release),
            "--install-link",
            str(install_link),
            "--dry-run",
        )
        self.assertEqual(1, rejected.returncode, rejected.stdout)
        self.assertRegex(rejected.stdout, r"(digest mismatch|writable)")

    def test_stable_lock_only_initialization_resume_is_narrow(self) -> None:
        source = self._create_clean_source_repository()
        release = self.root / "releases" / "lock-only-resume"
        build = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(release),
            "--release-id",
            "lock-only-resume",
        )
        self.assertEqual(0, build.returncode, build.stdout)
        skill_root = release / "skills" / "agentdesk"

        ignored_project = self.root / "ignored-control-plane-project"
        ignored_project.mkdir()
        created = run("git", "init", "-b", "main", cwd=ignored_project)
        self.assertEqual(0, created.returncode, created.stdout)
        (ignored_project / ".gitignore").write_text(
            "AGENTS.md\ndocs/pm/\n.agentdesk/runtime/\n",
            encoding="utf-8",
        )
        (ignored_project / "README.md").write_text(
            "business baseline\n", encoding="utf-8"
        )
        for command in (
            ("git", "add", ".gitignore", "README.md"),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "ignored business baseline",
            ),
        ):
            result = run(*command, cwd=ignored_project)
            self.assertEqual(0, result.returncode, result.stdout)
        ignored_initialization = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(ignored_project),
            "--project-id",
            "ignored-control-plane-project",
            "--mode",
            "standard",
        )
        self.assertEqual(
            2, ignored_initialization.returncode, ignored_initialization.stdout
        )
        self.assertIn(
            "effective Git ignore rules exclude versioned AgentDesk "
            "control-plane paths",
            ignored_initialization.stdout,
        )
        self.assertFalse(
            (ignored_project / "docs/pm/AGENTDESK.lock.json").exists()
        )
        self.assertFalse((ignored_project / "AGENTS.md").exists())
        status = run(
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            cwd=ignored_project,
        )
        self.assertEqual(0, status.returncode, status.stdout)
        self.assertEqual("", status.stdout)

        verifier_project = self.root / "ignored-after-prepare-project"
        verifier_project.mkdir()
        created = run("git", "init", "-b", "main", cwd=verifier_project)
        self.assertEqual(0, created.returncode, created.stdout)
        (verifier_project / "README.md").write_text(
            "business baseline\n", encoding="utf-8"
        )
        for command in (
            ("git", "add", "README.md"),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "business baseline",
            ),
        ):
            result = run(*command, cwd=verifier_project)
            self.assertEqual(0, result.returncode, result.stdout)
        prepared = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(verifier_project),
            "--project-id",
            "ignored-after-prepare-project",
            "--mode",
            "standard",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout)
        postcommit_line = next(
            line
            for line in prepared.stdout.splitlines()
            if line.startswith("POST_COMMIT_COMMAND ")
        )
        verifier_command = shlex.split(
            postcommit_line.removeprefix("POST_COMMIT_COMMAND ")
        )
        gitignore = verifier_project / ".gitignore"
        gitignore.write_text(
            gitignore.read_text(encoding="utf-8")
            + "AGENTS.md\ndocs/pm/\n",
            encoding="utf-8",
        )
        for command in (
            (
                "git",
                "add",
                "--force",
                ".gitignore",
                "AGENTS.md",
                "docs/pm",
            ),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "commit ignored control plane",
            ),
        ):
            result = run(*command, cwd=verifier_project)
            self.assertEqual(0, result.returncode, result.stdout)
        rejected_verification = run(*verifier_command)
        self.assertNotEqual(
            0,
            rejected_verification.returncode,
            rejected_verification.stdout,
        )
        self.assertIn(
            "public_control_plane_ignored",
            rejected_verification.stdout,
        )

        scratch = self.root / "scratch-initialization"
        initialized = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(scratch),
            "--project-id",
            "lock-only-project",
            "--mode",
            "standard",
            "--init-git",
        )
        self.assertEqual(0, initialized.returncode, initialized.stdout)

        project = self.root / "lock-only-project"
        project.mkdir()
        created = run("git", "init", "-b", "main", cwd=project)
        self.assertEqual(0, created.returncode, created.stdout)
        (project / "README.md").write_text("baseline\n", encoding="utf-8")
        for command in (
            ("git", "add", "README.md"),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "business baseline",
            ),
        ):
            result = run(*command, cwd=project)
            self.assertEqual(0, result.returncode, result.stdout)
        lock_path = project / "docs/pm/AGENTDESK.lock.json"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_bytes(
            (scratch / "docs/pm/AGENTDESK.lock.json").read_bytes()
        )
        self.assertFalse((project / "docs/pm/state/tasks.yaml").exists())

        resumed = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(project),
            "--project-id",
            "lock-only-project",
            "--mode",
            "standard",
        )
        self.assertEqual(0, resumed.returncode, resumed.stdout)
        self.assertTrue((project / "docs/pm/state/tasks.yaml").is_file())
        postcommit_line = next(
            line
            for line in resumed.stdout.splitlines()
            if line.startswith("POST_COMMIT_COMMAND ")
        )
        postcommit_command = shlex.split(
            postcommit_line.removeprefix("POST_COMMIT_COMMAND ")
        )

        ordinary_replay = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(project),
            "--project-id",
            "lock-only-project",
            "--mode",
            "standard",
        )
        self.assertEqual(
            0, ordinary_replay.returncode, ordinary_replay.stdout
        )
        prepared_replay = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(project),
            "--project-id",
            "lock-only-project",
            "--mode",
            "standard",
            "--migration-prepare",
        )
        self.assertEqual(0, prepared_replay.returncode, prepared_replay.stdout)
        for command in (
            ("git", "add", "-A"),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "initialize AgentDesk control plane",
            ),
        ):
            result = run(*command, cwd=project)
            self.assertEqual(0, result.returncode, result.stdout)
        verified = run(*postcommit_command)
        self.assertEqual(0, verified.returncode, verified.stdout)
        self.assertEqual("durable", json.loads(verified.stdout)["status"])

        legacy = self.root / "legacy-uncommitted-lock"
        legacy.mkdir()
        created = run("git", "init", "-b", "main", cwd=legacy)
        self.assertEqual(0, created.returncode, created.stdout)
        legacy_state = legacy / "docs/pm/state/tasks.yaml"
        legacy_state.parent.mkdir(parents=True)
        legacy_state.write_bytes(
            (scratch / "docs/pm/state/tasks.yaml").read_bytes()
        )
        for command in (
            ("git", "add", "docs/pm/state/tasks.yaml"),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "legacy control plane baseline",
            ),
        ):
            result = run(*command, cwd=legacy)
            self.assertEqual(0, result.returncode, result.stdout)
        legacy_lock = legacy / "docs/pm/AGENTDESK.lock.json"
        legacy_lock.write_bytes(
            (scratch / "docs/pm/AGENTDESK.lock.json").read_bytes()
        )
        blocked = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(legacy),
            "--project-id",
            "lock-only-project",
            "--mode",
            "standard",
        )
        self.assertEqual(2, blocked.returncode, blocked.stdout)
        self.assertIn("lock", blocked.stdout.lower())
        allowed = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(legacy),
            "--project-id",
            "lock-only-project",
            "--mode",
            "standard",
            "--migration-prepare",
        )
        self.assertEqual(0, allowed.returncode, allowed.stdout)

        marker_project = self.root / "marker-only-spoof"
        marker_project.mkdir()
        created = run("git", "init", "-b", "main", cwd=marker_project)
        self.assertEqual(0, created.returncode, created.stdout)
        marker = marker_project / "docs/pm/BOARD.md"
        marker.parent.mkdir(parents=True)
        marker.write_text("legacy board\n", encoding="utf-8")
        for command in (
            ("git", "add", "docs/pm/BOARD.md"),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                "legacy marker baseline",
            ),
        ):
            result = run(*command, cwd=marker_project)
            self.assertEqual(0, result.returncode, result.stdout)
        marker_lock = marker_project / "docs/pm/AGENTDESK.lock.json"
        marker_lock.write_bytes(
            (scratch / "docs/pm/AGENTDESK.lock.json").read_bytes()
        )
        spoofed = run(
            sys.executable,
            str(skill_root / "scripts" / "init_project.py"),
            "--project",
            str(marker_project),
            "--project-id",
            "lock-only-project",
            "--mode",
            "standard",
        )
        self.assertEqual(2, spoofed.returncode, spoofed.stdout)
        self.assertIn("explicit legacy adoption", spoofed.stdout)
        self.assertFalse(
            (marker_project / "docs/pm/state/tasks.yaml").exists()
        )

    def test_release_tools_isolate_packaged_checker_from_pythonpath(self) -> None:
        source = self._create_clean_source_repository()
        injection = self.root / "python-injection"
        injection.mkdir()
        marker = self.root / "sitecustomize-executed"
        (injection / "sitecustomize.py").write_text(
            "\n".join(
                (
                    "import os",
                    "from pathlib import Path",
                    "target = os.environ.get('AGENTDESK_INJECTION_MARKER')",
                    "if target:",
                    "    Path(target).write_text('executed\\n', encoding='utf-8')",
                    "",
                )
            ),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(injection)
        environment["AGENTDESK_INJECTION_MARKER"] = str(marker)
        release = self.root / "releases" / "isolated-checker"
        build = subprocess.run(
            [
                sys.executable,
                "-I",
                str(BUILD_TOOL),
                "--source",
                str(source),
                "--destination",
                str(release),
                "--release-id",
                "isolated-checker",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
            env=environment,
        )
        self.assertEqual(0, build.returncode, build.stdout)
        self.assertFalse(marker.exists())

        activated = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ACTIVATE_TOOL),
                "--release-directory",
                str(release),
                "--install-link",
                str(self.root / "install" / "agentdesk"),
                "--expected-release",
                "isolated-checker",
                "--dry-run",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
            env=environment,
        )
        self.assertEqual(0, activated.returncode, activated.stdout)
        self.assertFalse(marker.exists())

    def test_activation_detects_release_manifest_metadata_tampering(self) -> None:
        source = self._create_clean_source_repository()
        release = self.root / "releases" / "0.2.0-manifest-tamper"
        build = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(release),
            "--release-id",
            "0.2.0-manifest-tamper",
        )
        self.assertEqual(0, build.returncode, build.stdout)

        manifest_path = (
            release / "skills" / "agentdesk" / "release-manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["compatibility"]["reader"]["protocol_versions"].append(
            "agentdesk.protocol/v1"
        )
        manifest_path.chmod(0o644)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o444)

        rejected = run(
            sys.executable,
            str(ACTIVATE_TOOL),
            "--release-directory",
            str(release),
            "--install-link",
            str(self.root / "install" / "agentdesk"),
            "--dry-run",
        )

        self.assertEqual(1, rejected.returncode, rejected.stdout)
        self.assertIn("bundle digest mismatch", rejected.stdout)

    def test_activation_rejects_duplicate_manifest_keys(self) -> None:
        source = self._create_clean_source_repository()
        release = self.root / "releases" / "duplicate-manifest"
        build = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(release),
            "--release-id",
            "duplicate-manifest",
        )
        self.assertEqual(0, build.returncode, build.stdout)
        manifest_path = (
            release / "skills" / "agentdesk" / "release-manifest.json"
        )
        text = manifest_path.read_text(encoding="utf-8")
        self.assertTrue(text.rstrip().endswith("}"))
        duplicate = (
            text.rstrip()[:-1]
            + ',\n  "skill_release": "shadowed-release"\n}\n'
        )
        manifest_path.chmod(0o644)
        manifest_path.write_text(duplicate, encoding="utf-8")
        manifest_path.chmod(0o444)

        rejected = run(
            sys.executable,
            str(ACTIVATE_TOOL),
            "--release-directory",
            str(release),
            "--install-link",
            str(self.root / "install" / "agentdesk"),
            "--dry-run",
        )

        self.assertEqual(1, rejected.returncode, rejected.stdout)
        self.assertIn("duplicate JSON key 'skill_release'", rejected.stdout)

    def test_activation_runs_packaged_checker_before_switching(self) -> None:
        source = self._create_clean_source_repository()
        release = self.root / "releases" / "broken-checker"
        build = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(release),
            "--release-id",
            "broken-checker",
        )
        self.assertEqual(0, build.returncode, build.stdout)
        skill_root = release / "skills" / "agentdesk"
        checker = skill_root / "scripts" / "check_compatibility.py"
        checker.chmod(0o755)
        checker.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print('checker intentionally failed')\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        checker.chmod(0o555)

        module = self._load_activate_tool()
        manifest_path = skill_root / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["bundle_sha256"] = module._bundle_digest(skill_root, manifest)
        manifest_path.chmod(0o644)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o444)
        install_link = self.root / "install" / "agentdesk"

        rejected = run(
            sys.executable,
            str(ACTIVATE_TOOL),
            "--release-directory",
            str(release),
            "--install-link",
            str(install_link),
            "--dry-run",
        )

        self.assertEqual(1, rejected.returncode, rejected.stdout)
        self.assertIn("packaged compatibility checker rejected", rejected.stdout)
        self.assertFalse(install_link.exists())

    def test_activation_does_not_overwrite_a_raced_regular_file(self) -> None:
        source = self._create_clean_source_repository()
        release = self.root / "releases" / "activation-race"
        build = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(release),
            "--release-id",
            "activation-race",
        )
        self.assertEqual(0, build.returncode, build.stdout)
        module = self._load_activate_tool()
        install_link = self.root / "install" / "agentdesk"
        original_symlink_to = Path.symlink_to

        def race_then_create(
            path: Path,
            target: object,
            target_is_directory: bool = False,
        ) -> None:
            if path == install_link:
                path.write_text("owned by another process\n", encoding="utf-8")
            original_symlink_to(
                path,
                target,
                target_is_directory=target_is_directory,
            )

        with mock.patch.object(Path, "symlink_to", new=race_then_create):
            with self.assertRaises(module.ActivationError):
                module.activate(
                    release_directory=release,
                    install_link=install_link,
                    expected_release="activation-race",
                    dry_run=False,
                )

        self.assertTrue(install_link.is_file())
        self.assertFalse(install_link.is_symlink())
        self.assertEqual(
            "owned by another process\n",
            install_link.read_text(encoding="utf-8"),
        )

    def test_activation_rejects_a_symlinked_install_ancestor(self) -> None:
        source = self._create_clean_source_repository()
        release = self.root / "releases" / "safe-release"
        build = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(release),
            "--release-id",
            "safe-release",
        )
        self.assertEqual(0, build.returncode, build.stdout)
        real_install = self.root / "real-install"
        real_install.mkdir()
        linked_install = self.root / "linked-install"
        linked_install.symlink_to(real_install, target_is_directory=True)

        rejected = run(
            sys.executable,
            str(ACTIVATE_TOOL),
            "--release-directory",
            str(release),
            "--install-link",
            str(linked_install / "nested" / "agentdesk"),
            "--dry-run",
        )

        self.assertEqual(1, rejected.returncode, rejected.stdout)
        self.assertIn("must not contain symbolic links", rejected.stdout)

    def test_build_rejects_ignored_untracked_skill_file(self) -> None:
        source = self._create_clean_source_repository()
        repository = source.parents[1]
        exclude = repository / ".git" / "info" / "exclude"
        exclude.write_text(
            exclude.read_text(encoding="utf-8")
            + "\nskills/agentdesk/.env\n",
            encoding="utf-8",
        )
        (source / ".env").write_text(
            "PRIVATE_TOKEN=must-not-be-packaged\n",
            encoding="utf-8",
        )

        result = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(self.root / "releases" / "rejected-secret"),
            "--release-id",
            "rejected-secret",
        )

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("untracked/ignored file(s): .env", result.stdout)

    def test_build_materializes_head_blobs_not_worktree_bytes(self) -> None:
        source = self._create_clean_source_repository()
        repository = source.parents[1]
        relative = "skills/agentdesk/SKILL.md"
        head_blob = run("git", "show", f"HEAD:{relative}", cwd=repository)
        self.assertEqual(0, head_blob.returncode, head_blob.stdout)
        assume_unchanged = run(
            "git",
            "update-index",
            "--assume-unchanged",
            relative,
            cwd=repository,
        )
        self.assertEqual(0, assume_unchanged.returncode, assume_unchanged.stdout)
        skill_md = source / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8") + "\nWORKTREE-ONLY\n",
            encoding="utf-8",
        )
        status = run(
            "git",
            "status",
            "--porcelain=v1",
            "--",
            relative,
            cwd=repository,
        )
        self.assertEqual("", status.stdout)

        release = self.root / "releases" / "head-blob-source"
        result = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(release),
            "--release-id",
            "head-blob-source",
        )

        self.assertEqual(0, result.returncode, result.stdout)
        packaged = (
            release / "skills" / "agentdesk" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertEqual(head_blob.stdout, packaged)
        self.assertNotIn("WORKTREE-ONLY", packaged)

    def test_builder_seals_temp_tree_before_atomic_publish(self) -> None:
        source = self._create_clean_source_repository()
        destination = self.root / "releases" / "sealed-before-publish"
        module = self._load_build_tool()
        original_rename = os.rename
        original_verify = module._verify_read_only_release
        observed = {"verified": False, "renamed": False}

        def inspect_verification(*args: object, **kwargs: object) -> None:
            root = Path(args[0])
            original_verify(*args, **kwargs)
            self.assertEqual(0, stat.S_IMODE(root.stat().st_mode) & 0o222)
            observed["verified"] = True

        def inspect_and_rename(source_path: object, destination_path: object) -> None:
            temporary = Path(source_path)
            observed["renamed"] = True
            self.assertTrue(observed["verified"])
            for path in temporary.rglob("*"):
                if path.is_symlink():
                    self.fail(f"temporary release contains symlink: {path}")
                self.assertEqual(
                    0,
                    stat.S_IMODE(path.stat().st_mode) & 0o222,
                    f"published path was writable: {path}",
                )
            original_rename(source_path, destination_path)

        with mock.patch.object(
            module,
            "_verify_read_only_release",
            side_effect=inspect_verification,
        ), mock.patch.object(module.os, "rename", side_effect=inspect_and_rename):
            module.build_release(
                source=source,
                destination=destination,
                release_id="sealed-before-publish",
            )

        self.assertTrue(observed["verified"])
        self.assertTrue(observed["renamed"])
        self.assertTrue(destination.is_dir())
        self.assertEqual(0, stat.S_IMODE(destination.stat().st_mode) & 0o222)

    def test_builder_does_not_delete_a_raced_in_destination(self) -> None:
        source = self._create_clean_source_repository()
        destination = self.root / "releases" / "raced-release"
        module = self._load_build_tool()

        def race_destination(_: object, destination_path: object) -> None:
            raced = Path(destination_path)
            raced.write_text("owned by another process\n", encoding="utf-8")
            raise FileExistsError("simulated destination race")

        with mock.patch.object(
            module.os, "rename", side_effect=race_destination
        ):
            with self.assertRaises(FileExistsError):
                module.build_release(
                    source=source,
                    destination=destination,
                    release_id="raced-release",
                )

        self.assertTrue(destination.is_file())
        self.assertEqual(
            "owned by another process\n",
            destination.read_text(encoding="utf-8"),
        )

    def test_builder_rejects_a_symlinked_destination_parent(self) -> None:
        source = self._create_clean_source_repository()
        real_parent = self.root / "real-release-parent"
        real_parent.mkdir()
        linked_parent = self.root / "linked-release-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)

        result = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(linked_parent / "unsafe-release"),
            "--release-id",
            "unsafe-release",
        )

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("must not contain symbolic links", result.stdout)
        self.assertFalse((real_parent / "unsafe-release").exists())

    def test_tools_expose_help(self) -> None:
        for tool in (BUILD_TOOL, ACTIVATE_TOOL):
            result = run(sys.executable, str(tool), "--help")
            self.assertEqual(0, result.returncode, result.stdout)
            self.assertIn("usage:", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
