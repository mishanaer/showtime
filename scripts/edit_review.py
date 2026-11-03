#!/usr/bin/env python3
"""Build frame evidence for a proposed edit; never approve it visually.

The editor first normalizes its original capture (VFR is allowed) to an fps
grid starting at zero. Pass that normalized video here. Intervals are ordered,
half-open indexes on that grid; observation times remain source seconds, with
``steps`` containing event_id, result_ready and overlays_clear. The original
and normalized captures have different hashes. This module does not normalize
again or judge whether adjacent frames contain different native motion.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont, ImageOps


_COLUMNS = 4
_PER_PAGE = 80
_CELL_WIDTH = 400
_LOOP_NOTE = "Loop seam — manual review, seamless loop not guaranteed"


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _plan_evidence(intervals, observations, frame_count, fps):
    """Keep semantic duplicates: a cut pair must remain a visible pair."""
    fps = _number(fps, "fps")
    if fps <= 0:
        raise ValueError("fps must be positive")
    if not isinstance(intervals, (list, tuple)) or not intervals:
        raise ValueError("Need at least one retained interval")
    previous = 0
    for i, interval in enumerate(intervals):
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError(f"Interval {i} must contain [first, last)")
        first, last = interval
        if any(isinstance(n, bool) or not isinstance(n, int) for n in interval):
            raise ValueError(f"Interval {i} must use integer frame indexes")
        if not previous <= first < last <= frame_count:
            raise ValueError(f"Interval {i} overlaps, is unordered, empty or outside the source")
        previous = last
    if not isinstance(observations, dict) or observations.get("schema_version") != 1:
        raise ValueError("Observations must use schema_version 1")
    steps = observations.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Observations.steps must be a list")
    groups = []

    def evidence(kind, frame, label, **extra):
        return {"kind": kind, "source_frame": frame, "label": label, **extra}

    for i, (first, last) in enumerate(intervals):
        groups.append([
            evidence("interval_start", first, f"Interval {i + 1} start", interval_index=i),
            evidence("interval_end", last - 1, f"Interval {i + 1} end", interval_index=i),
        ])
    for i in range(len(intervals) - 1):
        groups.append([
            evidence("cut_left", intervals[i][1] - 1, f"Cut {i + 1} before", cut_index=i),
            evidence("cut_right", intervals[i + 1][0], f"Cut {i + 1} after", cut_index=i),
        ])
    seen = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("Each observation step must be an object")
        event_id = step.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen:
            raise ValueError("Observation event_id values must be nonempty and unique")
        seen.add(event_id)
        group = []
        for field, label in (("result_ready", "Result visible"),
                             ("overlays_clear", "Overlays clear")):
            seconds = _number(step.get(field), f"{event_id}.{field}")
            # Readiness means at or after the observed time, never just before.
            frame = math.ceil(seconds * fps - 1e-9)
            if seconds < 0 or not 0 <= frame < frame_count:
                raise ValueError(f"{event_id}.{field} is outside the source")
            group.append(evidence(field, frame, f"{event_id}: {label}",
                                  event_id=event_id, observation_seconds=seconds))
        groups.append(group)
    groups.append([
        evidence("loop_end", intervals[-1][1] - 1, f"{_LOOP_NOTE} end"),
        evidence("loop_start", intervals[0][0], f"{_LOOP_NOTE} start"),
    ])
    groups.append([evidence("final_frame", intervals[-1][1] - 1, "Last frame of the edit")])
    return groups


def _pages(groups):
    """Place every group intact; never truncate or split a cut pair."""
    pages, page = [], []
    for group in groups:
        if len(page) + len(group) > _PER_PAGE:
            pages.append(page)
            page = []
        page.extend(group)
    if page:
        pages.append(page)
    return pages


def _run(command):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"{command[0]} failed: {error.stderr[-3000:]}") from error


def _probe_source(capture):
    raw = json.loads(_run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_streams",
        "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate:frame=best_effort_timestamp_time",
        "-of", "json", str(capture),
    ]))
    streams = raw.get("streams", [])
    if not streams:
        raise ValueError("Capture has no video stream")
    frames = raw.get("frames", [])
    if not frames:
        raise ValueError("Capture has no decoded video frames")
    try:
        timestamps = [float(frame["best_effort_timestamp_time"]) for frame in frames]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Cannot label source frames without presentation timestamps") from error
    if not all(math.isfinite(value) for value in timestamps):
        raise ValueError("Source contains invalid presentation timestamps")
    return streams[0], timestamps


def _validate_grid(timestamps, fps):
    fps = _number(fps, "fps")
    if fps <= 0:
        raise ValueError("fps must be positive")
    if any(abs(timestamp - index / fps) > .001 for index, timestamp in enumerate(timestamps)):
        raise ValueError(f"Pass the editor's normalized {fps:g} fps capture starting at PTS zero, not the original VFR capture")


def _font(size):
    for name in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    # Recent Pillow's bundled default also supports the requested size.
    return ImageFont.load_default(size=size)


def _wrapped(draw, text, font, width):
    lines, current = [], ""
    for char in text.replace("\n", " "):
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = char
        else:
            current = candidate
    lines.append(current)
    return lines


def _sheet(path, items, page_number, page_count, fps):
    font, small = _font(18), _font(15)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    labels = [_wrapped(measure, item["label"], font, _CELL_WIDTH - 24) for item in items]
    label_height = max(len(lines) for lines in labels) * 22 + 52
    thumbnail_height = 470
    cell_height = thumbnail_height + label_height + 12
    rows = math.ceil(len(items) / _COLUMNS)
    sheet = Image.new("RGB", (_COLUMNS * _CELL_WIDTH, 76 + rows * cell_height), "#17191d")
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 10), f"Edit review | Page {page_number}/{page_count}",
              fill="white", font=font)
    draw.text((12, 39), f"Edit timeline at {fps:g} fps. Times come from the prepared video.",
              fill="#c6cad3", font=small)
    for index, (item, lines) in enumerate(zip(items, labels)):
        x, y = (index % _COLUMNS) * _CELL_WIDTH, 76 + (index // _COLUMNS) * cell_height
        with Image.open(item["image"]) as frame:
            thumb = ImageOps.contain(frame.convert("RGB"), (_CELL_WIDTH - 16, thumbnail_height))
            sheet.paste(thumb, (x + (_CELL_WIDTH - thumb.width) // 2,
                              y + (thumbnail_height - thumb.height) // 2))
        text_y = y + thumbnail_height + 5
        for line in lines:
            draw.text((x + 10, text_y), line, fill="white", font=font)
            text_y += 22
        draw.text((x + 10, text_y + 3),
                  f"Frame {item['source_frame']} | {item['source_seconds']:.3f} s",
                  fill="#c6cad3", font=small)
        if not item["retained"]:
            draw.text((x + 10, text_y + 23), "This frame is outside the edit", fill="#ffce85", font=small)
    sheet.save(path, quality=93)


def build_edit_review(capture: Path, intervals, observations: dict, output_dir: Path, fps=60) -> dict:
    """Return a review packet, its JSON/JPG paths and complete evidence counts.

    ``capture`` is the editor's normalized fps-grid video before concatenation,
    not the original VFR capture or the concatenated edit. Its PTS must start
    at zero; intervals index this video and observations use source seconds.
    Final frame means the final retained frame. Observation labels report input
    assertions; this function does not establish that result/overlays look right.
    """
    capture, output_dir = Path(capture).resolve(), Path(output_dir).resolve()
    if not capture.is_file():
        raise ValueError(f"Capture does not exist: {capture}")
    stream, timestamps = _probe_source(capture)
    _validate_grid(timestamps, fps)
    groups = _plan_evidence(intervals, observations, len(timestamps), fps)
    fps = float(fps)
    digest = hashlib.sha256()
    with capture.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    source_sha256 = digest.hexdigest()
    evidence = [item for group in groups for item in group]
    selected = sorted({item["source_frame"] for item in evidence})
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "edit-review-frames"
    frames_dir.mkdir(exist_ok=True)
    # One decode pass, with exact decoder indexes; no timestamp resampling.
    with tempfile.TemporaryDirectory(prefix=".edit-review-", dir=output_dir) as staging:
        pattern = Path(staging) / "%06d.jpg"
        select = "+".join(f"eq(n\\,{frame})" for frame in selected)
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(capture),
              "-map", "0:v:0", "-vf", f"select='{select}'", "-fps_mode", "passthrough",
              "-frames:v", str(len(selected)), "-q:v", "2", str(pattern)])
        extracted = sorted(Path(staging).glob("*.jpg"))
        if len(extracted) != len(selected):
            raise RuntimeError(f"Expected {len(selected)} source frames, extracted {len(extracted)}")
        for frame, filename in zip(selected, extracted):
            filename.replace(frames_dir / f"source-{frame:08d}.jpg")
    for item in evidence:
        frame = item["source_frame"]
        item.update(source_seconds=timestamps[frame], timeline_seconds=frame / fps,
                    retained=any(first <= frame < last for first, last in intervals),
                    image=str(frames_dir / f"source-{frame:08d}.jpg"))
    pages, sheet_paths = _pages(groups), []
    for number, items in enumerate(pages, 1):
        sheet_path = output_dir / f"edit-review-{number:03d}.jpg"
        _sheet(sheet_path, items, number, len(pages), fps)
        sheet_paths.append(str(sheet_path))
        for item in items:
            item["page"] = number
    report_path = output_dir / "edit-review.json"
    packet = {
        "schema_version": 1, "status": "needs_visual_review",
        "capture": str(capture), "capture_sha256": source_sha256,
        "frame_index_space": "normalized_capture",
        "observations_capture_sha256": observations.get("capture_sha256"),
        "source_probe": stream, "source_frame_count": len(timestamps), "timeline_fps": fps,
        "normalized_grid_validation": "passed",
        "observation_review_status": observations.get("review_status"),
        "intervals": intervals, "evidence_count": len(evidence),
        "unique_source_frame_count": len(selected), "page_count": len(pages),
        "images_per_page_max": _PER_PAGE, "contact_sheets": sheet_paths,
        "report": str(report_path), "evidence": evidence,
        "loop_seam": {"status": "needs_visual_review", "note": _LOOP_NOTE,
                      "end_frame": intervals[-1][1] - 1, "start_frame": intervals[0][0]},
        "manual_review_required": "Watch each cut and the return to the opening frame to check continuity.",
        "limitations": [
            "Review the video to judge visual quality and source motion.",
            "These frames come from the normalized video. A variable-rate source can produce repeated frames.",
            "The result_ready and overlays_clear frames come from the reviewer's observations.",
            "Watch the animation across each cut to check for jumps.",
        ],
    }
    report_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return packet
