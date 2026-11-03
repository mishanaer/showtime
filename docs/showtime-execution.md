# showtime execution

Commands assume `SHOWTIME` is the resolved skill root and `RUN_DIR` is the video's absolute working directory. Discover the app, simulator and input driver from the task; use script `--help` for additional options.

## Story

For a new video, first request the user’s scenario. Once received, ask separately for their choice of top crop, bottom crop or full phone. Use any answers already provided. After both are known, use the supplied scenario to show one benefit through **context → feature entry → action → visible result → loop**. Start at the nearest understandable screen; full navigation from the home screen is unnecessary. Show the result from the beneficiary's perspective when roles differ. Give it reading time before returning to the beginning without implying that success was undone.

Describe the setting, actors, supporting history/images and continuity. Keep names, avatars, dates and state consistent. Populate ordinary working screens plausibly; an intentionally empty editor needs no filler. Choose steps and pauses for the story, not a fixed tap count.

Present the story, composition and timing for the first approval. Then prepare actual content and storyboard frames using [Preparation](preparation.md). Record decisions through [the approval contracts](review-workflow.md).

## Capture

For new videos, verify that the simulator's actual device type matches the selected approved iPhone 17 or 18 family mockup; see [Preparation](preparation.md#phone-and-background). Verify a local app installation against its build with `scripts/verify_simulator_install.py`. Rehearse and reset the actual scene. Obtain coordinates from the current UI and use the full viewport in points.

```bash
python3 "$SHOWTIME/scripts/record_simulator.py" --simulator "$SIMULATOR_ID" --output "$RUN_DIR/raw.mp4" --scenario "$RUN_DIR/scenario.json" --prepared-content "$RUN_DIR/prepared-content.json" --approval-dir "$RUN_DIR/approvals" --tail-sentinel
```

For an authorized correction, also pass `--revision-file <correction-authorization.json>`. Wait for `ready_ms` in `raw.clock.json`, then execute actions in another session:

```bash
python3 "$SHOWTIME/scripts/native_action.py" --axe "$AXE_BIN" --clock "$RUN_DIR/raw.clock.json" --journal "$RUN_DIR/actions.json" --id "$ACTION_ID" --type "$ACTION_TYPE" --viewport "$VIEWPORT_WIDTH" "$VIEWPORT_HEIGHT" --point "$X" "$Y" --step "$ACTION_STEP"
```

Keep each journal with its own capture clock. Leave at least 25/60 seconds before the first contact for the tap animation's entry. For long presses, send down/up in one touch command and record `contact_start`, `contact_end` and `contact_timing`; command duration is not contact duration. HID timing estimates dispatch, not physical contact.

Set the product profile layout to `crop-top`, `crop-bottom` or `full` before preparing the stage. Both `prepare` and `render` also accept `--layout` after the subcommand. Use the same preset for both.

For character typing, use `--type type_text --text-file <file>`, omitting `--point` and `--step`. The default rate is 30 characters per second (1/30-second inter-character delay, plus driver overhead). This AXe route accepts printable US ASCII without a trailing newline. Use a verified app-side or other driver for unsupported characters. If hardware input changes the layout, hides a required keyboard or animates keys, use a demo driver to insert one character at a time into the focused native field. Verify the resulting text and check consecutive frames across the entire input for key highlights, popups and rings.

For a local simulator build, showtime includes [the native typing driver](../scripts/simulator/typing-driver.m). Compile it once in the run directory on the Mac hosting the simulator (bash):

```bash
DRIVER="$RUN_DIR/showtime-typing.dylib"
xcrun --sdk iphonesimulator clang -target "$(uname -m)-apple-ios15.0-simulator" -isysroot "$(xcrun --sdk iphonesimulator --show-sdk-path)" -fobjc-arc -dynamiclib -framework Foundation -framework UIKit "$SHOWTIME/scripts/simulator/typing-driver.m" -o "$DRIVER"
SIMCTL_CHILD_DYLD_INSERT_LIBRARIES="$DRIVER" xcrun simctl launch --terminate-running-process "$SIMULATOR_ID" "$BUNDLE_ID"
```

Open the scene and focus the empty native text view or text field. While recording, submit one UTF-8 file at a time (a trailing newline is input too):

```bash
APP_TMP="$(xcrun simctl get_app_container "$SIMULATOR_ID" "$BUNDLE_ID" data)/tmp"
rm -f "$APP_TMP/showtime-caption-result.json"
cp "$RUN_DIR/caption.txt" "$APP_TMP/showtime-caption.pending" && mv "$APP_TMP/showtime-caption.pending" "$APP_TMP/showtime-caption-command.txt"
```

Wait for `showtime-caption-result.json` in the same directory. Require its `value` to equal the input exactly. Save the result with the take: `characters` logs visible prefixes and Unix timestamps for journal alignment. The driver calls native `insertText:` once per composed character at approximately 30 per second. Treat the interval as typing with no tap coordinates. Verify keyboard appearance in the encoded preview. Relaunch the app normally to end the driver session.

Capture complete transitions, the result's reading interval and the loop return. Stop only this recorder with SIGINT using its clock's `recorder_pid`, and wait for completion. Exclude the late `--tail-sentinel` marker from the edit. Preparation-only captures are described in [Preparation](preparation.md#motion-preview).

## Edit

Watch the source and create `observations.json`; do not infer UI completion from command timings. The contract in `scripts/scene_timeline.py` is:

```text
schema_version: 1
capture_sha256: actual SHA-256 of raw.mp4
review_status: reviewed
steps: [{event_id, response_start, transition_end, result_ready, overlays_clear, read_seconds}]
protected_ranges: [{start, end, reason}]
```

Use source-video seconds, one step per journal event in order. Response cannot precede the action; completion markers cannot precede response. With no overlays, set `overlays_clear = response_start`. Reading starts at `max(transition_end, result_ready, overlays_clear)`.

Protect entire scrolls and navigation transitions, including motion between action windows. Shorten pauses only where the cut creates no visible jump or interruption of motion. Select a finite meaningful interval for continuously animated decoration. If the capture lacks the required ending, record it again rather than shortening the reading interval or inserting a freeze frame.

```bash
python3 "$SHOWTIME/scripts/edit_events.py" --capture "$RUN_DIR/raw.mp4" --events "$RUN_DIR/actions.json" --observations "$RUN_DIR/observations.json" --output-dir "$RUN_DIR/edit"
```

Outputs: `edited-capture.mp4`, `events.json`, `edit-timeline.json`, `edit-review/`. Inspect cuts in motion, not just contact sheets. VFR input is accepted and normalized to 60 fps before cutting without changing app speed; normalization cannot recover missing motion.

## Export

```bash
python3 "$SHOWTIME/scripts/compose_pilot.py" --config "$RUN_DIR/profile.json" --work-dir "$RUN_DIR/render-work" render --capture "$RUN_DIR/edit/edited-capture.mp4" --events "$RUN_DIR/edit/events.json" --output "$RUN_DIR/showtime.mp4"
python3 "$SHOWTIME/scripts/render_quality.py" "$RUN_DIR/showtime.mp4" --report "$RUN_DIR/render-quality.json"
```

Use the run's existing config for ongoing work. Always supply `--work-dir` inside the run. The product profile supplies the mockup, background and ready-to-use tap frames; no After Effects export is needed.

The renderer enforces 600–1200 frames at 60 fps. Inspect the result using [Visual acceptance](visual-acceptance.md), including several uninterrupted loops. Deliver the video and actual verification findings; preserve source materials and record the next step in `run.json`.
