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
    def test_export_requires_explicit_events_even_for_a_scene_without_taps(self):
        with self.assertRaisesRegex(ValueError, "events"):
            compositor.load_events(None, (402, 874))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text('{"events": []}')
            self.assertEqual(compositor.load_events(path, (402, 874)), ([], (402, 874)))
            tap = {"id": "open", "type": "tap", "time": 1, "x": 200, "y": 250}
            path.write_text(json.dumps({"events": [tap]}))
            self.assertEqual(compositor.load_events(path, (402, 874)), ([tap], (402, 874)))
            for invalid in ({"coordinate_space": {"width": 402, "height": 874}},
                            {"events": None}, {"events": ""}, None):
                path.write_text(json.dumps(invalid))
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "events"):
                    compositor.load_events(path, (402, 874))

    def test_render_cli_without_events_stops_before_loading_assets(self):
        argv = ["compose_pilot.py", "render", "--capture", "take.mp4", "--output", "showtime.mp4"]
        with patch.object(sys, "argv", argv), patch.object(compositor, "load_config") as load, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            compositor.main()
        self.assertEqual(error.exception.code, 2)
        load.assert_not_called()

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
        # Both explicit and inferred cuts must meet the minimum and fit the source.
        for requested, source_duration in ((9, 25), (None, 9), (26, 25), (1e308, 25), (0, 12)):
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



class ExportProfileTests(unittest.TestCase):
    """A take is bound to its layout and phone model, not to colors."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.product = json.loads(compositor.DEFAULT_CONFIG.read_text())
        self.approved = self.write("approved.json", self.product)
        self.review = {"profile_sha256": compositor.sha256(self.approved),
                       "profile_bound_sha256": compositor.bound_profile_sha256(compositor.load_config(self.approved))}

    def write(self, name, profile):
        path = self.root / name
        profile = json.loads(json.dumps(profile))
        for parent, key in ((profile["device"], "source"), (profile["device"], "manifest"),
                            (profile["background"], "source"), (profile["tap"], "sequence")):
            parent[key] = str((compositor.DEFAULT_CONFIG.parent / parent[key]).resolve())
        path.write_text(json.dumps(profile))
        return path

    def variant(self, change):
        profile = json.loads(json.dumps(self.product))
        change(profile)
        return self.write("variant.json", profile)

    def test_unbound_capture_and_the_approved_profile_pass(self):
        self.assertIsNone(compositor.check_export_profile({}, self.approved))
        self.assertEqual(compositor.check_export_profile(self.review, self.approved), {"profile": "approved"})

    def test_background_tap_color_and_phone_color_may_change(self):
        def recolor(profile):
            profile["background"].update(id="sage", palette=["#dbddbb", "#6ba587", "#d5d88d", "#88b884"])
            profile["tap"]["tint_rgb"] = [255, 255, 255]
            profile["device"].update(id="iphone-17-pro-deep-blue", color="Deep Blue",
                                     source="../assets/showtime/phones/iphone-17-pro-deep-blue.png")
        result = compositor.check_export_profile(self.review, self.variant(recolor))
        self.assertEqual(result["bound_fields"], "unchanged")
        self.assertEqual(result["approved_profile_sha256"], self.review["profile_sha256"])

    def test_layout_or_phone_model_change_needs_a_new_take(self):
        def pro_max(profile):
            profile["device"].update(model="iPhone 17 Pro Max", source_size=[1470, 3000],
                                     screen_insets=[67, 76, 67, 76])
        for path, layout in ((self.approved, "full"), (self.variant(pro_max), None)):
            with self.subTest(layout=layout), self.assertRaisesRegex(ValueError, "record a new take"):
                compositor.check_export_profile(self.review, path, layout)

    def test_ring_timing_size_captions_and_phone_position_are_bound(self):
        changes = {
            "anchor": lambda profile: profile["tap"].update(anchor_frame=20),
            "ring size": lambda profile: profile["tap"].update(maximum_diameter_fraction=0.3),
            "caption": lambda profile: profile.update(caption={"text": "New"}),
            "corner radius": lambda profile: profile["device"].update(screen_radius_fraction=[0.05, 0.03]),
            "phone position": lambda profile: profile["layouts"]["full-device"].update(left=100),
        }
        for name, change in changes.items():
            layout = "full-device" if name == "phone position" else None
            with self.subTest(change=name), self.assertRaisesRegex(ValueError, "record a new take"):
                compositor.check_export_profile(self.review, self.variant(change), layout)

    def test_take_without_recorded_framing_stays_strict(self):
        review = {"profile_sha256": self.review["profile_sha256"]}
        changed = self.variant(lambda profile: profile["tap"].update(tint_rgb=[255, 255, 255]))
        with self.assertRaisesRegex(ValueError, "predates export-only profile changes"):
            compositor.check_export_profile(review, changed)


class CaptureLengthTests(unittest.TestCase):
    def test_capture_length_is_the_picture_not_a_longer_audio_track(self):
        import shutil, subprocess
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg required")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=60:duration=1",
                            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2", "-map", "0:v", "-map", "1:a",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
            self.assertAlmostEqual(compositor.video_info(path)[2], 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
