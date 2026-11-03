# Visual acceptance

## Storyboard review

Before the second user approval, give a separate reviewer ordered app frames and the questions below, without the scenario, intended answers or reference videos. Use a fresh context (`fork_turns: "none"` where supported).

Ask what the frames show: context, feature entry, action, result, believable surroundings and continuity. The reviewer should identify ambiguous frames and distinguish observations from assumptions. Compare their answers with the intended story; fix unclear materials and repeat review. Record the actual answers using [the manifest contract](review-workflow.md#manifests). This is internal review, not a third user approval.

The author checks motion previews under [Preparation](preparation.md); still-frame comprehension does not establish animation quality.

## Final video

Watch the export and several uninterrupted loops. Check:

- The action visibly causes the promised benefit; context and entry are understandable without commentary.
- Names, avatars, dates, roles and scene state remain consistent. Text matches the manifest language and has sufficient unobstructed reading time.
- Reject any visible keypress highlight, popup letter or touch ring on keyboard keys while typing. Review the complete input interval in the exported video.
- Full-viewport geometry, corners, visible controls, keyboard predictions, character typing and absence of editorial text still meet the preparation rules; unrelated tooltips and hints remain hidden throughout the flow.
- Scrolls and native navigation remain continuous across cuts. Inspect neighboring frames at suspicious jumps; 60-fps encoding alone proves nothing about source motion.
- Taps hit their targets with complete entry/release phases. Holds use contact timing, not command duration.
- The approved framing, color and shadow remain correct. When changing backdrop blur, compare with/without frames: blur belongs under the ring, not its center or outside it.
- The loop follows a readable success state without implying its cancellation, freezing or truncating motion.

Run `render_quality.py` for decoding, duration and frame cadence; also verify output dimensions. Report actual evidence and remaining differences. Mark unchecked properties `unverified` and capture defects `needs-new-capture`; a partial fix is not a global visual pass.
