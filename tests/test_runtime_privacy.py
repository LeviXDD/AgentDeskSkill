from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = (
    REPOSITORY_ROOT
    / "skills"
    / "agentdesk"
    / "scripts"
    / "validate_project.py"
)
SPEC = importlib.util.spec_from_file_location("validate_project", VALIDATOR_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import guard
    raise RuntimeError(f"cannot load validator from {VALIDATOR_PATH}")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class RuntimePrivacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.project = Path(self.temporary_directory.name)
        self._git("init", "--quiet")

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.project), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _ignore_runtime(self) -> None:
        (self.project / ".gitignore").write_text(
            ".agentdesk/runtime/\n", encoding="utf-8"
        )

    def _create_runtime_files(self) -> None:
        for relative in VALIDATOR.RUNTIME_PRIVATE_FILES:
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")

    def _validate_privacy(self):
        reporter = VALIDATOR.Reporter()
        output = io.StringIO()
        with redirect_stdout(output):
            VALIDATOR._validate_runtime_privacy(self.project, reporter)
        return reporter, output.getvalue()

    def test_missing_optional_runtime_files_are_valid_when_ignored(self) -> None:
        self._ignore_runtime()

        reporter, output = self._validate_privacy()

        self.assertEqual(0, reporter.errors, output)
        self.assertEqual(
            2 * len(VALIDATOR.RUNTIME_PRIVATE_FILES), reporter.passes, output
        )

    def test_existing_untracked_runtime_files_are_valid_when_ignored(self) -> None:
        self._ignore_runtime()
        self._create_runtime_files()

        reporter, output = self._validate_privacy()

        self.assertEqual(0, reporter.errors, output)
        for relative in VALIDATOR.RUNTIME_PRIVATE_FILES:
            logical_path = relative.as_posix()
            self.assertIn(f"runtime file is not Git tracked: {logical_path}", output)
            self.assertIn(f"runtime file is gitignored: {logical_path}", output)

    def test_tracked_runtime_files_fail_even_when_ignore_rule_exists(self) -> None:
        self._ignore_runtime()
        self._create_runtime_files()
        self._git(
            "add",
            "--force",
            "--",
            *(relative.as_posix() for relative in VALIDATOR.RUNTIME_PRIVATE_FILES),
        )

        reporter, output = self._validate_privacy()

        self.assertEqual(len(VALIDATOR.RUNTIME_PRIVATE_FILES), reporter.errors, output)
        for relative in VALIDATOR.RUNTIME_PRIVATE_FILES:
            self.assertIn(
                f"{relative.as_posix()} must not be Git tracked",
                output,
            )

    def test_unignored_runtime_paths_fail_even_when_files_are_absent(self) -> None:
        reporter, output = self._validate_privacy()

        self.assertEqual(len(VALIDATOR.RUNTIME_PRIVATE_FILES), reporter.errors, output)
        for relative in VALIDATOR.RUNTIME_PRIVATE_FILES:
            self.assertIn(
                f"{relative.as_posix()} must be excluded by an effective Git ignore rule",
                output,
            )

    def test_project_validation_runs_runtime_privacy_check(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            reporter = VALIDATOR.validate(self.project, require_committed=False)

        rendered = output.getvalue()
        self.assertGreaterEqual(
            reporter.errors,
            len(VALIDATOR.RUNTIME_PRIVATE_FILES),
            rendered,
        )
        for relative in VALIDATOR.RUNTIME_PRIVATE_FILES:
            self.assertIn(
                f"{relative.as_posix()} must be excluded by an effective Git ignore rule",
                rendered,
            )

    def test_dispatch_rejects_an_unreachable_task_card_commit(self) -> None:
        state_path = self.project / "docs/pm/state/tasks.yaml"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps({"tasks": []}) + "\n", encoding="utf-8"
        )
        self._git("add", state_path.relative_to(self.project).as_posix())
        self._git(
            "-c",
            "user.name=AgentDesk Test",
            "-c",
            "user.email=agentdesk@example.invalid",
            "commit",
            "-m",
            "task-card baseline",
        )
        tree = self._git("rev-parse", "HEAD^{tree}").stdout.strip()
        orphan = self._git(
            "-c",
            "user.name=AgentDesk Test",
            "-c",
            "user.email=agentdesk@example.invalid",
            "commit-tree",
            tree,
        ).stdout.strip()

        dispatch = {"dispatch_id": "DSP-ORPHAN"}
        task = {
            "task_id": "TC-999",
            "task_card_commit": orphan,
            "current_dispatch": dispatch,
        }
        state_path.write_text(
            json.dumps({"tasks": [task]}) + "\n", encoding="utf-8"
        )
        self._git("add", state_path.relative_to(self.project).as_posix())
        self._git(
            "-c",
            "user.name=AgentDesk Test",
            "-c",
            "user.email=agentdesk@example.invalid",
            "commit",
            "-m",
            "dispatch task",
        )

        reporter = VALIDATOR.Reporter()
        output = io.StringIO()
        with redirect_stdout(output):
            VALIDATOR._validate_dispatch_evidence(
                self.project,
                task,
                None,
                dispatch,
                {},
                "tasks[0]",
                "dispatched",
                True,
                reporter,
            )

        self.assertGreater(reporter.errors, 0, output.getvalue())
        self.assertIn(
            "task_card_commit must be an ancestor of dispatch commit",
            output.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
