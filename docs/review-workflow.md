# Manifest and approval contracts

Use this reference when saving a decision or preparing a new capture. User decisions are ordinary chat messages; the agent records their provenance and hashes.

## Decisions

A new video needs one user approval: the scenario, composition and timing before production. After that, prepare and internally review the actual materials, record, export and complete all [delivery checks](visual-acceptance.md#every-delivered-video). Send the finished video without requesting approval of a preview or storyboard. An independently changed scenario needs a new scenario decision. Changes to prepared materials within the approved story require updated evidence and internal review, not another user approval.

Approval refers to the shown version, not the general process. Record real `source`, `reference`, exact `quote`, `decision: "approved"` and `manifest_sha256` in the decision JSON. Preserve exact quotes in their original language.

```bash
python3 "$SHOWTIME/scripts/review_gate.py" fingerprint --manifest "$RUN_DIR/scenario.json"
python3 "$SHOWTIME/scripts/review_gate.py" approve --scenario "$RUN_DIR/scenario.json" --approval-dir "$RUN_DIR/approvals" --decision-file "$RUN_DIR/scenario-decision.json"
python3 "$SHOWTIME/scripts/review_gate.py" check --scenario "$RUN_DIR/scenario.json" --approval-dir "$RUN_DIR/approvals"
```

After internal preparation, validate the materials before recording:

```bash
python3 "$SHOWTIME/scripts/review_gate.py" check --scenario "$RUN_DIR/scenario.json" --prepared-content "$RUN_DIR/prepared-content.json" --approval-dir "$RUN_DIR/approvals"
```

This requires the scenario decision and current preparation/frame-review evidence. It does not require or create `prepared-content.approval.json`. The result reports `target_user_reviewed: false`; internal checks do not imply user approval. Re-saving an identical scenario decision is idempotent; replaced receipts remain in `approvals/history/`.

## Manifests

One pair per video: `scenario.json`, `prepared-content.json`, plus `approvals/`. New captures require `schema_version: 2`. Both manifests contain `kind`, shared `scenario_id`, `content`, and `files: [{path, sha256}]`. File paths resolve from the manifest. Include all scene data, assets, settings and previews used. Prepared content also has `scenario_sha256`, obtained by fingerprinting the scenario.

| Manifest | Required content |
| --- | --- |
| Both | `content.language`: nonempty language code; `en` unless explicitly overridden |
| Scenario (`kind: "scenario"`) | `content.story`: nonempty `context`, `entry_point`, `action`, `payoff`, `loop`; `content.scene_plan` as below |
| Prepared (`kind: "prepared-content"`) | `content.scene_content`, `content.storyboard`, `content.story_review`; all new recordings require [preparation evidence](preparation.md#evidence) |

`scene_plan` and `scene_content`: nonempty `setting` and `continuity`; `actors` and `supporting_content` are lists of nonempty strings. Actors may be empty. Empty supporting content requires `intentional_empty_reason`. The plan describes intended surroundings; prepared content records exact texts and actual assets.

`storyboard`: entries `{id, role, file, description}` with unique IDs, nonempty descriptions and all roles `context`, `entry`, `action`, `result`. Each file must be in `files`; several roles may share one image.

`story_review`: `status: "passed"`, `basis: "frames_only"`, nonempty `reviewer` and `reference` to the actual independent review, `answers` containing `context`, `entry_point`, `action`, `result`, `believability`, `continuity`, and `materials_sha256`. Record actual reviewer answers, normally in English, after [frame review](visual-acceptance.md#storyboard-review).

Compute the review digest over the prepared manifest except `content.story_review`:

```bash
PYTHONPATH="$SHOWTIME/scripts" python3 -c 'import json, sys; from review_gate import story_materials_sha256; print(story_materials_sha256(json.load(open(sys.argv[1]))))' "$RUN_DIR/prepared-content.json"
```

Changes to other fields or referenced files require updated hashes and another internal review. Keep checked inputs unchanged during capture; tools do not lock them. V1 remains readable/fingerprintable, but does not authorize new captures; existing videos remain usable without migration.

## Requested corrections

An explicit correction request authorizes that scope without another confirmation. Keep the actual user request in the run's records. For scene corrections, retain the approved scenario, update the prepared materials and repeat internal checks. For a directly requested story change, record that instruction as the decision for the revised scenario. Export-only fixes may reuse existing footage: background, tap appearance and phone color, not layout or phone model. Apply the full delivery checklist to the revised MP4 before sending it.

Historical runs may still contain prepared-content approvals and `--revision-file` correction receipts. The legacy `approve --prepared-content` and `authorize-correction` commands remain supported for those records; do not generate them for new runs. Existing receipts are preserved, but the normal recording path no longer depends on a second user decision.

The gate validates structure, file hashes and decision/review bindings. The agent verifies visual meaning, language and correction scope; the gate cannot authenticate reviewer identity or prove visual quality.
