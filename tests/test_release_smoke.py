from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "agentdesk"
SCRIPT_NAMES = (
    "adopt_legacy_project.py",
    "check_compatibility.py",
    "init_project.py",
    "render_views.py",
    "select_model.py",
    "validate_consultations.py",
    "validate_project.py",
    "validate_runtime.py",
)


def run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )


def installable_skill_entries() -> list[Path]:
    return sorted(
        SKILL_ROOT.rglob("*"),
        key=lambda path: path.relative_to(SKILL_ROOT).as_posix(),
    )


def public_text_artifacts() -> list[Path]:
    suffixes = {".json", ".md", ".py", ".txt", ".yaml", ".yml"}
    extensionless = {".gitignore", "LICENSE"}
    artifacts = []
    for path in REPO_ROOT.rglob("*"):
        relative = path.relative_to(REPO_ROOT)
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        if (
            path.is_file()
            and not path.is_symlink()
            and (path.suffix in suffixes or path.name in extensionless)
        ):
            artifacts.append(path)
    return sorted(artifacts, key=lambda path: path.as_posix())


class ReleaseSmokeTests(unittest.TestCase):
    def test_repository_release_files_and_install_command(self) -> None:
        for relative in (
            "README.md",
            "LICENSE",
            ".gitignore",
            ".github/workflows/validate.yml",
        ):
            self.assertTrue((REPO_ROOT / relative).is_file(), relative)

        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn('release_tag="v0.2.1-beta"', readme)
        self.assertIn("--branch \"$release_tag\"", readme)
        self.assertIn("--source skills/agentdesk", readme)
        self.assertIn("--release-id \"$release_tag\"", readme)
        self.assertIn("--expected-release \"$release_tag\"", readme)
        self.assertIn('codex_home="${CODEX_HOME:-$HOME/.codex}"', readme)
        self.assertIn(
            'test -z "$(git status --porcelain=v1 --untracked-files=all)"',
            readme,
        )
        self.assertNotIn("--ref v0.2.1-beta", readme)
        self.assertIn("$agentdesk", readme)

        source_manifest = json.loads(
            (SKILL_ROOT / "release-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual("0.2.1-dev", source_manifest["skill_release"])
        self.assertEqual("development", source_manifest["release_status"])
        self.assertIsNone(source_manifest["source_commit"])
        self.assertIsNone(source_manifest["bundle_sha256"])

        template_runtime = (
            "skills/agentdesk/assets/project-template/.agentdesk/runtime/routes.yaml"
        )
        template_ignore = run(
            "git",
            "check-ignore",
            "--quiet",
            "--no-index",
            template_runtime,
            cwd=REPO_ROOT,
        )
        self.assertEqual(template_ignore.returncode, 1, template_ignore.stdout)

        root_runtime_ignore = run(
            "git",
            "check-ignore",
            "--quiet",
            "--no-index",
            ".agentdesk/runtime/routes.yaml",
            cwd=REPO_ROOT,
        )
        self.assertEqual(root_runtime_ignore.returncode, 0, root_runtime_ignore.stdout)

    def test_public_release_shell_blocks_are_fail_fast_and_valid(self) -> None:
        documents = (
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "03-getting-started.md",
            REPO_ROOT / "docs" / "10-sharing-and-portability.md",
        )
        selected_blocks: list[tuple[Path, str]] = []
        for document in documents:
            content = document.read_text(encoding="utf-8")
            for block in re.findall(r"```bash\n(.*?)```", content, re.DOTALL):
                if (
                    'release_tag="v0.2.1-beta"' in block
                    or 'backup_path="$backup_root/agentdesk-legacy-backup"' in block
                ):
                    selected_blocks.append((document, block))

        self.assertEqual(5, len(selected_blocks))
        for document, block in selected_blocks:
            self.assertIn("(\n  set -eu\n", block, str(document))
            parsed = run("sh", "-n", "-c", block, cwd=REPO_ROOT)
            self.assertEqual(0, parsed.returncode, f"{document}:\n{parsed.stdout}")

    def test_skill_metadata_and_interface_match_public_name(self) -> None:
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill_md.startswith("---\n"))
        self.assertIn("\nname: agentdesk\n", skill_md)
        self.assertRegex(skill_md, r"\ndescription:\s*\S")

        interface = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("$agentdesk", interface)

    def test_installable_skill_tree_is_complete_and_clean(self) -> None:
        required = (
            "SKILL.md",
            "release-manifest.json",
            "agents/openai.yaml",
            "scripts/adopt_legacy_project.py",
            "scripts/check_compatibility.py",
            "scripts/init_project.py",
            "scripts/render_views.py",
            "scripts/select_model.py",
            "scripts/validate_consultations.py",
            "scripts/validate_project.py",
            "scripts/validate_runtime.py",
            "references/consultation-protocol.md",
            "references/initialization-and-adoption.md",
            "references/versioning-and-upgrades.md",
            "assets/consultation-brief-template.md",
            "assets/consultation-record-template.yaml",
            "assets/consultation-response-template.md",
            "assets/consultation-response-r2-template.md",
            "assets/consultation-synthesis-template.md",
            "assets/project-template/docs/pm/CONSULTATION-POLICY.yaml",
            "assets/project-template/docs/pm/CONSULTATIONS.md",
            "assets/project-template/docs/pm/AGENTDESK.lock.json",
            "assets/project-template/.agentdesk/runtime/consultations.yaml",
            "assets/project-template/.agentdesk/runtime/model-bindings.yaml",
            "assets/project-template/.agentdesk/runtime/routes.yaml",
            "assets/project-template/.agentdesk/runtime/transport-receipts.yaml",
        )
        for relative in required:
            self.assertTrue((SKILL_ROOT / relative).is_file(), relative)

        self.assertFalse((SKILL_ROOT / "README.md").exists())
        for path in installable_skill_entries():
            self.assertFalse(path.is_symlink(), str(path))
            self.assertNotIn("__pycache__", path.parts)
            self.assertNotEqual(path.name, ".DS_Store")
            self.assertNotEqual(path.suffix, ".pyc")

    def test_entrypoint_and_initialization_links_resolve_inside_bundle(self) -> None:
        documents = (
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "references" / "initialization-and-adoption.md",
        )
        for document in documents:
            content = document.read_text(encoding="utf-8")
            links = re.findall(r"\[[^\]\n]+\]\(([^\s)]+)\)", content)
            self.assertTrue(links, str(document))
            for link in links:
                with self.subTest(document=document.name, link=link):
                    parsed = urlsplit(link)
                    if parsed.scheme or parsed.netloc:
                        continue
                    target = (
                        document.parent / unquote(parsed.path)
                        if parsed.path
                        else document
                    ).resolve()
                    self.assertIn(SKILL_ROOT.resolve(), target.parents)
                    self.assertTrue(target.is_file(), str(target))
                    if parsed.fragment:
                        headings = re.findall(
                            r"^#{1,6} (.+)$",
                            target.read_text(encoding="utf-8"),
                            re.MULTILINE,
                        )
                        anchors = {
                            re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                            for heading in headings
                        }
                        self.assertIn(unquote(parsed.fragment), anchors)

    def test_public_snapshot_contains_no_obvious_private_material(self) -> None:
        mac_home = re.escape("/" + "Users" + "/")
        linux_home = re.escape("/" + "home" + "/")
        patterns = (
            re.compile(mac_home + r"(?!example(?:/|\b))[^/\s]+(?:/|\b)", re.IGNORECASE),
            re.compile(linux_home + r"(?!example(?:/|\b))[^/\s]+(?:/|\b)", re.IGNORECASE),
            re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
            re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
            re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
        )
        patterns += (
            re.compile(r"/(?:private/)?var/folders/[A-Za-z0-9_/-]+"),
            re.compile(r"\bcodex-clipboard-[0-9a-f-]{20,}\b", re.IGNORECASE),
            re.compile(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
                re.IGNORECASE,
            ),
        )
        private_project_names = (
            "siki" + "_" + "noise",
            "photo" + "-" + "manager",
            "账号" + "运营",
            "口播" + "剪辑",
        )
        for path in public_text_artifacts():
            content = path.read_text(encoding="utf-8")
            for pattern in patterns:
                self.assertIsNone(pattern.search(content), f"{pattern.pattern}: {path}")
            for private_name in private_project_names:
                self.assertNotIn(private_name, content, f"{private_name}: {path}")

    def test_all_scripts_expose_help(self) -> None:
        for name in SCRIPT_NAMES:
            result = run(sys.executable, str(SKILL_ROOT / "scripts" / name), "--help")
            self.assertEqual(result.returncode, 0, f"{name}:\n{result.stdout}")
            self.assertIn("usage:", result.stdout.lower())

        render_help = run(
            sys.executable,
            str(SKILL_ROOT / "scripts" / "render_views.py"),
            "--help",
        )
        validator_help = run(
            sys.executable,
            str(SKILL_ROOT / "scripts" / "validate_project.py"),
            "--help",
        )
        self.assertIn("--migration-prepare", render_help.stdout)
        self.assertIn("--migration-prepare", validator_help.stdout)

    def test_migration_prepare_argument_guards(self) -> None:
        renderer = str(SKILL_ROOT / "scripts" / "render_views.py")
        validator = str(SKILL_ROOT / "scripts" / "validate_project.py")
        manifest = str(SKILL_ROOT / "release-manifest.json")

        render_check = run(
            sys.executable,
            renderer,
            "--migration-prepare",
            "--check",
        )
        self.assertEqual(2, render_check.returncode, render_check.stdout)
        self.assertIn(
            "--migration-prepare cannot be combined with --check",
            render_check.stdout,
        )

        render_manifest = run(
            sys.executable,
            renderer,
            "--migration-prepare",
            "--release-manifest",
            manifest,
        )
        self.assertEqual(2, render_manifest.returncode, render_manifest.stdout)
        self.assertIn(
            "--migration-prepare cannot be combined with --release-manifest",
            render_manifest.stdout,
        )

        validate_without_precommit = run(
            sys.executable,
            validator,
            "--migration-prepare",
        )
        self.assertEqual(
            2,
            validate_without_precommit.returncode,
            validate_without_precommit.stdout,
        )
        self.assertIn(
            "--migration-prepare requires --pre-commit",
            validate_without_precommit.stdout,
        )

    def test_bundled_python_children_ignore_pythonpath_sitecustomize(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="agentdesk-release-python-isolation-"
        ) as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            injection_root = root / "pythonpath-injection"
            injection_root.mkdir()
            marker = root / "sitecustomize-executed"
            (injection_root / "sitecustomize.py").write_text(
                "\n".join(
                    (
                        "import os",
                        "from pathlib import Path",
                        "marker = os.environ.get('AGENTDESK_TEST_INJECTION_MARKER')",
                        "if marker:",
                        "    Path(marker).write_text('executed\\n', encoding='utf-8')",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(injection_root)
            environment["AGENTDESK_TEST_INJECTION_MARKER"] = str(marker)

            cases = (
                (
                    "render_views.py",
                    ("--project", str(project), "--check"),
                    "version compatibility",
                ),
                (
                    "validate_project.py",
                    ("--project", str(project), "--require-runtime"),
                    "version compatibility",
                ),
                (
                    "select_model.py",
                    (
                        "--project",
                        str(project),
                        "--task-card-commit",
                        "0" * 40,
                        "--role-id",
                        "role-test",
                        "--risk",
                        "L0",
                    ),
                    "dispatch version gate failed",
                ),
            )
            for name, arguments, expected_output in cases:
                with self.subTest(script=name):
                    marker.unlink(missing_ok=True)
                    result = run(
                        sys.executable,
                        "-I",
                        str(SKILL_ROOT / "scripts" / name),
                        *arguments,
                        env=environment,
                    )
                    self.assertIn(expected_output, result.stdout)
                    self.assertFalse(
                        marker.exists(),
                        f"{name} child executed PYTHONPATH sitecustomize",
                    )

    def test_initializer_renderer_and_validator_smoke_flow(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentdesk-release-smoke-") as temp:
            project = Path(temp) / "demo"
            init_result = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "init_project.py"),
                "--project",
                str(project),
                "--project-id",
                "release-smoke",
                "--mode",
                "standard",
                "--pm-holder-id",
                "pm-release-smoke",
                "--init-git",
                "--allow-development-release",
            )
            self.assertEqual(init_result.returncode, 0, init_result.stdout)
            lock_path = project / "docs/pm/AGENTDESK.lock.json"
            lock_bytes = lock_path.read_bytes()
            lock = json.loads(lock_bytes)
            manifest = json.loads(
                (SKILL_ROOT / "release-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                {
                    "release": manifest["skill_release"],
                    "source_commit": manifest["source_commit"],
                    "bundle_sha256": manifest["bundle_sha256"],
                },
                lock["skill"],
            )
            self.assertEqual(manifest["protocol_version"], lock["protocol"]["version"])
            self.assertEqual(manifest["schema_set"]["schemas"], lock["schemas"])
            self.assertEqual(manifest["runtime_adapter"], lock["runtime_adapter"])

            idempotent_result = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "init_project.py"),
                "--project",
                str(project),
                "--project-id",
                "release-smoke",
                "--mode",
                "standard",
                "--pm-holder-id",
                "pm-release-smoke",
                "--allow-development-release",
            )
            self.assertEqual(
                idempotent_result.returncode, 2, idempotent_result.stdout
            )
            self.assertIn(
                "limited to creating a fresh disposable",
                idempotent_result.stdout,
            )
            self.assertEqual(lock_bytes, lock_path.read_bytes())

            compatibility_read = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "check_compatibility.py"),
                "--project",
                str(project),
                "--operation",
                "read",
                "--json",
            )
            self.assertEqual(
                compatibility_read.returncode, 0, compatibility_read.stdout
            )
            self.assertEqual(
                "warning", json.loads(compatibility_read.stdout)["status"]
            )

            compatibility_write = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "check_compatibility.py"),
                "--project",
                str(project),
                "--operation",
                "write",
                "--json",
            )
            self.assertEqual(
                compatibility_write.returncode, 1, compatibility_write.stdout
            )
            self.assertEqual(
                "incompatible", json.loads(compatibility_write.stdout)["status"]
            )
            self.assertTrue((project / "docs/pm/consultations").is_dir())
            self.assertTrue(
                (project / "docs/pm/CONSULTATION-POLICY.yaml").is_file()
            )
            self.assertTrue((project / "docs/pm/CONSULTATIONS.md").is_file())
            self.assertTrue(
                (project / ".agentdesk/runtime/consultations.yaml").is_file()
            )

            generated_before = {
                relative: (project / relative).read_bytes()
                for relative in (
                    "docs/pm/BOARD.md",
                    "docs/pm/STATUS.md",
                    "docs/pm/CONSULTATIONS.md",
                )
            }
            render_result = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "render_views.py"),
                "--project",
                str(project),
            )
            self.assertEqual(render_result.returncode, 1, render_result.stdout)
            self.assertIn(
                "version compatibility gate blocked write",
                render_result.stdout,
            )
            for relative, content in generated_before.items():
                self.assertEqual(content, (project / relative).read_bytes())

            migration_render = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "render_views.py"),
                "--project",
                str(project),
                "--migration-prepare",
            )
            self.assertEqual(
                migration_render.returncode, 1, migration_render.stdout
            )
            self.assertIn(
                "version compatibility gate blocked migrate",
                migration_render.stdout,
            )
            for relative, content in generated_before.items():
                self.assertEqual(content, (project / relative).read_bytes())
            self.assertIn(
                "source=docs/pm/consultations/*/consultation.yaml",
                (project / "docs/pm/CONSULTATIONS.md").read_text(encoding="utf-8"),
            )

            render_check = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "render_views.py"),
                "--project",
                str(project),
                "--check",
            )
            self.assertEqual(render_check.returncode, 0, render_check.stdout)

            consultation_result = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "validate_consultations.py"),
                "--project",
                str(project),
                "--require-runtime",
            )
            self.assertEqual(
                consultation_result.returncode, 0, consultation_result.stdout
            )

            validate_result = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "validate_project.py"),
                "--project",
                str(project),
                "--pre-commit",
                "--allow-development-release",
            )
            self.assertEqual(validate_result.returncode, 0, validate_result.stdout)
            self.assertRegex(validate_result.stdout, r"SUMMARY \d+ pass\(es\), \d+ warning\(s\), 0 error\(s\)")

            migration_validate = run(
                sys.executable,
                str(SKILL_ROOT / "scripts" / "validate_project.py"),
                "--project",
                str(project),
                "--pre-commit",
                "--migration-prepare",
            )
            self.assertEqual(
                migration_validate.returncode, 1, migration_validate.stdout
            )
            self.assertIn(
                "migrate requires release_status 'stable'",
                migration_validate.stdout,
            )

            ignored = run(
                "git",
                "-C",
                str(project),
                "check-ignore",
                "--quiet",
                "--no-index",
                ".agentdesk/runtime/routes.yaml",
            )
            self.assertEqual(ignored.returncode, 0, ignored.stdout)


if __name__ == "__main__":
    unittest.main()
