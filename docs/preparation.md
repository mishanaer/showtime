# Scene preparation

Apply these rules while preparing the scene for internal review. Use actual app frames in the final composition. Preparation is internal work after scenario approval, not a second user-facing approval stage.

## Phone and background

For new videos, use the bundled [phone mockups](../assets/showtime/phones.json): iPhone 17, 17 Pro, 17 Pro Max, 18 Pro or 18 Pro Max, in their catalog colors. Choose one of five [background palettes](../assets/showtime/gradients.json): Sage, Vivid, Graphite, Pastel or Warm. Night is excluded.

Choose the model, color and background automatically to complement the app and keep the phone clearly visible. Honor explicit user preferences; otherwise, do not ask a separate styling question. Show the choice in the storyboard so the user can change it. Keep it consistent throughout the video and subsequent revisions unless a change is requested.

Save the run profile as `$RUN_DIR/profile.json` and include it in the prepared manifest’s `files`. Set `content.preparation.profile` to its `{path, sha256}` entry. Save the selected catalog device fields and `background.id` / `background.palette` in the run's profile. The product profile is a starting configuration, not a mandatory color combination. Asset paths in the phone catalog resolve relative to that catalog; profile paths resolve relative to the original profile. Resolve both before saving a run-specific copy elsewhere.

Choose a model with an available matching simulator; either the approved iPhone 17 or 18 family is acceptable. Capture on that exact model using its complete native viewport. Verify the simulator's device type, not its editable display name. If the user explicitly selects an unavailable model, report the missing support before recording; do not substitute a different model without their agreement. Existing videos keep their configuration unless the user requests a migration.

## Visual rules

| Requirement | Preparation |
| --- | --- |
| Full viewport | Fit the complete app screen into the complete phone mockup; crop the composed phone at the canvas boundary. Preserve bottom corners throughout transitions. |
| Visible controls | Important buttons may be moved upward inside the app, retaining behavior and natural layout; this is already authorized. Update action/tap coordinates. Do not replace controls with video overlays. |
| No keyboard suggestions | Disable predictions in the recorded fields. Check opening, typing and reopening the keyboard. |
| Relevant hints only | Dismiss or disable tooltips, onboarding prompts and contextual hints unrelated to the video's topic in the app before preparing frames or recording. Keep those that demonstrate the featured behavior or explain its result. Check that unrelated hints do not reappear during the flow. |
| Character typing | Hide the insertion caret while characters appear; restore its original appearance on completion or interruption without adding a pause. Keep visible keys visually still: no pressed states, popup letters or touch indicators on keys. Review the entire typing interval and record both `visible_keypresses: false` and `visible_caret_during_typing: false` inside `checks.character_typing` only after verification. Type one character at a time into the real field at 30 characters per second by default; preview the pace and allow time to read. Do not paste the whole string or overlay simulated text. |
| No editorial text | Keep app text only. No titles, labels or explanations such as “Later” beside or over the phone. Labels on separate technical comparisons are allowed. |

Prepare storyboard roles `context`, `entry`, `action`, `result`; one image may cover multiple roles. Inspect typing and transitions in motion, including intermediate bottom-edge geometry. Fix defects before recording the finished video.

## Evidence

For new packages, add `content.preparation` to `prepared-content.json` v2. Existing runs do not need this extension retrofitted merely to continue.

| Field | Value |
| --- | --- |
| `viewport` | Full app window `{width, height}` in points |
| `framing` | One entry per distinct stage (`crop-top`, `crop-bottom` or `full`): `{"layout":"crop-top","device_id":"iphone-17-pro-silver","geometry":{"path":"stage/geometry.json","sha256":"…"}}`. Include the actual stage geometry or compositor render report in `files`. Use 1920-square stage coordinates before uniform preview downscaling. Inspect an encoded frame from each stage to confirm the preview used those layers. |
| `visible_height` | Visible screen height in the composition, in the same points; at most `viewport.height` |
| `keyboard_visible`, `typing_visible` | Booleans |
| `checks` | Entries below |

Each check has `status: "ready"`, a factual nonempty `note`, and `evidence: [{path, sha256}]`. Inspect evidence before marking ready. Paths resolve relative to the manifest and must also appear in its top-level `files` with matching actual hashes. Reuse evidence across checks.

| Check | Evidence | When `not_applicable` is allowed |
| --- | --- | --- |
| `full_height` | Transition video/GIF showing lower-edge geometry | Never |
| `visible_controls` | Action frames in final composition | Never |
| `keyboard_suggestions` | Keyboard opening, typing, reopening | Only when `keyboard_visible` is false |
| `character_typing` | Video/GIF of sequential native input | Only when `typing_visible` is false |
| `no_editorial_text` | Prepared frames/previews | Never |

`not_applicable` requires a reason in `note` and may have empty evidence.

## Internal motion checks

After scenario approval, use short technical rehearsals where needed to verify transitions or typing. These are internal checks, not a full draft to send for user approval:

```bash
python3 "$SHOWTIME/scripts/record_simulator.py" --preparation-preview --simulator "$SIMULATOR_ID" --output "$RUN_DIR/previews/transition.mp4" --scenario "$RUN_DIR/scenario.json" --approval-dir "$RUN_DIR/approvals" --tail-sentinel
```

Use the [capture lifecycle](showtime-execution.md#capture); exclude the sentinel from the inspected motion. This legacy-named flag marks an internal preparation capture. It cannot replace the checked production take. Do not record and compose a complete draft merely to ask permission for a clean take.

Before internal frame review:

```bash
python3 "$SHOWTIME/scripts/check_preparation.py" --manifest "$RUN_DIR/prepared-content.json"
```

`story_review` may still be absent. The checker validates evidence completeness and hashes, not image contents or tap indicators. Complete the [independent frame review](visual-acceptance.md#storyboard-review), then run `review_gate.py check` with the scenario, prepared content and approval directory. This validates internal preparation against the approved scenario without a prepared-content approval receipt. Proceed to recording and export; complete the full delivery checks before sending the finished video.
