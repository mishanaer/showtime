"""Capture identity and viewport checks without contacting a simulator."""

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


def load_script(name):
    spec = importlib.util.spec_from_file_location(name + "_capture_test", SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACTION = load_script("native_action")
EDITOR = load_script("edit_events")


class CaptureContractTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.clock_path = self.root / "take.clock.json"
        self.capture = self.root / "take.mp4"
        self.clock = {"output": str(self.capture), "simulator": "TEST-SIMULATOR",
                      "request_ms": 900, "ready_ms": 1000, "exit_code": None}
        self.write_clock()

    def write_clock(self):
        self.clock_path.write_text(json.dumps(self.clock))

    def finalized(self):
        self.clock.update(exit_code=0, wall_clock_drift_seconds=.01)
        self.write_clock()

    def journal(self, viewport=None):
        return ACTION.prepare_journal(self.clock_path, self.clock, viewport=viewport)

    def test_new_viewport_and_legacy_default(self):
        self.assertEqual(self.journal()["coordinate_space"], {"width": 402, "height": 874})
        for viewport in ([393, 852], [1024, 1366], [400.5, 800.5]):
            with self.subTest(viewport=viewport):
                journal = self.journal(viewport)
                self.assertEqual(journal["coordinate_space"], dict(zip(("width", "height"), viewport)))
                self.assertEqual(journal["capture_ref"]["request_ms"], 900)
                self.assertEqual(journal["capture_ref"]["output"], str(self.capture.resolve()))

    def test_existing_viewport_is_preserved_and_conflict_rejected(self):
        journal = self.journal([393, 852])
        self.assertEqual(ACTION.prepare_journal(self.clock_path, self.clock, journal)["coordinate_space"],
                         {"width": 393, "height": 852})
        self.assertIs(ACTION.prepare_journal(self.clock_path, self.clock, journal, [393, 852]), journal)
        with self.assertRaisesRegex(ValueError, "Viewport differs"):
            ACTION.prepare_journal(self.clock_path, self.clock, journal, [402, 874])
        self.assertEqual(journal["coordinate_space"], {"width": 393, "height": 852})

    def test_invalid_viewport_rejected_before_dispatch(self):
        for viewport in ([0, 800], [-1, 800], [True, 800], [float("nan"), 800], [400, float("inf")]):
            with self.subTest(viewport=viewport), self.assertRaisesRegex(ValueError, "Viewport"):
                self.journal(viewport)

    def test_new_journal_rejects_another_clock_before_appending(self):
        journal = self.journal()
        for changes in ({"request_ms": 901}, {"ready_ms": 1001}, {"simulator": "OTHER"},
                        {"output": str(self.root / "other.mp4")}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "another recording"):
                ACTION.prepare_journal(self.clock_path, {**self.clock, **changes}, journal)
        with self.assertRaisesRegex(ValueError, "another recording"):
            ACTION.prepare_journal(self.root / "other.clock.json", self.clock, journal)

    def test_legacy_clock_and_existing_unbound_events_remain_honest(self):
        old_clock = {"ready_ms": 1000, "exit_code": None, "simulator": "TEST-SIMULATOR"}
        legacy = {"coordinate_space": {"width": 393, "height": 852}, "events": [{"id": "old"}]}
        result = ACTION.prepare_journal(self.clock_path, old_clock, legacy)
        self.assertEqual(result["capture_ref"]["output"], str(self.capture.resolve()))
        self.assertIsNone(result["capture_ref"]["request_ms"])
        self.assertEqual(result["legacy_unbound_event_count"], 1)
        result["events"].append({"id": "new"})
        ACTION.prepare_journal(self.clock_path, old_clock, result)
        self.assertEqual(result["legacy_unbound_event_count"], 1)

    def test_editor_loads_final_clock_after_actions_and_binds_capture(self):
        journal = self.journal([393, 852])
        self.finalized()
        result = EDITOR.validate_capture_clock(journal, self.capture)
        self.assertTrue(result["capture_bound"])
        self.assertEqual(result["wall_clock_drift_seconds"], .01)
        self.assertFalse(result["physical_touch_down_measured"])
        with self.assertRaisesRegex(ValueError, "does not match"):
            EDITOR.validate_capture_clock(journal, self.root / "other.mp4")
        with self.assertRaisesRegex(ValueError, "does not match"):
            EDITOR.validate_capture_clock(journal)

    def test_editor_rejects_completed_rehearsal(self):
        self.clock["purpose"] = "preparation-preview"
        self.finalized()
        with self.assertRaisesRegex(ValueError, "final video"):
            EDITOR.validate_capture_clock(self.journal(), self.capture)

    def test_editor_rejects_unfinished_failed_or_drifted_recording(self):
        journal = self.journal()
        for completion in ({"exit_code": None}, {"exit_code": 1},
                           {"exit_code": 0}, {"exit_code": 0, "wall_clock_drift_seconds": .1}):
            with self.subTest(completion=completion):
                self.clock.update(completion)
                self.write_clock()
                with self.assertRaises(ValueError):
                    EDITOR.validate_capture_clock(journal, self.capture)

    def test_editor_rejects_replaced_or_missing_clock(self):
        journal = self.journal()
        self.finalized()
        self.clock["request_ms"] = 2000
        self.write_clock()
        with self.assertRaisesRegex(ValueError, "another recording"):
            EDITOR.validate_capture_clock(journal, self.capture)
        self.clock_path.unlink()
        with self.assertRaisesRegex(ValueError, "Cannot read"):
            EDITOR.validate_capture_clock(journal, self.capture)

    def test_legacy_editor_input_is_still_supported(self):
        self.assertIsNone(EDITOR.validate_capture_clock({"events": []}, self.capture))
        legacy = {"capture_clock": {"ready_ms": 1000, "exit_code": 0, "wall_clock_drift_seconds": 0}}
        self.assertFalse(EDITOR.validate_capture_clock(legacy)["capture_bound"])

    def test_malformed_reference_does_not_fall_back_to_legacy(self):
        for reference in (None, {}, {"clock_path": "relative.clock.json"}):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                EDITOR.validate_capture_clock({"capture_ref": reference}, self.capture)

    def test_cli_records_explicit_viewport_and_capture_reference(self):
        journal_path = self.root / "actions.json"
        argv = ["native_action.py", "--axe", "/test-only/axe", "--clock", str(self.clock_path),
                "--journal", str(journal_path), "--id", "tap", "--type", "tap", "--point", "40", "50",
                "--viewport", "393", "852", "--step", "tap -x 40 -y 50"]
        process = mock.Mock(stdout=iter(["Sending Touch\n", "HID event performed successfully.\n"]))
        process.wait.return_value = 0
        with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stdout", io.StringIO()), \
             mock.patch.object(ACTION.subprocess, "Popen", return_value=process):
            ACTION.main()
        result = json.loads(journal_path.read_text())
        self.assertEqual(result["coordinate_space"], {"width": 393, "height": 852})
        self.assertEqual(result["capture_ref"], ACTION.capture_reference(self.clock_path, self.clock))
        self.assertEqual(result["events"][0]["point"], [40, 50])


if __name__ == "__main__":
    unittest.main()
