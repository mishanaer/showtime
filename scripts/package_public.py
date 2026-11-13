#!/usr/bin/env python3
"""Create a fresh source distribution with the original showtime assets (no doodle pattern)."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
TAP_README = "# Tap ring\n\n`native/tap_%05d.png`: 41 RGBA PNG frames (0–40), 1080×1080, straight alpha, 60 fps, exported once from After Effects. Production needs no After Effects or conversion.\n\n- **Timing:** frame 25 is the squeeze after the expansion; align it with the contact. Entry starts 25/60 s earlier. Visible alpha ends after frame 34; keep all 41 frames.\n- **Color:** set `tap.tint_rgb` in the profile, or `tint_rgb` per event (for example `[255,255,255]` for white). The renderer recolors each frame's alpha mask without changing the motion. `monochrome_rgb` validates the source pixels; do not change it to recolor the output.\n- **Size:** `maximum_diameter_fraction` of the screen width (0.485 by default).\n- **Backdrop blur:** the compositor blurs the live video under the ring (`backdrop_sigma`, 42 at 1920×1920). It cannot be baked into the frames.\n- **Holds:** repeat only the contact frame for the hold interval; keep the original entry and release frames.\n"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def build(destination):
    destination.mkdir(parents=True, exist_ok=False)
    sources = ["README.md", "SKILL.md", "requirements.txt", "agents/openai.yaml",
               "docs/install.md", "docs/preparation.md", "docs/showtime-execution.md", "docs/review-workflow.md",
               "docs/visual-acceptance.md", "profiles/telegram-product.json",
               "scripts/simulator/typing-driver.m"]
    for path in sorted((ROOT / "scripts").glob("*.py")):
        if path.name not in {"edit_pilot.py", "tap-soften.py", "tap-white-variant.py"}:
            sources.append(path.relative_to(ROOT))
    sources.extend(path.relative_to(ROOT) for path in sorted((ROOT / "tests").glob("test_*.py")))
    for path in sources:
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)
    (destination / ".gitignore").write_text(".venv/\n__pycache__/\n.DS_Store\noutputs/\n")
    art = destination / "assets/showtime"
    (art / "phones").mkdir(parents=True)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    catalog = json.loads((ROOT / "assets/showtime/phones.json").read_text())
    for phone in catalog["phones"]:
        path = art / phone["source"]
        shutil.copyfile(ROOT / "assets/showtime" / phone["source"], path)
        phone["sha256"] = digest(path)
    write_json(art / "phones.json", {"schema_version": 1, "source": "showtime phone mockups by mishanaer",
        "note": "Paths are relative to this catalog. Copy device fields into the run profile with resolved asset paths. "
                "Model names identify the mockup; simulator availability must be verified separately.",
        "phones": catalog["phones"]})
    gradients = json.loads((ROOT / "assets/showtime/gradients.json").read_text())["gradients"]
    write_json(art / "gradients.json", {"schema_version": 1, "source": "showtime background palettes by mishanaer",
        "note": "Set background.palette in a run-specific profile.",
        "gradients": [{"id": g["id"], "label": g["label"], "palette": g["palette"]} for g in gradients]})
    tap = destination / "assets/tap/native"
    tap.mkdir(parents=True)
    for frame in sorted((ROOT / "assets/tap/native").glob("tap_*.png")):
        shutil.copyfile(frame, tap / frame.name)
    (tap.parent / "README.md").write_text(TAP_README)
    write_json(art / "provenance.json", {"schema_version": 1,
        "source": "Original showtime assets by mishanaer: phone mockups, background palettes and the After Effects tap ring export.",
        "assets": [{"file": str(path.relative_to(art)), "sha256": digest(path)} for path in sorted(art.rglob("*.png"))],
        "tap": [{"file": str(path.relative_to(destination)), "sha256": digest(path)} for path in sorted(tap.glob("*.png"))]})
    profile_path = destination / "profiles/telegram-product.json"
    profile = json.loads(profile_path.read_text())
    # The doodle pattern is not part of the public distribution.
    for key in ("pattern", "pattern_name", "pattern_blend", "pattern_opacity"):
        profile["background"].pop(key, None)
    pastel = next(g["palette"] for g in gradients if g["id"] == "pastel")
    profile["background"].update(palette=pastel, palette_note="showtime Pastel. Starting configuration; "
                                 "choose a palette from assets/showtime/gradients.json for each new video.")
    profile["comparison"]["labels"] = ["Telegram original", "showtime app recording"]
    profile["tap"]["tint_note"] = ("Tint for the bundled After Effects tap ring, calibrated on a light app background; "
                                   "native alpha and timing are preserved.")
    profile["notes"] = ["Keep the full native viewport; crop the composed phone at the canvas boundary.",
                        "Only application text is visible in product videos."]
    write_json(profile_path, profile)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New directory")
    build(parser.parse_args().destination)
