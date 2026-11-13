"""Review gate regressions; synthetic decisions stay in temporary test folders."""

import importlib.util
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_gate.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("review_gate_under_test", SCRIPT)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.approvals = self.root / "approvals"
        self.scenario_path = self.root / "scenario.json"
        self.prepared_path = self.root / "prepared.json"
        self.asset = self.root / "preview.png"
        self.asset.write_text("prepared content", encoding="utf-8")
        scene = {"setting": "A booking conversation", "actors": ["Customer", "Bike bot"],
                 "supporting_content": ["An existing booking and a request to change pickup"],
                 "continuity": "The same booking and participants remain visible"}
        self.scenario = {"schema_version": 2, "kind": "scenario", "scenario_id": "test-only",
                         "content": {"language": "en", "story": {
                             "context": "Customer has a bike booking", "entry_point": "Open booking conversation",
                             "action": "Change the pickup time", "payoff": "The booking shows the new time",
                             "loop": "Finish on the successful result before an editorial restart"},
                             "scene_plan": deepcopy(scene)}, "files": []}
        self.prepared = {"schema_version": 2, "kind": "prepared-content", "scenario_id": "test-only",
                         "content": {"language": "en", "summary": "Preview and fixture",
                                     "scene_content": deepcopy(scene),
                                     "storyboard": [{"id": role, "role": role, "file": "preview.png",
                                                     "description": f"Synthetic {role} fixture"}
                                                    for role in ("context", "entry", "action", "result")],
                                     "story_review": {"status": "passed", "reviewer": "Test reviewer",
                                                      "reference": "test://frames-only-review", "basis": "frames_only",
                                                      "answers": {key: f"Synthetic answer for {key}" for key in (
                                                          "context", "entry_point", "action", "result",
                                                          "believability", "continuity")}}},
                         "scenario_sha256": GATE.canonical_sha256(self.scenario),
                         "files": [{"path": "preview.png", "sha256": GATE.file_sha256(self.asset)}]}
        self.write(self.scenario_path, self.scenario)
        def fixture(name, data):
            path = self.root / name
            path.write_bytes(data)
            entry = {"path": name, "sha256": GATE.file_sha256(path)}
            self.prepared["files"].append(entry)
            return entry
        motion = fixture("transition.mp4", b"synthetic motion fixture")
        geometry = fixture("geometry.json", b'{"device":[310,90,1300,2658],"screen":[380,157,1159,2523]}')
        source_profile = SCRIPT.parents[1] / "profiles/telegram-product.json"
        profile = fixture("profile.json", source_profile.read_bytes())
        self.prepared["content"]["preparation"] = {"profile": profile,
            "viewport": {"width": 402, "height": 874}, "visible_height": 610,
            "keyboard_visible": False, "typing_visible": False,
            "framing": [{"layout": "crop-top", "device_id": "iphone-17-pro-silver", "geometry": geometry}],
            "checks": {name: {"status": "ready", "note": "Synthetic fixture", "evidence": [motion]}
                       for name in ("full_height", "visible_controls", "keyboard_suggestions", "character_typing", "no_editorial_text")}}
        self.refresh_review()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def refresh_review(self):
        # Synthetic internal review; not a human approval.
        self.prepared["content"]["story_review"]["materials_sha256"] = GATE.story_materials_sha256(self.prepared)
        self.write(self.prepared_path, self.prepared)

    def decision(self, manifest_path, **overrides):
        # Not a real approval: only these isolated test manifests are referenced.
        value = {"source": "test-fixture", "reference": "test://human-decision",
                 "quote": "Synthetic explicit approval used only by this test",
                 "decision": "approved",
                 "manifest_sha256": GATE.load_manifest(manifest_path)["manifest_sha256"]}
        value.update(overrides)
        path = self.root / "test-decision.json"
        self.write(path, value)
        return path

    def approve_scenario(self):
        return GATE.approve_review(self.scenario_path, None, self.approvals,
                                   self.decision(self.scenario_path))

    def approve_prepared(self):
        return GATE.approve_review(self.scenario_path, self.prepared_path, self.approvals,
                                   self.decision(self.prepared_path))

    def check_recording(self):
        return GATE.check_review_gate(self.scenario_path, self.prepared_path, self.approvals)

    def test_missing_approvals_fail_without_writing_anything(self):
        with self.assertRaisesRegex(GATE.ReviewGateError, "Missing scenario approval"):
            self.check_recording()
        self.assertFalse(self.approvals.exists())

    def test_scenario_approval_and_internal_checks_allow_recording_without_second_decision(self):
        self.approve_scenario()
        result = GATE.check_review_gate(self.scenario_path, None, self.approvals)
        self.assertEqual(result["stage"], "preparation")
        result = self.check_recording()
        self.assertEqual(result["stage"], "recording")
        self.assertTrue(result["story_materials_checked"])
        self.assertEqual(set(result["profile_framing"]),
                         {"layout", "canvas", "model", "source_size", "screen_insets"})
        self.assertFalse(result["target_user_reviewed"])
        self.assertFalse(result["visual_quality_automatically_verified"])
        self.assertNotIn("prepared_approval_sha256", result)
        self.assertEqual([p.name for p in self.approvals.glob("*.approval.json")],
                         ["scenario.approval.json"])

    def test_recording_requires_preparation_evidence(self):
        self.approve_scenario()
        del self.prepared["content"]["preparation"]
        self.refresh_review()
        with self.assertRaisesRegex(GATE.ReviewGateError, "preparation"):
            self.check_recording()

    def test_prepared_approval_cannot_precede_scenario_approval(self):
        with self.assertRaisesRegex(GATE.ReviewGateError, "Missing scenario approval"):
            self.approve_prepared()
        self.assertFalse(self.approvals.exists())

    def test_legacy_prepared_receipt_does_not_block_recording(self):
        self.approve_scenario()
        self.approve_prepared()
        result = self.check_recording()
        self.assertTrue(result["approved"])
        self.assertEqual(result["stage"], "recording")
        self.assertTrue(result["story_materials_checked"])
        self.assertFalse(result["visual_quality_automatically_verified"])
        self.assertEqual(len(list(self.approvals.glob("*.approval.json"))), 2)

    def test_changed_file_fails_even_with_same_size_and_mtime(self):
        self.approve_scenario()
        self.approve_prepared()
        before = self.asset.stat()
        self.asset.write_text("Prepared content", encoding="utf-8")
        os.utime(self.asset, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.asset.stat().st_size, before.st_size)
        with self.assertRaisesRegex(GATE.ReviewGateError, "File changed"):
            self.check_recording()

    def test_updated_materials_require_internal_review_not_another_user_decision(self):
        self.approve_scenario()
        self.approve_prepared()
        self.asset.write_text("New reviewed content is needed", encoding="utf-8")
        self.prepared["files"][0]["sha256"] = GATE.file_sha256(self.asset)
        self.write(self.prepared_path, self.prepared)
        with self.assertRaisesRegex(GATE.ReviewGateError, "frame review is outdated"):
            self.check_recording()
        self.refresh_review()
        self.assertEqual(self.check_recording()["stage"], "recording")

    def test_content_text_change_requires_current_internal_review(self):
        self.approve_scenario()
        self.approve_prepared()
        self.prepared["content"]["summary"] = "Different scene contents"
        self.write(self.prepared_path, self.prepared)
        with self.assertRaisesRegex(GATE.ReviewGateError, "frame review is outdated"):
            self.check_recording()
        self.refresh_review()
        self.assertEqual(self.check_recording()["stage"], "recording")

    def test_scenario_change_requires_new_approval_and_updated_materials(self):
        self.approve_scenario()
        self.approve_prepared()
        self.scenario["content"]["story"]["payoff"] = "Show a different result"
        self.write(self.scenario_path, self.scenario)
        with self.assertRaisesRegex(GATE.ReviewGateError, "scenario approval is outdated"):
            self.check_recording()
        self.approve_scenario()
        with self.assertRaisesRegex(GATE.ReviewGateError, "different scenario version"):
            self.check_recording()
        self.prepared["scenario_sha256"] = GATE.canonical_sha256(self.scenario)
        self.refresh_review()
        self.assertEqual(self.check_recording()["stage"], "recording")

    def test_same_content_reapproved_scenario_preserves_material_checks(self):
        self.approve_scenario()
        self.approve_prepared()
        receipt_path = self.approvals / "scenario.approval.json"
        original = receipt_path.read_bytes()
        original_hash = GATE.canonical_sha256(json.loads(original))
        new_decision = self.decision(self.scenario_path, reference="test://new-human-decision")
        GATE.approve_review(self.scenario_path, None, self.approvals, new_decision)
        archived = self.approvals / "history" / f"scenario-{original_hash}.json"
        self.assertEqual(archived.read_bytes(), original)
        self.assertEqual(json.loads(receipt_path.read_bytes())["human_decision"]["reference"],
                         "test://new-human-decision")
        self.assertEqual(self.check_recording()["stage"], "recording")

    def test_identical_scenario_approval_replay_preserves_recording_permission(self):
        first_result = self.approve_scenario()
        self.approve_prepared()
        path = self.approvals / "scenario.approval.json"
        original = path.read_bytes()
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        before = self.check_recording()

        self.assertEqual(self.approve_scenario(), first_result)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mtime_ns, 1_000_000_000)
        self.assertEqual(self.check_recording(), before)
        self.assertFalse((self.approvals / "history").exists())

    def test_identical_prepared_approval_preserves_receipt_bytes_and_formatting(self):
        self.approve_scenario()
        first_result = self.approve_prepared()
        path = self.approvals / "prepared-content.approval.json"
        path.write_text(json.dumps(json.loads(path.read_bytes()), sort_keys=True), encoding="utf-8")
        original = path.read_bytes()
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        before = self.check_recording()

        self.assertEqual(self.approve_prepared(), first_result)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mtime_ns, 1_000_000_000)
        self.assertEqual(self.check_recording(), before)
        self.assertFalse((self.approvals / "history").exists())

    def test_same_prepared_decision_with_new_parent_approval_is_not_a_replay(self):
        self.approve_scenario()
        self.approve_prepared()
        path = self.approvals / "prepared-content.approval.json"
        original = path.read_bytes()
        original_hash = GATE.canonical_sha256(json.loads(original))
        new_decision = self.decision(self.scenario_path, reference="test://new-human-decision")
        GATE.approve_review(self.scenario_path, None, self.approvals, new_decision)

        self.approve_prepared()
        updated = json.loads(path.read_bytes())
        self.assertNotEqual(updated["scenario_approval_sha256"],
                            json.loads(original)["scenario_approval_sha256"])
        self.assertEqual((self.approvals / "history" / f"prepared-content-{original_hash}.json").read_bytes(),
                         original)
        self.assertEqual(self.check_recording()["stage"], "recording")

    def test_corrupt_existing_receipt_is_not_silently_overwritten(self):
        self.approve_scenario()
        path = self.approvals / "scenario.approval.json"
        valid = json.loads(path.read_bytes())
        invalid_receipts = [b"{not-json", b'{}',
                            json.dumps({**valid, "approved_at": ""}).encode(),
                            json.dumps({**valid, "human_decision": {}}).encode(),
                            json.dumps({**valid, "files": [None]}).encode()]
        for corrupted in invalid_receipts:
            with self.subTest(corrupted=corrupted):
                path.write_bytes(corrupted)
                with self.assertRaises(GATE.ReviewGateError):
                    self.approve_scenario()
                self.assertEqual(path.read_bytes(), corrupted)
                self.assertFalse((self.approvals / "history").exists())

    def test_conflicting_archive_does_not_replace_active_receipt(self):
        self.approve_scenario()
        path = self.approvals / "scenario.approval.json"
        original = path.read_bytes()
        original_hash = GATE.canonical_sha256(json.loads(original))
        archive = self.approvals / "history" / f"scenario-{original_hash}.json"
        archive.parent.mkdir()
        archive.write_bytes(b"Unexpected existing archive")
        new_decision = self.decision(self.scenario_path, reference="test://new-human-decision")
        with self.assertRaisesRegex(GATE.ReviewGateError, "archived approval differs"):
            GATE.approve_review(self.scenario_path, None, self.approvals, new_decision)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(archive.read_bytes(), b"Unexpected existing archive")

    def test_reformatted_json_preserves_approval(self):
        self.approve_scenario()
        self.approve_prepared()
        self.prepared_path.write_text(json.dumps(self.prepared, sort_keys=True), encoding="utf-8")
        self.assertEqual(self.check_recording()["stage"], "recording")

    def correction_fixture(self):
        # Synthetic correction instructions exist only inside this test folder.
        self.approve_scenario()
        self.approve_prepared()
        baseline = self.check_recording()
        self.asset.write_text("Corrected synthetic preparation", encoding="utf-8")
        self.prepared["files"][0]["sha256"] = GATE.file_sha256(self.asset)
        self.refresh_review()
        decision = {"decision": "correction_requested",
                    "scenario_approval_sha256": baseline["scenario_approval_sha256"],
                    "base_prepared_approval_sha256": GATE.canonical_sha256(
                        json.loads((self.approvals / "prepared-content.approval.json").read_text())),
                    "target_prepared_sha256": GATE.load_manifest(self.prepared_path)["manifest_sha256"],
                    "instructions": [{"source": "test-fixture", "reference": "test://requested-correction",
                                      "quote": "Synthetic request to remove keyboard suggestions",
                                      "scope": "Disable suggestions without changing the story"}]}
        decision_path = self.root / "correction-decision.json"
        self.write(decision_path, decision)
        return decision_path, self.root / "correction-authorization.json"

    def authorize_fixture_correction(self, decision, revision):
        return GATE.authorize_correction(self.scenario_path, self.prepared_path,
                                         self.approvals, decision, revision)

    def test_requested_correction_allows_recording_without_claiming_target_was_reviewed(self):
        decision, revision = self.correction_fixture()
        baseline = {path: path.read_bytes() for path in self.approvals.glob("*.approval.json")}
        self.authorize_fixture_correction(decision, revision)
        self.assertEqual(self.check_recording()["stage"], "recording")
        allowed = GATE.check_review_gate(self.scenario_path, self.prepared_path, self.approvals, revision)
        self.assertEqual(allowed["stage"], "recording")
        self.assertEqual(allowed["authorization_kind"], "requested_correction")
        self.assertFalse(allowed["target_user_reviewed"])
        self.assertNotIn("prepared_approval_sha256", allowed)
        self.assertEqual({path: path.read_bytes() for path in baseline}, baseline)
        self.assertFalse((self.approvals / "history").exists())

    def test_correction_authorization_replay_is_read_only_and_cannot_replace_other_files(self):
        decision, revision = self.correction_fixture()
        first = self.authorize_fixture_correction(decision, revision)
        original = revision.read_bytes()
        os.utime(revision, ns=(1_000_000_000, 1_000_000_000))
        self.assertEqual(self.authorize_fixture_correction(decision, revision), first)
        self.assertEqual(revision.read_bytes(), original)
        self.assertEqual(revision.stat().st_mtime_ns, 1_000_000_000)
        baseline = self.approvals / "prepared-content.approval.json"
        baseline_bytes = baseline.read_bytes()
        with self.assertRaisesRegex(GATE.ReviewGateError, "already exists"):
            self.authorize_fixture_correction(decision, baseline)
        self.assertEqual(baseline.read_bytes(), baseline_bytes)

    def test_correction_decision_requires_exact_hashes_and_complete_instructions(self):
        decision_path, revision = self.correction_fixture()
        original = json.loads(decision_path.read_bytes())
        changes = [(key, "0" * 64) for key in ("scenario_approval_sha256",
                   "base_prepared_approval_sha256", "target_prepared_sha256")]
        changes += [("decision", "approved"), ("instructions", [])]
        for key, value in changes:
            with self.subTest(key=key):
                self.write(decision_path, {**original, key: value})
                with self.assertRaises(GATE.ReviewGateError):
                    self.authorize_fixture_correction(decision_path, revision)
                self.assertFalse(revision.exists())
        for key in ("source", "reference", "quote", "scope"):
            with self.subTest(instruction_field=key):
                changed = deepcopy(original)
                changed["instructions"][0][key] = " "
                self.write(decision_path, changed)
                with self.assertRaisesRegex(GATE.ReviewGateError, key):
                    self.authorize_fixture_correction(decision_path, revision)
                self.assertFalse(revision.exists())

    def test_correction_requires_both_existing_baseline_approvals(self):
        decision, revision = self.correction_fixture()
        for filename in ("scenario.approval.json", "prepared-content.approval.json"):
            with self.subTest(filename=filename):
                path = self.approvals / filename
                original = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises(GATE.ReviewGateError):
                        self.authorize_fixture_correction(decision, revision)
                    self.assertFalse(revision.exists())
                finally:
                    path.write_bytes(original)

    def test_correction_rejects_replaced_base_receipt_and_incomplete_baseline(self):
        decision, revision = self.correction_fixture()
        self.authorize_fixture_correction(decision, revision)
        path = self.approvals / "prepared-content.approval.json"
        original = json.loads(path.read_bytes())
        for key, value in (("approved_at", "different-recorded-time"), ("files", []),
                           ("human_decision", {}), ("scenario_id", "another-story"),
                           ("scenario_approval_sha256", "0" * 64)):
            with self.subTest(key=key):
                self.write(path, {**original, key: value})
                with self.assertRaises(GATE.ReviewGateError):
                    GATE.check_review_gate(self.scenario_path, self.prepared_path, self.approvals, revision)

    def test_correction_does_not_authorize_changed_scenario_or_target(self):
        decision, revision = self.correction_fixture()
        self.authorize_fixture_correction(decision, revision)
        self.scenario["content"]["story"]["action"] = "A different action"
        self.write(self.scenario_path, self.scenario)
        with self.assertRaisesRegex(GATE.ReviewGateError, "scenario approval is outdated"):
            GATE.check_review_gate(self.scenario_path, self.prepared_path, self.approvals, revision)
        self.scenario["content"]["story"]["action"] = "Change the pickup time"
        self.write(self.scenario_path, self.scenario)
        self.asset.write_text("An unrequested further change", encoding="utf-8")
        self.prepared["files"][0]["sha256"] = GATE.file_sha256(self.asset)
        self.refresh_review()
        with self.assertRaisesRegex(GATE.ReviewGateError, "target_prepared_sha256"):
            GATE.check_review_gate(self.scenario_path, self.prepared_path, self.approvals, revision)

    def test_correction_authorization_requires_matching_files_and_target_manifest(self):
        decision, revision = self.correction_fixture()
        self.authorize_fixture_correction(decision, revision)
        original = json.loads(revision.read_bytes())
        self.write(revision, {**original, "files": []})
        with self.assertRaisesRegex(GATE.ReviewGateError, "incomplete"):
            GATE.check_review_gate(self.scenario_path, self.prepared_path, self.approvals, revision)
        with self.assertRaisesRegex(GATE.ReviewGateError, "prepared-content"):
            GATE.check_review_gate(self.scenario_path, None, self.approvals, revision)

    def test_correction_does_not_bypass_current_prepared_material_review(self):
        decision, revision = self.correction_fixture()
        self.prepared["content"]["summary"] = "Changed since the synthetic internal review"
        self.write(self.prepared_path, self.prepared)
        with self.assertRaisesRegex(GATE.ReviewGateError, "frame review is outdated"):
            self.authorize_fixture_correction(decision, revision)
        self.assertFalse(revision.exists())

    def test_correction_cli_authorizes_and_checks_additively(self):
        decision, revision = self.correction_fixture()
        common = ["--scenario", str(self.scenario_path), "--prepared-content", str(self.prepared_path),
                  "--approval-dir", str(self.approvals)]
        authorized = subprocess.run([sys.executable, str(SCRIPT), "authorize-correction", *common,
                                     "--decision-file", str(decision), "--output", str(revision)],
                                    capture_output=True, text=True)
        self.assertEqual(authorized.returncode, 0, authorized.stderr)
        checked = subprocess.run([sys.executable, str(SCRIPT), "check", *common,
                                  "--revision-file", str(revision)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["authorization_kind"], "requested_correction")

    def test_nonapproval_missing_quote_and_wrong_digest_are_rejected(self):
        for overrides in ({"decision": "rejected"}, {"quote": " "},
                          {"reference": ""}, {"source": ""},
                          {"manifest_sha256": "0" * 64}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(GATE.ReviewGateError):
                    GATE.approve_review(self.scenario_path, None, self.approvals,
                                        self.decision(self.scenario_path, **overrides))
                self.assertFalse(self.approvals.exists())

    def test_old_human_decision_cannot_approve_changed_manifest(self):
        decision_path = self.decision(self.scenario_path)
        self.scenario["content"]["story"]["payoff"] = "Not what the human reviewed"
        self.write(self.scenario_path, self.scenario)
        with self.assertRaisesRegex(GATE.ReviewGateError, "different version of the materials"):
            GATE.approve_review(self.scenario_path, None, self.approvals, decision_path)
        self.assertFalse(self.approvals.exists())

    def test_missing_file_and_empty_prepared_files_fail(self):
        self.asset.unlink()
        with self.assertRaisesRegex(GATE.ReviewGateError, "Could not read file"):
            GATE.load_manifest(self.prepared_path)
        self.prepared["files"] = []
        self.write(self.prepared_path, self.prepared)
        with self.assertRaisesRegex(GATE.ReviewGateError, "at least one"):
            GATE.load_manifest(self.prepared_path)

    def test_partial_or_forged_receipt_without_human_decision_fails(self):
        self.approve_scenario()
        path = self.approvals / "scenario.approval.json"
        receipt = json.loads(path.read_text())
        del receipt["human_decision"]
        self.write(path, receipt)
        with self.assertRaisesRegex(GATE.ReviewGateError, "Incomplete approval record"):
            self.check_recording()

    def test_cli_check_fails_closed_and_fingerprint_is_read_only(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "check", "--scenario",
                                 str(self.scenario_path), "--prepared-content", str(self.prepared_path),
                                 "--approval-dir", str(self.approvals)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Missing scenario approval", result.stderr)
        for path, manifest in ((self.scenario_path, self.scenario), (self.prepared_path, self.prepared)):
            fingerprint = subprocess.run([sys.executable, str(SCRIPT), "fingerprint", "--manifest",
                                          str(path)], capture_output=True, text=True)
            self.assertEqual(fingerprint.returncode, 0, fingerprint.stderr)
            self.assertEqual(json.loads(fingerprint.stdout)["manifest_sha256"],
                             GATE.canonical_sha256(manifest))
        approval = subprocess.run([sys.executable, str(SCRIPT), "approve", "--scenario",
                                   str(self.scenario_path), "--approval-dir", str(self.approvals)],
                                  capture_output=True, text=True)
        self.assertEqual(approval.returncode, 2)
        self.assertIn("--decision-file", approval.stderr)
        self.assertFalse(self.approvals.exists())

    def test_v2_story_and_scene_plan_are_required(self):
        invalid = [(("language",), " "), (("story",), None), (("scene_plan",), None)]
        invalid += [(("story", key), " ") for key in ("context", "entry_point", "action", "payoff", "loop")]
        invalid += [(("scene_plan", key), None) for key in ("setting", "actors", "supporting_content", "continuity")]
        for keys, value in invalid:
            with self.subTest(keys=keys):
                manifest = deepcopy(self.scenario)
                target = manifest["content"]
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                self.write(self.scenario_path, manifest)
                with self.assertRaisesRegex(GATE.ReviewGateError, "content"):
                    GATE.load_manifest(self.scenario_path)

    def test_v2_prepared_requires_language_actual_scene_and_storyboard(self):
        for field in ("language", "scene_content", "storyboard", "story_review"):
            with self.subTest(field=field):
                manifest = deepcopy(self.prepared)
                del manifest["content"][field]
                self.write(self.prepared_path, manifest)
                with self.assertRaisesRegex(GATE.ReviewGateError, "content"):
                    GATE.load_manifest(self.prepared_path)

    def test_each_storyboard_role_is_required(self):
        for role in ("context", "entry", "action", "result"):
            with self.subTest(role=role):
                manifest = deepcopy(self.prepared)
                manifest["content"]["storyboard"] = [frame for frame in manifest["content"]["storyboard"]
                                                       if frame["role"] != role]
                self.write(self.prepared_path, manifest)
                with self.assertRaisesRegex(GATE.ReviewGateError, f"Missing storyboard roles: {role}"):
                    GATE.load_manifest(self.prepared_path)

    def test_storyboard_fields_and_ids_are_validated(self):
        for field, value in (("id", " "), ("role", ["context", "entry"]),
                             ("file", ""), ("description", " "), ("id", "entry")):
            with self.subTest(field=field, value=value):
                manifest = deepcopy(self.prepared)
                manifest["content"]["storyboard"][0][field] = value
                self.write(self.prepared_path, manifest)
                with self.assertRaisesRegex(GATE.ReviewGateError, "storyboard"):
                    GATE.load_manifest(self.prepared_path)

    def test_storyboard_file_must_resolve_to_a_declared_verified_file(self):
        extra = self.root / "other" / "preview.png"
        extra.parent.mkdir()
        extra.write_text("Exists, but was not declared", encoding="utf-8")
        self.prepared["content"]["storyboard"][0]["file"] = "other/preview.png"
        self.refresh_review()
        with self.assertRaisesRegex(GATE.ReviewGateError, "not listed"):
            GATE.load_manifest(self.prepared_path)
        self.prepared["content"]["storyboard"][0]["file"] = "./preview.png"
        self.refresh_review()
        # Several semantic roles may intentionally share one real frame.
        self.assertEqual(len(GATE.load_manifest(self.prepared_path)["files"]), len(self.prepared["files"]))

    def test_empty_supporting_content_requires_a_reason_in_both_manifests(self):
        for original, path, scene_key in ((self.scenario, self.scenario_path, "scene_plan"),
                                           (self.prepared, self.prepared_path, "scene_content")):
            for reason in (None, "", " ", "A new conversation is intentionally empty"):
                with self.subTest(kind=original["kind"], reason=reason):
                    manifest = deepcopy(original)
                    scene = manifest["content"][scene_key]
                    scene["supporting_content"] = []
                    if reason is not None:
                        scene["intentional_empty_reason"] = reason
                    if manifest["kind"] == "prepared-content":
                        manifest["content"]["story_review"]["materials_sha256"] = GATE.story_materials_sha256(manifest)
                    self.write(path, manifest)
                    if reason and reason.strip():
                        GATE.load_manifest(path)
                    else:
                        with self.assertRaisesRegex(GATE.ReviewGateError, "intentional_empty_reason"):
                            GATE.load_manifest(path)

    def test_scene_lists_reject_blank_entries_and_wrong_types(self):
        for field in ("actors", "supporting_content"):
            for invalid in ("A person", [" "], [1], None):
                with self.subTest(field=field, invalid=invalid):
                    manifest = deepcopy(self.prepared)
                    manifest["content"]["scene_content"][field] = invalid
                    self.write(self.prepared_path, manifest)
                    with self.assertRaisesRegex(GATE.ReviewGateError, field):
                        GATE.load_manifest(self.prepared_path)

    def test_frames_only_review_must_be_complete_and_passed(self):
        invalid = [(key, value) for key, value in (("status", "pending"), ("status", True),
                   ("basis", "scenario_and_frames"), ("reviewer", " "), ("reference", ""),
                   ("materials_sha256", "bad-hash"), ("answers", {}))]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                manifest = deepcopy(self.prepared)
                manifest["content"]["story_review"][key] = value
                self.write(self.prepared_path, manifest)
                with self.assertRaisesRegex(GATE.ReviewGateError, "story_review"):
                    GATE.load_manifest(self.prepared_path)
        for key in ("context", "entry_point", "action", "result", "believability", "continuity"):
            with self.subTest(answer=key):
                manifest = deepcopy(self.prepared)
                manifest["content"]["story_review"]["answers"][key] = " "
                self.write(self.prepared_path, manifest)
                with self.assertRaisesRegex(GATE.ReviewGateError, key):
                    GATE.load_manifest(self.prepared_path)

    def test_story_materials_digest_excludes_only_review_without_mutating_input(self):
        original = deepcopy(self.prepared)
        without_review = deepcopy(original)
        del without_review["content"]["story_review"]
        expected = GATE.canonical_sha256(without_review)
        self.assertEqual(GATE.story_materials_sha256(self.prepared), expected)
        self.assertEqual(GATE.story_materials_sha256(without_review), expected)
        self.assertEqual(self.prepared, original)
        changed_review = deepcopy(original)
        changed_review["content"]["story_review"]["reference"] = "test://different-review"
        self.assertEqual(GATE.story_materials_sha256(changed_review), expected)
        changed_top_level = {**original, "extra_material": "Must also be reviewed"}
        self.assertNotEqual(GATE.story_materials_sha256(changed_top_level), expected)

    def test_changed_description_scene_data_or_scenario_digest_invalidates_review(self):
        changes = (("description",), ("scene_content",), ("scenario_sha256",), ("language",))
        for (change,) in changes:
            with self.subTest(change=change):
                manifest = deepcopy(self.prepared)
                if change == "description":
                    manifest["content"]["storyboard"][0]["description"] = "A different reading of the frame"
                elif change == "scene_content":
                    manifest["content"]["scene_content"]["setting"] = "Another conversation"
                elif change == "language":
                    manifest["content"]["language"] = "fr"
                else:
                    manifest["scenario_sha256"] = "0" * 64
                self.write(self.prepared_path, manifest)
                with self.assertRaisesRegex(GATE.ReviewGateError, "frame review is outdated"):
                    GATE.load_manifest(self.prepared_path)

    def test_updated_frame_hash_requires_a_new_visual_review(self):
        self.asset.write_text("Changed frame bytes", encoding="utf-8")
        self.prepared["files"][0]["sha256"] = GATE.file_sha256(self.asset)
        self.write(self.prepared_path, self.prepared)
        with self.assertRaisesRegex(GATE.ReviewGateError, "frame review is outdated"):
            GATE.load_manifest(self.prepared_path)

    def test_changing_only_internal_review_needs_no_second_human_approval(self):
        self.approve_scenario()
        self.approve_prepared()
        self.prepared["content"]["story_review"]["reference"] = "test://another-review"
        self.write(self.prepared_path, self.prepared)
        GATE.load_manifest(self.prepared_path)
        self.assertEqual(self.check_recording()["stage"], "recording")

    def test_legacy_load_and_fingerprint_work_but_recording_and_prepared_approval_are_blocked(self):
        # Include both mixed-version combinations, even with valid old receipts.
        for scenario_version, prepared_version in ((1, 1), (1, 2), (2, 1)):
            with self.subTest(scenario_version=scenario_version, prepared_version=prepared_version):
                self.scenario["schema_version"] = scenario_version
                self.prepared["schema_version"] = prepared_version
                self.prepared["scenario_sha256"] = GATE.canonical_sha256(self.scenario)
                self.write(self.scenario_path, self.scenario)
                self.refresh_review()
                snapshot = GATE.load_manifest(self.prepared_path)
                self.assertEqual(snapshot["manifest_sha256"], GATE.canonical_sha256(self.prepared))
                fingerprint = subprocess.run([sys.executable, str(SCRIPT), "fingerprint", "--manifest",
                                              str(self.prepared_path)], capture_output=True, text=True)
                self.assertEqual(fingerprint.returncode, 0, fingerprint.stderr)
                self.approve_scenario()
                scenario_receipt = json.loads((self.approvals / "scenario.approval.json").read_text())
                historical_receipt = {"schema_version": 1, "kind": "prepared-content",
                    "scenario_id": "test-only", "manifest_sha256": snapshot["manifest_sha256"],
                    "files": snapshot["files"], "scenario_approval_sha256": GATE.canonical_sha256(scenario_receipt),
                    "approved_at": "2020-01-01T00:00:00Z",
                    "human_decision": json.loads(self.decision(self.prepared_path).read_text())}
                self.write(self.approvals / "prepared-content.approval.json", historical_receipt)
                with self.assertRaisesRegex(GATE.ReviewGateError, "schema_version 2 in both"):
                    self.check_recording()
                before = (self.approvals / "prepared-content.approval.json").read_bytes()
                with self.assertRaisesRegex(GATE.ReviewGateError, "schema_version 2 in both"):
                    self.approve_prepared()
                self.assertEqual((self.approvals / "prepared-content.approval.json").read_bytes(), before)

    def test_minimal_historical_v1_stays_readable_without_story_fields(self):
        self.scenario = {"schema_version": 1, "kind": "scenario", "scenario_id": "legacy",
                         "content": {"story": "Old story format"}, "files": []}
        self.prepared = {"schema_version": 1, "kind": "prepared-content", "scenario_id": "legacy",
                         "scenario_sha256": GATE.canonical_sha256(self.scenario),
                         "content": {"summary": "Old prepared package"},
                         "files": [{"path": "preview.png", "sha256": GATE.file_sha256(self.asset)}]}
        for path, manifest in ((self.scenario_path, self.scenario), (self.prepared_path, self.prepared)):
            self.write(path, manifest)
            self.assertEqual(GATE.load_manifest(path)["manifest_sha256"], GATE.canonical_sha256(manifest))
        self.assertFalse(self.approvals.exists())


if __name__ == "__main__":
    unittest.main()
