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
        raise ValueError("run.json должен быть объектом с schema_version: 1")
    for key in ("id", "app_path", "next_step"):
        if not _text(run.get(key)):
            raise ValueError(f"run.json: требуется непустая строка {key}")
    if not Path(run["app_path"]).is_absolute():
        raise ValueError("run.json: app_path должен быть абсолютным путём")
    if "simulator" not in run or (run["simulator"] is not None and not _text(run["simulator"])):
        raise ValueError("run.json: simulator должен быть строкой или null")
    if run.get("status") not in STATUSES:
        raise ValueError("run.json: неизвестный status")
    artifacts = run.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("run.json: artifacts должен быть объектом")
    for name, path in artifacts.items():
        if not _text(name) or not _text(path) or "\x00" in path or Path(path).is_absolute():
            raise ValueError("run.json: artifacts содержит неверное имя или относительный путь")
    return run


def _load(run_dir):
    path = Path(run_dir).resolve() / "run.json"
    try:
        return _validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as error:
        raise ValueError(f"Нет {path}; сначала выполните init") from error
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f"Некорректный JSON в {path}: {error}") from error


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
                raise ValueError(f"{destination} уже существует; init не перезаписывает запуск") from error
        else:
            os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def init_run(run_dir, app_path, simulator=None):
    run_dir = Path(run_dir).resolve()
    app_path = Path(app_path).expanduser().resolve(strict=True)
    if not app_path.is_dir():
        raise ValueError(f"Путь приложения должен быть каталогом: {app_path}")
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
            raise ValueError("Артефакт задаётся как NAME=PATH")
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = run_dir / target
        target = target.resolve(strict=True)
        if not target.is_file():
            raise ValueError(f"Артефакт должен быть файлом: {target}")
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
    init = commands.add_parser("init", help="Создать отдельный запуск без перезаписи")
    init.add_argument("--run-dir", required=True, type=Path)
    init.add_argument("--app-path", required=True, type=Path)
    init.add_argument("--simulator")
    note = commands.add_parser("note", help="Сохранить прогресс; не выдаёт допуск к записи")
    note.add_argument("--run-dir", required=True, type=Path)
    note.add_argument("--status", required=True, choices=STATUSES)
    note.add_argument("--next-step", required=True)
    note.add_argument("--artifact", action="append", default=[], metavar="NAME=PATH",
                      help="Существующий файл; относительный путь от --run-dir; можно повторять")
    status = commands.add_parser("status", help="Прочитать прогресс и наличие файлов")
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
