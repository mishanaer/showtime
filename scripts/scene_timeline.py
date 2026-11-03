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
        raise SceneTimelineError(f"{label}: требуется конечное число не меньше {minimum}.")
    return float(value)


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip():
        raise SceneTimelineError(f"{label}: требуется непустой идентификатор.")
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
    duration = _number(duration, "Длительность записи")
    if duration <= 0:
        raise SceneTimelineError("Длительность записи должна быть больше нуля.")
    if type(fps) is not int or fps <= 0:
        raise SceneTimelineError("fps должен быть положительным целым числом.")
    lead_seconds = _number(lead_seconds, "Время перед действием")
    if not isinstance(source_sha256, str) or not _HASH.fullmatch(source_sha256):
        raise SceneTimelineError("Нужен фактический SHA-256 исходной записи: 64 строчных hex-символа.")
    if not isinstance(observations, dict):
        raise SceneTimelineError("Наблюдения должны быть объектом JSON.")
    if type(observations.get("schema_version")) is not int or observations["schema_version"] != 1:
        raise SceneTimelineError("Для наблюдений требуется schema_version: 1.")
    if observations.get("review_status") != "reviewed":
        raise SceneTimelineError("Наблюдения ещё не проверены: требуется review_status: reviewed после просмотра записи.")
    if observations.get("capture_sha256") != source_sha256:
        raise SceneTimelineError("Наблюдения относятся к другой записи: SHA-256 не совпадает. Проверьте текущий исходник.")

    events = raw_events.get("events") if isinstance(raw_events, dict) else raw_events
    if not isinstance(events, list) or not events:
        raise SceneTimelineError("Нужен непустой список исходных действий events.")
    event_ids, actions = [], []
    previous_start = -1.0
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise SceneTimelineError(f"Действие {index}: ожидается объект.")
        event_id = _identifier(event.get("id"), f"Действие {index}, id")
        if event_id in event_ids:
            raise SceneTimelineError(f"Повторяющийся id исходного действия: {event_id}.")
        start = _number(event.get("source_start"), f"{event_id}, source_start")
        end = _number(event.get("source_end"), f"{event_id}, source_end")
        if not start <= end <= duration or start < previous_start:
            raise SceneTimelineError(f"Действие {event_id}: source_start/source_end должны идти по времени и находиться внутри записи.")
        previous_start = start
        event_ids.append(event_id)
        actions.append((start, end))

    steps = observations.get("steps")
    if not isinstance(steps, list):
        raise SceneTimelineError("В наблюдениях нужен список steps для всех действий.")
    step_ids = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise SceneTimelineError(f"Наблюдение {index}: ожидается объект.")
        event_id = _identifier(step.get("event_id"), f"Наблюдение {index}, event_id")
        if event_id in step_ids:
            raise SceneTimelineError(f"Повторяющееся наблюдение действия: {event_id}.")
        if event_id not in event_ids:
            raise SceneTimelineError(f"Наблюдение ссылается на неизвестное действие: {event_id}.")
        step_ids.append(event_id)
    if step_ids != event_ids:
        raise SceneTimelineError("Для каждого действия требуется ровно одно наблюдение steps в порядке исходных действий.")

    available_frames = _floor_frame(duration, fps)
    if available_frames <= 0:
        raise SceneTimelineError("В записи нет ни одного полного кадра при заданном fps.")
    intervals, step_checks = [], []
    for event_id, (start, end), step in zip(event_ids, actions, steps):
        response = _number(step.get("response_start"), f"{event_id}, response_start")
        markers = {name: _number(step.get(name), f"{event_id}, {name}")
                   for name in ("transition_end", "result_ready", "overlays_clear")}
        read_seconds = _number(step.get("read_seconds"), f"{event_id}, read_seconds")
        if not start <= response <= duration:
            raise SceneTimelineError(f"{event_id}: response_start не может предшествовать действию или выходить за запись.")
        if any(not response <= value <= duration for value in markers.values()):
            raise SceneTimelineError(f"{event_id}: отметки завершения должны быть после response_start и внутри записи.")
        ready = max(markers.values())
        # A response can occur during a long command. Preserve its release/end too.
        required_end = max(end, ready + read_seconds)
        if not math.isfinite(required_end) or required_end > duration + _ROUND_EPSILON:
            raise SceneTimelineError(f"{event_id}: не хватает записанного финала. Нужно до {required_end:.6f} с, запись заканчивается в {duration:.6f} с; перезапишите с полной паузой результата.")
        first = max(0, _floor_frame(start - lead_seconds, fps))
        # The interval end is exclusive. Even with zero reading time, retain
        # the frame at/after every completion marker, not only the preceding
        # transition. A marker at the capture's end therefore needs more video.
        last = max(_ceil_frame(required_end, fps), _floor_frame(start, fps) + 1,
                   _ceil_frame(ready, fps) + 1)
        if last > available_frames:
            raise SceneTimelineError(f"{event_id}: не хватает полного кадра для результата и паузы. Нужно {last} кадров, доступно {available_frames}; сокращать паузу нельзя.")
        intervals.append([first, last])
        step_checks.append({"event_id": event_id, "source_action": [start, end],
                            "response_start": response, **markers,
                            "read_seconds": read_seconds, "hold_start": ready,
                            "required_end": required_end, "frames": [first, last]})

    protected = observations.get("protected_ranges")
    if not isinstance(protected, list):
        raise SceneTimelineError("В наблюдениях нужен protected_ranges; укажите пустой список, если защищённых движений нет.")
    protected_checks = []
    for index, entry in enumerate(protected):
        if not isinstance(entry, dict):
            raise SceneTimelineError(f"Защищённое движение {index}: ожидается объект.")
        start = _number(entry.get("start"), f"Защищённое движение {index}, start")
        end = _number(entry.get("end"), f"Защищённое движение {index}, end")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise SceneTimelineError(f"Защищённое движение {index}: нужен непустой reason.")
        if not start < end <= duration:
            raise SceneTimelineError(f"Защищённое движение {index}: диапазон должен иметь положительную длительность и помещаться в запись.")
        first = _floor_frame(start, fps)
        last = max(_ceil_frame(end, fps), first + 1)
        if last > available_frames:
            raise SceneTimelineError(f"Защищённое движение {index}: не хватает полного записанного кадра; диапазон нельзя обрезать.")
        intervals.append([first, last])
        protected_checks.append({"start": start, "end": end, "reason": reason,
                                 "frames": [first, last]})

    merged = _merge(intervals)
    # Check actual frame coverage, not just success of interval construction.
    for required in intervals:
        if not any(first <= required[0] and last >= required[1] for first, last in merged):
            raise SceneTimelineError("Не удалось сохранить действие, чтение результата или защищённое движение целиком.")
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
