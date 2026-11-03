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
        raise ValueError("Для файла нужны path и SHA-256 из 64 строчных hex-символов")
    return entry["path"], entry["sha256"]


def _positive(value, label):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{label}: требуется положительное конечное число")


def check_preparation(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = _read_json(manifest_path)
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 2:
        raise ValueError("Подготовка требует schema_version: 2")
    if manifest.get("kind") != "prepared-content":
        raise ValueError("Ожидается kind: prepared-content")
    content = manifest.get("content")
    preparation = content.get("preparation") if isinstance(content, dict) else None
    if not isinstance(preparation, dict):
        raise ValueError("Нужен объект content.preparation")
    viewport = preparation.get("viewport")
    if not isinstance(viewport, dict):
        raise ValueError("Нужен preparation.viewport с полной шириной и высотой в points")
    for dimension in ("width", "height"):
        _positive(viewport.get(dimension), f"viewport.{dimension}")
    _positive(preparation.get("visible_height"), "visible_height")
    if preparation["visible_height"] > viewport["height"]:
        raise ValueError("visible_height не может превышать полную viewport.height")
    for field in ("keyboard_visible", "typing_visible"):
        if type(preparation.get(field)) is not bool:
            raise ValueError(f"preparation.{field} должен быть true или false")
    checks = preparation.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(CHECKS):
        raise ValueError("preparation.checks должен содержать пять проверок: " + ", ".join(CHECKS))
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("В manifest требуется список files")
    declared = {}
    for entry in files:
        path, digest = _file_entry(entry)
        resolved = (manifest_path.parent / path).resolve()
        if resolved in declared:
            raise ValueError(f"Файл повторяется в files: {path}")
        declared[resolved] = digest

    # A local preset name is insufficient: verify the actual stage geometry.
    framing = preparation.get("framing")
    if not isinstance(framing, list) or not framing:
        raise ValueError("framing: приложите геометрию каждого использованного кадрирования")
    from compose_pilot import PRODUCT_LAYOUTS, ROOT, geometry, use_layout
    product = _read_json(ROOT / "profiles/telegram-product.json")
    catalog = _read_json(ROOT / "assets/showtime/phones.json")
    phones = {phone["id"]: phone for phone in catalog["phones"]}
    for shot in framing:
        if not isinstance(shot, dict) or shot.get("layout") not in PRODUCT_LAYOUTS:
            raise ValueError("framing: разрешены только crop-top, crop-bottom и full")
        phone = phones.get(shot.get("device_id"))
        if phone is None:
            raise ValueError("framing: неизвестный макет телефона")
        path, digest = _file_entry(shot.get("geometry"))
        resolved = (manifest_path.parent / path).resolve()
        if declared.get(resolved) != digest or file_sha256(resolved) != digest:
            raise ValueError("framing: файл геометрии отсутствует в files или изменился")
        expected_config = {**product, "device": {**product["device"], **phone}}
        expected = geometry(use_layout(expected_config, shot["layout"]))
        actual = _read_json(resolved)
        actual = actual.get("geometry", actual)
        if not isinstance(actual, dict) or any(actual.get(k) != expected[k] for k in ("device", "screen")):
            raise ValueError("framing: геометрия сцены не соответствует " + shot["layout"])

    verified = {}
    for name in CHECKS:
        check = checks[name]
        if not isinstance(check, dict) or not _text(check.get("note")):
            raise ValueError(f"{name}: требуется объект проверки с непустым note")
        status = check.get("status")
        if status not in ("ready", "not_applicable"):
            raise ValueError(f"{name}: status должен быть ready или not_applicable")
        if status == "not_applicable":
            allowed = ((name == "keyboard_suggestions" and not preparation["keyboard_visible"])
                       or (name == "character_typing" and not preparation["typing_visible"]))
            if not allowed:
                raise ValueError(f"{name}: not_applicable недопустим для этой сцены")
        if name == "character_typing" and preparation["typing_visible"] and status == "ready":
            if check.get("visible_keypresses") is not False:
                raise ValueError("character_typing: проверьте весь ввод в движении; "
                                 "требуется visible_keypresses: false (без подсветки, попапов и колец на клавишах)")
        evidence = check.get("evidence")
        if not isinstance(evidence, list) or (status == "ready" and not evidence):
            raise ValueError(f"{name}: нужен список evidence; ready требует хотя бы один файл")
        motion_found = False
        for entry in evidence:
            path, digest = _file_entry(entry)
            resolved = (manifest_path.parent / path).resolve()
            if declared.get(resolved) != digest:
                raise ValueError(f"{name}: evidence отсутствует в files или имеет другой SHA-256: {path}")
            extension = Path(path).suffix.lower()
            if extension not in MEDIA:
                raise ValueError(f"{name}: evidence должен быть кадром или видео: {path}")
            motion_found = motion_found or extension in MOTION
            if resolved not in verified:
                verified[resolved] = file_sha256(resolved)
            if verified[resolved] != digest:
                raise ValueError(f"{name}: файл evidence изменился: {path}; обновите материалы и хеш")
        if status == "ready" and name in ("full_height", "character_typing") and not motion_found:
            raise ValueError(f"{name}: нужно превью движения (mp4, mov, webm или gif)")

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
