#!/usr/bin/env python3
"""Keep one video's progress in run.json; this is not an approval gate.

Artifact paths are relative to --run-dir, including relative CLI inputs.
Statuses are agent notes, not verified milestones. review_gate.py remains
responsible for checking scenario and prepared-content approvals.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from media_io import file_lock


STATUSES = ("planning", "preparing", "ready-to-record", "recorded", "editing", "rendered", "delivered")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _validate(run):
    if not isinstance(run, dict) or type(run.get("schema_version")) is not int or run["schema_version"] != 1:
        raise ValueError("Use a JSON object with schema_version 1 for run.json")
    for key in ("id", "app_path", "next_step"):
        if not _text(run.get(key)):
            raise ValueError(f"Set {key} in run.json to a nonempty string")
    if not Path(run["app_path"]).is_absolute():
        raise ValueError("Use an absolute path for app_path in run.json")
    if "simulator" not in run or (run["simulator"] is not None and not _text(run["simulator"])):
        raise ValueError("Set simulator in run.json to a string or null")
    if run.get("status") not in STATUSES:
        raise ValueError("Unknown status in run.json")
    artifacts = run.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Set artifacts in run.json to an object")
    for name, path in artifacts.items():
        if not _text(name) or not _text(path) or "\x00" in path or Path(path).is_absolute():
            raise ValueError("Check the artifact name and relative path in run.json")
    return run


def _load(run_dir):
    path = Path(run_dir).resolve() / "run.json"
    try:
        return _validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as error:
        raise ValueError(f"Run init first to create {path}") from error
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f"Invalid JSON in {path}: {error}") from error


def _save(run_dir, run, create=False):
    """Publish a complete JSON atomically; init never replaces an existing file."""
    destination = Path(run_dir) / "run.json"
    fd, temporary = tempfile.mkstemp(prefix=".run-", suffix=".json", dir=run_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(run, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            try:
                os.link(temporary, destination)
            except FileExistsError as error:
                raise ValueError(f"{destination} already exists. Run init in a new directory.") from error
        else:
            os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def init_run(run_dir, app_path, simulator=None):
    run_dir = Path(run_dir).resolve()
    app_path = Path(app_path).expanduser().resolve(strict=True)
    if not app_path.is_dir():
        raise ValueError(f"Choose an app directory: {app_path}")
    run = _validate({
        "schema_version": 1,
        "id": str(uuid.uuid4()),
        "app_path": str(app_path),
        "simulator": simulator,
        "status": "planning",
        "next_step": "Prepare the scenario.",
        "artifacts": {},
    })
    run_dir.mkdir(parents=True, exist_ok=True)
    _save(run_dir, run, create=True)
    return run


def note_run(run_dir, status, next_step, artifacts=()):
    run_dir = Path(run_dir).resolve()
    with file_lock(run_dir / ".run.lock"):
        return _note_locked(run_dir, status, next_step, artifacts)


def _note_locked(run_dir, status, next_step, artifacts):
    run = _load(run_dir)
    run.update(status=status, next_step=next_step)
    for item in artifacts:
        name, separator, path = item.partition("=")
        if not separator or not _text(name) or not _text(path):
            raise ValueError("Specify an artifact as NAME=PATH")
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = run_dir / target
        target = target.resolve(strict=True)
        if not target.is_file():
            raise ValueError(f"Choose an existing file for this artifact: {target}")
        run["artifacts"][name] = os.path.relpath(target, run_dir)
    _validate(run)
    _save(run_dir, run)
    return run


def status_run(run_dir):
    run_dir = Path(run_dir).resolve()
    run = _load(run_dir)
    return {
        "run": run,
        "artifact_exists": {name: (run_dir / path).is_file() for name, path in run["artifacts"].items()},
        "status_is_progress_only": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a new run")
    init.add_argument("--run-dir", required=True, type=Path)
    init.add_argument("--app-path", required=True, type=Path)
    init.add_argument("--simulator")
    note = commands.add_parser("note", help="Save progress without approving a recording")
    note.add_argument("--run-dir", required=True, type=Path)
    note.add_argument("--status", required=True, choices=STATUSES)
    note.add_argument("--next-step", required=True)
    note.add_argument("--artifact", action="append", default=[], metavar="NAME=PATH",
                      help="Existing file relative to --run-dir. Repeat to add more files.")
    status = commands.add_parser("status", help="Show run progress and check which files exist")
    status.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = init_run(args.run_dir, args.app_path, args.simulator)
        elif args.command == "note":
            result = note_run(args.run_dir, args.status, args.next_step, args.artifact)
        else:
            result = status_run(args.run_dir)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
