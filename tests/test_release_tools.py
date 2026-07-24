from __future__ import annotations

import importlib.util
import json
import os
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
        release = self.root / "releases" / "0.2.0-test.1"
        build = run(
            sys.executable,
            str(BUILD_TOOL),
            "--source",
            str(source),
            "--destination",
            str(release),
            "--release-id",
            "0.2.0-test.1",
        )
        self.assertEqual(0, build.returncode, build.stdout)
        build_result = json.loads(build.stdout)
        self.assertEqual("0.2.0-test.1", build_result["release_id"])

        skill_root = release / "skills" / "agentdesk"
        manifest = json.loads(
            (skill_root / "release-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual("stable", manifest["release_status"])
        self.assertEqual("0.2.0-test.1", manifest["skill_release"])
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

        lock_path = project / "docs/pm/AGENTDESK.lock.json"
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
            "0.2.0-test.1",
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
            "0.2.0-test.1",
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
