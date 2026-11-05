"""Decoded cadence checks for final exports."""

import importlib.util
import io
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render_quality.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("render_quality_under_test", SCRIPT)
QUALITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUALITY)


def probe_fixture(fps=60, count=60, offset=0):
    # Integer ticks avoid the test itself introducing rounded PTS.
    base = 60000
    ticks = 1001 if fps == "59.94" else base//fps
    rate = "60000/1001" if fps == "59.94" else str(fps)+"/1"
    return {
        "streams": [{"width": 1920, "height": 1920, "time_base": "1/60000", "avg_frame_rate": rate, "r_frame_rate": rate,
                     "duration_ts": ticks*count, "nb_frames": str(count)}],
        "frames": [{"best_effort_timestamp": offset+i*ticks, "duration": ticks} for i in range(count)],
    }


class RenderQualityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/"arbitrary-final-name.mov"
        self.path.write_bytes(b"test output identity")

    def audit(self, data, require=False, delivery=False):
        completed = subprocess.CompletedProcess([], 0, json.dumps(data), "")
        with mock.patch.object(QUALITY.subprocess, "run", return_value=completed) as call:
            method = QUALITY.audit_delivery if delivery else QUALITY.require_render_60fps if require else QUALITY.audit_render
            result = method(self.path)
        command = call.call_args.args[0]
        self.assertEqual(command[0], "ffprobe")
        self.assertNotIn("-vf", command)
        return result

    def test_cfr60_passes_with_nonzero_start_and_motion_stays_unverified(self):
        result = self.audit(probe_fixture(offset=123456), require=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["render_cadence_60fps"], "passed")
        self.assertEqual(result["motion_fidelity"], "unverified")
        self.assertEqual(result["scope"], "final_render_only")
        self.assertEqual(result["output"], str(self.path.resolve()))
        self.assertEqual(len(result["output_sha256"]), 64)

    def test_30_and_5994_fail_even_when_nominal_is_changed_to_60(self):
        for fps in (30, "59.94"):
            with self.subTest(fps=fps):
                data = probe_fixture(fps=fps)
                data["streams"][0]["r_frame_rate"] = "60/1"
                self.assertFalse(self.audit(data)["passed"])

    def test_declared_60_with_gap_fails_and_reports_actual_gap(self):
        data = probe_fixture()
        for frame in data["frames"][20:]:
            frame["best_effort_timestamp"] += 1000
        result = self.audit(data)
        self.assertFalse(result["passed"])
        self.assertEqual(result["timing"]["gap_count"], 1)
        self.assertAlmostEqual(result["timing"]["gap_examples"][0]["interval_ms"], 1000/30)

    def test_missing_nonfinite_and_nonmonotonic_pts_fail_closed(self):
        for kind in ("missing", "nan", "backwards", "duplicate"):
            with self.subTest(kind=kind):
                data = probe_fixture()
                if kind == "missing":
                    del data["frames"][20]["best_effort_timestamp"]
                elif kind == "nan":
                    data["frames"][20]["best_effort_timestamp"] = "NaN"
                elif kind == "backwards":
                    data["frames"][20]["best_effort_timestamp"] = 18000
                else:
                    data["frames"][20]["best_effort_timestamp"] = 19000
                self.assertFalse(self.audit(data)["passed"])

    def test_missing_durations_and_long_final_frame_fail(self):
        for kind in ("stream", "frame", "long-tail"):
            with self.subTest(kind=kind):
                data = probe_fixture()
                if kind == "stream":
                    del data["streams"][0]["duration_ts"]
                elif kind == "frame":
                    del data["frames"][-1]["duration"]
                else:
                    data["frames"][-1]["duration"] = 60000
                self.assertFalse(self.audit(data)["passed"])

    def test_one_frame_and_no_video_fail(self):
        self.assertFalse(self.audit(probe_fixture(count=1))["passed"])
        self.assertFalse(self.audit({"streams": [], "frames": []})["passed"])

    def test_report_cannot_overwrite_input_or_its_alias(self):
        alias = self.path.with_suffix(".json")
        os.link(self.path, alias)
        before = self.path.read_bytes()
        for report in (self.path, alias):
            with mock.patch.object(sys, "argv", ["check", str(self.path), "--report", str(report)]), \
                 mock.patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
                QUALITY.main()
            self.assertEqual(self.path.read_bytes(), before)

    def test_product_requires_square_master(self):
        data = probe_fixture(count=600)
        data["streams"][0]["width"] = 64
        report = self.audit(data, delivery=True)
        self.assertFalse(report["passed"])
        self.assertEqual(report["failures"][0]["code"], "delivery_dimensions")

    def test_require_raises_with_serializable_report(self):
        with self.assertRaises(QUALITY.RenderQualityError) as context:
            self.audit(probe_fixture(fps=30), require=True)
        self.assertFalse(context.exception.report["passed"])
        json.dumps(context.exception.report, allow_nan=False)

    def test_output_check_does_not_infer_source_cadence_from_sidecar(self):
        self.path.with_suffix(".clock.json").write_text('{"recorder":"simctl","capture_fps":"VFR"}')
        report = self.audit(probe_fixture())
        self.assertTrue(report["passed"])
        self.assertNotIn("recording_sidecar", report)
        self.assertTrue(report["policy"]["input_capture_may_be_vfr"])
        self.assertIsNone(report["policy"]["input_capture_fps_requirement"])
        self.assertTrue(report["policy"]["repeated_output_frames_allowed"])

    def test_ffprobe_failure_is_a_failed_report(self):
        with mock.patch.object(QUALITY.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["ffprobe"])):
            report = QUALITY.audit_render(self.path)
        self.assertFalse(report["passed"])
        self.assertEqual(report["failures"][0]["code"], "render_audit_error")

    def test_delivery_boundaries_use_decoded_frames_and_keep_cadence_result(self):
        for count, expected in ((599, False), (600, True), (900, True), (960, True),
                                (1200, True), (1201, True), (3600, True)):
            with self.subTest(count=count):
                report = self.audit(probe_fixture(count=count), delivery=True)
                self.assertEqual(report["passed"], expected)
                self.assertIsNone(report["delivery_policy"]["maximum_seconds"])
                self.assertIsNone(report["delivery_policy"]["maximum_decoded_frames"])
                self.assertEqual(report["render_cadence_60fps"], "passed")
                self.assertEqual(report["delivery_duration"]["status"], "passed" if expected else "failed")
                self.assertEqual(report["delivery_duration"]["seconds_at_verified_60fps"], count/60)
                if not expected:
                    self.assertEqual([item["code"] for item in report["failures"]], ["delivery_frame_count_out_of_range"])

    def test_delivery_cannot_turn_600_frames_at_30fps_into_ten_seconds(self):
        report = self.audit(probe_fixture(fps=30, count=600), delivery=True)
        self.assertFalse(report["passed"])
        self.assertEqual(report["render_cadence_60fps"], "failed")
        self.assertTrue(report["delivery_duration"]["frame_count_in_range"])
        self.assertEqual(report["delivery_duration"]["status"], "unverified")
        self.assertIsNone(report["delivery_duration"]["seconds_at_verified_60fps"])

    def test_delivery_missing_video_does_not_claim_duration_or_cadence(self):
        report = self.audit({"streams": [], "frames": []}, delivery=True)
        self.assertFalse(report["passed"])
        self.assertEqual(report["render_cadence_60fps"], "failed")
        self.assertEqual(report["delivery_duration"]["status"], "failed")
        self.assertIsNone(report["delivery_duration"]["seconds_at_verified_60fps"])

    def test_cadence_api_still_accepts_short_and_long_technical_outputs(self):
        for count in (120, 1200):
            with self.subTest(count=count):
                report = self.audit(probe_fixture(count=count), require=True)
                self.assertTrue(report["passed"])
                self.assertNotIn("delivery_policy", report)

    def test_delivery_preflight_quantizes_without_clamp_or_stretch(self):
        for duration, expected in ((10, 600), (12.5, 750), (15, 900), (16, 960), (20, 1200),
                                   (1201/60, 1201), (60, 3600), (10.004, 600), (14.99, 899)):
            with self.subTest(duration=duration):
                self.assertEqual(QUALITY.delivery_frame_count(duration), expected)
        for duration in (599/60, 2):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(ValueError, "at least 10 seconds"):
                    QUALITY.delivery_frame_count(duration)

    def test_delivery_preflight_rejects_invalid_durations(self):
        for duration in (None, True, False, "12", 0, -1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    QUALITY.delivery_frame_count(duration)

    def test_cli_defaults_to_delivery_and_cadence_only_is_explicit(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps(probe_fixture(count=120)), "")
        for extra, expected_exit, expected_pass in (([], 2, False), (["--cadence-only"], 0, True)):
            with self.subTest(extra=extra):
                stdout = io.StringIO()
                with mock.patch.object(sys, "argv", [str(SCRIPT), str(self.path), *extra]), \
                     mock.patch.object(sys, "stdout", stdout), \
                     mock.patch.object(QUALITY.subprocess, "run", return_value=completed):
                    self.assertEqual(QUALITY.main(), expected_exit)
                report = json.loads(stdout.getvalue())
                self.assertEqual(report["passed"], expected_pass)
                self.assertEqual(report["render_cadence_60fps"], "passed")

    def test_error_message_does_not_mislabel_duration_as_fps_failure(self):
        report = self.audit(probe_fixture(count=599), delivery=True)
        error = QUALITY.RenderQualityError(report)
        self.assertIn("at least 600 frames", str(error))
        self.assertNotIn("failed 60 fps validation", str(error))


if __name__ == "__main__":
    unittest.main()
