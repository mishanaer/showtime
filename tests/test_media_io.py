"""Frame-time checks for simulator captures before normalization."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("media_io_under_test", SCRIPTS / "media_io.py")
MEDIA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MEDIA)


class FrameTimeTests(unittest.TestCase):
    def check(self, rows):
        with mock.patch.object(MEDIA.subprocess, "check_output", return_value="\n".join(rows) + "\n") as call:
            result = MEDIA.check_frame_times("take.mp4")
        return result, call.call_args.args[0]

    def test_presentation_times_pass(self):
        result, _ = self.check(["0.000000", "1.675000,", "1.675000"])
        self.assertEqual(result["frames"], 3)

    def test_backwards_or_missing_times_fail(self):
        with self.assertRaisesRegex(ValueError, "go backwards"):
            self.check(["2.000000", "1.000000"])
        with self.assertRaisesRegex(ValueError, "no presentation time"):
            self.check(["N/A"])
        with self.assertRaisesRegex(ValueError, "no video frames"):
            self.check([])

    def test_normalization_decodes_by_presentation_time(self):
        self.assertEqual(MEDIA.DECODE_BY_PTS, ["-fflags", "+igndts"])
        editor = (SCRIPTS / "edit_events.py").read_text()
        self.assertIn('"-y", *DECODE_BY_PTS, "-i", source', editor)


if __name__ == "__main__":
    unittest.main()
