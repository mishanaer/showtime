#!/usr/bin/env python3
"""showtime pilot compositor. Python 3 + Pillow + NumPy + FFmpeg; no simulator access.

prepare: creates a clearly marked technical preview and reusable stage layers.
render:  real capture + optional timed PNG taps -> 1920-square 60 fps MP4.
compare: original on the left, showtime on the right, labelled 3840x2040 MP4.

Events JSON: {"coordinate_space":{"width":402,"height":874},"events":[
  {"time":1.2,"x":201,"y":510,"type":"tap","anchor_frame":25}]}
Coordinates may be points or pixels but must match coordinate_space. time is seconds
from the start of the input capture; --capture-start trims video AND event times.
For measured touch-down alignment, supply measured touch-down times. This renderer
does not infer physical touch timing from command timestamps.
"""

import argparse
import copy
import tempfile
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
from media_io import sha256, save_json, production_review, file_lock
from gesture_timing import gesture_frame_map, write_derived_sequence
from render_quality import audit_render, audit_delivery, delivery_frame_count, RenderQualityError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "profiles/telegram-product.json"


def load_config(path, layout=None):
    """Opt-in portable paths; historical profiles keep their original semantics."""
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    if config.get("path_base") == "config":
        for parent, keys in ((config, ("font",)),
                             (config.get("device", {}), ("source", "manifest")),
                             (config.get("background", {}), ("source", "pattern", "sharp_module")),
                             (config.get("caption", {}), ("font", "logo")),
                             (config.get("tap", {}), ("sequence",))):
            for key in keys:
                if parent.get(key):
                    parent[key] = str((path.parent / parent[key]).resolve())
    return use_layout(config, layout)


def export_framing(config):
    """What a take is bound to: the composition and the phone model it was framed for.

    Background, tap appearance and phone color may change after a take without
    a new recording; layout, canvas and the phone's geometry may not.
    """
    device = config["device"]
    return {"layout": config["layout"], "canvas": [config["canvas"]["width"], config["canvas"]["height"]],
            "model": device.get("model"), "source_size": list(device["source_size"]),
            "screen_insets": list(device["screen_insets"])}


def check_export_profile(review, config_path, layout=None):
    """Return how the export profile relates to the approved one, or raise."""
    approved = review.get("profile_sha256")
    if not approved:
        return None
    config = load_config(config_path, layout)
    if sha256(config_path) == approved and layout in (None, load_config(config_path)["layout"]):
        return {"profile": "approved"}
    framing = review.get("profile_framing")
    if framing is None:
        raise ValueError("Use the approved profile and framing for export (this take predates export-only profile changes)")
    if export_framing(config) != framing:
        raise ValueError("Layout and phone model are bound to the approved preparation; "
                         "change only the background, tap appearance or phone color, or record a new take")
    return {"profile": "changed after the take", "approved_profile_sha256": approved,
            "export_profile_sha256": sha256(config_path), "framing": "unchanged"}


def validate_editorial_policy(config):
    if config.get("policy", {}).get("editorial_text") is False and config.get("caption"):
        raise ValueError("This profile excludes editorial text. Remove caption from the configuration.")


def requires_node(config):
    bg = config.get("background", {})
    return bool(bg.get("pattern") and bg.get("pattern_opacity", 0) > 0
                and Path(bg["pattern"]).suffix.lower() == ".svg")


def run(args):
    subprocess.run([str(x) for x in args], check=True)


def probe(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ]))


def video_info(path):
    metadata = probe(path)
    stream = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    return stream["width"], stream["height"], float(metadata["format"]["duration"])


PRODUCT_LAYOUTS = ("crop-top", "crop-bottom", "full")


def geometry(config):
    d = config["device"]
    factor = d["width"] / d["source_size"][0]
    height = round(d["source_size"][1] * factor)
    if config.get("layout") == "full":
        height = config["canvas"]["height"] - 2 * d["top"]
        factor = height / d["source_size"][1]
    top, right, bottom, left = d["screen_insets"]
    sw = round((d["source_size"][0] - left - right) * factor)
    sh = round((d["source_size"][1] - top - bottom) * factor)
    return {
        "device": [d["left"], d["top"], d["width"], height],
        "screen": [round(d["left"] + left * factor), round(d["top"] + top * factor), sw, sh],
        "radius": [sw * d["screen_radius_fraction"][0], sh * d["screen_radius_fraction"][1]],
    }


def use_layout(config, name):
    result = copy.deepcopy(config)
    name = name or result.get("layout", "full-device")
    layouts = result.get("layouts", {})
    result["layout"] = name
    if name in PRODUCT_LAYOUTS:
        # Resolve product presets from the framework, never from a run override.
        product = json.loads((ROOT / "profiles/telegram-product.json").read_text())
        result["device"].update(product["layouts"]["reference-crop"])
        if name == "crop-bottom":
            device = result["device"]
            device["top"] = result["canvas"]["height"] - geometry(result)["device"][3] - device["top"]
        elif name == "full":
            device = result["device"]
            source_width, source_height = device["source_size"]
            height = result["canvas"]["height"] - 2 * device["top"]
            device["width"] = round(height * source_width / source_height)
            device["left"] = (result["canvas"]["width"] - device["width"]) // 2
    else:
        result["device"].update(layouts.get(name, {}))
    return result


def validate_visible_targets(events, coord_size, scaled_size, crop_offset, screen, canvas_size):
    """Reject a crop that hides an action; ring overflow itself is intentional."""
    coord_w, coord_h = coord_size
    scaled_w, scaled_h = scaled_size
    crop_x, crop_y = crop_offset
    sx, sy, sw, sh = screen
    canvas_w, canvas_h = canvas_size
    for event in events:
        x, y = event["x"], event["y"]
        if not 0 <= x <= coord_w or not 0 <= y <= coord_h:
            raise ValueError(f"Action {event.get('id', 'unnamed')} falls outside the source screen")
        px, py = sx + x / coord_w * scaled_w - crop_x, sy + y / coord_h * scaled_h - crop_y
        if not (max(0, sx) <= px < min(canvas_w, sx + sw)
                and max(0, sy) <= py < min(canvas_h, sy + sh)):
            raise ValueError(
                f"Framing hides action {event.get('id', 'unnamed')} "
                f"at ({px:.1f}, {py:.1f}). Adjust the scene to fit the selected frame "
                "and repeat the internal preparation checks before recording again."
            )


def tap_tint(config, event):
    """Color is a derived style; source alpha/geometry/timing remain unchanged."""
    color = event.get("tint_rgb", config.get("tint_rgb", config.get("monochrome_rgb")))
    if color is not None and (not isinstance(color, list) or len(color) != 3
                             or any(type(c) is not int or not 0 <= c <= 255 for c in color)):
        raise ValueError("Set tint_rgb to three integers from 0 to 255")
    if color is not None and config.get("monochrome_rgb") is None:
        raise ValueError("To tint a tap, verify the source color and set monochrome_rgb")
    return color


def rounded_mask(size, radius):
    # Elliptical corners preserve Dresser's independent horizontal/vertical radii.
    w, h = size
    rx, ry = [max(1, round(x)) for x in radius]
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((rx, 0, w-rx, h), fill=255)
    draw.rectangle((0, ry, w, h-ry), fill=255)
    for x in (0, w-2*rx):
        for y in (0, h-2*ry):
            draw.ellipse((x, y, x+2*rx, y+2*ry), fill=255)
    return mask


def mesh_background(config, directory):
    w, h = config["canvas"]["width"], config["canvas"]["height"]
    bg = config["background"]
    colors = np.array([[int(c[i:i+2], 16) for i in (1, 3, 5)] for c in bg["palette"]], dtype=np.float32)
    # Exact spatial mixing formula from Portfolio; rendered at delivery resolution.
    positions = [(0.8, 0.9), (0.35, 0.75), (0.2, 0.1), (0.65, 0.25)]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xx/w-0.5, yy/h-0.5
    theta = (0.35 * np.sqrt(dx*dx + dy*dy))**2 * 6.4
    px = np.clip(0.5 + dx*np.cos(theta) - dy*np.sin(theta), 0, 1)
    py = np.clip(0.5 + dx*np.sin(theta) + dy*np.cos(theta), 0, 1)
    total = np.zeros((h, w), dtype=np.float32)
    mixed = np.zeros((h, w, 3), dtype=np.float32)
    for color, (x, y) in zip(colors, positions):
        weight = np.maximum(0, 0.92 - np.sqrt((px-x)**2 + (py-y)**2))**3
        total += weight
        mixed += weight[:, :, None] * color
    mixed /= np.maximum(total[:, :, None], 1e-9)
    pattern = bg.get("pattern")
    if pattern and bg.get("pattern_opacity", 0) > 0:
        raster = directory / "pattern-mask.png"
        if requires_node(config):
            js = "const sharp=require(process.argv[1]);sharp(process.argv[2]).resize(Number(process.argv[4]),Number(process.argv[5]),{fit:'cover'}).png().toFile(process.argv[3]).catch(e=>{console.error(e);process.exit(1)});"
            run(["node", "-e", js, bg["sharp_module"], pattern, raster, w, h])
            with Image.open(raster) as source:
                pattern_image = source.convert("RGBA")
        else:
            with Image.open(pattern) as source:
                pattern_image = ImageOps.fit(source.convert("RGBA"), (w, h), method=Image.Resampling.LANCZOS)
            pattern_image.save(raster)
        alpha = np.array(pattern_image, dtype=np.float32)[:, :, 3:4] / 255
        opacity = alpha * bg["pattern_opacity"]
        # Portfolio's dark pattern over a bright palette, soft-light blend.
        base = mixed / 255
        mixed = 255 * (base * (1-opacity) + base*base * opacity)
    return Image.fromarray(np.uint8(np.clip(mixed, 0, 255)))


def prepare(config, directory, preview=None):
    validate_editorial_policy(config)
    directory.mkdir(parents=True, exist_ok=True)
    w, h = config["canvas"]["width"], config["canvas"]["height"]
    g = geometry(config)
    x, y, dw, dh = g["device"]
    sx, sy, sw, sh = g["screen"]
    d = config["device"]
    frame = Image.open(d["source"]).convert("RGBA")
    if list(frame.size) != d["source_size"]:
        raise ValueError(f"Dresser asset dimensions changed: {frame.size}")
    frame = frame.resize((dw, dh), Image.Resampling.LANCZOS)
    device_layer = Image.new("RGBA", (w, h))
    device_layer.paste(frame, (x, y))
    device_layer.save(directory / "device-layer.png")
    mask = rounded_mask((sw, sh), g["radius"])
    mask.save(directory / "screen-mask.png")
    background = mesh_background(config, directory).convert("RGBA")
    shadow = Image.new("RGBA", (w, h))
    shadow_mask = Image.new("L", (w, h))
    silhouette = rounded_mask((dw, dh), (120*dw/1350, 120*dw/1350))
    shadow_mask.paste(silhouette, (x+d["shadow_offset"][0], y+d["shadow_offset"][1]))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(d["shadow_blur"]))
    shadow.putalpha(shadow_mask.point(lambda p: round(p*d["shadow_opacity"])))
    background = Image.alpha_composite(background, shadow)
    caption = config.get("caption", {})
    if config.get("layout") in caption.get("layouts", []):
        scale = caption.get("raster_scale", 1)
        if type(scale) is not int or not 1 <= scale <= 4:
            raise ValueError("caption raster_scale must be an integer from 1 to 4")
        font = ImageFont.truetype(caption.get("font") or config.get("font"), round(caption["font_size"] * scale))
        anchor = caption.get("anchor", "mm")
        if scale == 1:
            ImageDraw.Draw(background).text((caption.get("x", w/2), caption["y"]), caption["text"], font=font, fill=caption["color"], anchor=anchor)
        else:
            # Render only the text tile at higher resolution to reduce rounding of
            # glyph advances. Keep the font's proportions and natural spacing.
            bounds = font.getbbox(caption["text"], anchor=anchor)
            left, top = [math.floor(v / scale) - 2 for v in bounds[:2]]
            right, bottom = [math.ceil(v / scale) + 2 for v in bounds[2:]]
            tile = Image.new("RGBA", ((right-left)*scale, (bottom-top)*scale))
            ImageDraw.Draw(tile).text((-left*scale, -top*scale), caption["text"], font=font, fill=caption["color"], anchor=anchor)
            tile = tile.resize((right-left, bottom-top), Image.Resampling.LANCZOS)
            background.alpha_composite(tile, (round(caption.get("x", w/2))+left, round(caption["y"])+top))
        if caption.get("logo"):
            logo = Image.open(caption["logo"]).convert("RGBA")
            logo.thumbnail(tuple(caption["logo_size"]), Image.Resampling.LANCZOS)
            background.alpha_composite(logo, tuple(caption["logo_position"]))
    background.convert("RGB").save(directory / "background.png")
    (directory / "geometry.json").write_text(json.dumps(g, indent=2)+"\n")
    if preview:
        screen = Image.new("RGBA", (sw, sh), "#F5F5F5")
        draw = ImageDraw.Draw(screen)
        font = ImageFont.load_default(size=48)
        small = ImageFont.load_default(size=32)
        for gy in range(0, sh, 160):
            draw.line((0, gy, sw, gy), fill="#DDDDDD", width=2)
        for gx in range(0, sw, 160):
            draw.line((gx, 0, gx, sh), fill="#DDDDDD", width=2)
        draw.rounded_rectangle((45, 165, sw-45, 490), radius=26, fill="#FFFFFF")
        draw.text((75, 205), "TECHNICAL PREVIEW", font=font, fill="#111111")
        draw.text((75, 285), "Preview of the phone frame", font=small, fill="#111111")
        draw.text((75, 355), f"Screen viewport: {sw} x {sh}", font=small, fill="#111111")
        for ax, ay in ((0.2, 0.35), (0.8, 0.45), (0.5, 0.62)):
            cx, cy = round(ax*sw), round(ay*sh)
            draw.ellipse((cx-12,cy-12,cx+12,cy+12), fill="#4C92DE")
        screen.convert("RGB").save(directory / "technical-screen.png")
        screen.putalpha(mask)
        background.alpha_composite(screen, (sx, sy))
        background = Image.alpha_composite(background, device_layer)
        background.convert("RGB").save(preview)
    return g


def load_events(path, source_size):
    if not path:
        raise ValueError("Supply --events for export; use an explicit empty list only for a scene without taps")
    value = json.loads(Path(path).read_text())
    if isinstance(value, list):
        return value, source_size
    if not isinstance(value, dict) or not isinstance(value.get("events"), list):
        raise ValueError("The events file must be a list or an object with an events list")
    space = value.get("coordinate_space", {})
    return value["events"], (space.get("width", source_size[0]), space.get("height", source_size[1]))


def publish_video(candidate, output, sidecars):
    """Finish all reports before touching a previously delivered video."""
    backups = {}
    installed = []
    for suffix, data in sidecars.items():
        destination = output.with_suffix(suffix)
        staged = candidate.with_suffix(suffix)
        save_json(staged, data)
        if destination.exists():
            backup = staged.with_suffix(".bak" + suffix)
            shutil.copyfile(destination, backup)
            backups[destination] = backup
    try:
        for suffix in sidecars:
            destination = output.with_suffix(suffix)
            candidate.with_suffix(suffix).replace(destination)
            installed.append(destination)
        candidate.replace(output)
    except OSError:
        for destination in reversed(installed):
            if destination in backups:
                backups[destination].replace(destination)
            else:
                destination.unlink()
        raise


def render(args, config, directory):
    validate_editorial_policy(config)
    source_w, source_h, source_duration = video_info(args.capture)
    duration = args.duration if args.duration is not None else source_duration - args.capture_start
    if duration <= 0 or args.capture_start < 0 or args.capture_start+duration > source_duration+0.1:
        raise ValueError("Capture trim must be inside the actual recording")
    fps = config["canvas"]["fps"]
    if fps != 60:
        raise ValueError("The final video must be exactly 60 fps")
    expected_frames = delivery_frame_count(duration)
    review = production_review(args.capture)
    profile_check = check_export_profile(review, args.config, getattr(args, "layout", None))

    duration = expected_frames / fps
    events, (coord_w, coord_h) = load_events(args.events, (source_w, source_h))
    g = prepare(config, directory)
    sx, sy, sw, sh = g["screen"]
    if coord_w <= 0 or coord_h <= 0:
        raise ValueError("coordinate_space width and height must be positive")
    if abs((coord_w/coord_h) / (source_w/source_h)-1) > 0.01:
        raise ValueError("Event coordinates and recorded image have different aspect ratios")
    tap = config["tap"]
    monochrome_rgb = tap.get("monochrome_rgb")
    if monochrome_rgb is not None and (
        not isinstance(monochrome_rgb, list) or len(monochrome_rgb) != 3
        or any(type(channel) is not int or not 0 <= channel <= 255 for channel in monochrome_rgb)
    ):
        raise ValueError("tap monochrome_rgb must contain three integer channels from 0 to 255")
    anchor = args.tap_anchor_frame if args.tap_anchor_frame is not None else tap["anchor_frame"]
    tap_width = args.tap_width or (round(sw*tap["maximum_diameter_fraction"]/(750/1080)) if tap.get("maximum_diameter_fraction") else tap["width"])
    stage_taps = tap.get("overlay_space", "screen") == "stage"
    if tap["fps"] != fps:
        raise ValueError("The tap and delivery frame rates must match to preserve every original frame")
    backdrop_sigma = tap.get("backdrop_sigma", 0)
    if backdrop_sigma < 0 or (backdrop_sigma > 0 and not stage_taps):
        raise ValueError("Backdrop blur requires stage overlays and a non-negative sigma")
    blur = args.tap_blur if args.tap_blur is not None else tap["blur"]
    if blur < 0:
        raise ValueError("tap blur cannot be negative")
    if blur > 0:
        raise ValueError("Use the preblurred sequence produced by tap-soften.py with --tap-blur 0; straight-alpha blur is not supported")
    included_events = [e for e in events if 0 <= e["time"]-args.capture_start < duration]
    unsupported = [e for e in included_events if e.get("type", "tap") not in ("tap", "long_press")]
    if unsupported:
        raise ValueError("Only touch-down indicators for tap/long-press are supported; swipes need a separate gesture asset")
    taps = included_events
    tap_sequence = args.tap_sequence or tap.get("sequence")
    if taps and not tap_sequence:
        raise ValueError("Tap events require tap.sequence in the profile or --tap-sequence")
    if taps:
        if tap_width <= 0:
            raise ValueError("tap width must be positive")
        for index in range(tap["start_number"], tap["start_number"]+tap["frames"]):
            try:
                frame_path = Path(tap_sequence % index)
            except (TypeError, ValueError) as error:
                raise ValueError("tap sequence needs an integer printf pattern, such as tap_%05d.png") from error
            if not frame_path.is_file():
                raise ValueError(f"Missing original tap frame: {frame_path}")
            with Image.open(frame_path) as image:
                if image.size != (1080,1080) or "A" not in image.getbands():
                    raise ValueError(f"Tap frames must be 1080x1080 with alpha: {frame_path}")
                if monochrome_rgb is not None:
                    pixels = np.asarray(image.convert("RGBA"))
                    visible_rgb = pixels[:, :, :3][pixels[:, :, 3] > 0]
                    if np.any(visible_rgb != monochrome_rgb):
                        raise ValueError(f"Tap has non-transparent pixels outside monochrome_rgb {monochrome_rgb}: {frame_path}")
    inputs = ["ffmpeg", "-hide_banner", "-y", "-loglevel", "warning",
              "-i", args.capture,
              "-loop", "1", "-framerate", str(fps), "-i", str(directory/"background.png"),
              "-loop", "1", "-framerate", str(fps), "-i", str(directory/"screen-mask.png"),
              "-loop", "1", "-framerate", str(fps), "-i", str(directory/"device-layer.png")]
    # Use explicit integer scale/crop for both video pixels and gesture geometry.
    scale = max(sw/source_w, sh/source_h)
    scaled_w, scaled_h = math.ceil(source_w*scale), math.ceil(source_h*scale)
    crop_x, crop_y = (scaled_w-sw)//2, (scaled_h-sh)//2
    validate_visible_targets(taps, (coord_w, coord_h), (scaled_w, scaled_h),
                             (crop_x, crop_y), g["screen"],
                             (config["canvas"]["width"], config["canvas"]["height"]))
    filters = [
        f"[0:v]trim=start={args.capture_start}:duration={duration},setpts=PTS-STARTPTS,fps={fps},scale={scaled_w}:{scaled_h}:flags=lanczos,crop={sw}:{sh}:{crop_x}:{crop_y},setsar=1,format=rgba[screen0]",
    ]
    if stage_taps:
        filters += ["[screen0][2:v]alphamerge[masked]",
                    f"[1:v][masked]overlay={sx}:{sy}:shortest=1[scene]",
                    "[scene][3:v]overlay=0:0:shortest=1,format=rgba[clean]",
                    f"color=c=black@0.0:s={config['canvas']['width']}x{config['canvas']['height']}:r={fps}:d={duration},format=rgba[tapcanvas0]"]
    event_report = []
    for n, event in enumerate(taps):
        output_tint = tap_tint(tap, event)
        anchor_n = event.get("anchor_frame", anchor)
        if not 0 <= anchor_n < tap["frames"]:
            raise ValueError(f"Invalid tap anchor_frame: {anchor_n}")
        if not 0 <= event["x"] <= coord_w or not 0 <= event["y"] <= coord_h:
            raise ValueError(f"Tap lies outside coordinate_space: {event}")
        event_time = event["time"] - args.capture_start
        anchor_output_frame = round(event_time * fps)
        start_frame = anchor_output_frame - anchor_n
        begin = start_frame / fps
        frame_map, hold_frames = gesture_frame_map(event, tap["frames"], anchor_n, fps)
        event_frames = len(frame_map)
        end_frame = start_frame + event_frames - 1
        if start_frame < 0 or end_frame >= expected_frames:
            raise ValueError(f"The clip cuts off gesture {event.get('id')}. Include the full contact and ring fade.")
        px = sx + event["x"]/coord_w*scaled_w - crop_x
        py = sy + event["y"]/coord_h*scaled_h - crop_y
        sequence = tap_sequence
        sequence_start = tap["start_number"]
        if hold_frames:
            sequence = write_derived_sequence(sequence, sequence_start, frame_map, directory / f"hold-{n}")
            sequence_start = 0
        inputs += ["-framerate", str(tap["fps"]), "-start_number", str(sequence_start), "-i", sequence]
        if monochrome_rgb is not None:
            # This path is valid only after every visible source pixel has been
            # checked above. Scale 8-bit coverage without RGB/alpha conversions,
            # then apply the requested derived color on the same frame stream.
            red, green, blue = output_tint
            filters += [
                f"[{4+n}:v]trim=end_frame={event_frames},alphaextract,scale={tap_width}:{tap_width}:flags=bilinear,split=2[tapmask{n}][tapbase{n}]",
                f"[tapbase{n}]format=rgba,lutrgb=r={red}:g={green}:b={blue}[tapink{n}]",
                f"[tapink{n}][tapmask{n}]alphamerge,settb=1/{fps},setpts=N+({start_frame})[tap{n}]",
            ]
        else:
            # Associated-color filtering avoids dark fringes for multicolor
            # assets; 16-bit/8-bit conversion can still round by one alpha level.
            resize = f"format=gbrap16le,premultiply=inplace=1:planes=7,scale={tap_width}:{tap_width}:flags=bilinear,unpremultiply=inplace=1:planes=7,format=rgba"
            filters.append(f"[{4+n}:v]trim=end_frame={event_frames},{resize},settb=1/{fps},setpts=N+({start_frame})[tap{n}]")
        if stage_taps:
            filters.append(f"[tapcanvas{n}][tap{n}]overlay=x={px-tap_width/2}:y={py-tap_width/2}:format=auto:eof_action=pass:repeatlast=0:enable='between(n,{max(0,start_frame)},{end_frame})'[tapcanvas{n+1}]")
        else:
            filters.append(f"[screen{n}][tap{n}]overlay=x={px-sx-tap_width/2}:y={py-sy-tap_width/2}:eof_action=pass:repeatlast=0:enable='between(n,{max(0,start_frame)},{end_frame})'[screen{n+1}]")
        event_report.append({"id":event.get("id"),"gesture_type":event.get("type", "tap"),"event_time":event_time,"anchor_frame":anchor_n,"anchor_output_frame":anchor_output_frame,"anchor_quantization_seconds":anchor_output_frame/fps-event_time,"tap_start":begin,"tap_start_frame":start_frame,"tap_end_frame":end_frame,"source_point":[event["x"],event["y"]],"canvas_point":[px,py]})
        event_report[-1]["tint_rgb"] = output_tint
        event_report[-1].update(visualization="sustained_contact_frame" if hold_frames else "canonical_touch_down_ring",
                               sequence_frames=event_frames, hold_frames=hold_frames,
                               hold_seconds=event.get("hold_seconds"), hold_timing=event.get("hold_timing"),
                               source_frame_map=frame_map)
    if stage_taps:
        if backdrop_sigma:
            # Blur the live scene, then reveal it only through the animated ring.
            # Native AEP alpha peaks at 128/255. Normalization gives full backdrop
            # strength at that peak while preserving its exact fade and geometry.
            opacity_peak = tap.get("native_opacity_peak", 128/255)
            filters += [f"[tapcanvas{len(taps)}]split=2[tint][masksource]",
                        f"[masksource]alphaextract,lut=y='min(val/{opacity_peak},255)'[ringmask]",
                        "[clean]split=2[clear][blursource]",
                        f"[blursource]gblur=sigma={backdrop_sigma}:steps=3[softscene]",
                        "[softscene][ringmask]alphamerge[backdrop]",
                        "[clear][backdrop]overlay=0:0:shortest=1:format=auto[glass]",
                        "[glass][tint]overlay=0:0:shortest=1:format=auto,format=yuv420p[out]"]
        else:
            filters += [f"[clean][tapcanvas{len(taps)}]overlay=0:0:shortest=1:format=auto,format=yuv420p[out]"]
    else:
        filters += [f"[screen{len(taps)}][2:v]alphamerge[masked]",
                    f"[1:v][masked]overlay={sx}:{sy}:shortest=1[scene]",
                    "[scene][3:v]overlay=0:0:shortest=1,format=yuv420p[out]"]
    graph = directory / "last-render.ffgraph"
    graph.write_text(";\n".join(filters)+"\n")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and any(output.samefile(path) for path in [args.capture]):
        raise ValueError("Choose an output path different from the source video")
    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        candidate = Path(staging) / output.name
        run(inputs+["-filter_complex_threads", "2", "-filter_complex", ";".join(filters), "-map", "[out]", "-an", "-t", str(duration), "-frames:v", str(expected_frames), "-r", str(fps), "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf), "-movflags", "+faststart", str(candidate)])
        quality = audit_delivery(candidate)
        if not quality["passed"]:
            raise RenderQualityError(quality)
        if quality["decoded_frame_count"] != expected_frames:
            raise ValueError("The render is missing planned frames")
        quality["output"] = str(output.resolve())
        report = {"capture":str(Path(args.capture).resolve()),"capture_sha256":sha256(args.capture),"capture_start":args.capture_start,"duration":duration,"output":str(output.resolve()),"geometry":g,"source_size":[source_w,source_h],"scaled_size":[scaled_w,scaled_h],"crop_offset":[crop_x,crop_y],"events":event_report,"profile_check":profile_check,"tap":{"sequence":tap_sequence,"fps":tap["fps"],"frames":tap["frames"],"width":tap_width,"blur":blur,"timing_stretched":False},"output_probe":probe(candidate)}
        report["layout"] = config.get("layout")
        report["quality"] = quality
        report["tap"]["tint_rgb"] = tap.get("tint_rgb", monochrome_rgb)
        report["tap"].update({"overlay_space": "stage" if stage_taps else "screen", "backdrop_sigma": backdrop_sigma, "backdrop_mask": "native_ring_alpha_normalized_to_peak" if backdrop_sigma else None, "maximum_diameter_fraction": tap.get("maximum_diameter_fraction"), "monochrome_rgb": monochrome_rgb, "spatial_resampling": "alpha_8bit_bilinear_with_validated_constant_rgb" if monochrome_rgb is not None else "premultiplied_16bit_bilinear_with_8bit_conversion_rounding", "temporal_resampling": "original entry/release frames at original fps; long press repeats the contact frame"})
        if args.events:
            report["events_source"] = {"path":str(Path(args.events).resolve()),"sha256":sha256(args.events)}
        if taps:
            report["tap"]["frame_sha256"] = {str(index):sha256(tap_sequence % index) for index in range(tap["start_number"], tap["start_number"]+tap["frames"])}
        report["provenance"] = {str(path):sha256(path) for path in (Path(__file__), args.config, Path(config["device"]["source"]), Path(config["background"]["source"]))}
        if config["background"].get("pattern"):
            pattern = Path(config["background"]["pattern"])
            report["provenance"][str(pattern)] = sha256(pattern)
        if config.get("layout") in config.get("caption", {}).get("layouts", []):
            caption = config["caption"]
            caption_font = Path(caption.get("font") or config.get("font"))
            report["caption"] = {**caption, "font_sha256": sha256(caption_font), "font_name": ImageFont.truetype(str(caption_font), caption["font_size"]).getname()}
            report["provenance"][str(caption_font)] = sha256(caption_font)
        publish_video(candidate, output, {".quality.json": quality, ".render.json": report})
    print(output)


def compare(args, config, directory):
    _, _, d1 = video_info(args.original)
    _, _, d2 = video_info(args.ours)
    limit = max(d1, d2) if args.pad_shorter else min(d1, d2)
    duration = args.duration or limit
    if duration <= 0 or duration > limit+0.1:
        raise ValueError("Comparison cannot silently extend either recording")
    padding = [max(0, duration-d1), max(0, duration-d2)]
    cell = config["canvas"]["width"]
    banner_h = config["comparison"]["label_height"]
    banner = Image.new("RGB", (cell*2, banner_h), "#111111")
    draw = ImageDraw.Draw(banner)
    # Use an installed font for localized comparison labels when available.
    if config.get("font"):
        font = ImageFont.truetype(config["font"], 46)
    else:
        try:
            font = ImageFont.truetype("Arial.ttf", 46)
        except OSError:
            font = ImageFont.load_default(size=46)
    labels = [args.label_original or config["comparison"]["labels"][0], args.label_ours or config["comparison"]["labels"][1]]
    if args.pad_shorter:
        for index, original_duration in enumerate((d1, d2)):
            if padding[index] > 1/config["canvas"]["fps"]:
                labels[index] += f" (hold after {original_duration:g}s)"
    for index, label in enumerate(labels):
        draw.text((index*cell+cell/2, banner_h/2), label, font=font, fill="#FFFFFF", anchor="mm")
    label_path = directory / "comparison-labels.png"
    banner.save(label_path)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fps = config["canvas"]["fps"]
    if fps != 60:
        raise ValueError("The comparison must be exactly 60 fps")
    filters = f"[0:v]setpts=PTS-STARTPTS,fps={fps},tpad=stop_mode=clone:stop_duration={padding[0]},scale={cell}:{cell}:force_original_aspect_ratio=decrease,pad={cell}:{cell}:(ow-iw)/2:(oh-ih)/2,setsar=1[a];[1:v]setpts=PTS-STARTPTS,fps={fps},tpad=stop_mode=clone:stop_duration={padding[1]},scale={cell}:{cell}:force_original_aspect_ratio=decrease,pad={cell}:{cell}:(ow-iw)/2:(oh-ih)/2,setsar=1[b];[a][b]hstack[body];[2:v][body]vstack=shortest=1,format=yuv420p[out]"
    if output.exists() and any(output.samefile(path) for path in [args.ours, args.original]):
        raise ValueError("Choose an output path different from the source video")
    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        candidate = Path(staging) / output.name
        run(["ffmpeg","-hide_banner","-y","-loglevel","warning","-i",args.original,"-i",args.ours,"-loop","1","-framerate",str(fps),"-i",label_path,"-filter_complex_threads","2","-filter_complex",filters,"-map","[out]","-an","-t",str(duration),"-r",str(fps),"-c:v","libx264","-preset",args.preset,"-crf",str(args.crf),"-movflags","+faststart",candidate])
        quality = audit_render(candidate)
        if not quality["passed"]:
            raise RenderQualityError(quality)
        quality["output"] = str(output.resolve())
        report = {
            "original": str(Path(args.original).resolve()), "ours": str(Path(args.ours).resolve()),
            "original_sha256": sha256(args.original), "ours_sha256": sha256(args.ours),
            "source_durations": [d1, d2], "output_duration": duration, "source_speed": [1, 1],
            "padding": "last_frame_hold" if args.pad_shorter else "none", "padding_seconds": padding,
            "labels": labels, "output_probe": probe(candidate), "quality": quality
        }
        publish_video(candidate, output, {".quality.json": quality, ".compare.json": report})
    print(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--work-dir", type=Path, help="Directory for generated stage layers; defaults to outputs/showtime-stage under the working directory")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="Generate stage layers and explicitly marked technical preview")
    p.add_argument("--preview", type=Path)
    p.add_argument("--layout", choices=PRODUCT_LAYOUTS + ("full-device", "reference-crop", "media-spoilers-crop"))
    p = sub.add_parser("render", help="Composite actual recording with explicit gesture events")
    p.add_argument("--capture", required=True)
    p.add_argument("--layout", choices=PRODUCT_LAYOUTS + ("full-device", "reference-crop", "media-spoilers-crop"))
    p.add_argument("--events", required=True, help="Edited gesture events; an explicit empty list only for a scene without taps")
    p.add_argument("--tap-sequence", help="Override the profile's ready-to-use PNG sequence, e.g. /path/tap_%%05d.png")
    p.add_argument("--tap-anchor-frame", type=int)
    p.add_argument("--tap-width", type=int)
    p.add_argument("--tap-blur", type=float, help="Must be 0; use tap-soften.py for premultiplied-alpha spatial blur")
    p.add_argument("--capture-start", type=float, default=0)
    p.add_argument("--duration", type=float, help="Final duration in seconds including the loop (minimum 10). Defaults to the remaining capture.")
    p.add_argument("--output", required=True)
    p.add_argument("--crf", type=int, default=18)
    p.add_argument("--preset", default="fast")
    p = sub.add_parser("compare", help="Labelled original-left / showtime-right comparison, no retiming")
    p.add_argument("--original", required=True)
    p.add_argument("--ours", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--duration", type=float)
    p.add_argument("--label-original")
    p.add_argument("--label-ours")
    p.add_argument("--pad-shorter", action="store_true", help="Show both complete clips at native speed; label and hold the shorter clip's last frame")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--preset", default="fast")
    args = parser.parse_args()
    config = load_config(args.config, getattr(args, "layout", None))
    required_tools = ["ffmpeg", "ffprobe"] if args.command != "prepare" else []
    if args.command in ("prepare", "render") and requires_node(config):
        required_tools.append("node")
    for tool in required_tools:
        if not shutil.which(tool):
            raise RuntimeError(f"Required tool is missing: {tool}")
    directory = (args.work_dir or Path("outputs/showtime-stage")).resolve()
    if args.command != "prepare" and Path(args.output).suffix.lower() != ".mp4":
        raise ValueError("Choose a separate .mp4 output file")
    with file_lock(directory / ".stage.lock"):
        if args.command == "prepare":
            preview = args.preview or directory/"technical-preview.png"
            prepare(config, directory, preview)
            print(preview)
        elif args.command == "render":
            with file_lock(Path(args.output).with_suffix(".export.lock")):
                render(args, config, directory)
        else:
            with file_lock(Path(args.output).with_suffix(".export.lock")):
                compare(args, config, directory)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"showtime compositor: {error}", file=sys.stderr)
        sys.exit(1)
