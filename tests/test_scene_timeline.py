"""Meaningful state-based editing cases using synthetic source observations."""

import copy
import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scene_timeline.py"
SPEC = importlib.util.spec_from_file_location("scene_timeline_under_test", SCRIPT)
TIMELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TIMELINE)
SOURCE_HASH = "a" * 64


class SceneTimelineTests(unittest.TestCase):
    def setUp(self):
        self.events = [{"id": "open", "source_start": 2.0, "source_end": 2.1},
                       {"id": "send", "source_start": 5.0, "source_end": 5.1}]
        self.observations = {"schema_version": 1, "capture_sha256": SOURCE_HASH,
                             "review_status": "reviewed", "protected_ranges": [],
                             "steps": [{"event_id": "open", "response_start": 2.1,
                                        "transition_end": 2.4, "result_ready": 2.3,
                                        "overlays_clear": 2.1, "read_seconds": .3},
                                       {"event_id": "send", "response_start": 5.1,
                                        "transition_end": 5.4, "result_ready": 5.3,
                                        "overlays_clear": 5.1, "read_seconds": .3}]}

    def plan(self, duration=10, **kwargs):
        return TIMELINE.plan_scene_intervals(self.events, self.observations, duration,
                                             SOURCE_HASH, **kwargs)

    def test_separate_actions_keep_completed_states_and_reading_time(self):
        result = self.plan()
        self.assertEqual(result["intervals"], [[99, 162], [279, 342]])
        self.assertEqual(result["checks"]["native_speed"], 1.0)
        self.assertAlmostEqual(result["checks"]["steps"][0]["hold_start"], 2.4)
        self.assertAlmostEqual(result["checks"]["steps"][0]["required_end"], 2.7)
        self.assertEqual(result["checks"]["output_frames"], 126)

    def test_delayed_notification_clearance_extends_final_hold(self):
        self.observations["steps"][1].update(overlays_clear=8.4, read_seconds=1.2)
        result = self.plan()
        self.assertEqual(result["intervals"][-1], [279, 576])
        self.assertEqual(result["checks"]["steps"][1]["hold_start"], 8.4)
        self.assertAlmostEqual(result["checks"]["steps"][1]["required_end"], 9.6)

    def test_rapid_actions_merge_without_dropping_either_response(self):
        self.events[1].update(source_start=2.35, source_end=2.45)
        self.observations["steps"][1].update(response_start=2.45, transition_end=2.8,
                                              result_ready=3.0, overlays_clear=2.45, read_seconds=.7)
        result = self.plan()
        self.assertEqual(result["intervals"], [[99, 222]])
        self.assertEqual([s["event_id"] for s in result["checks"]["steps"]], ["open", "send"])
        self.assertEqual(result["checks"]["cuts"], [])

    def test_protected_motion_bridges_gap_and_prevents_phase_jump(self):
        self.observations["protected_ranges"] = [{"start": 2.4, "end": 4.9,
                                                   "reason": "Complete one calendar rotation"}]
        result = self.plan()
        self.assertEqual(result["intervals"], [[99, 342]])
        self.assertTrue(result["checks"]["protected_ranges_preserved"])
        self.assertEqual(result["checks"]["cuts"], [])

    def test_protected_motion_inside_idle_is_kept_whole(self):
        self.observations["protected_ranges"] = [{"start": 3.1, "end": 3.9,
                                                   "reason": "An independently reviewed result animation"}]
        result = self.plan()
        self.assertEqual(result["intervals"], [[99, 162], [186, 234], [279, 342]])
        for cut in result["checks"]["cuts"]:
            self.assertFalse(3.1 < cut["source_out"] < 3.9)
            self.assertFalse(3.1 < cut["next_source_in"] < 3.9)

    def test_insufficient_tail_fails_without_silent_truncation(self):
        with self.assertRaisesRegex(TIMELINE.SceneTimelineError, "не хватает записанного финала"):
            self.plan(duration=5.6)
        # A source tail that cannot supply a complete outward-rounded frame also fails.
        self.observations["steps"][1].update(read_seconds=.305)
        with self.assertRaisesRegex(TIMELINE.SceneTimelineError, "не хватает полного кадра"):
            self.plan(duration=5.705)

    def test_long_action_is_preserved_even_when_result_appears_before_release(self):
        self.events[0]["source_end"] = 3.4
        result = self.plan()
        self.assertEqual(result["intervals"][0], [99, 204])
        self.assertEqual(result["checks"]["steps"][0]["required_end"], 3.4)

    def test_zero_reading_time_keeps_each_completion_marker_frame(self):
        # At 5.4s the ready frame is 324; at 5.405s the first frame at/after
        # readiness is 325. Half-open cuts must keep those frames themselves.
        for field in ("transition_end", "result_ready", "overlays_clear"):
            for seconds, required_frame in ((5.4, 324), (5.405, 325)):
                with self.subTest(field=field, seconds=seconds):
                    self.observations["steps"][1].update(
                        transition_end=5.1, result_ready=5.1, overlays_clear=5.1,
                        read_seconds=0)
                    self.observations["steps"][1][field] = seconds
                    result = self.plan()
                    first, last = result["intervals"][-1]
                    self.assertLessEqual(first, required_frame)
                    self.assertEqual(last, required_frame + 1)

    def test_completion_at_capture_end_requires_an_actual_following_frame(self):
        for field in ("transition_end", "result_ready", "overlays_clear"):
            with self.subTest(field=field):
                self.observations["steps"][1].update(
                    transition_end=5.1, result_ready=5.1, overlays_clear=5.1,
                    read_seconds=0)
                self.observations["steps"][1][field] = 10
                with self.assertRaisesRegex(TIMELINE.SceneTimelineError, "не хватает полного кадра"):
                    self.plan(duration=10)

    def test_unreviewed_or_wrong_capture_observations_fail(self):
        for field, value in (("capture_sha256", "b" * 64),
                             ("review_status", "draft"), ("schema_version", True)):
            with self.subTest(field=field):
                original = self.observations[field]
                self.observations[field] = value
                with self.assertRaises(TIMELINE.SceneTimelineError):
                    self.plan()
                self.observations[field] = original

    def test_duplicate_unknown_missing_and_reordered_steps_fail(self):
        original_steps = copy.deepcopy(self.observations["steps"])
        variants = [original_steps + [original_steps[0]], original_steps[:1],
                    list(reversed(original_steps)),
                    [original_steps[0], {**original_steps[1], "event_id": "unknown"}]]
        for steps in variants:
            with self.subTest(steps=steps):
                self.observations["steps"] = steps
                with self.assertRaises(TIMELINE.SceneTimelineError):
                    self.plan()
        self.observations["steps"] = original_steps
        self.events[1]["id"] = "open"
        with self.assertRaisesRegex(TIMELINE.SceneTimelineError, "Повторяющийся id"):
            self.plan()

    def test_invalid_markers_and_event_order_fail(self):
        variants = [("response_start", 1.9), ("transition_end", 2.0),
                    ("result_ready", 11), ("overlays_clear", float("nan")),
                    ("read_seconds", -1), ("read_seconds", float("inf")),
                    ("response_start", True)]
        original = copy.deepcopy(self.observations["steps"][0])
        for field, value in variants:
            with self.subTest(field=field, value=value):
                self.observations["steps"][0] = {**original, field: value}
                with self.assertRaises(TIMELINE.SceneTimelineError):
                    self.plan()
        self.observations["steps"][0] = original
        self.events.reverse()
        with self.assertRaisesRegex(TIMELINE.SceneTimelineError, "должны идти по времени"):
            self.plan()

    def test_invalid_protected_ranges_fail(self):
        for entry in ({"start": 3, "end": 2, "reason": "reversed"},
                      {"start": 3, "end": 11, "reason": "outside recording"},
                      {"start": 3, "end": 4, "reason": " "},
                      {"start": float("nan"), "end": 4, "reason": "not finite"}):
            with self.subTest(entry=entry):
                self.observations["protected_ranges"] = [entry]
                with self.assertRaises(TIMELINE.SceneTimelineError):
                    self.plan()

    def test_zero_duration_action_has_a_frame_and_lead_never_starts_negative(self):
        events = {"events": [{"id": "tap", "source_start": 0, "source_end": 0}]}
        observations = {"schema_version": 1, "capture_sha256": SOURCE_HASH,
                        "review_status": "reviewed", "protected_ranges": [],
                        "steps": [{"event_id": "tap", "response_start": 0,
                                   "transition_end": 0, "result_ready": 0,
                                   "overlays_clear": 0, "read_seconds": 0}]}
        result = TIMELINE.plan_scene_intervals(events, observations, 1, SOURCE_HASH)
        self.assertEqual(result["intervals"], [[0, 1]])
        events["events"][0].update(source_start=1, source_end=1)
        observations["steps"][0].update(response_start=1, transition_end=1,
                                          result_ready=1, overlays_clear=1)
        with self.assertRaisesRegex(TIMELINE.SceneTimelineError, "не хватает полного кадра"):
            TIMELINE.plan_scene_intervals(events, observations, 1, SOURCE_HASH)


if __name__ == "__main__":
    unittest.main()
