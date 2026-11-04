#!/usr/bin/env python3
"""Record one explicitly selected Simulator and timestamp simctl's ready marker.

Send SIGINT to the recorder_pid in the clock file to stop and finalize the MP4.
The marker is an approximate video origin, not a measured physical touch clock.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from review_gate import check_review_gate, ReviewGateError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True,
                        help="Approved scenario")
    parser.add_argument("--prepared-content", type=Path,
                        help="Internally checked materials, required for a final recording; no second user approval")
    parser.add_argument("--approval-dir", type=Path, required=True,
                        help="Directory containing the recorded approval decisions")
    parser.add_argument("--preparation-preview", action="store_true",
                        help="Internal technical rehearsal only, after scenario approval; not a user review stage")
    parser.add_argument("--revision-file", type=Path,
                        help="Optional legacy correction receipt from a two-approval run")
    parser.add_argument("--tail-sentinel", action="store_true", help="On stop, preserve idle video tail with a late 9:42 status marker; restore 9:41 after finalizing")
    args = parser.parse_args()
    if args.preparation_preview and args.revision_file is not None:
        parser.error("Use --revision-file only for a final recording")
    if not args.preparation_preview and args.prepared_content is None:
        parser.error("A final recording requires --prepared-content")
    try:
        if args.preparation_preview:
            review = check_review_gate(args.scenario, None, args.approval_dir)
        elif args.revision_file is not None:
            review = check_review_gate(args.scenario, args.prepared_content, args.approval_dir,
                                       revision_file=args.revision_file)
        else:
            review = check_review_gate(args.scenario, args.prepared_content, args.approval_dir)
    except ReviewGateError as error:
        parser.error(str(error))
    output = args.output.resolve()
    if output.exists():
        parser.error("Output already exists; choose a new take name")
    output.parent.mkdir(parents=True, exist_ok=True)
    clock_path = output.with_suffix(".clock.json")
    clock = {"simulator": args.simulator, "output": str(output), "recorder_pid": os.getpid(),
             "human_review": review,
             "purpose": "preparation-preview" if args.preparation_preview else "final",
             "request_ms": time.time_ns()/1_000_000, "ready_ms": None, "stop_requested_ms": None,
             "tail_sentinel": args.tail_sentinel, "sentinel_requested_ms": None,
             "clock_note": "Video origin is simctl's Recording started log marker. Command timings and marker latency are not physical touch-down measurements."}

    def save():
        temporary = clock_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(clock, indent=2)+"\n")
        temporary.replace(clock_path)

    process = subprocess.Popen(["xcrun", "simctl", "io", args.simulator, "recordVideo", "--codec=h264", str(output)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    clock["simctl_pid"] = process.pid
    save()

    def stop(_number, _frame):
        if clock["stop_requested_ms"] is None:
            clock["stop_requested_ms"] = time.time_ns()/1_000_000
            save()
            try:
                if args.tail_sentinel:
                    # Stop only after recording the whole result/reading hold.
                    # This late marker forces simctl's VFR track to retain the
                    # preceding idle interval. Exclude the marker in observations.
                    time.sleep(3.0)
                    clock["sentinel_requested_ms"] = time.time_ns()/1_000_000
                    subprocess.run(["xcrun", "simctl", "status_bar", args.simulator, "override", "--time", "9:42"], check=True)
                    clock["sentinel_completed_ms"] = time.time_ns()/1_000_000
                    save()
                    time.sleep(.2)
            finally:
                process.send_signal(signal.SIGINT)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for line in process.stdout:
            if "Recording started" in line and clock["ready_ms"] is None:
                clock["ready_ms"] = time.time_ns()/1_000_000
                clock["ready_uptime_ns"] = time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)
                save()
            print(line.rstrip(), flush=True)
        code = process.wait()
    finally:
        if clock["sentinel_requested_ms"] is not None:
            subprocess.run(["xcrun", "simctl", "status_bar", args.simulator, "override", "--time", "9:41"], check=True)
    clock["exit_code"] = code
    clock["completed_ms"] = time.time_ns()/1_000_000
    clock["completed_uptime_ns"] = time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)
    if clock["ready_ms"] is not None:
        clock["wall_clock_drift_seconds"] = (clock["completed_ms"]-clock["ready_ms"])/1000 - (clock["completed_uptime_ns"]-clock["ready_uptime_ns"])/1_000_000_000
    save()
    if code != 0 or clock["ready_ms"] is None:
        raise SystemExit("Simulator recording did not complete successfully; inspect the clock/log")
    print(clock_path, flush=True)


if __name__ == "__main__":
    main()
