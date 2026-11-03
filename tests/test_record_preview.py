"""Recorder entry contracts; all simulator/process operations are mocked."""

import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("recorder_preview_test", SCRIPTS / "record_simulator.py")
RECORDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECORDER)


class RecordPreviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = self.root / "preview.mp4"
        self.scenario = self.root / "scenario.json"
        self.prepared = self.root / "prepared.json"
        self.approvals = self.root / "approvals"
        self.revision = self.root / "revision.json"
        self.argv = ["record_simulator.py", "--simulator", "TEST-ONLY", "--output", str(self.output),
                     "--scenario", str(self.scenario), "--approval-dir", str(self.approvals)]

    def run_recorder(self, extra, review=None, error=None):
        process = mock.Mock(pid=123, stdout=iter(["Recording started\n"]))
        process.wait.return_value = 0
        with mock.patch.object(sys, "argv", self.argv + extra), \
             mock.patch.object(sys, "stdout", io.StringIO()), \
             mock.patch.object(sys, "stderr", io.StringIO()), \
             mock.patch.object(RECORDER, "check_review_gate", return_value=review, side_effect=error) as gate, \
             mock.patch.object(RECORDER.subprocess, "Popen", return_value=process) as popen, \
             mock.patch.object(RECORDER.signal, "signal"), \
             mock.patch.object(RECORDER.time, "clock_gettime_ns", return_value=1_000_000_000):
            try:
                RECORDER.main()
            except SystemExit as exception:
                return gate, popen, exception.code
        return gate, popen, None

    def test_scenario_only_preview_is_allowed_and_identified(self):
        review = {"approved": True, "stage": "preparation", "story_materials_checked": False}
        gate, popen, code = self.run_recorder(["--preparation-preview"], review)
        self.assertIsNone(code)
        gate.assert_called_once_with(self.scenario, None, self.approvals)
        popen.assert_called_once()
        clock = json.loads(self.output.with_suffix(".clock.json").read_text())
        self.assertEqual(clock["purpose"], "preparation-preview")
        self.assertEqual(clock["human_review"], review)
        self.assertEqual(clock["exit_code"], 0)
        self.assertNotIn("prepared_approval_sha256", clock["human_review"])

    def test_final_without_prepared_content_never_starts(self):
        gate, popen, code = self.run_recorder([])
        self.assertEqual(code, 2)
        gate.assert_not_called()
        popen.assert_not_called()
        self.assertFalse(self.output.with_suffix(".clock.json").exists())

    def test_preview_without_scenario_approval_is_blocked(self):
        gate, popen, code = self.run_recorder(["--preparation-preview"],
            error=RECORDER.ReviewGateError("Нет согласования сценария"))
        self.assertEqual(code, 2)
        gate.assert_called_once_with(self.scenario, None, self.approvals)
        popen.assert_not_called()
        self.assertFalse(self.output.with_suffix(".clock.json").exists())

    def test_revision_is_forwarded_for_final_capture(self):
        review = {"approved": True, "stage": "recording", "authorization": "revision"}
        gate, popen, code = self.run_recorder(
            ["--prepared-content", str(self.prepared), "--revision-file", str(self.revision)], review)
        self.assertIsNone(code)
        gate.assert_called_once_with(self.scenario, self.prepared, self.approvals, revision_file=self.revision)
        popen.assert_called_once()
        clock = json.loads(self.output.with_suffix(".clock.json").read_text())
        self.assertEqual(clock["purpose"], "final")
        self.assertEqual(clock["human_review"], review)

    def test_ordinary_final_keeps_three_argument_gate_call(self):
        gate, popen, code = self.run_recorder(["--prepared-content", str(self.prepared)],
                                             {"approved": True, "stage": "recording"})
        self.assertIsNone(code)
        gate.assert_called_once_with(self.scenario, self.prepared, self.approvals)
        popen.assert_called_once()

    def test_preview_cannot_use_revision_authorization(self):
        gate, popen, code = self.run_recorder(
            ["--preparation-preview", "--revision-file", str(self.revision)])
        self.assertEqual(code, 2)
        gate.assert_not_called()
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
