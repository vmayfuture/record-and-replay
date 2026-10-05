"""File-layout and preservation tests; no real user configuration is changed."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install.py"
SPEC = importlib.util.spec_from_file_location("skill_installer", SCRIPT)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
SOURCE = ROOT / "record-and-replay"


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, success=True):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0 if success else 2, result.stdout + result.stderr)
        return json.loads(result.stdout if success else result.stderr)

    def test_project_installation_for_all_four_directory_agents(self):
        for agent, directory in installer.DIRECTORIES.items():
            with self.subTest(agent=agent):
                project = self.directory / (agent + " project with spaces")
                result = self.cli("--agent", agent, "--project", project)
                target = project / directory / "skills" / installer.NAME
                self.assertEqual(result["status"], "installed")
                self.assertEqual(Path(result["destination"]), target)
                self.assertEqual(
                    sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()),
                    sorted(installer.FILES),
                )
                for name in installer.FILES:
                    self.assertEqual((target / name).read_bytes(), (SOURCE / name).read_bytes())

    def test_user_defaults_and_hermes_profile_override(self):
        with patch.object(Path, "home", return_value=self.directory), patch.dict(os.environ, {"HERMES_HOME": ""}):
            for agent, directory in installer.DIRECTORIES.items():
                self.assertEqual(installer.skill_root(agent), self.directory / directory / "skills")
            profile = self.directory / "profiles" / "research"
            with patch.dict(os.environ, {"HERMES_HOME": str(profile)}):
                self.assertEqual(installer.skill_root("hermes"), profile / "skills")

    def test_generic_installs_into_the_explicit_skills_root(self):
        target = self.directory / "custom skills"
        result = self.cli("--agent", "generic", "--skills-dir", target)
        self.assertEqual(Path(result["destination"]), target / installer.NAME)
        self.assertTrue((target / installer.NAME / "scripts" / "workflow.py").is_file())

    def test_repeated_install_preserves_identical_files_and_user_extras(self):
        root = self.directory / "skills"
        self.cli("--agent", "codex", "--skills-dir", root)
        extra = root / installer.NAME / "notes.txt"
        extra.write_text("local user note", encoding="utf-8")
        result = self.cli("--agent", "codex", "--skills-dir", root)
        self.assertEqual(result["status"], "already-installed")
        self.assertEqual(extra.read_text(encoding="utf-8"), "local user note")

    def test_modified_existing_install_is_not_overwritten(self):
        root = self.directory / "skills"
        self.cli("--agent", "claude-code", "--skills-dir", root)
        modified = root / installer.NAME / "SKILL.md"
        modified.write_text("my local customization", encoding="utf-8")
        self.cli("--agent", "claude-code", "--skills-dir", root, success=False)
        self.assertEqual(modified.read_text(encoding="utf-8"), "my local customization")

    def test_dry_run_creates_neither_directories_nor_zip(self):
        project = self.directory / "uncreated-project"
        result = self.cli("--agent", "codebuddy", "--project", project, "--dry-run")
        self.assertEqual(result["status"], "planned")
        self.assertFalse(project.exists())
        archive = self.directory / "uncreated" / "skill.zip"
        result = self.cli("--agent", "cherry-studio", "--zip", archive, "--dry-run")
        self.assertEqual(result["status"], "planned-export")
        self.assertFalse(archive.parent.exists())

    def test_cherry_zip_contains_the_full_skill_and_excludes_local_files(self):
        source = self.directory / "source"
        files = installer.payload(SOURCE)
        for name, content in files.items():
            file = source / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        (source / "parameters.local.json").write_text('{"token":"do-not-publish"}', encoding="utf-8")
        (source / "session.txt").write_text("do-not-publish", encoding="utf-8")
        archive_path = self.directory / "cherry.zip"
        result = self.cli("--agent", "cherry-studio", "--source", source, "--zip", archive_path)
        self.assertEqual(result["status"], "exported-for-import")
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(sorted(archive.namelist()), sorted(f"{installer.NAME}/{name}" for name in files))
            for name, content in files.items():
                self.assertEqual(archive.read(f"{installer.NAME}/{name}"), content)

    def test_export_is_repeatable_and_refuses_an_existing_other_file(self):
        archive = self.directory / "skill.zip"
        self.cli("--agent", "cherry-studio", "--zip", archive)
        original = archive.read_bytes()
        result = self.cli("--agent", "cherry-studio", "--zip", archive)
        self.assertEqual(result["status"], "already-exported")
        self.assertEqual(archive.read_bytes(), original)
        other = self.directory / "other.zip"
        other.write_bytes(b"existing user file")
        self.cli("--agent", "cherry-studio", "--zip", other, success=False)
        self.assertEqual(other.read_bytes(), b"existing user file")

    def test_invalid_source_fails_before_creating_destination(self):
        destination = self.directory / "skills"
        self.cli("--agent", "hermes", "--source", self.directory / "missing-source", "--skills-dir", destination, success=False)
        self.assertFalse(destination.exists())

    def test_agent_specific_options_are_validated_before_writing(self):
        for arguments in (
            ("--agent", "generic"),
            ("--agent", "cherry-studio", "--project", self.directory / "project"),
            ("--agent", "codex", "--zip", self.directory / "archive.zip"),
        ):
            with self.subTest(arguments=arguments):
                self.cli(*arguments, success=False)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_windows_crlf_skill_can_be_installed(self):
        source = self.directory / "windows-source"
        for name, content in installer.payload(SOURCE).items():
            file = source / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        root = self.directory / "skills"
        result = self.cli("--agent", "codex", "--source", source, "--skills-dir", root)
        self.assertEqual(result["status"], "installed")
        self.assertEqual((root / installer.NAME / "SKILL.md").read_bytes(), (source / "SKILL.md").read_bytes())
        archive = self.directory / "prebuilt.zip"
        self.cli("--agent", "cherry-studio", "--zip", archive)
        original = archive.read_bytes()
        result = self.cli("--agent", "cherry-studio", "--source", source, "--zip", archive)
        self.assertEqual(result["status"], "already-exported")
        self.assertEqual(archive.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
