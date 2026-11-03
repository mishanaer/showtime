"""Portable profile checks, using temporary output only; no simulator or encoder."""

import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import compose_pilot as compositor
import showtime_doctor as doctor
from PIL import Image

PROFILE = ROOT / "profiles/telegram-product.json"


class PortableProfileTests(unittest.TestCase):
    def test_relocated_profile_prepares_without_node_or_external_project_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "profiles").mkdir()
            copied = root / "profiles/telegram-product.json"
            shutil.copyfile(PROFILE, copied)
            shutil.copytree(ROOT / "assets/showtime", root / "assets/showtime")
            config = compositor.load_config(copied)
            self.assertEqual(Path(config["device"]["source"]), root / "assets/showtime/phones/iphone-17-pro-silver.png")
            self.assertNotIn("font", config)
            self.assertFalse(compositor.requires_node(config))
            output = root / "prepared"
            argv = ["compose_pilot.py", "--config", str(copied), "--work-dir", str(output), "prepare"]
            with patch.object(sys, "argv", argv), patch.object(compositor.shutil, "which", return_value=None), \
                    patch.object(compositor, "run", side_effect=AssertionError("prepare must not need a process")), \
                    contextlib.redirect_stdout(io.StringIO()):
                compositor.main()
            for filename in ("background.png", "device-layer.png", "technical-preview.png"):
                with Image.open(output / filename) as image:
                    self.assertEqual(image.size, (1920, 1920))
            geometry = json.loads((output / "geometry.json").read_text())
            self.assertGreater(geometry["screen"][3], 1920)

    def test_legacy_profile_resolution_and_geometry_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps({"font": "relative-font.otf", "device": {}, "layout": "legacy"}))
            self.assertEqual(compositor.load_config(path)["font"], "relative-font.otf")

    def test_svg_pattern_still_uses_legacy_sharp_route(self):
        config = {"canvas": {"width": 8, "height": 8}, "background": {
            "palette": ["#000000", "#111111", "#222222", "#333333"],
            "pattern": "original.svg", "pattern_opacity": 0.06, "sharp_module": "legacy-sharp"}}
        with tempfile.TemporaryDirectory() as directory:
            def rasterize(args):
                self.assertEqual(args[0], "node")
                self.assertEqual(args[3:5], ["legacy-sharp", "original.svg"])
                Image.new("RGBA", (8, 8), (0, 0, 0, 50)).save(args[5])
            with patch.object(compositor, "run", side_effect=rasterize) as run:
                self.assertEqual(compositor.mesh_background(config, Path(directory)).size, (8, 8))
            run.assert_called_once()

    def test_editorial_policy_blocks_caption_before_output_or_probe(self):
        config = {"policy": {"editorial_text": False}, "caption": {"text": "Later"}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "should-not-exist"
            with self.assertRaisesRegex(ValueError, "caption"):
                compositor.prepare(config, output)
            self.assertFalse(output.exists())
            with patch.object(compositor, "video_info") as probe, self.assertRaisesRegex(ValueError, "caption"):
                compositor.render(SimpleNamespace(), config, output)
            probe.assert_not_called()
        compositor.validate_editorial_policy({"caption": {"text": "Historical caption"}})

    def test_doctor_reports_optional_recording_dependencies_without_launching(self):
        which = lambda name: f"/tools/{name}" if name in ("ffmpeg", "ffprobe") else None
        with patch.object(doctor.shutil, "which", side_effect=which), \
                patch.object(compositor.subprocess, "run", side_effect=AssertionError("doctor must be read-only")):
            normal = doctor.diagnose(PROFILE)
            recording = doctor.diagnose(PROFILE, record=True)
        self.assertTrue(normal["ok"], normal["missing"])
        self.assertEqual(normal["missing"], [])
        self.assertEqual({entry["name"] for entry in recording["missing"]}, {"xcrun", "axe"})
        self.assertTrue(all(entry["action"] for entry in recording["missing"]))

    def test_doctor_reports_missing_tap_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            result = doctor.diagnose(PROFILE, tap_sequence=str(Path(directory) / "tap_%05d.png"))
        self.assertIn("tap_frames", {entry["name"] for entry in result["missing"]})


if __name__ == "__main__":
    unittest.main()
