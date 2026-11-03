#!/usr/bin/env python3
"""Read-only dependency and asset checks; never launches a simulator or installs tools."""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "profiles/telegram-product.json"


def diagnose(config_path, record=False, axe=None, tap_sequence=None):
    checks = []

    def check(name, passed, detail, action, required=True):
        checks.append({"name": name, "ok": bool(passed), "required": required,
                       "detail": str(detail), "action": None if passed else action})

    image_module = None
    for module, package in (("numpy", "numpy"), ("PIL", "Pillow")):
        try:
            installed = importlib.import_module(module)
            check(package, True, installed.__version__, "")
            if module == "PIL":
                image_module = importlib.import_module("PIL.Image")
        except (ImportError, OSError) as error:
            check(package, False, error, "Install the packages declared in requirements.txt in the selected Python environment.")

    for command in ("ffmpeg", "ffprobe"):
        resolved = shutil.which(command)
        check(command, resolved, resolved or "not found on PATH", f"Provide {command} on PATH.")
    xcrun = shutil.which("xcrun")
    check("xcrun", xcrun, xcrun or "not found on PATH", "Select an Xcode installation before recording.", record)
    axe_path = Path(axe).expanduser() if axe else None
    resolved_axe = str(axe_path.resolve()) if axe_path and axe_path.is_file() else (shutil.which(axe) if axe else shutil.which("axe"))
    executable_axe = resolved_axe and os.access(resolved_axe, os.X_OK)
    check("axe", executable_axe, resolved_axe or "needed for native actions when recording",
          "Pass --axe /path/to/axe or provide the executable on PATH before recording.", record)

    config_path = Path(config_path).resolve()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("expected a JSON object")
        check("profile", True, config_path, "")

        def resolve(value):
            path = Path(value)
            return ((config_path.parent / path) if config.get("path_base") == "config" else path).resolve()

        assets = {"device": config["device"]["source"],
                  "provenance": config["background"]["source"]}
        pattern = config["background"].get("pattern")
        if pattern:
            assets["pattern"] = pattern
        for name, value in assets.items():
            path = resolve(value)
            passed = path.is_file()
            detail = str(path)
            if passed and image_module is not None and name in ("device", "pattern") and path.suffix.lower() != ".svg":
                try:
                    with image_module.open(path) as image:
                        image.verify()
                        if name == "device" and list(image.size) != config["device"]["source_size"]:
                            raise ValueError("device dimensions differ from profile")
                except (OSError, ValueError) as error:
                    passed, detail = False, f"{path}: {error}"
            check(name, passed, detail, "Restore the asset named by the selected profile.")

        provenance = resolve(config["background"]["source"])
        if config.get("path_base") == "config" and provenance.is_file():
            data = json.loads(provenance.read_text(encoding="utf-8"))
            mismatches = []
            for entry in data.get("assets", []):
                path = provenance.parent / entry["file"]
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    mismatches.append(entry["file"])
            check("asset_hashes", not mismatches, mismatches or "bundled checksums match",
                  "Restore the bundled assets from the same showtime version.")

        if pattern and Path(pattern).suffix.lower() == ".svg" and config["background"].get("pattern_opacity", 0) > 0:
            check("node", shutil.which("node"), shutil.which("node") or "SVG pattern needs Node",
                  "Provide Node and sharp for this legacy SVG profile, or use the portable PNG profile.")
            sharp = config["background"].get("sharp_module")
            check("sharp", sharp and resolve(sharp).exists(), sharp or "missing sharp_module",
                  "Set background.sharp_module to the installed sharp package.")

        caption_allowed = config.get("policy", {}).get("editorial_text") is not False or not config.get("caption")
        check("editorial_text_policy", caption_allowed, "caption configuration checked",
              "Remove caption from the product profile.")
        sequence = str(Path(tap_sequence).resolve()) if tap_sequence else str(resolve(config.get("tap", {}).get("sequence", str(ROOT / "assets/tap/native/tap_%05d.png"))))
        tap = config["tap"]
        missing_frames = []
        for index in range(tap["start_number"], tap["start_number"] + tap["frames"]):
            path = Path(sequence % index)
            if not path.is_file():
                missing_frames.append(str(path))
            elif image_module is not None:
                try:
                    with image_module.open(path) as image:
                        if image.size != (1080, 1080) or "A" not in image.getbands():
                            raise ValueError("expected 1080x1080 with alpha")
                        image.verify()
                except (OSError, ValueError):
                    missing_frames.append(str(path))
        check("tap_frames", not missing_frames, missing_frames or f"{tap['frames']} frames available",
              "Restore the original tap sequence or pass --tap-sequence with its PNG pattern.")
    except (OSError, ValueError, KeyError, TypeError) as error:
        check("profile_assets", False, error, "Fix the selected profile and its asset paths.")

    missing = [{"name": entry["name"], "action": entry["action"]}
               for entry in checks if entry["required"] and not entry["ok"]]
    return {"ok": not missing, "config": str(config_path), "recording_requested": record,
            "checks": checks, "missing": missing,
            "next_action": missing[0]["action"] if missing else "Dependencies are available; continue preparing the scene."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--record", action="store_true", help="Require recording dependencies as well as rendering dependencies")
    parser.add_argument("--axe", help="Path to the native action executable")
    parser.add_argument("--tap-sequence", help="Optional PNG pattern, for example /path/tap_%%05d.png")
    args = parser.parse_args()
    result = diagnose(args.config, args.record, args.axe, args.tap_sequence)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
