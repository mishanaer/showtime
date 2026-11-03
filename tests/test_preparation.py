"""Preparation completeness uses synthetic files, not real app recordings."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_preparation as PREPARATION


SCRIPT = Path(PREPARATION.__file__)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "prepared-content.json"
        # Only integrity and declared media types are tested, not media decoding.
        self.video = self.file("transition.mp4", b"synthetic video evidence")
        self.frame = self.file("frame.png", b"synthetic image evidence")
        checks = {name: {"status": "ready", "evidence": [deepcopy(self.frame)],
                         "note": "Synthetic preparation declaration."} for name in PREPARATION.CHECKS}
        for name in ("full_height", "character_typing"):
            checks[name]["evidence"] = [deepcopy(self.video)]
        checks["character_typing"]["visible_keypresses"] = False
        self.manifest = {
            "schema_version": 2, "kind": "prepared-content",
            "content": {"preparation": {
                "viewport": {"width": 402, "height": 874}, "visible_height": 610,
                "keyboard_visible": True, "typing_visible": True, "checks": checks,
            }},
            "files": [deepcopy(self.frame), deepcopy(self.video)],
        }
        self.preparation = self.manifest["content"]["preparation"]
        self.geo = self.file("geometry.json", b'{"device":[310,90,1300,2658],"screen":[380,157,1159,2523]}')
        self.manifest["files"].append(self.geo)
        self.preparation["framing"] = [{"layout": "crop-top", "device_id": "iphone-17-pro-silver",
                                        "geometry": self.geo}]


    def file(self, name, content):
        (self.root / name).write_bytes(content)
        return {"path": name, "sha256": hashlib.sha256(content).hexdigest()}

    def write(self):
        self.path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def check(self):
        self.write()
        return PREPARATION.check_preparation(self.path)

    def test_preparation_passes_before_review_and_cli_is_read_only(self):
        self.write()
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.root.iterdir()}
        result = subprocess.run([sys.executable, str(SCRIPT), "--manifest", str(self.path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["preparation_complete"])
        self.assertFalse(report["visual_quality_automatically_verified"])
        self.assertEqual(report["evidence_files_checked"], 2)
        self.assertNotIn("story_review", self.manifest["content"])
        after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.root.iterdir()}
        self.assertEqual(before, after)

    def test_ready_requires_evidence_and_motion_cannot_be_a_static_frame(self):
        original = deepcopy(self.preparation["checks"])
        for name in PREPARATION.CHECKS:
            self.preparation["checks"] = deepcopy(original)
            self.preparation["checks"][name]["evidence"] = []
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "evidence"):
                self.check()
        for name in ("full_height", "character_typing"):
            self.preparation["checks"] = deepcopy(original)
            self.preparation["checks"][name]["evidence"] = [deepcopy(self.frame)]
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "mp4"):
                self.check()

    def test_typing_requires_explicit_absence_of_visible_keypresses(self):
        check = self.preparation["checks"]["character_typing"]
        for value in (True, 0, "false", None):
            check["visible_keypresses"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "visible_keypresses"):
                self.check()
        del check["visible_keypresses"]
        with self.assertRaisesRegex(ValueError, "visible_keypresses"):
            self.check()

    def test_small_phone_with_canonical_layout_label_is_rejected(self):
        wrong = self.file("small.json", b'{"device":[488,18,944,1930],"screen":[539,67,842,1832]}')
        self.manifest["files"].append(wrong)
        self.preparation["framing"][0]["geometry"] = wrong
        with self.assertRaisesRegex(ValueError, "geometry"):
            self.check()

    def test_changed_file_bytes_are_rejected(self):
        (self.root / self.video["path"]).write_bytes(b"changed after preparation")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.check()

    def test_every_check_is_required(self):
        original = deepcopy(self.preparation["checks"])
        for name in PREPARATION.CHECKS:
            self.preparation["checks"] = deepcopy(original)
            del self.preparation["checks"][name]
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "five"):
                self.check()

    def test_not_applicable_is_allowed_only_for_absent_keyboard_or_typing(self):
        for field in ("keyboard_visible", "typing_visible"):
            self.preparation[field] = False
        for name in ("keyboard_suggestions", "character_typing"):
            self.preparation["checks"][name].update(status="not_applicable", evidence=[])
        self.assertTrue(self.check()["preparation_complete"])
        for name, field in (("keyboard_suggestions", "keyboard_visible"),
                            ("character_typing", "typing_visible")):
            self.preparation[field] = True
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "not_applicable"):
                self.check()
            self.preparation[field] = False
        self.preparation["checks"]["full_height"].update(status="not_applicable", evidence=[])
        with self.assertRaisesRegex(ValueError, "not_applicable"):
            self.check()

    def test_typing_without_visible_keyboard_is_supported(self):
        self.preparation["keyboard_visible"] = False
        self.preparation["checks"]["keyboard_suggestions"].update(status="not_applicable", evidence=[])
        self.assertTrue(self.check()["preparation_complete"])

    def test_undeclared_or_differently_hashed_evidence_is_rejected(self):
        original = deepcopy(self.manifest["files"])
        self.manifest["files"] = [deepcopy(self.frame)]
        with self.assertRaisesRegex(ValueError, "files"):
            self.check()
        self.manifest["files"] = original
        self.manifest["files"][1]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.check()

    def test_invalid_dimensions_and_nonboolean_flags_fail(self):
        for value in (0, -1, True, "874", 10 ** 400):
            with self.subTest(value=value):
                self.preparation["viewport"]["height"] = value
                with self.assertRaisesRegex(ValueError, "height"):
                    self.check()
        self.preparation["viewport"]["height"] = 874
        self.preparation["visible_height"] = 875
        with self.assertRaisesRegex(ValueError, "visible_height"):
            self.check()
        self.preparation["visible_height"] = 874
        self.assertTrue(self.check()["preparation_complete"])
        self.preparation["keyboard_visible"] = 1
        with self.assertRaisesRegex(ValueError, "keyboard_visible"):
            self.check()

    def test_cli_reports_missing_manifest_without_creating_it(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--manifest", str(self.path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stderr)["preparation_complete"])
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
