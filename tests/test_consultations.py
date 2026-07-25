from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "agentdesk"
VALIDATOR_PATH = SKILL_ROOT / "scripts" / "validate_consultations.py"
RENDERER_PATH = SKILL_ROOT / "scripts" / "render_views.py"
SPEC = importlib.util.spec_from_file_location("validate_consultations", VALIDATOR_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import guard
    raise RuntimeError(f"cannot load validator from {VALIDATOR_PATH}")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
RENDERER_SPEC = importlib.util.spec_from_file_location("render_views", RENDERER_PATH)
if RENDERER_SPEC is None or RENDERER_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"cannot load renderer from {RENDERER_PATH}")
RENDERER = importlib.util.module_from_spec(RENDERER_SPEC)
RENDERER_SPEC.loader.exec_module(RENDERER)


TIMESTAMP = "2026-07-21T08:00:00Z"
DIGEST_SCOPE = "all-files-with-canonical-release-manifest"


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    canonical = copy.deepcopy(manifest)
    canonical["bundle_sha256"] = None
    return (
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _bundle_digest(bundle: Path, manifest_path: Path) -> str:
    aggregate = hashlib.sha256()
    for path in sorted(
        bundle.rglob("*"),
        key=lambda item: item.relative_to(bundle).as_posix(),
    ):
        if path.is_dir():
            continue
        relative = path.relative_to(bundle).as_posix()
        data = (
            _canonical_manifest_bytes(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
            if path == manifest_path
            else path.read_bytes()
        )
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(hashlib.sha256(data).hexdigest().encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def _make_tree_read_only(root: Path) -> None:
    files = [path for path in root.rglob("*") if path.is_file()]
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for path in files:
        path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555)
    root.chmod(0o555)


def _make_tree_writable(root: Path) -> None:
    if not root.exists():
        return
    root.chmod(0o755)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)


class ConsultationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.release_bundle = self.root / "release"
        self.release_bundle.mkdir()
        release_scripts = self.release_bundle / "scripts"
        release_scripts.mkdir()
        self.renderer_path = release_scripts / "render_views.py"
        shutil.copy2(RENDERER_PATH, self.renderer_path)
        shutil.copy2(
            SKILL_ROOT / "scripts" / "check_compatibility.py",
            release_scripts / "check_compatibility.py",
        )
        shutil.copy2(
            SKILL_ROOT / "scripts" / "validate_consultations.py",
            release_scripts / "validate_consultations.py",
        )
        self.release_manifest = self.release_bundle / "release-manifest.json"
        manifest = json.loads(
            (SKILL_ROOT / "release-manifest.json").read_text(encoding="utf-8")
        )
        manifest.update(
            {
                "skill_release": "consultation-test-0.2.0",
                "release_status": "stable",
                "source_commit": "a" * 40,
                "bundle_sha256": None,
                "digest_scope": DIGEST_SCOPE,
            }
        )
        self.release_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["bundle_sha256"] = _bundle_digest(
            self.release_bundle, self.release_manifest
        )
        self.release_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.release_identity = manifest
        self.addCleanup(_make_tree_writable, self.release_bundle)
        _make_tree_read_only(self.release_bundle)

    def _write_json(self, relative: str, value: object) -> Path:
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def _policy(self) -> dict[str, object]:
        return {
            "schema_version": "agentdesk.consultation-policy/v1",
            "enabled": True,
            "authorization_mode": "ask",
            "max_seats": 3,
            "max_rounds": 2,
            "require_independent_first_round": True,
            "allow_recursive_sessions": False,
            "default_access": "read_only",
            "require_leader_synthesis": True,
            "require_owner_confirmation_for_derivation": True,
        }

    def _record(
        self,
        *,
        seat_count: int = 1,
        state: str = "closed",
        authorized: bool = True,
    ) -> dict[str, object]:
        consultation_id = "C001"
        topic = "Should the account export feature ship?"
        short_topic = "Export decision"
        root = self.project / "docs/pm/consultations/C001"
        root.mkdir(parents=True, exist_ok=True)
        brief_path = root / "brief-r1.md"
        brief_path.write_text(
            "\n".join(
                (
                    "---",
                    "schema_version: agentdesk.consultation-brief/v1",
                    "consultation_id: C001",
                    f'topic: "{topic}"',
                    f'short_topic: "{short_topic}"',
                    "decision_owner: Owner",
                    "authorization_id: CA-C001-A1B2",
                    "round_limit: 1",
                    "created_by: Leader",
                    f'created_at: "{TIMESTAMP}"',
                    "---",
                    "## 为什么需要研讨",
                    "Need independent evidence.",
                    "## 需要回答的问题",
                    topic,
                    "## 已知背景",
                    "Known facts.",
                    "## 关键假设",
                    "One assumption.",
                    "## 范围",
                    "Read-only advice.",
                    "## 决策标准",
                    "Evidence quality.",
                    "## 可读上下文",
                    "Repository-safe context for every seat.",
                    "## 公共安全和权限边界",
                    "No writes.",
                    "## 回调要求",
                    "Proactive callback.",
                    "",
                )
            ),
            encoding="utf-8",
        )
        synthesis_path = root / "synthesis.md"
        synthesis_path.write_text(
            "\n".join(
                (
                    "---",
                    "schema_version: agentdesk.consultation-synthesis/v1",
                    "consultation_id: C001",
                    f'topic: "{topic}"',
                    "rounds_used: 1",
                    "outcome_status: proposed",
                    "confirmation_required: true",
                    "created_by: Leader",
                    f'created_at: "{TIMESTAMP}"',
                    "---",
                    "## 席位覆盖",
                    "Complete.",
                    "## 一致共识",
                    "Consensus.",
                    "## 条件性共识",
                    "Conditional consensus.",
                    "## 关键分歧",
                    "Disagreement.",
                    "## 少数意见",
                    "Minority view.",
                    "## 关键假设与残余风险",
                    "Residual risk.",
                    "## Leader 综合建议",
                    "Recommendation.",
                    "## 总体置信度",
                    "medium",
                    "## 需要 User / Owner 裁决",
                    "Confirm.",
                    "## 不完整性与降级",
                    "None.",
                    "## 确认门禁",
                    "Proposed only.",
                    "",
                )
            ),
            encoding="utf-8",
        )

        seats: list[dict[str, object]] = []
        responses: list[dict[str, object]] = []
        titles: list[str] = []
        for index in range(1, seat_count + 1):
            seat_no = f"{index:02d}"
            seat_id = f"{consultation_id}.{seat_no}"
            seat_name = f"Advisor {index}"
            title = f"{seat_id} . {seat_name}｜{short_topic}"
            titles.append(title)
            seats.append(
                {
                    "seat_no": seat_no,
                    "seat_id": seat_id,
                    "seat_name": seat_name,
                    "expected_thread_title": title,
                    "focus": f"Independent focus {index}",
                    "model_requirement": {
                        "minimum_tier": "advanced",
                        "deliberation_tier": "deep",
                        "required_capabilities": ["analysis"],
                    },
                    "status": "responded" if state != "draft" else "planned",
                }
            )
            response_path = root / "responses" / f"{seat_no}-r1.md"
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_path.write_text(
                "\n".join(
                    (
                        "---",
                        "schema_version: agentdesk.consultation-response/v1",
                        "consultation_id: C001",
                        f"response_id: {seat_id}.R1",
                        f"seat_id: {seat_id}",
                        f'seat_name: "{seat_name}"',
                        "round: 1",
                        "callback_id: null",
                        "response_status: completed",
                        "transport_mode: proactive_callback",
                        "confidence: medium",
                        f'created_at: "{TIMESTAMP}"',
                        "---",
                        "## 推荐结论",
                        "Recommendation.",
                        "## 核心依据",
                        "Evidence.",
                        "## 关键假设",
                        "Assumption.",
                        "## 风险与反例",
                        "Risk.",
                        "## 不确定事项",
                        "Unknown.",
                        "## 会改变本结论的信息",
                        "New evidence.",
                        "## 建议下一步",
                        "Confirm.",
                        "## 置信度",
                        "medium",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            responses.append(
                {
                    "response_id": f"{seat_id}.R1",
                    "seat_id": seat_id,
                    "status": "received",
                    "path": response_path.relative_to(self.project).as_posix(),
                    "callback_id": None,
                }
            )

        authorization = None
        if authorized:
            authorization = {
                "authorization_id": "CA-C001-A1B2",
                "kind": "explicit_batch",
                "granted_by": "Owner",
                "granted_at": TIMESTAMP,
                "topic_scope": topic,
                "max_visible_sessions": seat_count,
                "max_rounds": 1,
                "read_only_required": True,
                "authorized_titles": titles,
            }

        if state == "draft":
            rounds: list[dict[str, object]] = []
            synthesis: object = None
            outcome = "none"
            confirmation = {
                "confirmation_id": None,
                "status": "pending",
                "confirmed_by": None,
                "confirmed_scope": [],
                "recorded_at": None,
            }
        else:
            rounds = [
                {
                    "round": 1,
                    "independent": True,
                    "brief_path": brief_path.relative_to(self.project).as_posix(),
                    "brief_digest": "sha256:"
                    + hashlib.sha256(brief_path.read_bytes()).hexdigest(),
                    "trigger_reason": "Independent specialist analysis",
                    "responses": responses,
                }
            ]
            synthesis = synthesis_path.relative_to(self.project).as_posix()
            outcome = "confirmed" if state == "closed" else "proposed"
            confirmation = {
                "confirmation_id": "CC-C001-C3D4" if state == "closed" else None,
                "status": "confirmed" if state == "closed" else "pending",
                "confirmed_by": "Owner" if state == "closed" else None,
                "confirmed_scope": ["Use the recommendation"] if state == "closed" else [],
                "recorded_at": TIMESTAMP if state == "closed" else None,
            }

        return {
            "schema_version": "agentdesk.consultation/v1",
            "consultation_id": consultation_id,
            "topic": topic,
            "short_topic": short_topic,
            "state": state,
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
            "cancellation_reason": None,
            "creation_authorization": authorization,
            "seat_count": seat_count,
            "round_limit": 1,
            "seats": seats,
            "rounds": rounds,
            "synthesis_path": synthesis,
            "outcome_status": outcome,
            "confirmation": confirmation,
            "derived_refs": {"decision_ids": [], "task_card_paths": []},
        }

    def _runtime(self, record: dict[str, object]) -> dict[str, object]:
        source_thread_id = "thread-leader-c001"
        routes: dict[str, object] = {}
        prompts: dict[str, object] = {}
        response_receipts: dict[str, object] = {}
        seats = record["seats"]
        assert isinstance(seats, list)
        rounds = record.get("rounds")
        first_round = (
            rounds[0]
            if isinstance(rounds, list) and rounds and isinstance(rounds[0], dict)
            else {}
        )
        brief_digest = first_round.get("brief_digest")
        public_responses = first_round.get("responses")
        public_responses = public_responses if isinstance(public_responses, list) else []
        for raw_seat in seats:
            assert isinstance(raw_seat, dict)
            seat_id = str(raw_seat["seat_id"])
            thread_id = "thread-" + seat_id.lower().replace(".", "-")
            routes[seat_id] = {
                "expected_title": raw_seat["expected_thread_title"],
                "actual_title": raw_seat["expected_thread_title"],
                "thread_id": thread_id,
                "host_id": "host-main",
                "created_at": TIMESTAMP,
                "creation_evidence": "provider-create-" + seat_id,
                "status": "verified",
                "verified_at": TIMESTAMP,
                "model_binding": {
                    "requested_model": None,
                    "request_source": None,
                    "actual_model": None,
                    "status": "default_unverified",
                    "provider_evidence": None,
                },
            }
            prompt_key = f"{seat_id}.R1"
            prompts[prompt_key] = {
                "prompt_id": "PROMPT-" + seat_id,
                "seat_id": seat_id,
                "round": 1,
                "source_thread_id": source_thread_id,
                "destination_thread_id": thread_id,
                "brief_digest": brief_digest,
                "status": "acknowledged",
                "provider_receipt": "provider-prompt-" + seat_id,
                "sent_at": TIMESTAMP,
            }
            callback_id = "CALLBACK-" + seat_id
            public_response = next(
                (
                    item
                    for item in public_responses
                    if isinstance(item, dict) and item.get("seat_id") == seat_id
                ),
                None,
            )
            response_digest = None
            if isinstance(public_response, dict):
                raw_path = public_response.get("path")
                if isinstance(raw_path, str):
                    response_path = self.project / raw_path
                    if response_path.is_file():
                        response_digest = "sha256:" + hashlib.sha256(
                            response_path.read_bytes()
                        ).hexdigest()
            response_receipts[callback_id] = {
                "callback_id": callback_id,
                "prompt_id": "PROMPT-" + seat_id,
                "response_id": f"{seat_id}.R1",
                "seat_id": seat_id,
                "round": 1,
                "source_thread_id": thread_id,
                "destination_thread_id": source_thread_id,
                "response_digest": response_digest,
                "status": "received",
                "provider_receipt": "provider-response-" + seat_id,
                "received_at": TIMESTAMP,
            }
        return {
            "schema_version": "agentdesk.consultation-runtime/v1",
            "updated_at": TIMESTAMP,
            "consultations": {
                "C001": {
                    "source": {
                        "thread_id": source_thread_id,
                        "host_id": "host-main",
                        "status": "verified",
                        "verified_at": TIMESTAMP,
                    },
                    "seats": routes,
                    "prompt_receipts": prompts,
                    "response_receipts": response_receipts,
                }
            },
        }

    def _configure(self, record: dict[str, object]) -> None:
        self._write_json("docs/pm/CONSULTATION-POLICY.yaml", self._policy())
        self._write_json("docs/pm/consultations/C001/consultation.yaml", record)

    def _add_second_round(
        self, record: dict[str, object], *, include_response: bool
    ) -> None:
        record["round_limit"] = 2
        authorization = record["creation_authorization"]
        assert isinstance(authorization, dict)
        authorization["max_rounds"] = 2
        root = self.project / "docs/pm/consultations/C001"
        first_brief = root / "brief-r1.md"
        first_text = first_brief.read_text(encoding="utf-8").replace(
            "round_limit: 1", "round_limit: 2"
        )
        first_brief.write_text(first_text, encoding="utf-8")
        rounds = record["rounds"]
        assert isinstance(rounds, list) and isinstance(rounds[0], dict)
        rounds[0]["brief_digest"] = "sha256:" + hashlib.sha256(
            first_brief.read_bytes()
        ).hexdigest()
        second_brief = root / "brief-r2.md"
        second_brief.write_text(first_text, encoding="utf-8")
        responses: list[dict[str, object]] = []
        if include_response:
            first_response = root / "responses/01-r1.md"
            second_response = root / "responses/01-r2.md"
            second_response.write_text(
                first_response.read_text(encoding="utf-8")
                .replace("C001.01.R1", "C001.01.R2")
                .replace("round: 1", "round: 2"),
                encoding="utf-8",
            )
            responses.append(
                {
                    "response_id": "C001.01.R2",
                    "seat_id": "C001.01",
                    "status": "received",
                    "path": second_response.relative_to(self.project).as_posix(),
                    "callback_id": None,
                }
            )
        rounds.append(
            {
                "round": 2,
                "independent": False,
                "brief_path": second_brief.relative_to(self.project).as_posix(),
                "brief_digest": "sha256:"
                + hashlib.sha256(second_brief.read_bytes()).hexdigest(),
                "trigger_reason": (
                    "A material disagreement could change the recommendation."
                ),
                "responses": responses,
            }
        )

    def _tasks_state(self) -> None:
        self._write_json(
            "docs/pm/state/tasks.yaml",
            {
                "schema_version": "agentdesk.tasks/v2",
                "project_id": "consultation-test",
                "adoption_level": "standard",
                "updated_at": TIMESTAMP,
                "pm_control": {
                    "holder_id": "pm-test",
                    "lease_epoch": 1,
                    "mode": "timed",
                },
                "tasks": [],
            },
        )
        compatibility = self.release_identity["compatibility"]
        assert isinstance(compatibility, dict)
        reader = compatibility["reader"]
        writer = copy.deepcopy(compatibility["writer"])
        assert isinstance(writer, dict)
        self._write_json(
            "docs/pm/AGENTDESK.lock.json",
            {
                "schema_version": "agentdesk.project-lock/v1",
                "project_id": "consultation-test",
                "skill": {
                    "release": self.release_identity["skill_release"],
                    "source_commit": self.release_identity["source_commit"],
                    "bundle_sha256": self.release_identity["bundle_sha256"],
                },
                "protocol": {
                    "version": self.release_identity["protocol_version"],
                },
                "schemas": copy.deepcopy(
                    self.release_identity["schema_set"]["schemas"]
                ),
                "runtime_adapter": copy.deepcopy(
                    self.release_identity["runtime_adapter"]
                ),
                "operation_compatibility": {
                    "reader": copy.deepcopy(reader),
                    "writer": writer,
                },
            },
        )

    def _run_renderer(self, *extra: str) -> subprocess.CompletedProcess[str]:
        migration_prepare = (
            [] if "--check" in extra else ["--migration-prepare"]
        )
        return subprocess.run(
            [
                sys.executable,
                str(self.renderer_path),
                "--project",
                str(self.project),
                *migration_prepare,
                *extra,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def _validate(self, *, require_runtime: bool = False):
        with redirect_stdout(io.StringIO()):
            return VALIDATOR.validate(
                self.project, require_runtime=require_runtime
            )

    def _validate_with_output(self, *, require_runtime: bool = False):
        output = io.StringIO()
        with redirect_stdout(output):
            reporter = VALIDATOR.validate(
                self.project, require_runtime=require_runtime
            )
        return reporter, output.getvalue()

    def test_old_project_without_consultation_sources_remains_valid(self) -> None:
        reporter = self._validate()

        self.assertEqual(0, reporter.errors)
        self.assertGreaterEqual(reporter.passes, 1)

        self._tasks_state()
        rendered = self._run_renderer()
        self.assertEqual(0, rendered.returncode, rendered.stdout)
        self.assertFalse((self.project / "docs/pm/CONSULTATIONS.md").exists())

    def test_orphan_runtime_is_rejected_after_public_control_plane_deletion(self) -> None:
        record = self._record()
        self._write_json(".agentdesk/runtime/consultations.yaml", self._runtime(record))

        reporter = self._validate(require_runtime=True)

        self.assertGreater(reporter.errors, 0)

    def test_orphan_generated_consultation_view_is_rejected(self) -> None:
        view = self.project / "docs/pm/CONSULTATIONS.md"
        view.parent.mkdir(parents=True, exist_ok=True)
        view.write_text(
            "# Consultation Board\n"
            "<!-- GENERATED FILE: source=docs/pm/consultations/*/consultation.yaml -->\n",
            encoding="utf-8",
        )

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_renderer_rejects_orphan_generated_view_in_write_and_check_modes(self) -> None:
        self._tasks_state()
        view = self.project / "docs/pm/CONSULTATIONS.md"
        view.parent.mkdir(parents=True, exist_ok=True)
        view.write_text(
            "# Consultation Board\n"
            "<!-- GENERATED FILE: source=docs/pm/consultations/*/consultation.yaml -->\n",
            encoding="utf-8",
        )

        for extra in ((), ("--check",)):
            with self.subTest(extra=extra):
                rendered = self._run_renderer(*extra)
                self.assertEqual(1, rendered.returncode, rendered.stdout)
                self.assertIn("orphaned", rendered.stdout)
        self.assertFalse((self.project / "docs/pm/BOARD.md").exists())
        self.assertFalse((self.project / "docs/pm/STATUS.md").exists())

    def test_renderer_write_gate_blocks_legacy_unpinned_without_writing(self) -> None:
        self._tasks_state()
        (self.project / "docs/pm/AGENTDESK.lock.json").unlink()
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*")
            if path.is_file()
        }

        rendered = self._run_renderer()

        self.assertEqual(1, rendered.returncode, rendered.stdout)
        self.assertIn("legacy_unpinned", rendered.stdout)
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_valid_public_and_runtime_records_pass(self) -> None:
        record = self._record()
        self._configure(record)
        self._write_json(".agentdesk/runtime/consultations.yaml", self._runtime(record))

        reporter = self._validate(require_runtime=True)

        self.assertEqual(0, reporter.errors)

    def test_seat_limit_and_exact_title_are_enforced(self) -> None:
        record = self._record(seat_count=4, state="draft", authorized=False)
        seats = record["seats"]
        assert isinstance(seats, list) and isinstance(seats[0], dict)
        seats[0]["expected_thread_title"] = "wrong title"
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_authorization_is_required_before_visible_activity(self) -> None:
        record = self._record(authorized=False)
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_scoped_auto_authorization_may_omit_exact_title_list(self) -> None:
        record = self._record()
        authorization = record["creation_authorization"]
        assert isinstance(authorization, dict)
        authorization["kind"] = "scoped_auto"
        authorization["authorized_titles"] = []
        self._configure(record)

        reporter = self._validate()

        self.assertEqual(0, reporter.errors)

    def test_first_round_brief_digest_must_match_shared_brief(self) -> None:
        record = self._record()
        rounds = record["rounds"]
        assert isinstance(rounds, list) and isinstance(rounds[0], dict)
        rounds[0]["brief_digest"] = "sha256:" + "0" * 64
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_brief_timestamp_must_precede_round_responses_and_synthesis(self) -> None:
        record = self._record()
        record["updated_at"] = "2031-07-21T09:00:00Z"
        confirmation = record["confirmation"]
        assert isinstance(confirmation, dict)
        confirmation["recorded_at"] = "2031-07-21T08:00:00Z"
        brief_path = self.project / "docs/pm/consultations/C001/brief-r1.md"
        brief_path.write_text(
            brief_path.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2030-07-21T08:00:00Z"',
            ),
            encoding="utf-8",
        )
        rounds = record["rounds"]
        assert isinstance(rounds, list) and isinstance(rounds[0], dict)
        rounds[0]["brief_digest"] = "sha256:" + hashlib.sha256(
            brief_path.read_bytes()
        ).hexdigest()
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_public_runtime_identifiers_are_rejected(self) -> None:
        record = self._record()
        record["thread_id"] = "private-thread-id"
        rounds = record["rounds"]
        assert isinstance(rounds, list) and isinstance(rounds[0], dict)
        responses = rounds[0]["responses"]
        assert isinstance(responses, list) and isinstance(responses[0], dict)
        responses[0]["callback_id"] = "private-callback-id"
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_public_record_text_cannot_hide_runtime_values_or_local_paths(self) -> None:
        record = self._record()
        seats = record["seats"]
        confirmation = record["confirmation"]
        assert isinstance(seats, list) and isinstance(seats[0], dict)
        assert isinstance(confirmation, dict)
        seats[0]["focus"] = (
            "Analyze thread_id: LIVE-SECRET and local_path: /root/private"
        )
        confirmation["confirmed_scope"] = [
            "Use provider_receipt: LIVE-SECRET only as private evidence"
        ]
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 3)

    def test_derivation_is_rejected_before_owner_confirmation(self) -> None:
        record = self._record(state="awaiting_confirmation")
        derived = record["derived_refs"]
        assert isinstance(derived, dict)
        derived["decision_ids"] = ["DEC-C001"]
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_confirmed_derivation_requires_closed_state_and_nonempty_scope(self) -> None:
        record = self._record(state="awaiting_confirmation")
        record["outcome_status"] = "confirmed"
        confirmation = record["confirmation"]
        assert isinstance(confirmation, dict)
        confirmation.update(
            {
                "confirmation_id": "CC-C001-C3D4",
                "status": "confirmed",
                "confirmed_by": "Owner",
                "confirmed_scope": [],
                "recorded_at": TIMESTAMP,
            }
        )
        derived = record["derived_refs"]
        assert isinstance(derived, dict)
        derived["decision_ids"] = ["DEC-C001"]
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 3)

    def test_owner_confirmation_scope_requires_substantive_text(self) -> None:
        record = self._record()
        confirmation = record["confirmation"]
        assert isinstance(confirmation, dict)
        confirmation["confirmed_scope"] = ["."]
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_runtime_requires_distinct_exact_routes_and_response_receipt(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        consultation = runtime["consultations"]
        assert isinstance(consultation, dict)
        runtime_record = consultation["C001"]
        assert isinstance(runtime_record, dict)
        source = runtime_record["source"]
        routes = runtime_record["seats"]
        prompts = runtime_record["prompt_receipts"]
        receipts = runtime_record["response_receipts"]
        assert (
            isinstance(source, dict)
            and isinstance(routes, dict)
            and isinstance(prompts, dict)
            and isinstance(receipts, dict)
        )
        seat_route = routes["C001.01"]
        assert isinstance(seat_route, dict)
        seat_route["thread_id"] = source["thread_id"]
        seat_route["actual_title"] = "wrong title"
        prompts.clear()
        receipts.clear()
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreaterEqual(reporter.errors, 2)

    def test_runtime_identifiers_reject_placeholder_tokens(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        source = runtime_record["source"]
        seat = runtime_record["seats"]["C001.01"]
        prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
        receipts = runtime_record["response_receipts"]
        response = receipts.pop("CALLBACK-C001.01")
        assert isinstance(source, dict) and isinstance(seat, dict)
        assert isinstance(prompt, dict) and isinstance(response, dict)
        source["thread_id"] = "TODO"
        source["host_id"] = "TBD"
        seat["thread_id"] = "XXXX"
        seat["host_id"] = "TODO"
        seat["model_binding"] = {
            "requested_model": "TODO",
            "request_source": "user_explicit",
            "actual_model": "TODO",
            "status": "verified",
            "provider_evidence": {"status": "verified"},
        }
        prompt["prompt_id"] = "TODO"
        prompt["source_thread_id"] = "TODO"
        prompt["destination_thread_id"] = "XXXX"
        response["callback_id"] = "TBD"
        response["source_thread_id"] = "XXXX"
        response["destination_thread_id"] = "TODO"
        receipts["TBD"] = response
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreaterEqual(reporter.errors, 6)

    def test_runtime_identifiers_require_alphanumeric_content(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        source = runtime_record["source"]
        seat = runtime_record["seats"]["C001.01"]
        prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
        receipts = runtime_record["response_receipts"]
        response = receipts.pop("CALLBACK-C001.01")
        assert isinstance(source, dict) and isinstance(seat, dict)
        assert isinstance(prompt, dict) and isinstance(response, dict)
        source["thread_id"] = "!"
        source["host_id"] = "."
        seat["thread_id"] = "?"
        seat["host_id"] = "-"
        seat["model_binding"] = {
            "requested_model": ".",
            "request_source": "user_explicit",
            "actual_model": ".",
            "status": "verified",
            "provider_evidence": {"status": "verified"},
        }
        prompt["prompt_id"] = "#"
        prompt["source_thread_id"] = "!"
        prompt["destination_thread_id"] = "?"
        response["callback_id"] = "$"
        response["source_thread_id"] = "?"
        response["destination_thread_id"] = "!"
        receipts["$"] = response
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreaterEqual(reporter.errors, 6)

    def test_waiting_authorization_cannot_hide_created_seat_or_authorization(self) -> None:
        record = self._record(state="draft", authorized=False)
        record["state"] = "awaiting_authorization"
        authorized = self._record()["creation_authorization"]
        record["creation_authorization"] = authorized
        seats = record["seats"]
        assert isinstance(seats, list) and isinstance(seats[0], dict)
        seats[0]["status"] = "created"
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_public_artifact_schema_and_runtime_leaks_are_rejected(self) -> None:
        record = self._record()
        self._configure(record)
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            "---\nschema_version: evil/v9\nthread_id: leaked\ncallback_id: LIVE\n---\n",
            encoding="utf-8",
        )

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_public_artifact_body_cannot_hide_prompt_or_callback_ids(self) -> None:
        record = self._record()
        self._configure(record)
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            response_path.read_text(encoding="utf-8")
            + "\nprompt_id: LIVE-PROMPT\ncallback_id: LIVE-CALLBACK\n",
            encoding="utf-8",
        )

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_public_artifact_body_rejects_nested_credentials_and_local_paths(self) -> None:
        record = self._record()
        self._configure(record)
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            response_path.read_text(encoding="utf-8")
            + "\n- session_id: LIVE-SESSION\n"
            + "- api_key: secret\n"
            + "local_path: /tmp/private-response.md\n"
            + '{"thread_id":"thread-private"}\n'
            + "THREAD_ID: UPPERCASE-SECRET\n"
            + "threadId: CAMEL-SECRET\n"
            + "| thread_id | TABLE-SECRET |\n"
            + "{thread_id: OBJECT-SECRET}\n"
            + "The live thread_id is PROSE-SECRET\n"
            + "local_path: /etc/passwd\n",
            encoding="utf-8",
        )

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_public_artifact_rejects_runtime_value_syntax_variants(self) -> None:
        variants = (
            "THREAD_ID: UPPERCASE-SECRET",
            "threadId: CAMEL-SECRET",
            "thread-id: HYPHEN-SECRET",
            "thread id: SPACE-SECRET",
            "| thread_id | TABLE-SECRET |",
            "{thread_id: OBJECT-SECRET}",
            "The live thread_id is PROSE-SECRET",
            '{"thread_id":"JSON-SECRET"}',
            "api-key: sk-live-example",
            "api key: sk-live-example",
            "api\u00a0key: sk-live-example",
            "api.key: sk-live-example",
            "api_key：sk-live-example",
        )
        for variant in variants:
            with self.subTest(variant=variant):
                record = self._record()
                self._configure(record)
                response_path = (
                    self.project
                    / "docs/pm/consultations/C001/responses/01-r1.md"
                )
                response_path.write_text(
                    response_path.read_text(encoding="utf-8")
                    + f"\n{variant}\n",
                    encoding="utf-8",
                )

                reporter = self._validate()

                self.assertGreater(reporter.errors, 0)

    def test_public_artifact_rejects_common_local_absolute_roots(self) -> None:
        paths = (
            "/root/private",
            "/opt/secret",
            "/etc/passwd",
            "/workspace/private",
            "/mnt/data/private",
            "/private/tmp/agentdesk/receipt.json",
            "/srv/agentdesk/receipt.json",
            "/Applications/secret",
        )
        for local_path in paths:
            with self.subTest(local_path=local_path):
                record = self._record()
                self._configure(record)
                response_path = (
                    self.project
                    / "docs/pm/consultations/C001/responses/01-r1.md"
                )
                response_path.write_text(
                    response_path.read_text(encoding="utf-8")
                    + f"\nEvidence was read from {local_path}.\n",
                    encoding="utf-8",
                )

                reporter = self._validate()

                self.assertGreater(reporter.errors, 0)

    def test_all_public_artifacts_reject_unfilled_body_placeholders(self) -> None:
        artifact_paths = {
            "brief": "docs/pm/consultations/C001/brief-r1.md",
            "response": "docs/pm/consultations/C001/responses/01-r1.md",
            "synthesis": "docs/pm/consultations/C001/synthesis.md",
        }
        for label, relative in artifact_paths.items():
            with self.subTest(artifact=label):
                record = self._record()
                path = self.project / relative
                path.write_text(
                    path.read_text(encoding="utf-8") + "\n<TBD>\n",
                    encoding="utf-8",
                )
                if label == "brief":
                    rounds = record["rounds"]
                    assert isinstance(rounds, list) and isinstance(rounds[0], dict)
                    rounds[0]["brief_digest"] = "sha256:" + hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                self._configure(record)

                reporter = self._validate()

                self.assertGreater(reporter.errors, 0)

    def test_all_public_artifacts_require_nonempty_sections(self) -> None:
        cases = {
            "brief": (
                "docs/pm/consultations/C001/brief-r1.md",
                "## 为什么需要研讨\nNeed independent evidence.",
                "## 为什么需要研讨\n",
            ),
            "response": (
                "docs/pm/consultations/C001/responses/01-r1.md",
                "## 推荐结论\nRecommendation.",
                "## 推荐结论\n",
            ),
            "synthesis": (
                "docs/pm/consultations/C001/synthesis.md",
                "## 席位覆盖\nComplete.",
                "## 席位覆盖\n",
            ),
        }
        for label, (relative, old, new) in cases.items():
            with self.subTest(artifact=label):
                record = self._record()
                path = self.project / relative
                path.write_text(
                    path.read_text(encoding="utf-8").replace(old, new),
                    encoding="utf-8",
                )
                if label == "brief":
                    rounds = record["rounds"]
                    assert isinstance(rounds, list) and isinstance(rounds[0], dict)
                    rounds[0]["brief_digest"] = "sha256:" + hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                self._configure(record)

                reporter = self._validate()

                self.assertGreater(reporter.errors, 0)

    def test_missing_response_requires_null_path_and_synthesis_disclosure(self) -> None:
        record = self._record()
        rounds = record["rounds"]
        seats = record["seats"]
        assert isinstance(rounds, list) and isinstance(rounds[0], dict)
        assert isinstance(seats, list) and isinstance(seats[0], dict)
        responses = rounds[0]["responses"]
        assert isinstance(responses, list) and isinstance(responses[0], dict)
        responses[0]["status"] = "missing"
        seats[0]["status"] = "missing"
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_response_and_synthesis_artifact_timestamps_follow_causal_order(self) -> None:
        record = self._record()
        record["updated_at"] = "2040-07-21T09:00:00Z"
        confirmation = record["confirmation"]
        assert isinstance(confirmation, dict)
        confirmation["recorded_at"] = "2026-07-21T08:30:00Z"
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            response_path.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2020-07-21T08:00:00Z"',
            ),
            encoding="utf-8",
        )
        synthesis_path = self.project / "docs/pm/consultations/C001/synthesis.md"
        synthesis_path.write_text(
            synthesis_path.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2030-07-21T08:00:00Z"',
            ),
            encoding="utf-8",
        )
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_response_artifact_cannot_predate_runtime_prompt(self) -> None:
        record = self._record()
        record["updated_at"] = "2026-07-21T09:00:00Z"
        confirmation = record["confirmation"]
        assert isinstance(confirmation, dict)
        confirmation["recorded_at"] = "2026-07-21T08:30:00Z"
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            response_path.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:05:00Z"',
            ),
            encoding="utf-8",
        )
        synthesis_path = self.project / "docs/pm/consultations/C001/synthesis.md"
        synthesis_path.write_text(
            synthesis_path.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:25:00Z"',
            ),
            encoding="utf-8",
        )
        self._configure(record)
        runtime = self._runtime(record)
        runtime["updated_at"] = "2026-07-21T09:00:00Z"
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
        response = runtime_record["response_receipts"]["CALLBACK-C001.01"]
        assert isinstance(prompt, dict) and isinstance(response, dict)
        prompt["sent_at"] = "2026-07-21T08:10:00Z"
        response["received_at"] = "2026-07-21T08:20:00Z"
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter, diagnostics = self._validate_with_output(require_runtime=True)

        self.assertGreater(reporter.errors, 0)
        self.assertIn("created_at must not precede its prompt sent_at", diagnostics)

    def test_callback_receipt_cannot_follow_synthesis(self) -> None:
        record = self._record()
        record["updated_at"] = "2026-07-21T09:00:00Z"
        confirmation = record["confirmation"]
        assert isinstance(confirmation, dict)
        confirmation["recorded_at"] = "2026-07-21T08:30:00Z"
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            response_path.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:15:00Z"',
            ),
            encoding="utf-8",
        )
        synthesis_path = self.project / "docs/pm/consultations/C001/synthesis.md"
        synthesis_path.write_text(
            synthesis_path.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:20:00Z"',
            ),
            encoding="utf-8",
        )
        self._configure(record)
        runtime = self._runtime(record)
        runtime["updated_at"] = "2026-07-21T09:00:00Z"
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
        response = runtime_record["response_receipts"]["CALLBACK-C001.01"]
        assert isinstance(prompt, dict) and isinstance(response, dict)
        prompt["sent_at"] = "2026-07-21T08:10:00Z"
        response["received_at"] = "2026-07-21T08:25:00Z"
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter, diagnostics = self._validate_with_output(require_runtime=True)

        self.assertGreater(reporter.errors, 0)
        self.assertIn("received_at must not follow synthesis created_at", diagnostics)

    def test_round_two_prompt_waits_for_all_round_one_callbacks(self) -> None:
        record = self._record()
        self._add_second_round(record, include_response=True)
        record["updated_at"] = "2026-07-21T09:00:00Z"
        confirmation = record["confirmation"]
        assert isinstance(confirmation, dict)
        confirmation["recorded_at"] = "2026-07-21T09:00:00Z"
        root = self.project / "docs/pm/consultations/C001"
        first_response = root / "responses/01-r1.md"
        first_response.write_text(
            first_response.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:05:00Z"',
            ),
            encoding="utf-8",
        )
        second_response = root / "responses/01-r2.md"
        second_response.write_text(
            second_response.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:30:00Z"',
            )
            + "\n## Round 2 补充（仅第二轮填写）\n"
            + "- 结论是否改变：`unchanged`\n"
            + "The evidence did not change the recommendation.\n",
            encoding="utf-8",
        )
        synthesis_path = root / "synthesis.md"
        synthesis_path.write_text(
            synthesis_path.read_text(encoding="utf-8")
            .replace("rounds_used: 1", "rounds_used: 2")
            .replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:50:00Z"',
            ),
            encoding="utf-8",
        )
        self._configure(record)
        runtime = self._runtime(record)
        runtime["updated_at"] = "2026-07-21T09:00:00Z"
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        source = runtime_record["source"]
        seat = runtime_record["seats"]["C001.01"]
        prompts = runtime_record["prompt_receipts"]
        receipts = runtime_record["response_receipts"]
        assert isinstance(source, dict) and isinstance(seat, dict)
        assert isinstance(prompts, dict) and isinstance(receipts, dict)
        first_prompt = prompts["C001.01.R1"]
        first_callback = receipts["CALLBACK-C001.01"]
        assert isinstance(first_prompt, dict) and isinstance(first_callback, dict)
        first_prompt["sent_at"] = "2026-07-21T08:00:00Z"
        first_callback["received_at"] = "2026-07-21T08:20:00Z"
        rounds = record["rounds"]
        assert isinstance(rounds, list) and isinstance(rounds[1], dict)
        prompts["C001.01.R2"] = {
            "prompt_id": "PROMPT-C001.01-R2",
            "seat_id": "C001.01",
            "round": 2,
            "source_thread_id": source["thread_id"],
            "destination_thread_id": seat["thread_id"],
            "brief_digest": rounds[1]["brief_digest"],
            "status": "acknowledged",
            "provider_receipt": "provider-prompt-C001.01-R2",
            "sent_at": "2026-07-21T08:10:00Z",
        }
        receipts["CALLBACK-C001.01-R2"] = {
            "callback_id": "CALLBACK-C001.01-R2",
            "prompt_id": "PROMPT-C001.01-R2",
            "response_id": "C001.01.R2",
            "seat_id": "C001.01",
            "round": 2,
            "source_thread_id": seat["thread_id"],
            "destination_thread_id": source["thread_id"],
            "response_digest": "sha256:"
            + hashlib.sha256(second_response.read_bytes()).hexdigest(),
            "status": "received",
            "provider_receipt": "provider-response-C001.01-R2",
            "received_at": "2026-07-21T08:40:00Z",
        }
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter, diagnostics = self._validate_with_output(require_runtime=True)

        self.assertGreater(reporter.errors, 0)
        self.assertIn(
            "Round 2 prompts must not precede completion of all received Round 1 callbacks",
            diagnostics,
        )

    def test_runtime_rejects_orphan_nested_facts_and_orphan_consultation(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        consultations = runtime["consultations"]
        assert isinstance(consultations, dict)
        runtime_record = consultations["C001"]
        assert isinstance(runtime_record, dict)
        seats = runtime_record["seats"]
        prompts = runtime_record["prompt_receipts"]
        receipts = runtime_record["response_receipts"]
        assert isinstance(seats, dict) and isinstance(prompts, dict) and isinstance(receipts, dict)
        seats["C999.01"] = {"garbage": True}
        prompts["C999.01.R1"] = {"garbage": True}
        receipts["ORPHAN"] = {"response_id": "C999.01.R1"}
        consultations["C999"] = {"garbage": True}
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreaterEqual(reporter.errors, 4)

    def test_runtime_rejects_execution_worker_route_reuse(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        consultations = runtime["consultations"]
        assert isinstance(consultations, dict)
        runtime_record = consultations["C001"]
        assert isinstance(runtime_record, dict)
        seats = runtime_record["seats"]
        assert isinstance(seats, dict) and isinstance(seats["C001.01"], dict)
        seat_thread = seats["C001.01"]["thread_id"]
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)
        self._write_json(
            ".agentdesk/runtime/routes.yaml",
            {
                "schema_version": "agentdesk.routes/v2",
                "updated_at": TIMESTAMP,
                "routes": {
                    "DEV": {
                        "role_no": "R1",
                        "thread_id": seat_thread,
                    }
                },
            },
        )

        reporter = self._validate(require_runtime=True)

        self.assertGreater(reporter.errors, 0)

    def test_policy_caps_bound_scoped_authorization(self) -> None:
        record = self._record()
        authorization = record["creation_authorization"]
        assert isinstance(authorization, dict)
        authorization["kind"] = "scoped_auto"
        authorization["authorized_titles"] = []
        authorization["max_visible_sessions"] = 3
        authorization["max_rounds"] = 2
        policy = self._policy()
        policy["max_seats"] = 1
        policy["max_rounds"] = 1
        self._write_json("docs/pm/CONSULTATION-POLICY.yaml", policy)
        self._write_json("docs/pm/consultations/C001/consultation.yaml", record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_cancelled_cannot_claim_owner_confirmation(self) -> None:
        record = self._record()
        record["state"] = "cancelled"
        record["cancellation_reason"] = "Owner stopped the roundtable."
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_cancelled_responded_seat_requires_preserved_round_evidence(self) -> None:
        record = self._record()
        record["state"] = "cancelled"
        record["cancellation_reason"] = "Cancelled after session creation."
        record["rounds"] = []
        record["synthesis_path"] = None
        record["outcome_status"] = "none"
        record["confirmation"] = {
            "confirmation_id": None,
            "status": "not_required",
            "confirmed_by": None,
            "confirmed_scope": [],
            "recorded_at": None,
        }
        self._configure(record)
        runtime = self._runtime(record)
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        runtime_record["prompt_receipts"] = {}
        runtime_record["response_receipts"] = {}
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreater(reporter.errors, 0)

    def test_in_progress_terminal_seat_status_requires_response_fact(self) -> None:
        for status in ("responded", "missing", "blocked"):
            with self.subTest(status=status):
                record = self._record()
                record["state"] = "round_1_in_progress"
                record["synthesis_path"] = None
                record["outcome_status"] = "none"
                record["confirmation"] = {
                    "confirmation_id": None,
                    "status": "pending",
                    "confirmed_by": None,
                    "confirmed_scope": [],
                    "recorded_at": None,
                }
                rounds = record["rounds"]
                seats = record["seats"]
                assert isinstance(rounds, list) and isinstance(rounds[0], dict)
                assert isinstance(seats, list) and isinstance(seats[0], dict)
                rounds[0]["responses"] = []
                seats[0]["status"] = status
                self._configure(record)

                reporter = self._validate()

                self.assertGreater(reporter.errors, 0)

    def test_closed_cannot_keep_an_unconfirmed_proposal(self) -> None:
        record = self._record()
        record["outcome_status"] = "proposed"
        record["confirmation"] = {
            "confirmation_id": None,
            "status": "not_required",
            "confirmed_by": None,
            "confirmed_scope": [],
            "recorded_at": None,
        }
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_cancelled_preserves_existing_partial_second_round(self) -> None:
        record = self._record()
        record["state"] = "cancelled"
        record["cancellation_reason"] = "The second round was stopped after dispatch."
        record["outcome_status"] = "proposed"
        record["round_limit"] = 2
        authorization = record["creation_authorization"]
        assert isinstance(authorization, dict)
        authorization["max_rounds"] = 2
        root = self.project / "docs/pm/consultations/C001"
        first_brief = root / "brief-r1.md"
        first_text = first_brief.read_text(encoding="utf-8").replace(
            "round_limit: 1", "round_limit: 2"
        )
        first_brief.write_text(first_text, encoding="utf-8")
        rounds = record["rounds"]
        assert isinstance(rounds, list) and isinstance(rounds[0], dict)
        rounds[0]["brief_digest"] = "sha256:" + hashlib.sha256(first_brief.read_bytes()).hexdigest()
        second_brief = root / "brief-r2.md"
        second_brief.write_text(first_text, encoding="utf-8")
        rounds.append(
            {
                "round": 2,
                "independent": False,
                "brief_path": second_brief.relative_to(self.project).as_posix(),
                "brief_digest": "sha256:" + hashlib.sha256(second_brief.read_bytes()).hexdigest(),
                "trigger_reason": "A material disagreement could change the recommendation.",
                "responses": [],
            }
        )
        synthesis_path = root / "synthesis.md"
        synthesis_path.write_text(
            synthesis_path.read_text(encoding="utf-8").replace(
                "rounds_used: 1", "rounds_used: 2"
            ),
            encoding="utf-8",
        )
        record["confirmation"] = {
            "confirmation_id": None,
            "status": "not_required",
            "confirmed_by": None,
            "confirmed_scope": [],
            "recorded_at": None,
        }
        self._configure(record)
        self._write_json(".agentdesk/runtime/consultations.yaml", self._runtime(record))

        reporter = self._validate(require_runtime=True)

        self.assertEqual(0, reporter.errors)

    def test_cancelled_second_round_still_requires_complete_first_round(self) -> None:
        record = self._record()
        self._add_second_round(record, include_response=False)
        record["state"] = "cancelled"
        record["cancellation_reason"] = "The second round was stopped."
        record["outcome_status"] = "proposed"
        rounds = record["rounds"]
        assert isinstance(rounds, list) and isinstance(rounds[0], dict)
        rounds[0]["responses"] = []
        seats = record["seats"]
        assert isinstance(seats, list) and isinstance(seats[0], dict)
        seats[0]["status"] = "missing"
        synthesis_path = self.project / "docs/pm/consultations/C001/synthesis.md"
        synthesis_path.write_text(
            synthesis_path.read_text(encoding="utf-8").replace(
                "rounds_used: 1", "rounds_used: 2"
            ),
            encoding="utf-8",
        )
        record["confirmation"] = {
            "confirmation_id": None,
            "status": "not_required",
            "confirmed_by": None,
            "confirmed_scope": [],
            "recorded_at": None,
        }
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_runtime_receipt_requires_evidence_and_chronology(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        consultations = runtime["consultations"]
        assert isinstance(consultations, dict)
        runtime_record = consultations["C001"]
        assert isinstance(runtime_record, dict)
        prompts = runtime_record["prompt_receipts"]
        receipts = runtime_record["response_receipts"]
        assert isinstance(prompts, dict) and isinstance(receipts, dict)
        prompt = prompts["C001.01.R1"]
        response = receipts["CALLBACK-C001.01"]
        assert isinstance(prompt, dict) and isinstance(response, dict)
        prompt["provider_receipt"] = 0
        prompt["sent_at"] = "2026-07-21T09:00:00Z"
        response["received_at"] = "2026-07-21T08:00:00Z"
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreaterEqual(reporter.errors, 2)

    def test_runtime_receipts_bind_public_brief_and_response_bytes(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
        response = runtime_record["response_receipts"]["CALLBACK-C001.01"]
        assert isinstance(prompt, dict) and isinstance(response, dict)
        prompt["brief_digest"] = "sha256:" + "0" * 64
        response["response_digest"] = "sha256:" + "0" * 64
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreaterEqual(reporter.errors, 2)

    def test_runtime_rejects_failed_receipts_and_model_evidence(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
        response = runtime_record["response_receipts"]["CALLBACK-C001.01"]
        seat = runtime_record["seats"]["C001.01"]
        assert isinstance(prompt, dict) and isinstance(response, dict)
        assert isinstance(seat, dict)
        prompt["provider_receipt"] = {"status": "failed", "error": "denied"}
        response["provider_receipt"] = {"ok": False, "error": "denied"}
        seat["model_binding"] = {
            "requested_model": "model-A",
            "request_source": "user_explicit",
            "actual_model": "model-A",
            "status": "verified",
            "provider_evidence": {"status": "failed", "error": "denied"},
        }
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreaterEqual(reporter.errors, 3)

    def test_runtime_rejects_nonterminal_evidence_statuses(self) -> None:
        statuses = (
            "pending",
            "queued",
            "timeout",
            "timed_out",
            "not_delivered",
            "unavailable",
            "unknown",
        )
        for status in statuses:
            with self.subTest(status=status):
                record = self._record()
                self._configure(record)
                runtime = self._runtime(record)
                runtime_record = runtime["consultations"]["C001"]
                assert isinstance(runtime_record, dict)
                prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
                response = runtime_record["response_receipts"]["CALLBACK-C001.01"]
                seat = runtime_record["seats"]["C001.01"]
                assert isinstance(prompt, dict) and isinstance(response, dict)
                assert isinstance(seat, dict)
                prompt["provider_receipt"] = {"status": status}
                response["provider_receipt"] = {"status": status}
                seat["creation_evidence"] = {"status": status}
                seat["model_binding"] = {
                    "requested_model": "model-A",
                    "request_source": "user_explicit",
                    "actual_model": "model-A",
                    "status": "verified",
                    "provider_evidence": {"status": status},
                }
                self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

                reporter = self._validate(require_runtime=True)

                self.assertGreaterEqual(reporter.errors, 4)

    def test_runtime_chronology_is_bound_to_authorization_and_confirmation(self) -> None:
        record = self._record()
        record["updated_at"] = "2026-07-21T09:00:00Z"
        authorization = record["creation_authorization"]
        confirmation = record["confirmation"]
        assert isinstance(authorization, dict) and isinstance(confirmation, dict)
        authorization["granted_at"] = "2026-07-21T08:05:00Z"
        confirmation["recorded_at"] = "2026-07-21T08:15:00Z"
        self._configure(record)
        runtime = self._runtime(record)
        runtime["updated_at"] = "2026-07-21T09:00:00Z"
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
        response = runtime_record["response_receipts"]["CALLBACK-C001.01"]
        assert isinstance(prompt, dict) and isinstance(response, dict)
        prompt["sent_at"] = "2026-07-21T08:10:00Z"
        response["received_at"] = "2026-07-21T08:20:00Z"
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreaterEqual(reporter.errors, 3)

    def test_runtime_must_stay_empty_before_creation_authorization(self) -> None:
        record = self._record(state="draft", authorized=False)
        record["state"] = "awaiting_authorization"
        self._configure(record)
        self._write_json(
            ".agentdesk/runtime/consultations.yaml", self._runtime(self._record())
        )

        reporter = self._validate(require_runtime=True)

        self.assertGreaterEqual(reporter.errors, 3)

    def test_polled_recovery_cannot_pass_proactive_runtime_closure(self) -> None:
        record = self._record()
        self._configure(record)
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            response_path.read_text(encoding="utf-8").replace(
                "transport_mode: proactive_callback",
                "transport_mode: polled_recovery",
            ),
            encoding="utf-8",
        )
        self._write_json(".agentdesk/runtime/consultations.yaml", self._runtime(record))

        reporter = self._validate(require_runtime=True)

        self.assertGreater(reporter.errors, 0)

    def test_task_card_lineage_is_bidirectional_and_confirmation_gated(self) -> None:
        record = self._record(state="awaiting_confirmation")
        self._configure(record)
        card_path = self.project / "docs/pm/tasks/TC-001-r1-derived.md"
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(
            "\n".join(
                (
                    "---",
                    "schema_version: agentdesk.task-card/v2",
                    "task_id: TC-001",
                    "revision: 1",
                    "source_consultation_id: C001",
                    "source_confirmation_id: CC-C001-C3D4",
                    "---",
                    "# Derived card",
                    "",
                )
            ),
            encoding="utf-8",
        )

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_task_card_cannot_reference_consultation_with_null_lineage(self) -> None:
        record = self._record(state="awaiting_confirmation")
        self._configure(record)
        card_path = self.project / "docs/pm/tasks/TC-002-r1-derived.md"
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(
            "\n".join(
                (
                    "---",
                    "schema_version: agentdesk.task-card/v2",
                    "task_id: TC-002",
                    "revision: 1",
                    "source_consultation_id: null",
                    "source_confirmation_id: null",
                    "---",
                    "# Derived from consultation C001",
                    "",
                )
            ),
            encoding="utf-8",
        )

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_unknown_task_and_decision_lineage_fail_without_control_plane(self) -> None:
        card_path = self.project / "docs/pm/tasks/TC-009-r1-derived.md"
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(
            "---\n"
            "schema_version: agentdesk.task-card/v2\n"
            "task_id: TC-009\n"
            "revision: 1\n"
            "source_consultation_id: C999\n"
            "source_confirmation_id: CC-C999-Z9Y8\n"
            "---\n",
            encoding="utf-8",
        )
        decisions = self.project / "docs/pm/DECISIONS.md"
        decisions.parent.mkdir(parents=True, exist_ok=True)
        decisions.write_text(
            "| ID | Decision | Source / confirmation |\n"
            "| --- | --- | --- |\n"
            "| DEC-999 | Rogue | C999 + CC-C999-Z9Y8 |\n",
            encoding="utf-8",
        )

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_verified_model_override_must_match_exact_user_request(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        consultations = runtime["consultations"]
        assert isinstance(consultations, dict)
        runtime_record = consultations["C001"]
        assert isinstance(runtime_record, dict)
        seats = runtime_record["seats"]
        assert isinstance(seats, dict)
        seat = seats["C001.01"]
        assert isinstance(seat, dict)
        seat["model_binding"] = {
            "requested_model": "model-A",
            "request_source": "user_explicit",
            "actual_model": "model-B",
            "status": "verified",
            "provider_evidence": "provider-model-receipt",
        }
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreater(reporter.errors, 0)

    def test_verified_model_requires_an_exact_user_request(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        seat = runtime_record["seats"]["C001.01"]
        assert isinstance(seat, dict)
        seat["model_binding"] = {
            "requested_model": None,
            "request_source": None,
            "actual_model": "model-A",
            "status": "verified",
            "provider_evidence": {"status": "verified", "model": "model-A"},
        }
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreater(reporter.errors, 0)

    def test_exact_model_request_must_be_verified_for_strong_closure(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        runtime_record = runtime["consultations"]["C001"]
        assert isinstance(runtime_record, dict)
        seat = runtime_record["seats"]["C001.01"]
        assert isinstance(seat, dict)
        seat["model_binding"] = {
            "requested_model": "model-A",
            "request_source": "user_explicit",
            "actual_model": None,
            "status": "override_unverified",
            "provider_evidence": None,
        }
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreater(reporter.errors, 0)

    def test_confirmed_task_and_decision_lineage_passes(self) -> None:
        record = self._record()
        card_relative = "docs/pm/tasks/TC-001-r1-derived.md"
        card_path = self.project / card_relative
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(
            "\n".join(
                (
                    "---",
                    "schema_version: agentdesk.task-card/v2",
                    "task_id: TC-001",
                    "revision: 1",
                    "source_consultation_id: C001",
                    "source_confirmation_id: CC-C001-C3D4",
                    "---",
                    "# Derived card",
                    "",
                )
            ),
            encoding="utf-8",
        )
        decisions = self.project / "docs/pm/DECISIONS.md"
        decisions.parent.mkdir(parents=True, exist_ok=True)
        decisions.write_text(
            "| ID | Decision | Source / confirmation |\n"
            "| --- | --- | --- |\n"
            "| DEC-001 | Use the recommendation | C001 + CC-C001-C3D4 |\n",
            encoding="utf-8",
        )
        derived = record["derived_refs"]
        assert isinstance(derived, dict)
        derived["decision_ids"] = ["DEC-001"]
        derived["task_card_paths"] = [card_relative]
        self._configure(record)

        reporter = self._validate()

        self.assertEqual(0, reporter.errors)

    def test_leader_cannot_self_sign_owner_gates_or_use_placeholder_ids(self) -> None:
        record = self._record()
        authorization = record["creation_authorization"]
        confirmation = record["confirmation"]
        assert isinstance(authorization, dict) and isinstance(confirmation, dict)
        authorization["authorization_id"] = "CA-C001-XXXX"
        authorization["granted_by"] = "Leader"
        confirmation["confirmation_id"] = "CC-C001-XXXX"
        confirmation["confirmed_by"] = "Leader"
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 4)

    def test_short_topic_cannot_add_title_separator_period(self) -> None:
        record = self._record(state="draft", authorized=False)
        record["short_topic"] = "Export.v2"
        seats = record["seats"]
        assert isinstance(seats, list) and isinstance(seats[0], dict)
        seats[0]["expected_thread_title"] = "C001.01 . Advisor 1｜Export.v2"
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_owner_actor_suffix_rejects_placeholder_tokens(self) -> None:
        for actor in ("Owner:TODO", "Owner:TBD", "User:XXXX", "Owner:pending"):
            record = self._record()
            authorization = record["creation_authorization"]
            confirmation = record["confirmation"]
            assert isinstance(authorization, dict) and isinstance(confirmation, dict)
            authorization["granted_by"] = actor
            confirmation["confirmed_by"] = actor
            self._configure(record)

            reporter = self._validate()
            self.assertGreater(reporter.errors, 0, actor)

        record = self._record()
        authorization = record["creation_authorization"]
        confirmation = record["confirmation"]
        assert isinstance(authorization, dict) and isinstance(confirmation, dict)
        authorization["granted_by"] = "Owner:levi.01"
        confirmation["confirmed_by"] = "Owner:levi.01"
        self._configure(record)
        reporter = self._validate()
        self.assertEqual(reporter.errors, 0)

    def test_public_text_fields_reject_placeholder_tokens(self) -> None:
        record = self._record(state="draft", authorized=False)
        record["topic"] = "TBD"
        record["short_topic"] = "TODO"
        seats = record["seats"]
        assert isinstance(seats, list) and isinstance(seats[0], dict)
        seats[0]["focus"] = "XXXX"
        seats[0]["expected_thread_title"] = "C001.01 . Advisor 1｜TODO"
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 3)

    def test_safe_record_template_placeholders_must_be_replaced(self) -> None:
        template_path = SKILL_ROOT / "assets/consultation-record-template.yaml"
        record = json.loads(template_path.read_text(encoding="utf-8"))
        record["created_at"] = TIMESTAMP
        record["updated_at"] = TIMESTAMP
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_round_two_response_must_declare_changed_stance(self) -> None:
        record = self._record()
        self._add_second_round(record, include_response=True)
        record["state"] = "round_2_complete"
        record["synthesis_path"] = None
        record["outcome_status"] = "none"
        record["confirmation"] = {
            "confirmation_id": None,
            "status": "pending",
            "confirmed_by": None,
            "confirmed_scope": [],
            "recorded_at": None,
        }
        self._configure(record)

        reporter = self._validate()

        self.assertGreaterEqual(reporter.errors, 2)

    def test_renderer_is_deterministic_and_check_detects_drift(self) -> None:
        record = self._record()
        self._configure(record)
        self._tasks_state()
        self._write_json(".agentdesk/runtime/consultations.yaml", self._runtime(record))

        first = self._run_renderer()
        self.assertEqual(0, first.returncode, first.stdout)
        view = self.project / "docs/pm/CONSULTATIONS.md"
        first_content = view.read_text(encoding="utf-8")
        self.assertIn("C001", first_content)
        self.assertIn("| closed |", first_content)
        self.assertNotIn("thread-leader-c001", first_content)

        check = self._run_renderer("--check")
        self.assertEqual(0, check.returncode, check.stdout)
        second = self._run_renderer()
        self.assertEqual(0, second.returncode, second.stdout)
        self.assertEqual(first_content, view.read_text(encoding="utf-8"))

        view.write_text(first_content + "manual drift\n", encoding="utf-8")
        drift = self._run_renderer("--check")
        self.assertEqual(1, drift.returncode, drift.stdout)
        self.assertIn("CONSULTATIONS.md is out of date", drift.stdout)

    def test_renderer_refuses_invalid_consultation_projection(self) -> None:
        self._tasks_state()
        self._write_json("docs/pm/CONSULTATION-POLICY.yaml", self._policy())
        self._write_json(
            "docs/pm/consultations/C001/consultation.yaml",
            {
                "schema_version": "agentdesk.consultation/v1",
                "consultation_id": "C001",
                "short_topic": "Invalid projection",
                "state": "closed",
            },
        )

        rendered = self._run_renderer()

        self.assertEqual(1, rendered.returncode, rendered.stdout)
        self.assertIn("failed public validation", rendered.stdout)
        self.assertFalse((self.project / "docs/pm/CONSULTATIONS.md").exists())

    def test_renderer_validates_the_same_in_memory_snapshot_it_projects(self) -> None:
        valid_record = self._record()
        invalid_record = copy.deepcopy(valid_record)
        invalid_record["state"] = "invalid-after-load"
        self._configure(invalid_record)
        self._tasks_state()
        original_validate = RENDERER._validate_consultation_sources

        def replace_disk_then_validate(
            project: Path,
            policy: dict[str, object] | None,
            records: list[tuple[str, dict[str, object]]],
        ) -> None:
            self._write_json(
                "docs/pm/consultations/C001/consultation.yaml", valid_record
            )
            original_validate(project, policy, records)

        output = io.StringIO()
        with mock.patch.object(
            RENDERER,
            "_validate_consultation_sources",
            side_effect=replace_disk_then_validate,
        ), mock.patch.object(
            RENDERER,
            "_require_version_compatibility",
        ), redirect_stdout(output):
            exit_code = RENDERER.main(["--project", str(self.project)])

        self.assertEqual(1, exit_code, output.getvalue())
        self.assertIn("failed public validation", output.getvalue())
        self.assertFalse((self.project / "docs/pm/CONSULTATIONS.md").exists())

    def test_renderer_rolls_back_all_views_when_third_install_fails(self) -> None:
        record = self._record()
        self._configure(record)
        self._tasks_state()
        board = self.project / "docs/pm/BOARD.md"
        status = self.project / "docs/pm/STATUS.md"
        consultations = self.project / "docs/pm/CONSULTATIONS.md"
        previous = {
            board: "old board\n",
            status: "old status\n",
            consultations: "old consultations\n",
        }
        for path, content in previous.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        real_replace = os.replace

        def fail_third_stage(source: object, destination: object) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                destination_path.name == "CONSULTATIONS.md"
                and ".CONSULTATIONS.md.stage." in source_path.name
            ):
                raise OSError("simulated third-view install failure")
            real_replace(source, destination)

        output = io.StringIO()
        with mock.patch.object(
            RENDERER.os, "replace", side_effect=fail_third_stage
        ), mock.patch.object(
            RENDERER,
            "_require_version_compatibility",
        ), redirect_stdout(output):
            exit_code = RENDERER.main(["--project", str(self.project)])

        self.assertEqual(1, exit_code, output.getvalue())
        self.assertIn("simulated third-view install failure", output.getvalue())
        for path, content in previous.items():
            self.assertEqual(content, path.read_text(encoding="utf-8"))
        leftovers = [
            path.name
            for path in (self.project / "docs/pm").iterdir()
            if ".stage." in path.name or ".rollback." in path.name
        ]
        self.assertEqual([], leftovers)

    def test_public_artifact_rejects_any_angle_bracket_placeholder(self) -> None:
        record = self._record()
        self._configure(record)
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            response_path.read_text(encoding="utf-8").replace(
                "Recommendation.", "<recommendation>", 1
            ),
            encoding="utf-8",
        )

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_public_privacy_scan_allows_safe_natural_language(self) -> None:
        self.assertTrue(
            VALIDATOR._nonplaceholder_public_string(
                "How should pending invoices with latency <100ms be handled?"
            )
        )
        record = self._record()
        self._configure(record)
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            response_path.read_text(encoding="utf-8")
            + "\nPassword is hashed with Argon2 before storage.\n",
            encoding="utf-8",
        )

        reporter = self._validate()

        self.assertEqual(0, reporter.errors)

    def test_runtime_rejects_nested_or_malformed_control_evidence(self) -> None:
        mutations = (
            ("creation", {"transport": {"status": "failed", "error": "denied"}}),
            ("prompt", {"transport": {"status": "failed"}}),
            ("response", {"transport": {"status": "failed"}}),
            ("creation", {"status": []}),
            ("prompt", {"success": "false"}),
            ("response", {"status": []}),
            ("creation", [1]),
            ("prompt", [True]),
            ("response", [{"metadata": {}}]),
            ("model", [1]),
        )
        for target, evidence in mutations:
            with self.subTest(target=target, evidence=evidence):
                record = self._record()
                self._configure(record)
                runtime = self._runtime(record)
                runtime_record = runtime["consultations"]["C001"]
                seat = runtime_record["seats"]["C001.01"]
                prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
                response = runtime_record["response_receipts"]["CALLBACK-C001.01"]
                if target == "creation":
                    seat["creation_evidence"] = evidence
                elif target == "prompt":
                    prompt["provider_receipt"] = evidence
                elif target == "response":
                    response["provider_receipt"] = evidence
                else:
                    seat["model_binding"] = {
                        "requested_model": "model-A",
                        "request_source": "user_explicit",
                        "actual_model": "model-A",
                        "status": "verified",
                        "provider_evidence": evidence,
                    }
                self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

                reporter = self._validate(require_runtime=True)

                self.assertGreater(reporter.errors, 0)

    def test_runtime_evidence_allows_empty_ordinary_metadata(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        runtime_record = runtime["consultations"]["C001"]
        seat = runtime_record["seats"]["C001.01"]
        prompt = runtime_record["prompt_receipts"]["C001.01.R1"]
        response = runtime_record["response_receipts"]["CALLBACK-C001.01"]
        seat["creation_evidence"] = {
            "status": "created",
            "metadata": {},
            "retries": 0,
        }
        prompt["provider_receipt"] = {
            "status": "sent",
            "metadata": {},
            "note": "",
        }
        response["provider_receipt"] = {
            "status": "received",
            "metadata": {},
            "retries": 0,
        }
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertEqual(0, reporter.errors)

    def test_callback_may_precede_leader_response_persistence(self) -> None:
        record = self._record()
        record["updated_at"] = "2026-07-21T09:00:00Z"
        confirmation = record["confirmation"]
        assert isinstance(confirmation, dict)
        confirmation["recorded_at"] = "2026-07-21T08:40:00Z"
        response_path = self.project / "docs/pm/consultations/C001/responses/01-r1.md"
        response_path.write_text(
            response_path.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:25:00Z"',
            ),
            encoding="utf-8",
        )
        synthesis_path = self.project / "docs/pm/consultations/C001/synthesis.md"
        synthesis_path.write_text(
            synthesis_path.read_text(encoding="utf-8").replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:30:00Z"',
            ),
            encoding="utf-8",
        )
        self._configure(record)
        runtime = self._runtime(record)
        runtime["updated_at"] = "2026-07-21T09:00:00Z"
        runtime_record = runtime["consultations"]["C001"]
        runtime_record["prompt_receipts"]["C001.01.R1"]["sent_at"] = (
            "2026-07-21T08:10:00Z"
        )
        runtime_record["response_receipts"]["CALLBACK-C001.01"]["received_at"] = (
            "2026-07-21T08:20:00Z"
        )
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertEqual(0, reporter.errors)

    def test_runtime_events_cannot_follow_public_record_update(self) -> None:
        record = self._record()
        record["updated_at"] = "2026-07-21T08:10:00Z"
        self._configure(record)
        runtime = self._runtime(record)
        runtime["updated_at"] = "2026-07-21T08:30:00Z"
        runtime_record = runtime["consultations"]["C001"]
        runtime_record["source"]["verified_at"] = "2026-07-21T08:20:00Z"
        seat = runtime_record["seats"]["C001.01"]
        seat["created_at"] = "2026-07-21T08:20:00Z"
        seat["verified_at"] = "2026-07-21T08:21:00Z"
        runtime_record["prompt_receipts"]["C001.01.R1"]["sent_at"] = (
            "2026-07-21T08:22:00Z"
        )
        runtime_record["response_receipts"]["CALLBACK-C001.01"]["received_at"] = (
            "2026-07-21T08:23:00Z"
        )
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter, diagnostics = self._validate_with_output(require_runtime=True)

        self.assertGreater(reporter.errors, 0)
        self.assertGreaterEqual(diagnostics.count("public record updated_at"), 4)

    def test_missing_response_prompt_cannot_follow_synthesis(self) -> None:
        record = self._record()
        record["updated_at"] = "2026-07-21T09:00:00Z"
        confirmation = record["confirmation"]
        seats = record["seats"]
        rounds = record["rounds"]
        assert isinstance(confirmation, dict)
        assert isinstance(seats, list) and isinstance(seats[0], dict)
        assert isinstance(rounds, list) and isinstance(rounds[0], dict)
        responses = rounds[0]["responses"]
        assert isinstance(responses, list) and isinstance(responses[0], dict)
        confirmation["recorded_at"] = "2026-07-21T08:30:00Z"
        seats[0]["status"] = "missing"
        responses[0]["status"] = "missing"
        responses[0]["path"] = None
        synthesis_path = self.project / "docs/pm/consultations/C001/synthesis.md"
        synthesis_path.write_text(
            synthesis_path.read_text(encoding="utf-8")
            .replace(
                f'created_at: "{TIMESTAMP}"',
                'created_at: "2026-07-21T08:10:00Z"',
            )
            .replace("Complete.", "C001.01 missing; recommendation is incomplete."),
            encoding="utf-8",
        )
        self._configure(record)
        runtime = self._runtime(record)
        runtime["updated_at"] = "2026-07-21T09:00:00Z"
        runtime_record = runtime["consultations"]["C001"]
        runtime_record["prompt_receipts"]["C001.01.R1"]["sent_at"] = (
            "2026-07-21T08:20:00Z"
        )
        runtime_record["response_receipts"] = {}
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter, diagnostics = self._validate_with_output(require_runtime=True)

        self.assertGreater(reporter.errors, 0)
        self.assertIn("sent_at must not follow synthesis created_at", diagnostics)

    def test_decision_lineage_requires_exact_confirmation_token(self) -> None:
        record = self._record()
        derived = record["derived_refs"]
        assert isinstance(derived, dict)
        derived["decision_ids"] = ["DEC-001"]
        decisions = self.project / "docs/pm/DECISIONS.md"
        decisions.parent.mkdir(parents=True, exist_ok=True)
        decisions.write_text(
            "| ID | Decision | Source / confirmation |\n"
            "| --- | --- | --- |\n"
            "| DEC-001 | Use recommendation | C001 + CC-C001-C3D4X |\n",
            encoding="utf-8",
        )
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_cancelled_none_outcome_cannot_keep_synthesis(self) -> None:
        record = self._record()
        record["state"] = "cancelled"
        record["cancellation_reason"] = "Owner stopped the consultation."
        record["outcome_status"] = "none"
        record["confirmation"] = {
            "confirmation_id": None,
            "status": "pending",
            "confirmed_by": None,
            "confirmed_scope": [],
            "recorded_at": None,
        }
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_authorized_blocked_creation_needs_no_fake_seat_route(self) -> None:
        record = self._record(state="draft", authorized=False)
        record["state"] = "authorized"
        authorization = self._record()["creation_authorization"]
        assert isinstance(authorization, dict)
        record["creation_authorization"] = authorization
        seats = record["seats"]
        assert isinstance(seats, list) and isinstance(seats[0], dict)
        seats[0]["status"] = "blocked"
        self._configure(record)
        runtime = {
            "schema_version": "agentdesk.consultation-runtime/v1",
            "updated_at": TIMESTAMP,
            "consultations": {
                "C001": {
                    "source": {
                        "thread_id": "thread-leader-c001",
                        "host_id": "host-main",
                        "status": "verified",
                        "verified_at": TIMESTAMP,
                    },
                    "seats": {},
                    "prompt_receipts": {},
                    "response_receipts": {},
                }
            },
        }
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertEqual(0, reporter.errors)

    def test_response_receipt_prompt_id_must_match_prompt(self) -> None:
        record = self._record()
        self._configure(record)
        runtime = self._runtime(record)
        runtime_record = runtime["consultations"]["C001"]
        runtime_record["response_receipts"]["CALLBACK-C001.01"]["prompt_id"] = (
            "PROMPT-WRONG"
        )
        self._write_json(".agentdesk/runtime/consultations.yaml", runtime)

        reporter = self._validate(require_runtime=True)

        self.assertGreater(reporter.errors, 0)

    def test_brief_requires_readable_context_section(self) -> None:
        record = self._record()
        brief_path = self.project / "docs/pm/consultations/C001/brief-r1.md"
        brief_path.write_text(
            brief_path.read_text(encoding="utf-8").replace(
                "## 可读上下文\nRepository-safe context for every seat.\n", ""
            ),
            encoding="utf-8",
        )
        rounds = record["rounds"]
        assert isinstance(rounds, list) and isinstance(rounds[0], dict)
        rounds[0]["brief_digest"] = "sha256:" + hashlib.sha256(
            brief_path.read_bytes()
        ).hexdigest()
        self._configure(record)

        reporter = self._validate()

        self.assertGreater(reporter.errors, 0)

    def test_round_templates_are_separate_and_unambiguous(self) -> None:
        round_one = (
            SKILL_ROOT / "assets/consultation-response-template.md"
        ).read_text(encoding="utf-8")
        round_two = (
            SKILL_ROOT / "assets/consultation-response-r2-template.md"
        ).read_text(encoding="utf-8")

        self.assertNotIn("## Round 2 补充", round_one)
        self.assertIn("round: 1", round_one)
        self.assertIn("round: 2", round_two)
        self.assertIn("## Round 2 补充", round_two)


if __name__ == "__main__":
    unittest.main()
