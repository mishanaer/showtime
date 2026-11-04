#!/usr/bin/env python3
"""Run one AXe batch action and journal its HID dispatch, relative to a recording.

Dispatch log timestamps approximate touch timing; they do not measure device
touch-down. Coordinates must come from a current native UI observation.
type_text sends printable US ASCII characters in one batch with inter-character
pauses; actual duration also includes HID dispatch overhead. Keyboard suggestion
settings and caret hiding must be prepared separately in the app or Simulator;
this HID route does not control the field's caret appearance.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import time
import shlex
from media_io import file_lock, save_json


def capture_reference(clock_path, clock):
    """Stable recording identity; completion fields deliberately stay outside it."""
    clock_path = Path(clock_path).resolve()
    output = clock.get("output")
    if output is None:
        # Older clock files may predate the explicit output/request fields.
        output = clock_path.with_suffix("").with_suffix(".mp4")
    if not isinstance(output, (str, Path)) or not str(output):
        raise ValueError("Recording clock has no valid output path")
    if not isinstance(clock.get("simulator"), str) or not clock["simulator"]:
        raise ValueError("Recording clock has no simulator identity")
    for name in ("ready_ms", "request_ms"):
        value = clock.get(name)
        if (value is None and name == "request_ms"):
            continue
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Recording clock has no valid " + name)
    return {"clock_path": str(clock_path),
            "output": str((clock_path.parent / output).resolve()),
            "simulator": clock["simulator"], "request_ms": clock.get("request_ms"),
            "ready_ms": clock["ready_ms"]}


def prepare_journal(clock_path, clock, journal=None, viewport=None):
    """Validate identity and point geometry before dispatching any action."""
    reference = capture_reference(clock_path, clock)
    if viewport is not None:
        if len(viewport) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                                     for v in viewport):
            raise ValueError("Viewport width and height must be positive finite numbers")
        requested = dict(zip(("width", "height"), viewport))
    else:
        requested = None
    if journal is None:
        journal = {"coordinate_space": requested or {"width": 402, "height": 874},
                   "clock_note": "AXe HID dispatch log timestamps; not measured physical touch-down. Native UI timing review is required.",
                   "events": []}
    if not isinstance(journal, dict) or not isinstance(journal.get("events"), list):
        raise ValueError("Journal must contain an events list")
    space = journal.get("coordinate_space")
    if not isinstance(space, dict) or any(type(space.get(k)) not in (int, float)
                                         or not math.isfinite(space[k]) or space[k] <= 0
                                         for k in ("width", "height")):
        raise ValueError("Journal has no valid coordinate_space")
    if requested is not None and any(space[k] != requested[k] for k in requested):
        raise ValueError("Viewport differs from the existing journal; use its original geometry or a new journal")
    if "capture_ref" in journal and journal["capture_ref"] != reference:
        raise ValueError("Journal belongs to another recording clock; use a new journal for this take")
    if "capture_ref" not in journal and journal["events"]:
        # Keep in-flight legacy journals usable without claiming old events were bound.
        journal["legacy_unbound_event_count"] = len(journal["events"])
    journal["capture_ref"] = reference
    return journal


def character_steps(text, delay):
    """Build AXe batch steps, never shell code or one process per character."""
    if not text:
        raise ValueError("The text file is empty. Add the text to type.")
    if any(not 32 <= ord(character) <= 126 for character in text):
        raise ValueError("AXe type accepts printable US ASCII, including spaces. "
                         "Unicode, line breaks, and tabs require a different input method. "
                         "Use character-by-character input in the app or a verified driver "
                         "that types each character.")
    if type(delay) not in (int, float) or not math.isfinite(delay) or delay <= 0:
        raise ValueError("The delay between characters must be a positive finite number of seconds")
    steps = []
    for index, character in enumerate(text):
        if index:
            steps.append(f"sleep {delay:g}")
        steps.append("type " + shlex.quote(character))
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axe", type=Path, required=True)
    parser.add_argument("--clock", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--type", choices=["tap", "long_press", "swipe", "paste", "type_text"], required=True)
    parser.add_argument("--tap-style", choices=["physical", "simulator"], default="physical",
                        help="AXe tap dispatch mode; simulator is allowed only for a tap step")
    parser.add_argument("--point", type=float, nargs=2)
    parser.add_argument("--viewport", type=float, nargs=2, metavar=("WIDTH", "HEIGHT"),
                        help="Native viewport in points; defaults to 402x874 for new legacy journals")
    parser.add_argument("--step", help="AXe step; required except for --type type_text")
    parser.add_argument("--text-file", type=Path, help="UTF-8 file with printable US ASCII for type_text")
    parser.add_argument("--character-delay", type=float, default=1 / 30,
                        help="Pause between characters, plus dispatch overhead; default: 1/30 second (30 characters/sec)")
    args = parser.parse_args()
    text = None
    text = ""
    if args.type == "type_text":
        if args.text_file is None or args.step is not None or args.point is not None:
            parser.error("Use --text-file for type_text and omit --step and --point")
        try:
            text = args.text_file.read_text(encoding="utf-8")
            steps = character_steps(text, args.character_delay)
        except (OSError, UnicodeError, ValueError) as error:
            parser.error(str(error))
    else:
        if not args.step or args.text_file is not None:
            parser.error("This action needs --step. Use --text-file only with type_text.")
        steps = [args.step]
    if args.tap_style == "simulator":
        if args.type != "tap":
            parser.error("Use simulator mode only with --type tap")
        try:
            tokens = shlex.split(args.step)
        except ValueError as error:
            parser.error("Invalid AXe command: " + str(error))
        if not tokens or tokens[0] != "tap":
            parser.error("Simulator mode requires an AXe tap command")
    if args.type == "long_press":
        tokens = shlex.split(args.step)
        if not tokens or tokens[0] != "touch" or "--down" not in tokens or "--up" not in tokens:
            parser.error("Use one touch command with --down and --up for a hold")
    with file_lock(args.journal.with_suffix(".lock")):
        dispatch_action(args, steps, parser, text)


def dispatch_action(args, steps, parser, text):
    clock = json.loads(args.clock.read_text())
    if clock.get("ready_ms") is None or clock.get("exit_code") is not None or clock.get("stop_requested_ms") is not None:
        raise SystemExit("Recording must be running and ready")
    journal = json.loads(args.journal.read_text()) if args.journal.exists() else None
    try:
        journal = prepare_journal(args.clock, clock, journal, args.viewport)
    except ValueError as error:
        parser.error(str(error))
    if any(event["id"] == args.id for event in journal["events"]):
        raise SystemExit("Duplicate action id")
    if args.type in ("tap", "long_press"):
        tokens = shlex.split(args.step)
        expected = "tap" if args.type == "tap" else "touch"
        try:
            if tokens[0] != expected or tokens.count("-x") != 1 or tokens.count("-y") != 1:
                raise ValueError("Supply one pair of -x and -y coordinates for the touch")
            point = [float(tokens[tokens.index(axis) + 1]) for axis in ("-x", "-y")]
            bounds = journal["coordinate_space"]
            if not (0 <= point[0] < bounds["width"] and 0 <= point[1] < bounds["height"]):
                raise ValueError("The touch falls outside the screen")
            if args.point is not None and point != args.point:
                raise ValueError("The ring coordinates differ from the touch coordinates")
            args.point = point
        except (ValueError, IndexError) as error:
            parser.error(str(error))
    started = time.time_ns()/1_000_000
    command = [str(args.axe), "batch", "--udid", clock["simulator"],
               "--tap-style", args.tap_style, "--verbose"]
    for step in steps:
        command.extend(("--step", step))
    process = subprocess.Popen(command,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    for line in process.stdout:
        lines.append({"ms": time.time_ns()/1_000_000, "text": line.rstrip()})
    code = process.wait()
    ended = time.time_ns()/1_000_000
    dispatch = [line["ms"] for line in lines if line["text"].startswith("Sending ")]
    completed = [line["ms"] for line in lines if line["text"] == "HID event performed successfully."]
    log_path = args.journal.with_name(args.journal.stem+"-"+args.id+".dispatch.json")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({"step": args.step, "steps": steps, "tap_style": args.tap_style, "command_start_ms": started,
        "command_end_ms": ended, "exit_code": code, "lines": lines}, indent=2)+"\n")
    if code or not dispatch or not completed:
        raise SystemExit("Action failed or lacks HID dispatch evidence: "+str(log_path))
    event = {"id": args.id, "type": args.type, "tap_style": args.tap_style,
        "source_start": (dispatch[0]-clock["ready_ms"])/1000,
        "source_end": (completed[-1]-clock["ready_ms"])/1000,
        "alignment": "axe_hid_dispatch_log; physical_touch_down_not_measured",
        "dispatch_log": str(log_path.resolve())}
    if args.type == "type_text":
        event.update(character_count=len(text), character_delay_seconds=args.character_delay,
                     typing_timing="Inter-character pause plus HID overhead; source span comes from dispatch logs")
    if args.type == "long_press":
        if (len(dispatch) != 2 or len(completed) != 2 or dispatch[1] <= dispatch[0]
                or not dispatch[0] <= completed[0] <= dispatch[1] <= completed[1]):
            raise SystemExit("The driver did not confirm both down and up. The hold was rejected.")
        event.update(contact_start=(dispatch[0]-clock["ready_ms"])/1000,
                     contact_end=(dispatch[1]-clock["ready_ms"])/1000,
                     contact_timing="hid_dispatch_estimate")
    if args.point is not None:
        event["point"] = args.point
    journal["events"].append(event)
    save_json(args.journal, journal)
    print(json.dumps(event))


if __name__ == "__main__":
    main()
