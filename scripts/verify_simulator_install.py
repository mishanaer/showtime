#!/usr/bin/env python3
"""Compare an artifact with one explicitly selected Simulator installation.

Read-only simctl queries and SHA-256 checks; never installs, launches, stops, or
uninstalls an app. Exit 0 means all on-disk executable bytes and app identifiers
match, 1 means a mismatch, and 2 means verification could not be completed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import uuid


APP_FIELDS = (
    "CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleVersion",
    "CFBundleExecutable",
)


def read_info(bundle):
    info_path = bundle / "Info.plist"
    if not info_path.is_file():
        info_path = bundle / "Resources/Info.plist"
    with info_path.open("rb") as stream:
        info = plistlib.load(stream)
    if not isinstance(info, dict):
        raise ValueError("Invalid bundle Info.plist: " + str(info_path))
    return info


def app_metadata(app):
    info = read_info(app)
    for key in APP_FIELDS:
        if not isinstance(info.get(key), str) or not info[key]:
            raise ValueError("Missing or invalid " + key + " in " + str(app))
    return {key: info[key] for key in APP_FIELDS}


def executable_path(bundle, info, app):
    name = info.get("CFBundleExecutable")
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
        raise ValueError("Invalid CFBundleExecutable in " + str(bundle))
    executable = bundle / name
    if not executable.resolve().is_relative_to(app.resolve()):
        raise ValueError("Executable resolves outside the app: " + str(executable))
    return executable


def binary_inventory(app, info):
    main = executable_path(app, info, app)
    binaries = {main.relative_to(app).as_posix(): ("main", main)}
    for bundle in sorted(app.rglob("*")):
        if bundle.is_dir() and bundle.suffix in (".framework", ".appex", ".app"):
            executable = executable_path(bundle, read_info(bundle), app)
            binaries[executable.relative_to(app).as_posix()] = (bundle.suffix[1:], executable)
        elif bundle.suffix == ".dylib" and bundle.is_file():
            if not bundle.resolve().is_relative_to(app.resolve()):
                raise ValueError("Library resolves outside the app: " + str(bundle))
            binaries[bundle.relative_to(app).as_posix()] = ("dylib", bundle)
    return binaries


def fingerprint(path):
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise ValueError("Executable changed while hashing: " + str(path))
        return {"sha256": digest.hexdigest(), "bytes": after.st_size, "mtime_ns": after.st_mtime_ns}
    except (OSError, ValueError) as error:
        return {"error": str(error)}


def compare_apps(artifact_app, installed_app):
    artifact_app, installed_app = Path(artifact_app).resolve(), Path(installed_app).resolve()
    artifact_info, installed_info = app_metadata(artifact_app), app_metadata(installed_app)
    metadata = [
        {"field": key, "artifact": artifact_info[key], "installed": installed_info[key],
         "match": artifact_info[key] == installed_info[key]}
        for key in APP_FIELDS
    ]
    artifact_files = binary_inventory(artifact_app, artifact_info)
    installed_files = binary_inventory(installed_app, installed_info)
    binaries = []
    for relative in sorted(artifact_files.keys() | installed_files.keys()):
        artifact = fingerprint(artifact_files[relative][1]) if relative in artifact_files else None
        installed = fingerprint(installed_files[relative][1]) if relative in installed_files else None
        match = bool(artifact and installed and "sha256" in artifact and "sha256" in installed
                     and artifact["sha256"] == installed["sha256"])
        binaries.append({"path": relative, "role": (artifact_files.get(relative) or installed_files[relative])[0],
                         "artifact": artifact, "installed": installed, "match": match})
    return {"passed": all(row["match"] for row in metadata + binaries),
            "artifact": {"app_path": str(artifact_app), **artifact_info},
            "installed": {"app_path": str(installed_app), **installed_info},
            "metadata": metadata, "binaries": binaries}


def simctl(*arguments):
    result = subprocess.run(["xcrun", "simctl", *arguments], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("simctl " + arguments[0] + " failed: " + result.stderr.strip())
    return result.stdout.strip()


def simulator_info(udid):
    inventory = json.loads(simctl("list", "devices", "--json"))
    matches = [dict(device, runtime=runtime)
               for runtime, devices in inventory["devices"].items()
               for device in devices if device.get("udid", "").upper() == udid]
    if len(matches) != 1:
        raise ValueError("Expected exactly one Simulator with UDID " + udid)
    device = matches[0]
    if not device.get("isAvailable", False):
        raise ValueError("Simulator runtime is unavailable for " + udid)
    return {key: device.get(key) for key in ("udid", "name", "state", "runtime", "isAvailable")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", "--artifact-app", type=Path, required=True)
    parser.add_argument("--simulator", "--udid", required=True, help="Exact Simulator UDID; 'booted' is not accepted")
    parser.add_argument("--output", "--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        udid = str(uuid.UUID(args.simulator)).upper()
    except ValueError:
        parser.error("--simulator must be an exact UUID")
    artifact = args.artifact.resolve()
    output = args.output.resolve()
    if output.is_relative_to(artifact):
        parser.error("The JSON report must be outside the artifact app")
    report = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
              "requested_udid": udid, "artifact_app": str(artifact), "passed": False}
    installed = None
    try:
        info = app_metadata(artifact)
        report["artifact"] = {"app_path": str(artifact), **info}
        report["simulator"] = simulator_info(udid)
        installed = Path(simctl("get_app_container", udid, info["CFBundleIdentifier"], "app")).resolve()
        if output.is_relative_to(installed):
            parser.error("The JSON report must be outside the installed app")
        report.update(compare_apps(artifact, installed))
        report["status"] = "passed" if report["passed"] else "mismatch"
        code = 0 if report["passed"] else 1
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError, plistlib.InvalidFileException) as error:
        report.update(status="error", error=str(error))
        code = 2
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output)
    summary = {"status": report["status"], "simulator": udid,
               "binaries_checked": len(report.get("binaries", [])), "report": str(output)}
    if "error" in report:
        summary["error"] = report["error"]
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
