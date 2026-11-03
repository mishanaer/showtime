"""Shared media/file operations, independent of historical pilot timelines."""

from contextlib import contextmanager
import fcntl
import hashlib
import tempfile
import json
from pathlib import Path
import subprocess


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def production_review(capture):
    clock_path = Path(capture).with_suffix(".clock.json")
    if not clock_path.is_file():
        timeline = clock_path.parent / "edit-timeline.json"
        if Path(capture).name == "edited-capture.mp4" and timeline.is_file():
            # Older edits predate derived clock files. Recover the original purpose.
            details = json.loads(timeline.read_text())
            original = Path(details["source"]).with_suffix(".clock.json")
            if original.is_file():
                return review_for_clock(json.loads(original.read_text()))
        return {}
    clock = json.loads(clock_path.read_text())
    return review_for_clock(clock)


def review_for_clock(clock):
    purpose = clock.get("purpose")
    if purpose is None:
        return {}  # Imported captures and older recording clocks
    review = clock.get("human_review", {})
    if purpose != "final" or review.get("story_materials_checked") is not True or clock.get("exit_code") != 0:
        raise ValueError("A final video needs a completed take with approved materials")
    return review


def run(command):
    subprocess.run([str(x) for x in command], check=True)


def probe(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate,duration,nb_frames,time_base:format=duration",
        "-of", "json", str(path),
    ]))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        staged = Path(directory) / "report.json"
        staged.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        staged.replace(path)
