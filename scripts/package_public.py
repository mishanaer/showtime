#!/usr/bin/env python3
"""Create a fresh source distribution with original, procedurally drawn artwork."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
COLORS = {"Black": "#262a30", "Silver": "#c3c7cc", "White": "#e6e8eb",
          "Sage": "#9fae97", "Mist Blue": "#a6b6ca", "Glacier": "#a4c1ca",
          "Burgundy": "#874454", "Deep Blue": "#324967", "Cosmic Orange": "#ba694a",
          "Lavender": "#aaa0bc"}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def build(destination):
    destination.mkdir(parents=True, exist_ok=False)
    sources = ["README.md", "SKILL.md", "requirements.txt", "agents/openai.yaml",
               "docs/preparation.md", "docs/showtime-execution.md", "docs/review-workflow.md",
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
    catalog = json.loads((ROOT / "assets/showtime/phones.json").read_text())
    for phone in catalog["phones"]:
        w, h = phone["source_size"]
        top, right, bottom, left = phone["screen_insets"]
        image = Image.new("RGBA", (w, h))
        draw = ImageDraw.Draw(image)
        radius = round(w * 0.1)
        draw.rounded_rectangle((0, 0, w-1, h-1), radius, fill=COLORS[phone["color"]])
        draw.rounded_rectangle((9, 9, w-10, h-10), radius-8, fill="#141619")
        draw.rounded_rectangle((left, top, w-right-1, h-bottom-1), round(w*0.08), fill=(0,0,0,0))
        path = art / phone["source"]
        image.save(path)
        phone["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(art / "phones.json", {"schema_version": 1, "source": "Showtime schematic frames",
                                     "phones": catalog["phones"]})
    pattern = Image.new("RGBA", (512, 512))
    draw = ImageDraw.Draw(pattern)
    for y in range(16, 512, 48):
        for x in range(16, 512, 48):
            draw.ellipse((x, y, x+5, y+5), fill="#212733")
    pattern.save(art / "pattern.png")
    palettes = {"sage":["#c8dacd","#aec5b1","#e1e4cb","#b2c8bd"],
                "vivid":["#89b4ee","#c3a5f2","#cebfee","#99cfde"],
                "graphite":["#252a33","#31394b","#202633","#414654"],
                "pastel":["#b4cede","#dfcee5","#bfd6d8","#e3d8e7"],
                "warm":["#e8ba9f","#dcb4bf","#ead9b1","#d5aba0"]}
    write_json(art / "telegram-gradients.json", {"schema_version":1,"source":"Showtime",
        "gradients":[{"id":key,"label":key.title(),"palette":value} for key,value in palettes.items()]})
    tap = destination / "assets/tap/native"
    tap.mkdir(parents=True)
    for frame in range(41):
        image = Image.new("RGBA", (1080, 1080))
        radius = round(240 + 135 * min(frame/25, 1))
        alpha = round(128 * max(0, min(frame/25, (40-frame)/15)))
        ImageDraw.Draw(image).ellipse((540-radius, 540-radius, 540+radius, 540+radius),
                                      outline=(49,123,255,alpha), width=24)
        image.save(tap / f"tap_{frame:05d}.png")
    (tap.parent / "README.md").write_text(
        "# Tap ring\n\n41 procedural RGBA frames at 60 fps. Frame 25 aligns with contact. "
        "The renderer can tint the ring and sustain the contact frame for a hold.\n")
    assets = [{"file":str(path.relative_to(art)), "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
              for path in sorted(art.rglob("*.png"))]
    write_json(art / "provenance.json", {"schema_version":1, "source":"Drawn by scripts/package_public.py", "assets":assets})
    profile_path = destination / "profiles/telegram-product.json"
    profile = json.loads(profile_path.read_text())
    profile["background"].update(palette=palettes["pastel"], pattern_name="Dot grid", palette_note="Showtime pastel")
    profile["comparison"]["labels"] = ["Telegram original", "Showtime app recording"]
    profile["tap"]["tint_note"] = "Tint for the bundled procedural contact ring."
    profile["notes"] = ["Schematic frame sizes follow the configured device geometry.",
                        "Only application text is visible in product videos."]
    write_json(profile_path, profile)
    with tempfile.TemporaryDirectory() as temporary:
        subprocess.run([sys.executable, "-B", str(destination / "scripts/compose_pilot.py"),
                        "--work-dir", temporary, "prepare", "--layout", "full"], check=True,
                       stdout=subprocess.DEVNULL)
        examples = destination / "examples"
        examples.mkdir()
        with Image.open(Path(temporary) / "technical-preview.png") as preview:
            preview.thumbnail((800, 800))
            preview.save(examples / "stage.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New directory")
    build(parser.parse_args().destination)
