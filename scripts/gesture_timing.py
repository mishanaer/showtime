"""Derived hold: retain the AEP entry/release, sustain its contact frame."""
import math
from pathlib import Path
import os
import shutil


def gesture_frame_map(event, frame_count, anchor_frame, fps=60):
    """Return source-frame indices and contact-frame count at delivery fps.

    The first release frame is exactly round(hold_seconds * fps) frames after
    the contact anchor. The original anchor is the first frame of that hold,
    not an extra frame preceding it. Recording fps does not enter this mapping.
    """
    if not 0 <= anchor_frame < frame_count:
        raise ValueError("Опорный кадр жеста вне исходной последовательности")
    if event.get("type", "tap") != "long_press":
        return list(range(frame_count)), 0
    duration = event.get("hold_seconds")
    if (isinstance(duration, bool) or not isinstance(duration, (float, int))
            or not math.isfinite(duration) or not 0 < duration <= 60):
        raise ValueError("Для удержания требуется hold_seconds от 0 до 60 секунд из журнала контакта")
    if event.get("hold_timing") not in ("hid_dispatch_estimate", "observed", "measured"):
        raise ValueError("Для удержания укажите происхождение времени hold_timing; длительность команды не заменяет контакт")
    count = round(duration * fps)
    if count < 1:
        raise ValueError("Удержание короче одного представимого кадра финального видео; проверьте время контакта")
    return list(range(anchor_frame)) + [anchor_frame] * count + list(range(anchor_frame + 1, frame_count)), count


def write_derived_sequence(pattern, start_number, frames, directory):
    """Reuse immutable source PNG bytes, adding only contact-frame references."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for index, source_index in enumerate(frames):
        source = Path(pattern % (start_number + source_index)).resolve()
        destination = directory / f"tap_{index:05d}.png"
        if destination.exists():
            destination.unlink()
        try:
            os.link(source, destination)
        except OSError:
            shutil.copyfile(source, destination)
    return str(directory / "tap_%05d.png")
