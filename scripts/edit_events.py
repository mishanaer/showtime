#!/usr/bin/env python3
"""Edit reviewed action outcomes at native speed, delivering constant 60 fps.

Sample the full capture at 60 fps before frame-indexed cuts.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import subprocess

from media_io import probe, run, save_json, sha256, production_review, review_for_clock
from gesture_timing import gesture_frame_map
from scene_timeline import plan_scene_intervals
from native_action import capture_reference

FPS = 60


def validate_capture_clock(raw, capture=None):
    clock = raw.get("capture_clock")
    reference = raw.get("capture_ref")
    if "capture_ref" in raw:
        if not isinstance(reference, dict) or not isinstance(reference.get("clock_path"), str):
            raise ValueError("Invalid capture_ref in action journal")
        path = Path(reference["clock_path"])
        if not path.is_absolute():
            raise ValueError("capture_ref clock_path must be absolute")
        try:
            clock = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise ValueError("Cannot read recording clock: " + str(path)) from error
        if not isinstance(clock, dict) or capture_reference(path, clock) != reference:
            raise ValueError("Action journal belongs to another recording clock")
        if capture is None or Path(reference["output"]).resolve() != Path(capture).resolve():
            raise ValueError("Action journal recording does not match --capture")
    if clock is None:
        return None
    if not isinstance(clock, dict):
        raise ValueError("Invalid recording clock")
    if clock.get("ready_ms") is None or clock.get("exit_code") != 0:
        raise ValueError("Recording clock has no successful ready/finalize state")
    review_for_clock(clock)
    drift = clock.get("wall_clock_drift_seconds")
    if type(drift) not in (int, float) or not math.isfinite(drift) or abs(drift) > .05:
        raise ValueError("Capture wall clock drift exceeds 50 ms or was not checked")
    return {"wall_clock_drift_seconds": drift, "physical_touch_down_measured": False,
            "capture_bound": reference is not None,
            "legacy_unbound_event_count": raw.get("legacy_unbound_event_count", 0)}


def derived_clock(take_review, edit_review, source_hash):
    """Clock for the edited capture: carry the take's review forward unchanged.

    Export checks human_review (approval, checked materials, profile hash), so the
    edit packet must sit beside it, never replace it. An imported capture has no
    take review and stays imported.
    """
    clock = {"human_review": take_review, "edit_review": edit_review,
             "exit_code": 0, "source_sha256": source_hash}
    if take_review:
        clock["purpose"] = "final"
    return clock


def visual_gesture(event):
    """Translate recorded contact into visual timing; never infer hold from CLI duration."""
    result = {"id": event["id"], "type": event.get("type", "tap"),
              "source_time": event["source_start"], "x": event["point"][0], "y": event["point"][1],
              "anchor_frame": 25, "command_duration": event["source_end"] - event["source_start"],
              "alignment": event.get("alignment", "recorded_command_start; physical_touch_not_measured")}
    if "tint_rgb" in event:
        result["tint_rgb"] = event["tint_rgb"]
    if result["type"] == "long_press":
        start, end = event.get("contact_start"), event.get("contact_end")
        if any(type(value) not in (float, int) or not math.isfinite(value) for value in (start, end)):
            raise ValueError(f"{event['id']}: a hold needs contact_start and contact_end from the down/up log or a reviewed recording")
        if not event["source_start"] <= start < end <= event["source_end"]:
            raise ValueError(f"{event['id']}: the contact interval must fall within the action")
        result.update(source_time=start, hold_seconds=end-start, hold_timing=event.get("contact_timing"))
        gesture_frame_map(result, 41, 25, FPS)
    return result


def plan_edit(raw, observations, duration, source_sha256):
    # Validate original evidence before extending ranges for the visual asset.
    plan_scene_intervals(raw, observations, duration, source_sha256, fps=FPS)
    gestures = [visual_gesture(event) for event in raw["events"]
                if event.get("type", "tap") in ("tap", "long_press") and "point" in event]
    protected = copy.deepcopy(observations)
    for gesture in gestures:
        frame_map, _ = gesture_frame_map(gesture, 41, 25, FPS)
        anchor = round(gesture["source_time"] * FPS)
        if anchor < 25:
            raise ValueError(f"{gesture['id']}: keep at least 25 frames before contact so the ring can appear")
        first, last = anchor-25, anchor-25+len(frame_map)
        protected["protected_ranges"].append({"start": first/FPS, "end": last/FPS,
                                              "reason": "Complete gesture: " + gesture["id"]})
    plan = plan_scene_intervals(raw, protected, duration, source_sha256, fps=FPS, lead_seconds=25/FPS)
    plan["gestures"] = gestures
    return plan


def remap_gestures(gestures, intervals):
    mapped, cursor = [], 0
    for first, last in intervals:
        for gesture in gestures:
            source_frame = round(gesture["source_time"]*FPS)
            if first <= source_frame < last:
                event = dict(gesture)
                event.pop("source_time")
                frame = cursor + source_frame-first
                event.update(frame=frame, time=frame/FPS)
                mapped.append(event)
        cursor += last-first
    if len(mapped) != len(gestures):
        raise ValueError("The edit lost or duplicated a gesture")
    return mapped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True,
                        help="Reviewed times for the response, visible result, and disappearance of overlays")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.capture.resolve()
    raw = json.loads(args.events.read_text())
    observations = json.loads(args.observations.read_text())
    clock_validation = validate_capture_clock(raw, source)
    take_review = production_review(source)
    metadata = probe(source)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    duration = float(video.get("duration", metadata["format"]["duration"]))
    source_hash = sha256(source)
    plan = plan_edit(raw, observations, duration, source_hash)
    intervals = plan["intervals"]
    output = args.output_dir.resolve()
    work = output / "event-edit"
    work.mkdir(parents=True, exist_ok=True)
    # Sample before trimming so a held frame remains visible until the next update.
    normalized = work / "capture-60fps.mp4"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", source,
         "-vf", "fps=60:start_time=0:round=near", "-an", "-c:v", "libx264", "-preset", "fast",
         "-crf", "17", "-threads", "4", "-pix_fmt", "yuv420p", normalized])
    normalized_probe = probe(normalized)
    normalized_video = next(s for s in normalized_probe["streams"] if s["codec_type"] == "video")
    if int(normalized_video["nb_frames"]) < intervals[-1][1]:
        raise ValueError("Record the full result and reading pause before editing")
    files, segments, cursor = [], [], 0
    for index, (first, last) in enumerate(intervals):
        filename = work / f"segment-{index:02d}.mp4"
        count = last-first
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", normalized,
             "-vf", f"trim=start_frame={first}:end_frame={last},setpts=PTS-STARTPTS", "-frames:v", count,
             "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-threads", "4",
             "-pix_fmt", "yuv420p", filename])
        encoded = next(s for s in probe(filename)["streams"] if s["codec_type"] == "video")
        if int(encoded["nb_frames"]) != count:
            raise ValueError("Encoded segment has an unexpected frame count")
        files.append(filename)
        segments.append({"source_in": first/FPS, "source_out": last/FPS, "output_in": cursor/FPS,
                         "output_frames": count, "speed": 1.0})
        cursor += count
    concat = work / "concat.txt"
    concat.write_text("".join("file '"+str(f).replace("'", "'\\''")+"'\n" for f in files))
    capture = output / "edited-capture.mp4"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat,
         "-c", "copy", "-movflags", "+faststart", capture])
    final_probe = probe(capture)
    encoded = next(s for s in final_probe["streams"] if s["codec_type"] == "video")
    if int(encoded["nb_frames"]) != cursor or encoded["avg_frame_rate"] != "60/1":
        raise ValueError("Final output failed frame-count/cadence validation")
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(capture), "-f", "null", "-"], check=True)
    from edit_review import build_edit_review
    review = build_edit_review(normalized, intervals, observations, output / "edit-review", fps=FPS)
    if review:
        save_json(capture.with_suffix(".clock.json"), derived_clock(take_review, review, source_hash))
    save_json(output/"events.json", {"coordinate_space": raw["coordinate_space"], "fps": FPS, "duration": cursor/FPS,
                                   "timing_note": raw.get("clock_note", "Command timing; physical touch-down is not measured."),
                                   "events": remap_gestures(plan["gestures"], intervals)})
    save_json(output/"edit-timeline.json", {"source": str(source), "source_sha256": source_hash,
        "event_log": str(args.events.resolve()), "event_log_sha256": sha256(args.events),
        "observations": str(args.observations.resolve()), "observations_sha256": sha256(args.observations),
        "method": "Reviewed outcomes: completed transitions, visible results, cleared overlays and reading time; protect continuous motion and gestures.",
        "native_speed": 1.0, "sampling": "VFR sampled at 60 fps; native speed, no motion interpolation",
        "normalized_capture": str(normalized), "normalized_capture_sha256": sha256(normalized),
        "segments": segments, "state_checks": plan["checks"], "capture_clock_validation": clock_validation,
        "frames": cursor, "duration": cursor/FPS, "output_sha256": sha256(capture), "output_probe": final_probe,
        "visual_review": review, "visual_cut_and_touch_alignment_reviewed": False})
    print(capture)


if __name__ == "__main__":
    main()
