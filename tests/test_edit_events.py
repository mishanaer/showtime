"""Editor integration: reviewed states, complete gestures and recorded contact evidence."""

import copy
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
    spec = importlib.util.spec_from_file_location(name + "_under_test", SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EDITOR = load_script("edit_events")
NATIVE_ACTION = load_script("native_action")
SOURCE_HASH = "a" * 64


class EditEventsTests(unittest.TestCase):
    def setUp(self):
        self.tap = {"id": "open", "type": "tap", "source_start": 2.0, "source_end": 2.1,
                    "point": [50, 100], "alignment": "hid_dispatch_estimate"}
        self.hold = {"id": "menu", "type": "long_press", "source_start": 6.0, "source_end": 7.0,
                     "contact_start": 6.1, "contact_end": 6.8, "contact_timing": "hid_dispatch_estimate",
                     "point": [100, 200], "tint_rgb": [139, 167, 211],
                     "alignment": "axe_hid_dispatch_log; physical_touch_down_not_measured"}
        self.raw = {"coordinate_space": {"width": 402, "height": 874}, "events": [self.tap, self.hold]}
        self.observations = {"schema_version": 1, "capture_sha256": SOURCE_HASH,
                             "review_status": "reviewed", "protected_ranges": [],
                             "steps": [{"event_id": "open", "response_start": 2.1,
                                        "transition_end": 2.12, "result_ready": 2.12,
                                        "overlays_clear": 2.1, "read_seconds": 0},
                                       {"event_id": "menu", "response_start": 6.2,
                                        "transition_end": 6.6, "result_ready": 6.6,
                                        "overlays_clear": 6.2, "read_seconds": .1}]}

    def plan(self, duration=12):
        return EDITOR.plan_edit(self.raw, self.observations, duration, SOURCE_HASH)

    def test_hold_uses_contact_not_command_duration_and_preserves_provenance(self):
        gesture = EDITOR.visual_gesture(self.hold)
        self.assertEqual(gesture["source_time"], 6.1)
        self.assertAlmostEqual(gesture["hold_seconds"], .7)
        self.assertEqual(gesture["command_duration"], 1.0)
        self.assertEqual(gesture["hold_timing"], "hid_dispatch_estimate")
        self.assertIn("physical_touch_down_not_measured", gesture["alignment"])
        self.assertEqual(gesture["tint_rgb"], [139, 167, 211])

    def test_contact_start_end_are_required_without_command_duration_fallback(self):
        for missing in ("contact_start", "contact_end"):
            with self.subTest(missing=missing):
                event = copy.deepcopy(self.hold)
                del event[missing]
                # A plausible CLI duration must not manufacture the absent contact evidence.
                event["command_duration"] = .7
                with self.assertRaisesRegex(ValueError, "contact_start and contact_end"):
                    EDITOR.visual_gesture(event)

    def test_invalid_or_outside_contact_and_missing_provenance_fail(self):
        variants = [{"contact_start": True}, {"contact_end": float("nan")},
                    {"contact_start": 5.9}, {"contact_end": 7.1},
                    {"contact_end": 6.1}, {"contact_end": 6.0},
                    {"contact_timing": None}, {"contact_timing": "command_duration"}]
        for changes in variants:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    EDITOR.visual_gesture({**self.hold, **changes})

    def test_plan_keeps_complete_tap_and_hold_entry_release_without_mutating_evidence(self):
        before = copy.deepcopy(self.observations)
        plan = self.plan()
        self.assertEqual(self.observations, before)
        protected = plan["checks"]["protected_ranges"]
        self.assertEqual(len(protected), 2)
        for gesture in plan["gestures"]:
            mapping, _ = EDITOR.gesture_frame_map(gesture, 41, 25, 60)
            first = round(gesture["source_time"] * 60)-25
            last = first+len(mapping)
            self.assertTrue(any(a <= first and b >= last for a, b in plan["intervals"]))
            self.assertTrue(any(item["frames"] == [first, last] for item in protected))
        # The tap's reviewed state was already ready at 2.12; the release lasts longer.
        self.assertGreater(plan["intervals"][0][1]/60, 2.12)
        # The long command ends at 7.0; the complete release must survive past it.
        self.assertGreater(plan["intervals"][-1][1]/60, 7.0)

    def test_insufficient_preroll_or_release_tail_fails_instead_of_clipping_gesture(self):
        self.tap.update(source_start=.2, source_end=.3)
        self.observations["steps"][0].update(response_start=.3, transition_end=.32,
                                               result_ready=.32, overlays_clear=.3)
        with self.assertRaisesRegex(ValueError, "before contact"):
            self.plan()
        self.tap.update(source_start=2.0, source_end=2.1)
        self.observations["steps"][0].update(response_start=2.1, transition_end=2.12,
                                               result_ready=2.12, overlays_clear=2.1)
        # Reviewed states and command fit, but the release asset needs until 7.05 s.
        with self.assertRaises(ValueError):
            self.plan(duration=7.0)

    def test_clean_result_reading_time_is_kept_after_late_overlay(self):
        self.observations["steps"][1].update(overlays_clear=9.5, read_seconds=1.25)
        plan = self.plan()
        self.assertEqual(plan["checks"]["steps"][1]["hold_start"], 9.5)
        self.assertEqual(plan["checks"]["steps"][1]["required_end"], 10.75)
        self.assertEqual(plan["intervals"][-1][1], 645)
        with self.assertRaises(ValueError):
            self.plan(duration=10.5)

    def test_unreviewed_or_wrong_source_observations_fail_before_gesture_planning(self):
        for key, value in (("review_status", "draft"), ("capture_sha256", "b"*64)):
            with self.subTest(key=key):
                observations = {**self.observations, key: value}
                with mock.patch.object(EDITOR, "visual_gesture", side_effect=AssertionError("must not run")):
                    with self.assertRaises(ValueError):
                        EDITOR.plan_edit(self.raw, observations, 12, SOURCE_HASH)

    def test_remap_across_cuts_keeps_contact_duration_color_and_evidence(self):
        plan = self.plan()
        mapped = EDITOR.remap_gestures(plan["gestures"], plan["intervals"])
        self.assertEqual([event["id"] for event in mapped], ["open", "menu"])
        first, second = plan["intervals"]
        expected_hold_frame = (first[1]-first[0])+round(6.1*60)-second[0]
        self.assertEqual(mapped[1]["frame"], expected_hold_frame)
        self.assertEqual(mapped[1]["time"], expected_hold_frame/60)
        self.assertNotIn("source_time", mapped[1])
        self.assertAlmostEqual(mapped[1]["hold_seconds"], .7)
        self.assertEqual(mapped[1]["hold_timing"], "hid_dispatch_estimate")
        self.assertEqual(mapped[1]["tint_rgb"], self.hold["tint_rgb"])
        self.assertEqual(mapped[1]["command_duration"], 1.0)

    def test_remap_rejects_lost_and_duplicated_gestures(self):
        gesture = EDITOR.visual_gesture(self.tap)
        for intervals in ([[0, 120]], [[100, 160], [110, 170]]):
            with self.subTest(intervals=intervals):
                with self.assertRaisesRegex(ValueError, "lost or duplicated"):
                    EDITOR.remap_gestures([gesture], intervals)

    def test_clock_does_not_attest_to_video_cadence(self):
        self.assertIsNone(EDITOR.validate_capture_clock(self.raw))
        raw = {"capture_clock": {"ready_ms": 1234, "exit_code": 0,
                                  "wall_clock_drift_seconds": .01, "source_frame_rate": "VFR"}}
        result = EDITOR.validate_capture_clock(raw)
        self.assertNotIn("source_frame_rate", result)
        self.assertFalse(result["physical_touch_down_measured"])


class NativeActionJournalTests(unittest.TestCase):
    """No device action is performed: the CLI process and clock are fully mocked."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.clock = self.root / "recording.clock.json"
        self.journal = self.root / "actions.json"
        self.clock.write_text(json.dumps({"ready_ms": 1000, "exit_code": None, "simulator": "TEST-ONLY"}))

    def run_action(self, lines, times_ms, step="touch -x 100 -y 200 --down --up --delay 0.7"):
        process = mock.Mock(stdout=iter(line+"\n" for line in lines))
        process.wait.return_value = 0
        argv = ["native_action.py", "--axe", "/test-only/axe", "--clock", str(self.clock),
                "--journal", str(self.journal), "--id", "menu", "--type", "long_press",
                "--point", "100", "200", "--step", step]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stdout", io.StringIO()), \
             mock.patch.object(NATIVE_ACTION.subprocess, "Popen", return_value=process) as popen, \
             mock.patch.object(NATIVE_ACTION.time, "time_ns", side_effect=[round(t*1_000_000) for t in times_ms]):
            NATIVE_ACTION.main()
        return popen

    def test_down_up_dispatch_are_separate_from_completion_and_cli_duration(self):
        popen = self.run_action(["Sending Touch <hidden>", "HID event performed successfully.",
                                "Sending Touch <hidden>", "HID event performed successfully."],
                               [1100, 1200, 1210, 1900, 1920, 1950])
        event = json.loads(self.journal.read_text())["events"][0]
        self.assertEqual(event["source_start"], .2)
        self.assertEqual(event["source_end"], .92)
        self.assertEqual(event["contact_start"], .2)
        self.assertEqual(event["contact_end"], .9)
        self.assertEqual(event["contact_timing"], "hid_dispatch_estimate")
        self.assertIn("physical_touch_down_not_measured", event["alignment"])
        self.assertEqual(popen.call_args.args[0].count("--step"), 1)
        dispatch = json.loads(Path(event["dispatch_log"]).read_text())
        self.assertEqual(dispatch["command_start_ms"], 1100)
        self.assertEqual(dispatch["command_end_ms"], 1950)
        # The resulting journal is directly acceptable to the editor.
        self.assertAlmostEqual(EDITOR.visual_gesture(event)["hold_seconds"], .7)

    def test_missing_up_dispatch_does_not_write_a_contact_event(self):
        with self.assertRaisesRegex(SystemExit, "hold was rejected"):
            self.run_action(["Sending Touch <hidden>", "HID event performed successfully."],
                            [1100, 1200, 1210, 1950])
        self.assertFalse(self.journal.exists())
        self.assertTrue((self.root / "actions-menu.dispatch.json").exists())

    def test_nonincreasing_dispatch_does_not_write_contact_event(self):
        with self.assertRaisesRegex(SystemExit, "hold was rejected"):
            self.run_action(["Sending Touch <hidden>", "HID event performed successfully.",
                             "Sending Touch <hidden>", "HID event performed successfully."],
                            [1100, 1200, 1210, 1200, 1220, 1950])
        self.assertFalse(self.journal.exists())

    def test_missing_up_completion_does_not_write_invalid_contact_event(self):
        with self.assertRaises(SystemExit):
            self.run_action(["Sending Touch <hidden>", "HID event performed successfully.",
                             "Sending Touch <hidden>"],
                            [1100, 1200, 1210, 1900, 1950])
        self.assertFalse(self.journal.exists())


if __name__ == "__main__":
    unittest.main()
