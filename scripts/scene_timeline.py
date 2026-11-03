#!/usr/bin/env python3
"""Plan cuts from reviewed source-video observations, without changing UI speed.

Observations are an agent's visual evidence, not a new human approval:
  {"schema_version": 1, "capture_sha256": "<actual capture SHA-256>",
   "review_status": "reviewed",
   "steps": [{"event_id": "send", "response_start": 1.1,
              "transition_end": 1.4, "result_ready": 1.5,
              "overlays_clear": 3.0, "read_seconds": 1.0}],
   "protected_ranges": [{"start": 1.1, "end": 1.8,
                         "reason": "Complete the visible motion"}]}

All times are seconds in the original capture. Every raw event must have one
step, in the same order. The three completion markers may occur in any order,
but never before the observed response. Use response_start for overlays_clear
when no overlay exists. Declared protected motion is retained in full, including
ranges between action windows; no cut may occur inside such a range. Continuous
decoration needs a finite, visually chosen protected range, not an endless wait.

Intervals use CFR output frames [first, last), with outward rounding. This
planner never shortens the requested reading time to fit an incomplete capture.
It validates claims and coverage; it cannot verify that observations are true.
"""

import math
import re


class SceneTimelineError(ValueError):
    """Reviewed observations cannot produce a complete, safe source timeline."""


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ROUND_EPSILON = 1e-9


def _number(value, label, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise SceneTimelineError(f"{label} must be a finite number greater than or equal to {minimum}.")
    return float(value)


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip():
        raise SceneTimelineError(f"{label} needs a nonempty identifier.")
    return value


def _floor_frame(seconds, fps):
    return math.floor(seconds * fps + _ROUND_EPSILON)


def _ceil_frame(seconds, fps):
    return math.ceil(seconds * fps - _ROUND_EPSILON)


def _merge(intervals):
    merged = []
    for first, last in sorted(intervals):
        if merged and first <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    return merged


def plan_scene_intervals(raw_events, observations, duration, source_sha256,
                         fps=60, lead_seconds=.35):
    """Return {intervals: [[first, last), ...], checks: {...}} or fail closed.

raw_events accepts the existing events list or its {"events": [...]} wrapper.
Only reviewed observations tied to the actual source SHA-256 are accepted.
"""
    duration = _number(duration, "Recording duration")
    if duration <= 0:
        raise SceneTimelineError("Recording duration must be positive.")
    if type(fps) is not int or fps <= 0:
        raise SceneTimelineError("Set fps to a positive integer.")
    lead_seconds = _number(lead_seconds, "Lead time before the action")
    if not isinstance(source_sha256, str) or not _HASH.fullmatch(source_sha256):
        raise SceneTimelineError("Supply the actual SHA-256 of the recording as 64 lowercase hexadecimal characters.")
    if not isinstance(observations, dict):
        raise SceneTimelineError("Supply observations as a JSON object.")
    if type(observations.get("schema_version")) is not int or observations["schema_version"] != 1:
        raise SceneTimelineError("Use schema_version 1 for observations.")
    if observations.get("review_status") != "reviewed":
        raise SceneTimelineError("Review the recording before setting review_status to reviewed.")
    if observations.get("capture_sha256") != source_sha256:
        raise SceneTimelineError("The observations refer to a different recording. Check the source SHA-256.")

    events = raw_events.get("events") if isinstance(raw_events, dict) else raw_events
    if not isinstance(events, list) or not events:
        raise SceneTimelineError("Supply a nonempty events list of recorded actions.")
    event_ids, actions = [], []
    previous_start = -1.0
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise SceneTimelineError(f"Action {index} must be an object.")
        event_id = _identifier(event.get("id"), f"Action {index} ID")
        if event_id in event_ids:
            raise SceneTimelineError(f"Duplicate action ID: {event_id}.")
        start = _number(event.get("source_start"), f"{event_id}, source_start")
        end = _number(event.get("source_end"), f"{event_id}, source_end")
        if not start <= end <= duration or start < previous_start:
            raise SceneTimelineError(f"Action {event_id}: timestamps must be in order and within the recording.")
        previous_start = start
        event_ids.append(event_id)
        actions.append((start, end))

    steps = observations.get("steps")
    if not isinstance(steps, list):
        raise SceneTimelineError("Add a steps list with an observation for every action.")
    step_ids = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise SceneTimelineError(f"Observation {index} must be an object.")
        event_id = _identifier(step.get("event_id"), f"Observation {index} event_id")
        if event_id in step_ids:
            raise SceneTimelineError(f"Duplicate observation for action {event_id}.")
        if event_id not in event_ids:
            raise SceneTimelineError(f"Observation refers to unknown action {event_id}.")
        step_ids.append(event_id)
    if step_ids != event_ids:
        raise SceneTimelineError("Supply exactly one observation per action in source order.")

    available_frames = _floor_frame(duration, fps)
    if available_frames <= 0:
        raise SceneTimelineError("The recording contains no complete frame at the selected frame rate.")
    intervals, step_checks = [], []
    for event_id, (start, end), step in zip(event_ids, actions, steps):
        response = _number(step.get("response_start"), f"{event_id}, response_start")
        markers = {name: _number(step.get(name), f"{event_id}, {name}")
                   for name in ("transition_end", "result_ready", "overlays_clear")}
        read_seconds = _number(step.get("read_seconds"), f"{event_id}, read_seconds")
        if not start <= response <= duration:
            raise SceneTimelineError(f"{event_id}: response_start must fall between the action and the end of the recording.")
        if any(not response <= value <= duration for value in markers.values()):
            raise SceneTimelineError(f"{event_id}: completion markers must fall between response_start and the end of the recording.")
        ready = max(markers.values())
        # A response can occur during a long command. Preserve its release/end too.
        required_end = max(end, ready + read_seconds)
        if not math.isfinite(required_end) or required_end > duration + _ROUND_EPSILON:
            raise SceneTimelineError(f"{event_id}: the recording ends too soon. Need {required_end:.6f} s, but the take ends at {duration:.6f} s. Record the full reading pause.")
        first = max(0, _floor_frame(start - lead_seconds, fps))
        # The interval end is exclusive. Even with zero reading time, retain
        # the frame at/after every completion marker, not only the preceding
        # transition. A marker at the capture's end therefore needs more video.
        last = max(_ceil_frame(required_end, fps), _floor_frame(start, fps) + 1,
                   _ceil_frame(ready, fps) + 1)
        if last > available_frames:
            raise SceneTimelineError(f"{event_id}: not enough complete frames for the result and reading pause. Need {last} frames, but only {available_frames} are available.")
        intervals.append([first, last])
        step_checks.append({"event_id": event_id, "source_action": [start, end],
                            "response_start": response, **markers,
                            "read_seconds": read_seconds, "hold_start": ready,
                            "required_end": required_end, "frames": [first, last]})

    protected = observations.get("protected_ranges")
    if not isinstance(protected, list):
        raise SceneTimelineError("Add protected_ranges to the observations. Use an empty list when no motion needs protection.")
    protected_checks = []
    for index, entry in enumerate(protected):
        if not isinstance(entry, dict):
            raise SceneTimelineError(f"Protected range {index} must be an object.")
        start = _number(entry.get("start"), f"Protected range {index} start")
        end = _number(entry.get("end"), f"Protected range {index} end")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise SceneTimelineError(f"Add a nonempty reason to protected range {index}.")
        if not start < end <= duration:
            raise SceneTimelineError(f"Protected range {index} must have positive duration and fit within the recording.")
        first = _floor_frame(start, fps)
        last = max(_ceil_frame(end, fps), first + 1)
        if last > available_frames:
            raise SceneTimelineError(f"Protected range {index} needs a complete recorded frame. Keep the full range.")
        intervals.append([first, last])
        protected_checks.append({"start": start, "end": end, "reason": reason,
                                 "frames": [first, last]})

    merged = _merge(intervals)
    # Check actual frame coverage, not just success of interval construction.
    for required in intervals:
        if not any(first <= required[0] and last >= required[1] for first, last in merged):
            raise SceneTimelineError("The edit cannot preserve the full action, reading pause, or protected motion.")
    cuts = [{"source_out": left[1] / fps, "next_source_in": right[0] / fps,
             "removed_seconds": (right[0] - left[1]) / fps}
            for left, right in zip(merged, merged[1:])]
    return {"intervals": merged,
            "checks": {"capture_sha256": source_sha256, "review_status": "reviewed",
                       "observations_kind": "agent_visual_observations_not_human_approval",
                       "fps": fps, "source_duration": duration,
                       "available_frames": available_frames, "lead_seconds": lead_seconds,
                       "native_speed": 1.0, "frame_interval": "[first,last)",
                       "steps": step_checks, "protected_ranges": protected_checks,
                       "all_actions_preserved": True, "all_result_holds_preserved": True,
                       "protected_ranges_preserved": True, "cuts": cuts,
                       "output_frames": sum(last - first for first, last in merged)}}
