# Visual acceptance

## Storyboard review

During internal preparation, give a separate reviewer ordered app frames and the questions below, without the scenario, intended answers or reference videos. Use a fresh context (`fork_turns: "none"` where supported).

Ask what the frames show: context, feature entry, action, result, believable surroundings and continuity. The reviewer should identify ambiguous frames and distinguish observations from assumptions. Compare their answers with the intended story; fix unclear materials and repeat review. Record the actual answers using [the manifest contract](review-workflow.md#manifests). This is internal review; do not pause for user approval of the storyboard or a preview.

The author checks motion previews under [Preparation](preparation.md); still-frame comprehension does not establish animation quality.

## Every delivered video

Apply this full checklist to the first video sent to the user and every revision. Watch the actual encoded export at normal speed and several uninterrupted loops; frame samples alone do not satisfy motion review. Check:

- The action visibly causes the promised benefit; context and entry are understandable without commentary.
- Names, avatars, dates, roles and scene state remain consistent. Text matches the manifest language and has sufficient unobstructed reading time.
- Reject any visible keypress highlight, popup letter or touch ring on keyboard keys while typing. Review the complete input interval in the exported video.
- The insertion caret stays hidden while characters appear and regains its original appearance immediately after the final character. Check the first character, the whole typing interval and the return; reject a flashing or lagging caret or an added pause for its restoration. A caret already hidden by the app may remain hidden. During rehearsal, also confirm restoration after interrupted input or focus loss.
- Full-viewport geometry, corners, visible controls, keyboard predictions, character typing and absence of editorial text still meet the preparation rules; unrelated tooltips and hints remain hidden throughout the flow.
- Scrolls and native navigation remain continuous across cuts. Inspect neighboring frames at suspicious jumps; 60-fps encoding alone proves nothing about source motion.
- Each tap and hold in the edited action sequence has a corresponding indicator in the render report and a visible ring at the correct target and time in the encoded video, with complete entry/release phases. Holds use contact timing, not command duration. Reject missing, extra or invisible indicators; an empty render-event list is valid only when the scene actually has no taps or holds. Never add rings to character typing.
- The approved framing, color and shadow remain correct. When changing backdrop blur, compare with/without frames: blur belongs under the ring, not its center or outside it.
- The loop follows a readable success state without implying its cancellation, freezing or truncating motion.

Run `render_quality.py` without `--cadence-only` for full decoding, duration, dimensions and frame cadence. Save verification findings with the exact MP4 path and SHA-256, the technical report, the tap/event comparison and the results of the visual checks above. Technical checks do not establish visual acceptance.

Fix failed checks before delivery and repeat affected checks against the revised export. Mark unchecked properties `unverified` and capture defects `needs-new-capture`; neither is a passing delivery. If a required check cannot be completed, report the blocker instead of asking the user to approve an incomplete preview. Send the passing export as the finished video; reuse it unchanged when no correction is needed.
