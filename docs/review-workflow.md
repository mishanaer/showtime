# Manifest and approval contracts

Use this reference when saving a decision or preparing a new capture. User decisions are ordinary chat messages; the agent records their provenance and hashes.

## Decisions

A new video needs approval of (1) its story before scene preparation and (2) the actual prepared materials after independent frame review, before the final take. A changed story requires both decisions again. Independently changed prepared content requires the second decision again. Direct user corrections use [requested corrections](#requested-corrections); editing reusable footage needs no new capture approval.

Approval refers to the shown version, not the general process. Record real `source`, `reference`, exact `quote`, `decision: "approved"` and `manifest_sha256` in the decision JSON. Preserve exact quotes in their original language.

```bash
python3 "$SHOWTIME/scripts/review_gate.py" fingerprint --manifest "$RUN_DIR/scenario.json"
python3 "$SHOWTIME/scripts/review_gate.py" approve --scenario "$RUN_DIR/scenario.json" --approval-dir "$RUN_DIR/approvals" --decision-file "$RUN_DIR/scenario-decision.json"
python3 "$SHOWTIME/scripts/review_gate.py" check --scenario "$RUN_DIR/scenario.json" --approval-dir "$RUN_DIR/approvals"
```

For the second decision, fingerprint `prepared-content.json` and add `--prepared-content "$RUN_DIR/prepared-content.json"` to `approve` and `check`, using its own decision file. Re-saving an identical decision is idempotent; replaced receipts remain in `approvals/history/`.

## Manifests

One pair per video: `scenario.json`, `prepared-content.json`, plus `approvals/`. New captures require `schema_version: 2`. Both manifests contain `kind`, shared `scenario_id`, `content`, and `files: [{path, sha256}]`. File paths resolve from the manifest. Include all scene data, assets, settings and previews used. Prepared content also has `scenario_sha256`, obtained by fingerprinting the scenario.

| Manifest | Required content |
| --- | --- |
| Both | `content.language`: nonempty language code; `en` unless explicitly overridden |
| Scenario (`kind: "scenario"`) | `content.story`: nonempty `context`, `entry_point`, `action`, `payoff`, `loop`; `content.scene_plan` as below |
| Prepared (`kind: "prepared-content"`) | `content.scene_content`, `content.storyboard`, `content.story_review`; all new recording approvals require [preparation evidence](preparation.md#evidence) |

`scene_plan` and `scene_content`: nonempty `setting` and `continuity`; `actors` and `supporting_content` are lists of nonempty strings. Actors may be empty. Empty supporting content requires `intentional_empty_reason`. The plan describes intended surroundings; prepared content records exact texts and actual assets.

`storyboard`: entries `{id, role, file, description}` with unique IDs, nonempty descriptions and all roles `context`, `entry`, `action`, `result`. Each file must be in `files`; several roles may share one image.

`story_review`: `status: "passed"`, `basis: "frames_only"`, nonempty `reviewer` and `reference` to the actual independent review, `answers` containing `context`, `entry_point`, `action`, `result`, `believability`, `continuity`, and `materials_sha256`. Record actual reviewer answers, normally in English, after [frame review](visual-acceptance.md#storyboard-review).

Compute the review digest over the prepared manifest except `content.story_review`:

```bash
PYTHONPATH="$SHOWTIME/scripts" python3 -c 'import json, sys; from review_gate import story_materials_sha256; print(story_materials_sha256(json.load(open(sys.argv[1]))))' "$RUN_DIR/prepared-content.json"
```

Changes to other fields or referenced files require updated hashes and another internal review. Keep approved inputs unchanged during capture; tools do not lock them. V1 remains readable/fingerprintable, but does not authorize new captures; existing videos remain usable without migration.

## Requested corrections

For a user-requested correction to an approved video, retain the baseline decisions and scenario. Prepare revised materials and update their internal review. Save a correction request with:

```text
decision: correction_requested
scenario_approval_sha256: canonical hash of scenario.approval.json
base_prepared_approval_sha256: canonical hash of the original prepared-content.approval.json
target_prepared_sha256: fingerprint of the revised prepared-content manifest
instructions: [{source, reference, quote, scope}]
```

Use `review_gate.canonical_sha256` for receipt hashes, `fingerprint` for manifests. Include all applicable user instructions with exact quotes and scope.

```bash
python3 "$SHOWTIME/scripts/review_gate.py" authorize-correction --scenario "$RUN_DIR/scenario.json" --prepared-content "$RUN_DIR/prepared-content.json" --approval-dir "$RUN_DIR/approvals" --decision-file "$RUN_DIR/correction-request.json" --output "$RUN_DIR/correction-authorization.json"
```

Pass that file as `--revision-file` to `review_gate.py check` and `record_simulator.py`, alongside scenario, prepared content and approvals. It authorizes the specified revision without overwriting baseline approvals or claiming the user viewed it (`target_user_reviewed: false`).

The gate validates structure, file hashes and decision/review bindings. The agent verifies visual meaning, language and correction scope; the gate cannot authenticate reviewer identity or prove visual quality.
