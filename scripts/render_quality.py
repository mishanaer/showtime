#!/usr/bin/env python3
"""Check exported videos: 60 Hz cadence, delivery format and basic content.

audit_delivery also checks the final duration, the delivery format (silent
H.264 4:2:0, square pixels, upright, starting at zero) and that the picture is
neither blank nor static. With a render report it checks that every tap ring
shows. These checks cannot establish that the app animated smoothly or that a
ring sits on the right control.
"""

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

from media_io import save_json


TARGET_FPS = Fraction(60)
FRAME_PERIOD = 1 / TARGET_FPS
TIMESTAMP_TOLERANCE = Fraction(1, 2000)  # 0.5 ms, including muxer rounding.
RATE_TOLERANCE = Fraction(1, 200)  # 0.005 fps; 59.94 is not exact 60.
DELIVERY_MIN_FRAMES = 600


class RenderQualityError(RuntimeError):
    def __init__(self, report):
        self.report = report
        reasons = "; ".join(item["message"] for item in report["failures"])
        super().__init__("Final video failed validation: " + reasons)


def _fraction(value):
    """Fraction rejects NaN/Infinity as well as absent/invalid timestamps."""
    if value is None or isinstance(value, bool):
        raise ValueError("missing numeric value")
    return Fraction(str(value))


def _seconds(value, integer_key, seconds_key, time_base):
    if integer_key in value:
        return _fraction(value[integer_key]) * time_base
    return _fraction(value.get(seconds_key))


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _new_report(path):
    return {
        "schema_version": 1,
        "scope": "final_render_only",
        "output": str(path),
        "output_sha256": None,
        "passed": False,
        "render_cadence_60fps": "failed",
        "motion_fidelity": "unverified",
        "policy": {
            "target_fps": "60/1",
            "timestamp_tolerance_ms": float(TIMESTAMP_TOLERANCE * 1000),
            "observed_rate_tolerance_fps": float(RATE_TOLERANCE),
            "input_capture_may_be_vfr": True,
            "input_capture_fps_requirement": None,
            "repeated_output_frames_allowed": True,
            "minimum_decoded_frames": 2,
        },
        "failures": [],
        "limitations": [
            "Only the supplied file is measured.",
            "PTS and durations establish output frame timing, not unique visual states or motion fidelity.",
            "Exporting at 60 fps does not reconstruct motion absent from the input recording.",
            "This audit invokes ffprobe only; it never applies fps filters, interpolation or timestamp repair.",
        ],
    }


def _fail(report, code, message, **details):
    report["failures"].append({"code": code, "message": message, **details})


def _audit_timing(data, report):
    streams = data.get("streams", [])
    if not streams:
        _fail(report, "missing_video_stream", "No video stream found")
        return
    stream = streams[0]
    report["stream"] = stream
    try:
        time_base = _fraction(stream.get("time_base"))
        if time_base <= 0:
            raise ValueError("non-positive time base")
    except (ValueError, ZeroDivisionError):
        _fail(report, "invalid_time_base", "The video stream has no valid time base")
        return
    try:
        average_rate = _fraction(stream.get("avg_frame_rate"))
    except (ValueError, ZeroDivisionError):
        average_rate = None
    if average_rate != TARGET_FPS:
        _fail(report, "average_rate_not_exact_60", "The average stream frame rate must be exactly 60/1",
              actual=stream.get("avg_frame_rate"))
    try:
        stream_duration = _seconds(stream, "duration_ts", "duration", time_base)
        if stream_duration <= 0:
            raise ValueError("non-positive duration")
    except (ValueError, ZeroDivisionError):
        stream_duration = None
        _fail(report, "invalid_stream_duration", "The video stream has no valid duration")

    frames = data.get("frames", [])
    report["decoded_frame_count"] = len(frames)
    if len(frames) < 2:
        _fail(report, "too_few_frames", "At least two decoded frames are required")
        return
    timestamps, durations = [], []
    bad_pts, bad_durations = [], []
    for index, frame in enumerate(frames):
        try:
            timestamps.append(_seconds(frame, "best_effort_timestamp", "best_effort_timestamp_time", time_base))
        except (ValueError, ZeroDivisionError):
            bad_pts.append(index)
        try:
            if "duration" in frame or "duration_time" in frame:
                duration = _seconds(frame, "duration", "duration_time", time_base)
            else:
                duration = _seconds(frame, "pkt_duration", "pkt_duration_time", time_base)
            if duration <= 0:
                raise ValueError("non-positive frame duration")
            durations.append(duration)
        except (ValueError, ZeroDivisionError):
            bad_durations.append(index)
    if bad_pts:
        _fail(report, "invalid_frame_pts", "Some frames have no finite presentation timestamp", count=len(bad_pts), frame_indices=bad_pts[:20])
    if bad_durations:
        _fail(report, "invalid_frame_duration", "Some frames have no valid duration", count=len(bad_durations), frame_indices=bad_durations[:20])
    if durations:
        report["frame_duration_ms"] = {"min": float(min(durations)*1000), "max": float(max(durations)*1000), "last": float(durations[-1]*1000)}
    if not bad_durations:
        wrong_durations = [i for i, duration in enumerate(durations) if abs(duration-FRAME_PERIOD) > TIMESTAMP_TOLERANCE]
        if wrong_durations:
            _fail(report, "frame_duration_not_60hz", "Frame durations differ from 1/60 second",
                  count=len(wrong_durations), frame_indices=wrong_durations[:20])
    if bad_pts:
        return

    intervals = [b-a for a, b in zip(timestamps, timestamps[1:])]
    nonmonotonic = [i+1 for i, dt in enumerate(intervals) if dt <= 0]
    if nonmonotonic:
        _fail(report, "nonmonotonic_pts", "Frame timestamps repeat or go backwards", frame_indices=nonmonotonic[:20])
    step_errors = [abs(dt-FRAME_PERIOD) for dt in intervals]
    grid_errors = [abs(t-timestamps[0]-i*FRAME_PERIOD) for i, t in enumerate(timestamps)]
    gaps = [{"after_frame": i, "from_pts": float(timestamps[i]), "to_pts": float(timestamps[i+1]), "interval_ms": float(dt*1000)}
            for i, dt in enumerate(intervals) if dt > FRAME_PERIOD+TIMESTAMP_TOLERANCE]
    step_bad = sum(error > TIMESTAMP_TOLERANCE for error in step_errors)
    span = timestamps[-1]-timestamps[0]
    observed_rate = Fraction(len(timestamps)-1) / span if span > 0 else None
    report["timing"] = {
        "first_pts": float(timestamps[0]), "last_pts": float(timestamps[-1]),
        "first_pts_offset_normalized": True,
        "observed_fps": float(observed_rate) if observed_rate is not None else None,
        "interval_min_ms": float(min(intervals)*1000), "interval_max_ms": float(max(intervals)*1000),
        "maximum_step_error_ms": float(max(step_errors)*1000),
        "maximum_grid_error_ms": float(max(grid_errors)*1000),
        "interval_violations": step_bad, "gap_count": len(gaps), "gap_examples": gaps[:20],
    }
    if step_bad:
        _fail(report, "irregular_frame_intervals", "Frame intervals do not follow a steady 60 Hz cadence", count=step_bad)
    if max(grid_errors) > TIMESTAMP_TOLERANCE:
        _fail(report, "off_60hz_grid", "Frame timestamps drift beyond the allowed 60 Hz tolerance")
    if observed_rate is None or abs(observed_rate-TARGET_FPS) > RATE_TOLERANCE:
        _fail(report, "observed_rate_not_60", "Decoded timestamps do not match 60 fps")
    if stream_duration is not None:
        expected_duration = len(frames)*FRAME_PERIOD
        if abs(stream_duration-expected_duration) > TIMESTAMP_TOLERANCE:
            _fail(report, "stream_duration_mismatch", "Stream duration does not match the frame count at 60 fps",
                  actual_seconds=float(stream_duration), expected_seconds=float(expected_duration))
        if not bad_durations and abs(stream_duration-(span+durations[-1])) > TIMESTAMP_TOLERANCE:
            _fail(report, "timeline_tail_mismatch", "Stream duration does not match the end of the last frame")


def audit_render(path):
    """Measure the cadence of the supplied file without converting it."""
    path = Path(path).resolve()
    report = _new_report(path)
    try:
        before = path.stat()
        report["output_sha256"] = _file_sha256(path)
        report["output_bytes"] = before.st_size
        completed = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_frames",
            "-show_entries", "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,time_base,duration,duration_ts,nb_frames:frame=best_effort_timestamp,best_effort_timestamp_time,duration,duration_time,pkt_duration,pkt_duration_time",
            "-of", "json", str(path),
        ], check=True, capture_output=True, text=True, timeout=180)
        if completed.stderr.strip():
            _fail(report, "decode_errors", "The decoder reported errors", details=completed.stderr.strip()[:1500])
        _audit_timing(json.loads(completed.stdout), report)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or _file_sha256(path) != report["output_sha256"]:
            _fail(report, "output_changed_during_audit", "The output changed during validation. Check the completed file again.")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        _fail(report, "render_audit_error", "Could not verify the output video", details=str(error)[:1500])
    report["passed"] = not report["failures"]
    report["render_cadence_60fps"] = "passed" if report["passed"] else "failed"
    return report


def require_render_60fps(path):
    """Require final output at 60 Hz."""
    report = audit_render(path)
    if not report["passed"]:
        raise RenderQualityError(report)
    return report


def delivery_frame_count(duration):
    """Quantize requested seconds to 60 fps, then enforce the frame minimum.

    No clamping or stretching occurs. The caller must render exactly the returned
    count at 60 fps; source-video frame rate is irrelevant to this calculation.
    """
    if type(duration) not in (int, float):
        raise ValueError("Enter a positive finite duration in seconds")
    try:
        seconds = _fraction(duration)
    except (ValueError, ZeroDivisionError):
        raise ValueError("Enter a positive finite duration in seconds") from None
    if seconds <= 0:
        raise ValueError("Enter a positive finite duration in seconds")
    frames = round(seconds * TARGET_FPS)
    if frames < DELIVERY_MIN_FRAMES:
        raise ValueError("The final video must last at least 10 seconds (600 frames at 60 fps).")
    return frames


def audit_delivery(path):
    """Audit product duration plus cadence, keeping their results distinct."""
    report = audit_render(path)
    report["scope"] = "product_delivery"
    report["delivery_policy"] = {
        "minimum_seconds": DELIVERY_MIN_FRAMES // 60, "maximum_seconds": None,
        "minimum_decoded_frames": DELIVERY_MIN_FRAMES,
        "maximum_decoded_frames": None,
        "target_fps": "60/1", "boundaries_inclusive": True,
        "automatic_clamp_or_stretch": False,
    }
    frames = report.get("decoded_frame_count")
    frame_count_valid = type(frames) is int and frames >= DELIVERY_MIN_FRAMES
    cadence_passed = report["render_cadence_60fps"] == "passed"
    # A 600-frame 30 fps file lasts 20 seconds, not 10. Do not derive its actual
    # duration from the target 60 fps when the cadence audit has failed.
    report["delivery_duration"] = {
        "status": "passed" if frame_count_valid and cadence_passed else "unverified" if frame_count_valid else "failed",
        "decoded_frame_count": frames,
        "frame_count_in_range": frame_count_valid,
        "seconds_at_verified_60fps": frames / 60 if type(frames) is int and cadence_passed else None,
        "requires_verified_60fps": True,
    }
    if not frame_count_valid:
        _fail(report, "delivery_frame_count_out_of_range",
              "The final video must contain at least 600 frames at 60 fps (10 seconds)",
              actual_decoded_frames=frames, minimum=DELIVERY_MIN_FRAMES, maximum=None)
    stream = report.get("stream", {})
    if stream and (stream.get("width"), stream.get("height")) != (1920, 1920):
        _fail(report, "delivery_dimensions", "The final video must be 1920 × 1920")
    _audit_media(path, report)
    report["passed"] = not report["failures"]
    return report


def _audit_media(path, report):
    try:
        _audit_format(path, report)
        _audit_content(path, report)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        _fail(report, "content_audit_error", "Could not check the video content", details=str(error)[:1500])


CONTENT_SIZE = 64  # decoded thumbnail edge for content checks
BLANK_RANGE = 8  # luma levels; a real app screen spans far more
RING_MIN_EXPECTED = 3.0  # below this the ring tint barely differs from the screen under it
RING_MIN_SHARE = 0.35  # share of the expected tint change that must actually show


def _probe_streams(path):
    data = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                                     check=True, capture_output=True, text=True, timeout=180).stdout)
    return data.get("streams", [])


def _audit_format(path, report):
    """The delivery format: silent H.264 4:2:0, square pixels, upright, starting at zero."""
    streams = _probe_streams(path)
    video = [s for s in streams if s.get("codec_type") == "video"]
    others = [s.get("codec_type") for s in streams if s.get("codec_type") != "video"]
    report["delivery_format"] = {"codec": None, "pix_fmt": None, "extra_streams": others}
    if not video:
        return
    stream = video[0]
    report["delivery_format"].update(codec=stream.get("codec_name"), pix_fmt=stream.get("pix_fmt"),
                                     sample_aspect_ratio=stream.get("sample_aspect_ratio"))
    if stream.get("codec_name") != "h264" or stream.get("pix_fmt") != "yuv420p":
        _fail(report, "delivery_codec", "The final video must be H.264 with yuv420p pixels",
              codec=stream.get("codec_name"), pix_fmt=stream.get("pix_fmt"))
    if stream.get("sample_aspect_ratio") not in (None, "1:1", "0:1", "N/A"):
        _fail(report, "delivery_pixel_aspect", "The final video must use square pixels",
              sample_aspect_ratio=stream.get("sample_aspect_ratio"))
    rotation = [d.get("rotation") for d in stream.get("side_data_list", []) if d.get("rotation")]
    if rotation or stream.get("tags", {}).get("rotate") not in (None, "0"):
        _fail(report, "delivery_rotation", "The final video must not carry rotation metadata")
    if others:
        _fail(report, "delivery_extra_streams", "The final video must contain only the video stream", streams=others)
    start = stream.get("start_time")
    if start not in (None, "N/A") and abs(float(start)) > float(FRAME_PERIOD) / 2:
        _fail(report, "delivery_start_time", "The final video must start at zero", start_time=start)


def _decode_gray(path, size=CONTENT_SIZE):
    command = ["ffmpeg", "-v", "error", "-i", str(path),
               "-vf", f"scale={size}:{size}:flags=area,format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(command, check=True, capture_output=True, timeout=600).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, size, size).astype(np.int16)


def _audit_content(path, report):
    """Catch a blank picture or a video in which nothing ever changes."""
    frames = _decode_gray(path)
    if not len(frames):
        return
    changes = np.abs(np.diff(frames, axis=0)).max(axis=(1, 2)) if len(frames) > 1 else np.zeros(0)
    report["content"] = {"luma_range": int(frames.max() - frames.min()),
                         "changing_frames": int((changes > 0).sum())}
    if report["content"]["luma_range"] < BLANK_RANGE:
        _fail(report, "content_blank", "The final video shows no picture")
    elif not report["content"]["changing_frames"]:
        _fail(report, "content_static", "Nothing changes in the final video")


def _decode_rgb_crop(path, frame, box):
    width, height = box[2] - box[0], box[3] - box[1]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          f"select=eq(n\\,{frame}),format=rgb24,crop={width}:{height}:{box[0]}:{box[1]}",
                          "-vsync", "0", "-frames:v", "1", "-f", "rawvideo", "-"],
                         check=True, capture_output=True, timeout=600).stdout
    if len(raw) != width * height * 3:
        return None
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3).astype(np.float32)


def audit_tap_rings(path, rings, report):
    """Each tap ring must tint the picture where and when it is drawn.

    rings: {"sequence", "start_number", "width", "frames", "events"} from the render.
    The ring's own alpha at the contact frame weights a comparison between the
    contact frame and a frame without this ring (just before it starts, or just
    after it ends). The expected change is what that ring's tint would do to the
    reference picture; when the tint is too close to the screen under it (a white
    ring on a white app) the ring is reported as unverifiable, not as missing.
    A change in the app under the ring can hide a missing ring; a ring that is
    present but off target fails like a missing one.
    """
    from PIL import Image
    results = []
    width, total = rings["width"], rings["frames"]
    for event in rings["events"]:
        contact, start, end = event["anchor_output_frame"], event["tap_start_frame"], event["tap_end_frame"]
        reference = start - 1 if start >= 1 else end + 1 if end + 1 < total else None
        result = {"id": event.get("id"), "contact_frame": contact, "reference_frame": reference}
        results.append(result)
        if reference is None:
            result["status"] = "unverifiable"
            continue
        index = rings.get("start_number", 0) + event["source_frame_map"][contact - start]
        with Image.open(rings["sequence"] % index) as image:
            alpha = image.getchannel("A").resize((width, width), Image.BILINEAR)
        alpha = np.asarray(alpha, np.float32) / 255
        x, y = event["canvas_point"]
        left, top = round(x - width / 2), round(y - width / 2)
        box = [max(0, left), max(0, top), min(1920, left + width), min(1920, top + width)]
        alpha = alpha[box[1] - top:box[3] - top, box[0] - left:box[2] - left]
        frames = [_decode_rgb_crop(path, n, box) for n in (contact, reference)]
        if any(frame is None for frame in frames) or alpha.sum() <= 0:
            _fail(report, "tap_ring_frames_missing", "The tap ring frames are outside the video", event=event.get("id"))
            result["status"] = "failed"
            continue
        drawn, plain = frames
        weight = alpha[..., None]
        tint = np.asarray(event.get("tint_rgb", [255, 255, 255]), np.float32)
        observed = float((weight * np.abs(drawn - plain)).sum() / (3 * alpha.sum()))
        expected = float((weight * weight * np.abs(tint - plain)).sum() / (3 * alpha.sum()))
        result.update(observed_change=round(observed, 2), expected_change=round(expected, 2))
        if expected < RING_MIN_EXPECTED:
            result["status"] = "unverifiable"
        elif observed < RING_MIN_SHARE * expected:
            result["status"] = "failed"
            _fail(report, "tap_ring_invisible", "A tap ring does not show at its contact frame",
                  event=event.get("id"), observed_change=result["observed_change"],
                  expected_change=result["expected_change"])
        else:
            result["status"] = "passed"
    report["tap_rings"] = results
    report["passed"] = not report["failures"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("render", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--cadence-only", action="store_true",
                        help="Check frame cadence only for comparisons and technical previews")
    parser.add_argument("--render-report", type=Path,
                        help="compose_pilot render report; also checks that every tap ring shows")
    args = parser.parse_args()
    if args.report and (args.render.resolve() == args.report.resolve() or
                        (args.report.exists() and args.render.exists() and args.report.samefile(args.render))):
        parser.error("The report path points to the video. Choose a separate JSON file.")
    report = (audit_render if args.cadence_only else audit_delivery)(args.render)
    if args.render_report and not args.cadence_only:
        render = json.loads(args.render_report.read_text())
        if render.get("quality", {}).get("output_sha256") not in (None, report.get("output_sha256")):
            _fail(report, "render_report_mismatch", "The render report belongs to another video")
        elif render.get("events"):
            try:
                audit_tap_rings(args.render, {**render["tap"], "frames": report.get("decoded_frame_count") or 0,
                                              "events": render["events"]}, report)
            except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
                _fail(report, "tap_ring_audit_error", "Could not check the tap rings", details=str(error)[:1500])
        report["passed"] = not report["failures"]
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        save_json(args.report, report)
    print(text, end="")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
