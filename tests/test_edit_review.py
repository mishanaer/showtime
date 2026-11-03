"""Review packets preserve exact edit evidence; synthetic video only."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image


SPEC = importlib.util.spec_from_file_location(
    "edit_review_under_test", Path(__file__).resolve().parents[1] / "scripts/edit_review.py")
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


class EditReviewTests(unittest.TestCase):
    def test_cut_and_loop_pairs_are_complete_across_pages(self):
        intervals = [[2 * i, 2 * i + 1] for i in range(30)]
        groups = REVIEW._plan_evidence(intervals, {"schema_version": 1, "steps": []}, 60, 60)
        pages = REVIEW._pages(groups)
        self.assertEqual([len(page) for page in pages], [80, 41])
        self.assertEqual(sum(map(len, pages)), sum(map(len, groups)))
        for page in pages:
            for index, item in enumerate(page):
                if item["kind"] == "cut_left":
                    self.assertEqual(page[index + 1]["kind"], "cut_right")
                    self.assertEqual(page[index + 1]["cut_index"], item["cut_index"])
                if item["kind"] == "loop_end":
                    self.assertEqual(item["source_frame"], 58)
                    self.assertEqual(page[index + 1]["kind"], "loop_start")
                    self.assertEqual(page[index + 1]["source_frame"], 0)

    def test_invalid_intervals_and_unlocatable_observations_fail(self):
        obs = {"schema_version": 1, "steps": []}
        for intervals in ([], [[0, 0]], [[0, 5], [4, 6]], [[False, 3]], [[0, 11]]):
            with self.subTest(intervals=intervals), self.assertRaises(ValueError):
                REVIEW._plan_evidence(intervals, obs, 10, 60)
        for timestamp in (-.1, float("nan"), 10 / 60, {"frame": 1}):
            invalid = {"schema_version": 1, "steps": [
                {"event_id": "send", "result_ready": timestamp, "overlays_clear": 0}]}
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                REVIEW._plan_evidence([[0, 10]], invalid, 10, 60)

    def test_original_vfr_or_shifted_pts_cannot_be_mistaken_for_edit_grid(self):
        REVIEW._validate_grid([0, .016667, .033333], 60)
        for timestamps in ([0, 1 / 60, .1], [0, 1 / 30, 2 / 30], [.1, .1 + 1 / 60]):
            with self.subTest(timestamps=timestamps), self.assertRaises(ValueError):
                REVIEW._validate_grid(timestamps, 60)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_packet_extracts_actual_frames_and_keeps_visual_review_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            colors = [(20 + i * 35, 50, 220 - i * 25) for i in range(6)]
            for index, color in enumerate(colors):
                Image.new("RGB", (64, 96), color).save(root / f"{index:03d}.png")
            source = root / "normalized.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-framerate", "60", "-i",
                            str(root / "%03d.png"), "-c:v", "libx264", "-crf", "10",
                            "-pix_fmt", "yuv420p", str(source)], check=True)
            observations = {"schema_version": 1, "capture_sha256": "f" * 64,
                            "review_status": "reviewed", "steps": [{"event_id": "send",
                            "result_ready": .02, "overlays_clear": 4 / 60}]}
            packet = REVIEW.build_edit_review(source, [[1, 3], [4, 6]], observations, root / "review")
            self.assertEqual(packet["status"], "needs_visual_review")
            self.assertNotIn("visual_passed", packet)
            self.assertEqual(packet["observations_capture_sha256"], "f" * 64)
            self.assertNotEqual(packet["capture_sha256"], "f" * 64)
            self.assertEqual(packet["evidence_count"], 11)
            self.assertEqual(packet["loop_seam"]["end_frame"], 5)
            self.assertEqual(packet["loop_seam"]["start_frame"], 1)
            ready = next(item for item in packet["evidence"] if item["kind"] == "result_ready")
            self.assertEqual(ready["source_frame"], 2)  # at/after readiness, not before
            for item in packet["evidence"]:
                index = item["source_frame"]
                with Image.open(item["image"]) as image:
                    self.assertLess(max(abs(a - b) for a, b in zip(image.getpixel((32, 48)), colors[index])), 8)
                self.assertAlmostEqual(item["source_seconds"], index / 60, places=5)
            with Image.open(packet["contact_sheets"][0]) as sheet:
                self.assertLessEqual(sheet.width, 1600)
            self.assertTrue(Path(packet["report"]).is_file())


if __name__ == "__main__":
    unittest.main()
