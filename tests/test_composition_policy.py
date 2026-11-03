import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compose_pilot as compositor
import record_simulator


class CompositionPolicyTests(unittest.TestCase):
    def test_failed_report_preserves_old_video_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stage").mkdir()
            candidate = root / "stage/new.mp4"
            candidate.write_bytes(b"new")
            output = root / "showtime.mp4"
            output.write_bytes(b"old")
            quality = output.with_suffix(".quality.json")
            quality.write_text("old report")
            with self.assertRaises(TypeError):
                compositor.publish_video(candidate, output, {".quality.json": {}, ".render.json": object()})
            self.assertEqual(output.read_bytes(), b"old")
            self.assertEqual(quality.read_text(), "old report")

    def test_invalid_delivery_duration_stops_before_stage_or_encoder(self):
        # Neither an explicit long cut nor the default full source may bypass
        # the product limit. Zero must not silently select the source duration.
        for requested, source_duration in ((9, 25), (21, 25), (None, 25), (0, 12)):
            with self.subTest(requested=requested, source_duration=source_duration):
                args = SimpleNamespace(capture="unused.mp4", duration=requested, capture_start=0)
                with patch.object(compositor, "video_info", return_value=(402, 874, source_duration)), \
                        patch.object(compositor, "prepare") as prepare, \
                        patch.object(compositor, "run") as encode:
                    with self.assertRaises(ValueError):
                        compositor.render(args, {"canvas": {"fps": 60}}, Path("unused-stage"))
                    prepare.assert_not_called()
                    encode.assert_not_called()

    def test_telegram_crop_rejects_a_bottom_action(self):
        config = compositor.use_layout(json.loads(compositor.DEFAULT_CONFIG.read_text()), None)
        self.assertEqual(config["layout"], "crop-top")
        screen = compositor.geometry(config)["screen"]
        sw, sh = screen[2:]
        # Bottom composer buttons from a normal 402x874 capture must not vanish.
        with self.assertRaisesRegex(ValueError, "hides action send"):
            compositor.validate_visible_targets(
                [{"id": "send", "x": 360, "y": 840}], (402, 874),
                (sw, sh), (0, 0), screen, (1920, 1920))

    def test_product_crops_share_scale_and_mirror_edge_clearance(self):
        profile = compositor.ROOT / "profiles/telegram-product.json"
        top = compositor.geometry(compositor.load_config(profile, "crop-top"))
        bottom = compositor.geometry(compositor.load_config(profile, "crop-bottom"))
        self.assertEqual(top["device"], [310, 90, 1300, 2658])
        self.assertEqual(bottom["device"], [310, -828, 1300, 2658])
        self.assertEqual(top["screen"][2:], bottom["screen"][2:])
        screen = bottom["screen"]
        compositor.validate_visible_targets([{"id": "send", "x": 360, "y": 840}],
            (402, 874), screen[2:], (0, 0), screen, (1920, 1920))

    def test_full_device_keeps_the_same_bottom_action(self):
        config = compositor.use_layout(json.loads(compositor.DEFAULT_CONFIG.read_text()), "full-device")
        screen = compositor.geometry(config)["screen"]
        compositor.validate_visible_targets(
            [{"id": "send", "x": 360, "y": 840}], (402, 874),
            screen[2:], (0, 0), screen, (1920, 1920))

    def test_derived_tint_keeps_source_color_and_supports_event_override(self):
        tap = {"monochrome_rgb": [49, 123, 255], "tint_rgb": [139, 167, 211]}
        self.assertEqual(compositor.tap_tint(tap, {}), [139, 167, 211])
        self.assertEqual(compositor.tap_tint(tap, {"tint_rgb": [200, 100, 100]}), [200, 100, 100])
        self.assertEqual(tap["monochrome_rgb"], [49, 123, 255])

    def test_historical_profile_keeps_canonical_color(self):
        self.assertEqual(compositor.tap_tint({"monochrome_rgb": [49, 123, 255]}, {}), [49, 123, 255])

    def test_invalid_and_unverified_tint_rejected(self):
        with self.assertRaises(ValueError):
            compositor.tap_tint({"monochrome_rgb": [49, 123, 255]}, {"tint_rgb": [256, 1, 1]})
        with self.assertRaises(ValueError):
            compositor.tap_tint({}, {"tint_rgb": [10, 20, 30]})

    def test_unapproved_recording_never_starts_simulator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv = ["record_simulator.py", "--simulator", "test-device", "--output", str(root / "capture.mp4"),
                    "--scenario", str(root / "missing-scenario.json"),
                    "--prepared-content", str(root / "missing-content.json"),
                    "--approval-dir", str(root / "reviews")]
            with patch.object(sys, "argv", argv), patch.object(record_simulator.subprocess, "Popen") as launch:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    record_simulator.main()
                self.assertEqual(error.exception.code, 2)
                launch.assert_not_called()
                self.assertFalse((root / "capture.clock.json").exists())


if __name__ == "__main__":
    unittest.main()
