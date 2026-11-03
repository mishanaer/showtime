"""Progress persistence only; no app, simulator or production artifacts are used."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "showtime_run.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("showtime_run_under_test", SCRIPT)
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)


class ShowtimeRunTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.app = self.root / "app"
        self.app.mkdir()
        self.run_dir = self.root / "run"
        self.manifest = self.run_dir / "run.json"

    def test_progress_and_relative_artifacts_survive_separate_cli_calls(self):
        result = self.cli("init", "--run-dir", self.run_dir, "--app-path", self.app, "--simulator", "test-device")
        self.assertEqual(result.returncode, 0, result.stderr)
        initial = json.loads(result.stdout)
        self.assertEqual(initial["app_path"], str(self.app))
        self.assertEqual(initial["status"], "planning")
        self.assertEqual(initial["simulator"], "test-device")
        (self.run_dir / "frame.png").write_bytes(b"test frame")
        external = self.root / "scenario.json"
        external.write_text("{}", encoding="utf-8")
        result = self.cli("note", "--run-dir", self.run_dir, "--status", "preparing",
                          "--next-step", "Preview the input transition.",
                          "--artifact", "frame=frame.png", "--artifact", f"scenario={external}")
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = json.loads(result.stdout)
        self.assertEqual(updated["id"], initial["id"])
        self.assertEqual(updated["artifacts"], {"frame": "frame.png", "scenario": "../scenario.json"})
        result = self.cli("status", "--run-dir", self.run_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["run"], updated)
        self.assertEqual(report["artifact_exists"], {"frame": True, "scenario": True})
        self.assertTrue(report["status_is_progress_only"])

    def test_simultaneous_notes_keep_all_artifacts(self):
        RUN.init_run(self.run_dir, self.app)
        processes = []
        for i in range(6):
            name = f"file-{i}.txt"
            (self.run_dir / name).touch()
            processes.append(subprocess.Popen([sys.executable, str(SCRIPT), "note", "--run-dir", str(self.run_dir),
                    "--status", "editing", "--next-step", "Review", "--artifact", f"{i}={name}"],
                    stdout=subprocess.DEVNULL))
        self.assertEqual([process.wait() for process in processes], [0] * 6)
        self.assertEqual(len(json.loads(self.manifest.read_text())["artifacts"]), 6)

    def test_init_never_overwrites_existing_manifest(self):
        RUN.init_run(self.run_dir, self.app)
        before = self.manifest.read_bytes()
        with self.assertRaisesRegex(ValueError, "init"):
            RUN.init_run(self.run_dir, self.app)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()), ["run.json"])

    def test_missing_or_directory_artifact_does_not_save_partial_note(self):
        RUN.init_run(self.run_dir, self.app)
        before = self.manifest.read_bytes()
        (self.run_dir / "valid.txt").write_text("fixture", encoding="utf-8")
        for invalid in ("missing.txt", "."):
            with self.subTest(invalid=invalid), self.assertRaises((OSError, ValueError)):
                RUN.note_run(self.run_dir, "recorded", "Edit the recording.",
                             ["valid=valid.txt", f"bad={invalid}"])
            self.assertEqual(self.manifest.read_bytes(), before)

    def test_status_reports_missing_artifacts_without_writing(self):
        RUN.init_run(self.run_dir, self.app)
        artifact = self.run_dir / "preview.png"
        artifact.write_bytes(b"fixture")
        RUN.note_run(self.run_dir, "preparing", "Continue preparation.", ["preview=preview.png"])
        artifact.unlink()
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.run_dir.iterdir()}
        with mock.patch.object(RUN, "_save", side_effect=AssertionError("status must not write")):
            report = RUN.status_run(self.run_dir)
        after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.run_dir.iterdir()}
        self.assertEqual(report["artifact_exists"], {"preview": False})
        self.assertEqual(before, after)

    def test_failed_atomic_replace_preserves_manifest_and_cleans_temporary_file(self):
        RUN.init_run(self.run_dir, self.app)
        before = self.manifest.read_bytes()
        with mock.patch.object(RUN.os, "replace", side_effect=OSError("simulated write failure")):
            with self.assertRaises(OSError):
                RUN.note_run(self.run_dir, "editing", "Continue editing.")
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()), [".run.lock", "run.json"])

    def test_schema_and_path_errors_return_nonzero_json_error(self):
        result = self.cli("status", "--run-dir", self.run_dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("error", json.loads(result.stderr))
        self.assertFalse(self.run_dir.exists())
        result = self.cli("init", "--run-dir", self.run_dir, "--app-path", self.root / "missing-app")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.run_dir.exists())
        RUN.init_run(self.run_dir, self.app)
        self.manifest.write_text('{"schema_version": true}', encoding="utf-8")
        result = self.cli("status", "--run-dir", self.run_dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("schema_version", json.loads(result.stderr)["error"])

    def cli(self, *arguments):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, arguments)],
                              capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
