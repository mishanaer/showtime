"""Contact/release timing and byte preservation for derived hold sequences."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gesture_timing.py"
SPEC = importlib.util.spec_from_file_location("gesture_timing_under_test", SCRIPT)
GESTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GESTURE)


class GestureFrameMapTests(unittest.TestCase):
    FRAME_COUNT = 41
    ANCHOR = 25

    def hold(self, seconds=1.0, provenance="observed", **extra):
        return {"type": "long_press", "hold_seconds": seconds,
                "hold_timing": provenance, **extra}

    def frame_map(self, event):
        return GESTURE.gesture_frame_map(event, self.FRAME_COUNT, self.ANCHOR, fps=60)

    def test_canonical_tap_is_unchanged_and_needs_no_hold_metadata(self):
        for event in ({}, {"type": "tap"},
                      {"type": "tap", "hold_seconds": float("nan"), "hold_timing": "unused"}):
            with self.subTest(event=event):
                frames, hold_frames = self.frame_map(event)
                self.assertEqual(frames, list(range(self.FRAME_COUNT)))
                self.assertEqual(hold_frames, 0)

    def test_release_begins_exactly_at_quantized_hold_duration_from_anchor(self):
        for seconds in (1 / 60, .5, .7, 1.0, 2.35, 60.0):
            with self.subTest(seconds=seconds):
                frames, hold_frames = self.frame_map(self.hold(seconds))
                duration_frames = round(seconds * 60)
                release_index = frames.index(self.ANCHOR + 1)
                self.assertEqual(release_index - self.ANCHOR, duration_frames)
                self.assertEqual(hold_frames, duration_frames)
                self.assertEqual(len(frames), self.FRAME_COUNT + duration_frames - 1)

    def test_entry_and_release_phases_keep_every_original_frame_in_order(self):
        frames, hold_frames = self.frame_map(self.hold(.7))
        self.assertEqual(frames[:self.ANCHOR], list(range(self.ANCHOR)))
        self.assertEqual(frames[self.ANCHOR:self.ANCHOR + hold_frames],
                         [self.ANCHOR] * hold_frames)
        self.assertEqual(frames[self.ANCHOR + hold_frames:],
                         list(range(self.ANCHOR + 1, self.FRAME_COUNT)))
        self.assertEqual(set(frames), set(range(self.FRAME_COUNT)))

    def test_one_frame_contact_keeps_original_sequence(self):
        frames, hold_frames = self.frame_map(self.hold(1 / 60))
        self.assertEqual(frames, list(range(self.FRAME_COUNT)))
        self.assertEqual(hold_frames, 1)

    def test_invalid_or_unrepresentable_hold_duration_fails(self):
        for value in (None, True, False, "1.0", float("nan"), float("inf"),
                      float("-inf"), 0, -1, 60.01, .001):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.frame_map(self.hold(value))

    def test_hold_requires_explicit_contact_timing_provenance(self):
        for provenance in (None, "", "command_duration", "guessed"):
            with self.subTest(provenance=provenance):
                with self.assertRaisesRegex(ValueError, "source of the contact"):
                    self.frame_map(self.hold(provenance=provenance))
        for provenance in ("hid_dispatch_estimate", "observed", "measured"):
            with self.subTest(provenance=provenance):
                frames, hold_frames = self.frame_map(self.hold(provenance=provenance))
                self.assertEqual(frames[self.ANCHOR], self.ANCHOR)
                self.assertEqual(hold_frames, 60)

    def test_source_recording_fps_does_not_change_final_60fps_contact(self):
        for source_fps in (24, 30, 59.94, 120):
            with self.subTest(source_fps=source_fps):
                frames, hold_frames = self.frame_map(self.hold(.7, source_fps=source_fps))
                self.assertEqual(hold_frames, 42)
                self.assertEqual(frames.index(self.ANCHOR + 1), self.ANCHOR + 42)


class DerivedSequenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.start_number = 7
        # Encoding is deliberately irrelevant: the helper must preserve opaque bytes.
        self.payloads = [b"\x89PNG\r\n\x1a\n" + bytes([index]) * 31 for index in range(6)]
        for index, payload in enumerate(self.payloads):
            self.source_path(index).write_bytes(payload)
        self.pattern = str(self.source / "native_%05d.png")
        self.output = self.root / "derived"
        self.frames = [0, 1, 2, 2, 2, 3, 4, 5]
        self.before_hashes = self.source_hashes()

    def source_path(self, index):
        return self.source / f"native_{self.start_number + index:05d}.png"

    def source_hashes(self):
        return [hashlib.sha256(self.source_path(index).read_bytes()).hexdigest()
                for index in range(len(self.payloads))]

    def assert_sequence_matches_source(self, pattern, frames):
        for output_index, source_index in enumerate(frames):
            self.assertEqual(Path(pattern % output_index).read_bytes(), self.payloads[source_index])
        self.assertEqual(self.source_hashes(), self.before_hashes)

    def test_hardlinked_sequence_keeps_source_bytes_and_repeats_anchor_only(self):
        pattern = GESTURE.write_derived_sequence(self.pattern, self.start_number,
                                                 self.frames, self.output)
        self.assertEqual(pattern, str(self.output / "tap_%05d.png"))
        self.assert_sequence_matches_source(pattern, self.frames)
        for output_index, source_index in enumerate(self.frames):
            self.assertEqual(Path(pattern % output_index).stat().st_ino,
                             self.source_path(source_index).stat().st_ino)

    def test_copy_fallback_keeps_identical_bytes_without_source_mutation(self):
        with mock.patch.object(GESTURE.os, "link", side_effect=OSError("cross-device link")):
            pattern = GESTURE.write_derived_sequence(self.pattern, self.start_number,
                                                     self.frames, self.output)
        self.assert_sequence_matches_source(pattern, self.frames)
        self.assertNotEqual(Path(pattern % 2).stat().st_ino, self.source_path(2).stat().st_ino)

    def test_regeneration_replaces_previous_links_without_modifying_originals(self):
        GESTURE.write_derived_sequence(self.pattern, self.start_number, self.frames, self.output)
        replacement = [0, 1, 2, 3, 4, 5]
        pattern = GESTURE.write_derived_sequence(self.pattern, self.start_number,
                                                 replacement, self.output)
        self.assert_sequence_matches_source(pattern, replacement)


if __name__ == "__main__":
    unittest.main()
