from __future__ import annotations

import copy
import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPOSITORY_ROOT
    / "skills"
    / "agentdesk"
    / "scripts"
    / "check_compatibility.py"
)
SOURCE_COMMIT = "1" * 40
BUNDLE_SHA256 = "2" * 64

SCHEMAS = {
    "acceptance": "agentdesk.acceptance/v2",
    "routes": "agentdesk.routes/v2",
    "tasks": "agentdesk.tasks/v2",
    "transport_receipts": "agentdesk.transport-receipts/v1",
}
RUNTIME_SCHEMAS = {
    "routes": "agentdesk.routes/v2",
    "transport_receipts": "agentdesk.transport-receipts/v1",
}
SCHEMA_VERSIONS = sorted(set(SCHEMAS.values()) | set(RUNTIME_SCHEMAS.values()))


def calculate_bundle_digest(bundle_root: Path, manifest_path: Path) -> str:
    entries = []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_sha256"] = None
    canonical_manifest = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    for path in sorted(
        bundle_root.rglob("*"),
        key=lambda item: item.relative_to(bundle_root).as_posix(),
    ):
        if path.is_dir():
            continue
        relative = path.relative_to(bundle_root).as_posix()
        effective_bytes = (
            canonical_manifest if path == manifest_path else path.read_bytes()
        )
        entries.append((relative, hashlib.sha256(effective_bytes).hexdigest()))
    aggregate = hashlib.sha256()
    for relative, file_digest in entries:
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(file_digest.encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def release_manifest(
    *,
    source_commit: Optional[str] = SOURCE_COMMIT,
    bundle_sha256: Optional[str] = BUNDLE_SHA256,
    release_status: str = "stable",
) -> Dict[str, Any]:
    return {
        "schema_version": "agentdesk.release-manifest/v1",
        "skill_release": "0.2.0",
        "release_status": release_status,
        "source_commit": source_commit,
        "bundle_sha256": bundle_sha256,
        "digest_scope": "all-files-with-canonical-release-manifest",
        "protocol_version": "agentdesk.protocol/v2",
        "schema_set": {
            "id": "agentdesk.schema-set/v1",
            "schemas": copy.deepcopy(SCHEMAS),
        },
        "runtime_adapter": {
            "id": "codex",
            "contract_version": "agentdesk.codex-runtime-adapter/v1",
            "runtime_schemas": copy.deepcopy(RUNTIME_SCHEMAS),
        },
        "compatibility": {
            "project_lock_schemas": ["agentdesk.project-lock/v1"],
            "reader": {
                "protocol_versions": ["agentdesk.protocol/v2"],
                "schema_versions": list(SCHEMA_VERSIONS),
                "runtime_adapter_contracts": [
                    "agentdesk.codex-runtime-adapter/v1"
                ],
            },
            "writer": {
                "protocol_versions": ["agentdesk.protocol/v2"],
                "schema_versions": list(SCHEMA_VERSIONS),
                "runtime_adapter_contracts": [
                    "agentdesk.codex-runtime-adapter/v1"
                ],
                "requires_exact_release": True,
                "requires_verified_bundle": True,
            },
        },
    }


def project_lock(
    *,
    source_commit: Optional[str] = SOURCE_COMMIT,
    bundle_sha256: Optional[str] = BUNDLE_SHA256,
) -> Dict[str, Any]:
    return {
        "schema_version": "agentdesk.project-lock/v1",
        "project_id": "test-project",
        "skill": {
            "release": "0.2.0",
            "source_commit": source_commit,
            "bundle_sha256": bundle_sha256,
        },
        "protocol": {"version": "agentdesk.protocol/v2"},
        "schemas": copy.deepcopy(SCHEMAS),
        "runtime_adapter": {
            "id": "codex",
            "contract_version": "agentdesk.codex-runtime-adapter/v1",
            "runtime_schemas": copy.deepcopy(RUNTIME_SCHEMAS),
        },
        "operation_compatibility": {
            "reader": {
                "protocol_versions": ["agentdesk.protocol/v2"],
                "schema_versions": list(SCHEMA_VERSIONS),
                "runtime_adapter_contracts": [
                    "agentdesk.codex-runtime-adapter/v1"
                ],
            },
            "writer": {
                "protocol_versions": ["agentdesk.protocol/v2"],
                "schema_versions": list(SCHEMA_VERSIONS),
                "runtime_adapter_contracts": [
                    "agentdesk.codex-runtime-adapter/v1"
                ],
                "requires_exact_release": True,
                "requires_verified_bundle": True,
            },
        },
    }


class VersionLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="agentdesk-version-lock-"
        )
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name).resolve()
        self.addCleanup(self.make_bundle_writable)
        self.project = self.root / "project"
        self.project.mkdir()
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.script = self.bundle / "scripts" / "check_compatibility.py"
        self.script.parent.mkdir()
        shutil.copy2(SCRIPT, self.script)
        self.bundle_payload = self.bundle / "SKILL.md"
        self.bundle_payload.write_text("test bundle\n", encoding="utf-8")
        self.manifest_path = self.bundle / "release-manifest.json"
        self.write_json(
            self.manifest_path,
            release_manifest(bundle_sha256=None),
        )
        self.bundle_digest = calculate_bundle_digest(
            self.bundle, self.manifest_path
        )
        self.write_json(
            self.manifest_path,
            release_manifest(bundle_sha256=self.bundle_digest),
        )

    def write_json(self, path: Path, value: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_lock(self, value: Dict[str, Any]) -> None:
        self.write_json(
            self.project / "docs" / "pm" / "AGENTDESK.lock.json",
            value,
        )
        state_path = self.project / "docs" / "pm" / "state" / "tasks.yaml"
        if not state_path.exists():
            self.write_json(
                state_path,
                {
                    "schema_version": "agentdesk.tasks/v2",
                    "project_id": "test-project",
                    "tasks": [],
                },
            )

    def seal_bundle(self) -> None:
        for path in self.bundle.rglob("*"):
            if path.is_symlink():
                continue
            if path.is_file():
                mode = stat.S_IMODE(path.stat().st_mode)
                path.chmod(0o555 if mode & 0o111 else 0o444)
        for path in sorted(
            (path for path in self.bundle.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            path.chmod(0o555)
        self.bundle.chmod(0o555)

    def make_bundle_writable(self) -> None:
        if not self.bundle.exists():
            return
        try:
            self.bundle.chmod(0o700)
        except OSError:
            return
        for path in self.bundle.rglob("*"):
            if path.is_symlink():
                continue
            try:
                path.chmod(0o700 if path.is_dir() else 0o600)
            except OSError:
                pass

    def commit_current_lock(self, message: str = "pin project release") -> str:
        commands = (
            ("git", "init", "-b", "main"),
            ("git", "add", "docs/pm/AGENTDESK.lock.json"),
            (
                "git",
                "-c",
                "user.name=AgentDesk Test",
                "-c",
                "user.email=agentdesk@example.invalid",
                "commit",
                "-m",
                message,
            ),
        )
        for command in commands:
            result = subprocess.run(
                command,
                cwd=self.project,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stdout)
        result = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=self.project,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout)
        return result.stdout.strip()

    def write_manifest_with_digest(self, value: Dict[str, Any]) -> str:
        manifest = copy.deepcopy(value)
        manifest["bundle_sha256"] = None
        self.write_json(self.manifest_path, manifest)
        digest = calculate_bundle_digest(self.bundle, self.manifest_path)
        manifest["bundle_sha256"] = digest
        self.write_json(self.manifest_path, manifest)
        self.bundle_digest = digest
        return digest

    def exact_lock(self) -> Dict[str, Any]:
        return project_lock(bundle_sha256=self.bundle_digest)

    def run_check(
        self,
        operation: str,
        *,
        manifest_path: Optional[Path] = None,
        seal_bundle: bool = True,
    ) -> Tuple[subprocess.CompletedProcess[str], Dict[str, Any]]:
        if seal_bundle:
            self.seal_bundle()
        result = subprocess.run(
            [
                sys.executable,
                str(self.script),
                "--project",
                str(self.project),
                "--operation",
                operation,
                "--release-manifest",
                str(manifest_path or self.manifest_path),
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
        except json.JSONDecodeError as exc:  # pragma: no cover - clearer failure
            self.fail(
                f"checker did not emit JSON: {exc}\n"
                f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
            )
        return result, payload

    def assert_code(
        self, payload: Dict[str, Any], collection: str, code: str
    ) -> None:
        self.assertIn(
            code,
            [item["code"] for item in payload[collection]],
            payload,
        )

    def test_help_lists_required_interface(self) -> None:
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
            "--operation",
            "--release-manifest",
            "--json",
        ):
            self.assertIn(option, result.stdout)
        for operation in ("read", "validate", "write", "migrate"):
            self.assertIn(operation, result.stdout)

    def test_legacy_unpinned_is_warning_for_read_only_operations(self) -> None:
        for operation in ("read", "validate"):
            with self.subTest(operation=operation):
                result, payload = self.run_check(operation)
                self.assertEqual(0, result.returncode, payload)
                self.assertTrue(payload["compatible"])
                self.assertEqual("warning", payload["status"])
                self.assertEqual(
                    "legacy-unpinned", payload["project_lock"]["state"]
                )
                self.assert_code(payload, "warnings", "legacy_unpinned")

    def test_legacy_unpinned_fails_closed_for_mutating_operations(self) -> None:
        for operation in ("write", "migrate"):
            with self.subTest(operation=operation):
                result, payload = self.run_check(operation)
                self.assertEqual(1, result.returncode, payload)
                self.assertFalse(payload["compatible"])
                self.assertEqual("incompatible", payload["status"])
                self.assert_code(payload, "errors", "legacy_unpinned")

    def test_verified_exact_lock_allows_every_operation(self) -> None:
        self.write_lock(self.exact_lock())

        for operation in ("read", "validate", "write", "migrate"):
            with self.subTest(operation=operation):
                result, payload = self.run_check(operation)
                self.assertEqual(0, result.returncode, payload)
                self.assertTrue(payload["compatible"])
                self.assertEqual("compatible", payload["status"])
                self.assertEqual([], payload["warnings"])
                self.assertEqual([], payload["errors"])

    def test_migration_is_blocked_by_an_active_attempt(self) -> None:
        self.write_lock(self.exact_lock())
        task_card_commit = self.commit_current_lock()
        self.write_json(
            self.project / "docs" / "pm" / "state" / "tasks.yaml",
            {
                "schema_version": "agentdesk.tasks/v2",
                "project_id": "test-project",
                "tasks": [
                    {
                        "task_id": "TC-001",
                        "state": "in_progress",
                        "attempt": 1,
                        "blocked_attempt_valid": None,
                        "current_dispatch": {"dispatch_id": "DSP-001"},
                        "task_card_commit": task_card_commit,
                    }
                ],
            },
        )

        write_result, write_payload = self.run_check("write")
        self.assertEqual(0, write_result.returncode, write_payload)

        migrate_result, migrate_payload = self.run_check("migrate")
        self.assertEqual(1, migrate_result.returncode, migrate_payload)
        self.assert_code(migrate_payload, "errors", "active_attempts")

    def test_writer_rejects_a_mutable_bundle(self) -> None:
        self.write_lock(self.exact_lock())

        result, payload = self.run_check("write", seal_bundle=False)

        self.assertEqual(1, result.returncode, payload)
        self.assert_code(payload, "errors", "mutable_bundle")

    def test_writer_rejects_project_id_mismatch(self) -> None:
        self.write_lock(self.exact_lock())
        self.write_json(
            self.project / "docs" / "pm" / "state" / "tasks.yaml",
            {
                "schema_version": "agentdesk.tasks/v2",
                "project_id": "different-project",
                "tasks": [],
            },
        )

        result, payload = self.run_check("write")

        self.assertEqual(1, result.returncode, payload)
        self.assert_code(payload, "errors", "project_id_mismatch")

    def test_writer_rejects_a_symlinked_state_parent(self) -> None:
        self.write_lock(self.exact_lock())
        state_directory = self.project / "docs" / "pm" / "state"
        shutil.rmtree(state_directory)
        external_state = self.root / "external-state"
        external_state.mkdir()
        self.write_json(
            external_state / "tasks.yaml",
            {
                "schema_version": "agentdesk.tasks/v2",
                "project_id": "test-project",
                "tasks": [],
            },
        )
        state_directory.symlink_to(external_state, target_is_directory=True)

        result, payload = self.run_check("write")

        self.assertEqual(1, result.returncode, payload)
        self.assert_code(payload, "errors", "unsafe_symlink")

    def test_active_writer_rejects_lock_from_an_older_commit(self) -> None:
        old_lock = self.exact_lock()
        old_lock["skill"]["release"] = "old-release"
        self.write_lock(old_lock)
        task_card_commit = self.commit_current_lock("commit old lock")
        self.write_lock(self.exact_lock())
        self.write_json(
            self.project / "docs" / "pm" / "state" / "tasks.yaml",
            {
                "schema_version": "agentdesk.tasks/v2",
                "project_id": "test-project",
                "tasks": [
                    {
                        "task_id": "TC-001",
                        "state": "in_progress",
                        "attempt": 1,
                        "blocked_attempt_valid": None,
                        "current_dispatch": {"dispatch_id": "DSP-001"},
                        "task_card_commit": task_card_commit,
                    }
                ],
            },
        )

        result, payload = self.run_check("write")

        self.assertEqual(1, result.returncode, payload)
        self.assert_code(
            payload, "errors", "active_attempt_lock_mismatch"
        )

    def test_mutating_operations_reject_ambiguous_task_state(self) -> None:
        self.write_lock(self.exact_lock())
        cases = (
            (
                "unknown-schema",
                {
                    "schema_version": "agentdesk.tasks/v99",
                    "project_id": "test-project",
                    "tasks": [],
                },
            ),
            (
                "unknown-state",
                {
                    "schema_version": "agentdesk.tasks/v2",
                    "project_id": "test-project",
                    "tasks": [
                        {
                            "task_id": "TC-001",
                            "state": "inprogres",
                            "attempt": 1,
                            "blocked_attempt_valid": None,
                            "current_dispatch": None,
                        }
                    ],
                },
            ),
            (
                "missing-attempt",
                {
                    "schema_version": "agentdesk.tasks/v2",
                    "project_id": "test-project",
                    "tasks": [
                        {
                            "task_id": "TC-001",
                            "state": "ready",
                            "blocked_attempt_valid": None,
                            "current_dispatch": None,
                        }
                    ],
                },
            ),
            (
                "active-without-dispatch",
                {
                    "schema_version": "agentdesk.tasks/v2",
                    "project_id": "test-project",
                    "tasks": [
                        {
                            "task_id": "TC-001",
                            "state": "in_progress",
                            "attempt": 1,
                            "blocked_attempt_valid": None,
                            "current_dispatch": None,
                        }
                    ],
                },
            ),
            (
                "blocked-with-unknown-attempt-validity",
                {
                    "schema_version": "agentdesk.tasks/v2",
                    "project_id": "test-project",
                    "tasks": [
                        {
                            "task_id": "TC-001",
                            "state": "blocked",
                            "attempt": 1,
                            "blocked_attempt_valid": None,
                            "current_dispatch": None,
                        }
                    ],
                },
            ),
        )
        for label, state in cases:
            with self.subTest(case=label):
                self.write_json(
                    self.project / "docs" / "pm" / "state" / "tasks.yaml",
                    state,
                )
                for operation in ("write", "migrate"):
                    with self.subTest(operation=operation):
                        result, payload = self.run_check(operation)
                        self.assertEqual(1, result.returncode, payload)
                        self.assert_code(
                            payload, "errors", "invalid_task_state"
                        )

    def test_development_identity_warns_for_read_and_blocks_write(self) -> None:
        self.write_json(
            self.manifest_path,
            release_manifest(source_commit=None, bundle_sha256=None),
        )
        self.write_lock(project_lock(source_commit=None, bundle_sha256=None))

        read_result, read_payload = self.run_check("read")
        self.assertEqual(0, read_result.returncode, read_payload)
        self.assertEqual("warning", read_payload["status"])
        self.assert_code(
            read_payload, "warnings", "release_identity_unverified"
        )
        self.assert_code(
            read_payload, "warnings", "project_identity_unverified"
        )

        write_result, write_payload = self.run_check("write")
        self.assertEqual(1, write_result.returncode, write_payload)
        self.assert_code(
            write_payload, "errors", "release_identity_unverified"
        )
        self.assert_code(
            write_payload, "errors", "project_identity_unverified"
        )

    def test_bundle_tamper_warns_for_read_and_blocks_write(self) -> None:
        self.write_lock(self.exact_lock())
        self.bundle_payload.write_text("tampered bundle\n", encoding="utf-8")

        read_result, read_payload = self.run_check("read")
        self.assertEqual(0, read_result.returncode, read_payload)
        self.assertEqual("warning", read_payload["status"])
        self.assertFalse(read_payload["release"]["bundle_verified"])
        self.assert_code(
            read_payload, "warnings", "bundle_digest_mismatch"
        )

        write_result, write_payload = self.run_check("write")
        self.assertEqual(1, write_result.returncode, write_payload)
        self.assert_code(
            write_payload, "errors", "bundle_digest_mismatch"
        )

    def test_release_manifest_metadata_tamper_changes_bundle_digest(self) -> None:
        self.write_lock(self.exact_lock())
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["unexpected_metadata"] = "tampered"
        self.write_json(self.manifest_path, manifest)

        read_result, read_payload = self.run_check("read")
        self.assertEqual(0, read_result.returncode, read_payload)
        self.assert_code(
            read_payload, "warnings", "bundle_digest_mismatch"
        )

        write_result, write_payload = self.run_check("write")
        self.assertEqual(1, write_result.returncode, write_payload)
        self.assert_code(
            write_payload, "errors", "bundle_digest_mismatch"
        )

    def test_non_stable_release_cannot_authorize_writer_or_migration(self) -> None:
        digest = self.write_manifest_with_digest(
            release_manifest(bundle_sha256=None, release_status="candidate")
        )
        self.write_lock(project_lock(bundle_sha256=digest))

        read_result, read_payload = self.run_check("read")
        self.assertEqual(0, read_result.returncode, read_payload)
        self.assertTrue(read_payload["compatible"])

        for operation in ("write", "migrate"):
            with self.subTest(operation=operation):
                result, payload = self.run_check(operation)
                self.assertEqual(1, result.returncode, payload)
                self.assert_code(
                    payload, "errors", "release_status_not_stable"
                )

    def test_external_manifest_override_is_read_only(self) -> None:
        self.write_lock(self.exact_lock())
        external_manifest = self.root / "candidate" / "release-manifest.json"
        external_manifest.parent.mkdir()
        shutil.copy2(self.manifest_path, external_manifest)

        read_result, read_payload = self.run_check(
            "read", manifest_path=external_manifest
        )
        self.assertEqual(0, read_result.returncode, read_payload)
        self.assertNotIn(
            "external_release_manifest_forbidden",
            [item["code"] for item in read_payload["warnings"]],
        )

        for operation in ("write", "migrate"):
            with self.subTest(operation=operation):
                result, payload = self.run_check(
                    operation, manifest_path=external_manifest
                )
                self.assertEqual(1, result.returncode, payload)
                self.assert_code(
                    payload,
                    "errors",
                    "external_release_manifest_forbidden",
                )

    def test_writer_policy_tamper_breaks_exact_identity(self) -> None:
        lock = self.exact_lock()
        lock["operation_compatibility"]["writer"][
            "requires_verified_bundle"
        ] = False
        self.write_lock(lock)

        result, payload = self.run_check("write")

        self.assertEqual(1, result.returncode, payload)
        self.assert_code(payload, "errors", "release_identity_mismatch")
        self.assertIn(
            "operation_compatibility.writer",
            payload["errors"][-1]["message"],
        )

    def test_reader_policy_tamper_breaks_exact_identity(self) -> None:
        manifest = release_manifest(bundle_sha256=None)
        manifest["compatibility"]["reader"]["schema_versions"].append(
            "agentdesk.tasks/v1"
        )
        digest = self.write_manifest_with_digest(manifest)
        lock = project_lock(bundle_sha256=digest)
        lock["operation_compatibility"]["reader"] = copy.deepcopy(
            manifest["compatibility"]["reader"]
        )
        lock["operation_compatibility"]["reader"]["schema_versions"].remove(
            "agentdesk.tasks/v1"
        )
        self.write_lock(lock)

        result, payload = self.run_check("validate")

        self.assertEqual(0, result.returncode, payload)
        self.assert_code(payload, "warnings", "release_identity_mismatch")
        self.assertIn(
            "operation_compatibility.reader",
            payload["warnings"][-1]["message"],
        )

    def test_bundle_symlink_is_rejected(self) -> None:
        self.write_lock(self.exact_lock())
        (self.bundle / "linked-skill.md").symlink_to(self.bundle_payload)

        read_result, read_payload = self.run_check("read")
        self.assertEqual(0, read_result.returncode, read_payload)
        self.assert_code(read_payload, "warnings", "unsafe_bundle")

        write_result, write_payload = self.run_check("write")
        self.assertEqual(1, write_result.returncode, write_payload)
        self.assert_code(write_payload, "errors", "unsafe_bundle")

    def test_repository_candidate_manifest_and_lock_template_agree(self) -> None:
        candidate_manifest_path = (
            REPOSITORY_ROOT
            / "skills"
            / "agentdesk"
            / "release-manifest.json"
        )
        candidate_manifest = json.loads(
            candidate_manifest_path.read_text(encoding="utf-8")
        )
        template_path = (
            REPOSITORY_ROOT
            / "skills"
            / "agentdesk"
            / "assets"
            / "project-template"
            / "docs"
            / "pm"
            / "AGENTDESK.lock.json"
        )
        rendered = template_path.read_text(encoding="utf-8")
        replacements = {
            "{{PROJECT_ID}}": "candidate-contract-test",
            "{{AGENTDESK_SKILL_RELEASE}}": candidate_manifest[
                "skill_release"
            ],
            "{{AGENTDESK_SOURCE_COMMIT_JSON}}": json.dumps(
                candidate_manifest["source_commit"]
            ),
            "{{AGENTDESK_BUNDLE_SHA256_JSON}}": json.dumps(
                candidate_manifest["bundle_sha256"]
            ),
        }
        for placeholder, replacement in replacements.items():
            rendered = rendered.replace(placeholder, replacement)
        lock_path = self.project / "docs" / "pm" / "AGENTDESK.lock.json"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text(rendered, encoding="utf-8")

        result, payload = self.run_check(
            "read", manifest_path=candidate_manifest_path
        )

        self.assertEqual(0, result.returncode, payload)
        self.assertEqual("warning", payload["status"])
        self.assertEqual("pinned", payload["project_lock"]["state"])
        self.assert_code(
            payload, "warnings", "release_identity_unverified"
        )
        self.assert_code(
            payload, "warnings", "project_identity_unverified"
        )

    def test_different_release_may_read_but_cannot_write(self) -> None:
        lock = self.exact_lock()
        lock["skill"]["release"] = "0.1.0"
        lock["skill"]["source_commit"] = "a" * 40
        lock["skill"]["bundle_sha256"] = "b" * 64
        self.write_lock(lock)

        read_result, read_payload = self.run_check("validate")
        self.assertEqual(0, read_result.returncode, read_payload)
        self.assertEqual("warning", read_payload["status"])
        self.assert_code(
            read_payload, "warnings", "release_identity_mismatch"
        )

        write_result, write_payload = self.run_check("migrate")
        self.assertEqual(1, write_result.returncode, write_payload)
        self.assert_code(
            write_payload, "errors", "release_identity_mismatch"
        )

    def test_unknown_project_lock_schema_major_fails_closed(self) -> None:
        lock = self.exact_lock()
        lock["schema_version"] = "agentdesk.project-lock/v2"
        self.write_lock(lock)

        result, payload = self.run_check("read")
        self.assertEqual(1, result.returncode, payload)
        self.assert_code(payload, "errors", "unknown_schema_major")

    def test_unknown_component_schema_major_fails_closed(self) -> None:
        lock = self.exact_lock()
        lock["schemas"]["tasks"] = "agentdesk.tasks/v99"
        old_version = "agentdesk.tasks/v2"
        new_version = "agentdesk.tasks/v99"
        for policy in ("reader", "writer"):
            versions = lock["operation_compatibility"][policy][
                "schema_versions"
            ]
            lock["operation_compatibility"][policy]["schema_versions"] = [
                new_version if value == old_version else value
                for value in versions
            ]
        self.write_lock(lock)

        result, payload = self.run_check("validate")
        self.assertEqual(1, result.returncode, payload)
        self.assert_code(payload, "errors", "unknown_schema_major")

    def test_writer_compatibility_must_exactly_cover_project(self) -> None:
        lock = self.exact_lock()
        lock["operation_compatibility"]["writer"]["schema_versions"].append(
            "agentdesk.extra/v1"
        )
        self.write_lock(lock)

        result, payload = self.run_check("write")
        self.assertEqual(1, result.returncode, payload)
        self.assert_code(payload, "errors", "incomplete_compatibility")

    def test_non_json_lock_fails_closed(self) -> None:
        lock_path = self.project / "docs" / "pm" / "AGENTDESK.lock.json"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text(
            "schema_version: agentdesk.project-lock/v1\n",
            encoding="utf-8",
        )

        result, payload = self.run_check("read")
        self.assertEqual(1, result.returncode, payload)
        self.assert_code(payload, "errors", "invalid_json")


if __name__ == "__main__":
    unittest.main()
