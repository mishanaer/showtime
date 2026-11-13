# Tap ring

`native/tap_%05d.png`: 41 RGBA PNG frames (0–40), 1080×1080, straight alpha, 60 fps, exported once from After Effects. Production needs no After Effects or conversion.

- **Timing:** frame 25 is the squeeze after the expansion; align it with the contact. Entry starts 25/60 s earlier. Visible alpha ends after frame 34; keep all 41 frames.
- **Color:** set `tap.tint_rgb` in the profile, or `tint_rgb` per event (for example `[255,255,255]` for white). The renderer recolors each frame's alpha mask without changing the motion. `monochrome_rgb` validates the source pixels; do not change it to recolor the output.
- **Size:** `maximum_diameter_fraction` of the screen width (0.485 by default).
- **Backdrop blur:** the compositor blurs the live video under the ring (`backdrop_sigma`, 42 at 1920×1920). It cannot be baked into the frames.
- **Holds:** repeat only the contact frame for the hold interval; keep the original entry and release frames.
