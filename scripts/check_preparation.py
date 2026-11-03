#!/usr/bin/env python3
"""Check preparation evidence before review, without approving or changing files.

This checks declarations, media extensions and file hashes. It does not inspect
the depicted UI or prove visual quality. A partial prepared-content v2 manifest
without story_review is accepted; review_gate.py still checks later approvals.
"""

import argparse
import json
import math
from pathlib import Path
import re
import sys

from review_gate import _read_json, file_sha256


CHECKS = ("full_height", "visible_controls", "keyboard_suggestions", "character_typing", "no_editorial_text")
MOTION = {".mp4", ".mov", ".webm", ".gif"}
MEDIA = MOTION | {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic", ".avif"}
HASH = re.compile(r"[0-9a-f]{64}\Z")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _file_entry(entry):
    if (not isinstance(entry, dict) or not _text(entry.get("path"))
            or "\x00" in entry["path"] or not isinstance(entry.get("sha256"), str)
            or HASH.fullmatch(entry["sha256"]) is None):
        raise ValueError("Each file needs a path and a 64-character lowercase SHA-256 hash")
    return entry["path"], entry["sha256"]


def _positive(value, label):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{label} must be a positive finite number")


def check_preparation(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = _read_json(manifest_path)
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 2:
        raise ValueError("Preparation requires schema_version: 2")
    if manifest.get("kind") != "prepared-content":
        raise ValueError("Set kind to prepared-content")
    content = manifest.get("content")
    preparation = content.get("preparation") if isinstance(content, dict) else None
    if not isinstance(preparation, dict):
        raise ValueError("Add a content.preparation object")
    viewport = preparation.get("viewport")
    if not isinstance(viewport, dict):
        raise ValueError("Set the full viewport width and height in points in preparation.viewport")
    for dimension in ("width", "height"):
        _positive(viewport.get(dimension), f"viewport.{dimension}")
    _positive(preparation.get("visible_height"), "visible_height")
    if preparation["visible_height"] > viewport["height"]:
        raise ValueError("visible_height exceeds viewport.height")
    for field in ("keyboard_visible", "typing_visible"):
        if type(preparation.get(field)) is not bool:
            raise ValueError(f"Set preparation.{field} to true or false")
    checks = preparation.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(CHECKS):
        raise ValueError("preparation.checks must include all five checks: " + ", ".join(CHECKS))
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("Add a files list to the manifest")
    declared = {}
    for entry in files:
        path, digest = _file_entry(entry)
        resolved = (manifest_path.parent / path).resolve()
        if resolved in declared:
            raise ValueError(f"Duplicate file in files: {path}")
        declared[resolved] = digest

    # A local preset name is insufficient: verify the actual stage geometry.
    framing = preparation.get("framing")
    if not isinstance(framing, list) or not framing:
        raise ValueError("Attach the geometry for each framing used")
    from compose_pilot import PRODUCT_LAYOUTS, ROOT, geometry, use_layout
    product = _read_json(ROOT / "profiles/telegram-product.json")
    catalog = _read_json(ROOT / "assets/showtime/phones.json")
    phones = {phone["id"]: phone for phone in catalog["phones"]}
    for shot in framing:
        if not isinstance(shot, dict) or shot.get("layout") not in PRODUCT_LAYOUTS:
            raise ValueError("Choose crop-top, crop-bottom, or full for framing")
        phone = phones.get(shot.get("device_id"))
        if phone is None:
            raise ValueError("Unknown phone mockup in framing")
        path, digest = _file_entry(shot.get("geometry"))
        resolved = (manifest_path.parent / path).resolve()
        if declared.get(resolved) != digest or file_sha256(resolved) != digest:
            raise ValueError("The geometry file is missing from files or has changed")
        expected_config = {**product, "device": {**product["device"], **phone}}
        expected = geometry(use_layout(expected_config, shot["layout"]))
        actual = _read_json(resolved)
        actual = actual.get("geometry", actual)
        if not isinstance(actual, dict) or any(actual.get(k) != expected[k] for k in ("device", "screen")):
            raise ValueError("The stage geometry does not match " + shot["layout"])

    verified = {}
    for name in CHECKS:
        check = checks[name]
        if not isinstance(check, dict) or not _text(check.get("note")):
            raise ValueError(f"{name} needs a check object with a nonempty note")
        status = check.get("status")
        if status not in ("ready", "not_applicable"):
            raise ValueError(f"Set {name}.status to ready or not_applicable")
        if status == "not_applicable":
            allowed = ((name == "keyboard_suggestions" and not preparation["keyboard_visible"])
                       or (name == "character_typing" and not preparation["typing_visible"]))
            if not allowed:
                raise ValueError(f"{name} applies to this scene. Use ready instead of not_applicable.")
        if name == "character_typing" and preparation["typing_visible"] and status == "ready":
            if check.get("visible_keypresses") is not False:
                raise ValueError("For character_typing, watch the entire typing sequence. "
                                 "Set visible_keypresses to false only if the keys show no highlights, popups, or touch rings.")
        evidence = check.get("evidence")
        if not isinstance(evidence, list) or (status == "ready" and not evidence):
            raise ValueError(f"{name} needs an evidence list. A ready check needs at least one file.")
        motion_found = False
        for entry in evidence:
            path, digest = _file_entry(entry)
            resolved = (manifest_path.parent / path).resolve()
            if declared.get(resolved) != digest:
                raise ValueError(f"{name} evidence is missing from files or its SHA-256 differs: {path}")
            extension = Path(path).suffix.lower()
            if extension not in MEDIA:
                raise ValueError(f"{name} evidence must be an image or video: {path}")
            motion_found = motion_found or extension in MOTION
            if resolved not in verified:
                verified[resolved] = file_sha256(resolved)
            if verified[resolved] != digest:
                raise ValueError(f"{name} evidence changed: {path}. Update the materials and hash.")
        if status == "ready" and name in ("full_height", "character_typing") and not motion_found:
            raise ValueError(f"{name} needs a motion preview (mp4, mov, webm, or gif)")

    return {
        "manifest": str(manifest_path),
        "preparation_complete": True,
        "visual_quality_automatically_verified": False,
        "checks": {name: checks[name]["status"] for name in CHECKS},
        "evidence_files_checked": len(verified),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = check_preparation(args.manifest)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"preparation_complete": False,
                          "visual_quality_automatically_verified": False,
                          "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
