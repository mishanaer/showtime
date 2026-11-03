"""AXe character typing contracts; all device processes are mocked."""

import io
import itertools
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import native_action
from edit_events import plan_edit


class NativeTypingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.clock = self.root / "capture.clock.json"
        self.clock.write_text(json.dumps({"ready_ms": 1000, "exit_code": None,
                                          "simulator": "TEST-ONLY"}), encoding="utf-8")
        self.journal = self.root / "actions.json"
        self.text = self.root / "text.txt"

    def args(self, *extra):
        return ["native_action.py", "--axe", "/test-only/axe", "--clock", str(self.clock),
                "--journal", str(self.journal), "--id", "input", *map(str, extra)]

    def run_mocked(self, argv):
        process = mock.Mock(stdout=iter(["Sending Key first\n", "HID event performed successfully.\n",
                                        "Sending Key last\n", "HID event performed successfully.\n"]))
        process.wait.return_value = 0
        with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stdout", io.StringIO()), \
                mock.patch.object(native_action.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(native_action.time, "time_ns", side_effect=itertools.count(1_100_000_000, 100_000_000)):
            native_action.main()
        return popen

    def test_mismatched_tap_coordinates_are_rejected_before_dispatch(self):
        args = self.args("--type", "tap", "--point", "50", "50", "--step", "tap -x 12 -y 22")
        with self.assertRaises(SystemExit):
            self.run_mocked(args)

    def test_all_printable_characters_round_trip_through_batch_quoting(self):
        text = "".join(chr(code) for code in range(32, 127))
        steps = native_action.character_steps(text, .05)
        self.assertEqual(len(steps), 2 * len(text) - 1)
        self.assertEqual([shlex.split(step) for step in steps[::2]], [["type", character] for character in text])
        self.assertTrue(all(shlex.split(step) == ["sleep", "0.05"] for step in steps[1::2]))

    def test_single_batch_contains_each_character_and_journal_preserves_input_span(self):
        self.text.write_text("A '$", encoding="utf-8")
        popen = self.run_mocked(self.args("--type", "type_text", "--text-file", self.text,
                                          "--character-delay", "0.04"))
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        self.assertNotIn("shell", popen.call_args.kwargs)
        steps = [argv[index + 1] for index, value in enumerate(argv) if value == "--step"]
        self.assertEqual(steps, native_action.character_steps("A '$", .04))
        journal = json.loads(self.journal.read_text())
        event = journal["events"][0]
        self.assertEqual(event["type"], "type_text")
        self.assertEqual(event["character_count"], 4)
        self.assertEqual(event["character_delay_seconds"], .04)
        self.assertEqual(event["source_start"], .2)
        self.assertEqual(event["source_end"], .5)
        self.assertNotIn("point", event)
        observations = {"schema_version": 1, "capture_sha256": "a" * 64, "review_status": "reviewed",
                        "steps": [{"event_id": "input", "response_start": .2, "transition_end": .5,
                                   "result_ready": .5, "overlays_clear": .5, "read_seconds": .5}],
                        "protected_ranges": []}
        plan = plan_edit(journal, observations, 2, "a" * 64)
        self.assertEqual(plan["gestures"], [])
        self.assertGreaterEqual(plan["intervals"][-1][1], 60)

    def test_empty_unicode_and_control_text_fail_before_dispatch(self):
        for text in ("", "Alpha 👋", "Привет", "line\nbreak", "tab\there"):
            with self.subTest(text=text):
                self.text.write_text(text, encoding="utf-8")
                with mock.patch.object(sys, "argv", self.args("--type", "type_text", "--text-file", self.text)), \
                        mock.patch.object(sys, "stderr", io.StringIO()), \
                        mock.patch.object(native_action.subprocess, "Popen") as popen, \
                        self.assertRaises(SystemExit):
                    native_action.main()
                popen.assert_not_called()
                self.assertFalse(self.journal.exists())

    def test_invalid_character_delay_is_rejected(self):
        for delay in (0, -.01, float("nan"), float("inf")):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                native_action.character_steps("Hello", delay)

    def test_existing_step_action_still_uses_one_unchanged_step(self):
        step = "tap -x 100 -y 200"
        popen = self.run_mocked(self.args("--type", "tap", "--point", "100", "200", "--step", step))
        argv = popen.call_args.args[0]
        self.assertEqual(argv.count("--step"), 1)
        self.assertEqual(argv[argv.index("--step") + 1], step)
        event = json.loads(self.journal.read_text())["events"][0]
        self.assertEqual(event["point"], [100, 200])
        self.assertNotIn("character_count", event)

    def test_other_action_still_requires_step_before_dispatch(self):
        with mock.patch.object(sys, "argv", self.args("--type", "tap")), \
                mock.patch.object(sys, "stderr", io.StringIO()), \
                mock.patch.object(native_action.subprocess, "Popen") as popen, \
                self.assertRaises(SystemExit):
            native_action.main()
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
