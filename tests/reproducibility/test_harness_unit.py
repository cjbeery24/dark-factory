"""Unit tests for ``tests/reproducibility/harness.py``.

These tests do NOT invoke the LLM — they validate the harness's
parsers, anchor normalizer, evaluator, and aggregator against
synthetic outputs. They run on every CI invocation alongside the
rest of the unit suite.

The LLM-invoking reproducibility tests live in
``test_typology_catch_rate.py`` with a ``@pytest.mark.llm`` decorator
and are excluded from default runs via the ``llm`` marker.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from tests.reproducibility.harness import (
    DENSIFIER_SKILL_FILE,
    FIXTURES_ROOT,
    POSTGRES_VALIDATOR_ADDENDUM,
    TYPOLOGY_FILE,
    VALID_TYPOLOGY_SLOTS,
    VALIDATOR_AGENT_FILE,
    VALIDATOR_BARRIER_PROMPT,
    Fixture,
    RunEvaluation,
    _decode_partial,
    _extract_step_45_section,
    _normalize_field_name,
    aggregate_fixture,
    build_densifier_prompt,
    build_validator_prompt,
    evaluate_run,
    extract_result_text,
    load_fixtures,
    normalize_anchor,
    parse_densifier_output,
    parse_validator_output,
)


class TestFixtureLoader(TestCase):
    def test_all_fixtures_load(self) -> None:
        fixtures = load_fixtures()
        # 8 TP + 4 standalone refinement = 12 fixtures (per Neo's
        # redistribution; T4 group includes one refinement counter-example
        # at type-folder level, but it is still TP-shape — `expected_catch`
        # tracking is in the JSON oracle).
        self.assertEqual(len(fixtures), 12)
        ids = [f.fixture_id for f in fixtures]
        self.assertEqual(len(set(ids)), 12, "fixture_ids must be unique")

    def test_fixture_typology_coverage(self) -> None:
        fixtures = load_fixtures()
        true_positive_categories = [
            f.type_category for f in fixtures if f.expected_catch
        ]
        # Per Neo: 2x T1 + 1x T2 + 1x T3 + 2x T4 (TP + borderline) + 1x T5 = 7 TP.
        # The T4 refinement-internals fixture has expected_catch=False, so
        # it counts under refinement here.
        from collections import Counter
        c = Counter(true_positive_categories)
        self.assertEqual(c["T1"], 2, f"T1 TP coverage: got {c['T1']}, want 2")
        self.assertEqual(c["T2"], 1)
        self.assertEqual(c["T3"], 1)
        self.assertEqual(c["T4"], 2, "T4 TP + borderline should both expected_catch=True")
        self.assertEqual(c["T5"], 1)

    def test_refinement_fixtures_present(self) -> None:
        fixtures = load_fixtures()
        refinements = [f for f in fixtures if not f.expected_catch]
        # 1 inside T4 (refinement-internals) + 4 standalone = 5 total.
        self.assertEqual(len(refinements), 5)
        for f in refinements:
            self.assertEqual(f.permitted_slots, [])
            self.assertIsNone(f.expected_anchor_pattern)

    def test_active_adapters_are_lists(self) -> None:
        for f in load_fixtures():
            self.assertIsInstance(f.active_adapters, list)
            for a in f.active_adapters:
                self.assertIsInstance(a, str)

    def test_borderline_fixture_declares_multi_permitted_slots(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        borderline = fixtures.get("T4-borderline-entity")
        self.assertIsNotNone(borderline)
        self.assertEqual(set(borderline.permitted_slots), {"T1", "T4"})
        self.assertTrue(borderline.discriminator_stability_required)


class TestDecodePartial(TestCase):
    """Linus H1 — the timeout-path decoder must handle bytes / str / None
    without crashing. CPython's ``subprocess.TimeoutExpired.std{out,err}``
    is documented as ``bytes | None`` regardless of ``text=True``, but
    behavior could shift in a future release."""

    def test_none_returns_empty(self) -> None:
        self.assertEqual(_decode_partial(None), "")

    def test_bytes_decoded(self) -> None:
        self.assertEqual(_decode_partial(b"hello"), "hello")

    def test_invalid_utf8_replaced(self) -> None:
        self.assertEqual(_decode_partial(b"\xff\xfe"), "��")

    def test_str_returned_as_is(self) -> None:
        self.assertEqual(_decode_partial("already-text"), "already-text")


class TestNormalizeFieldName(TestCase):
    """Linus H2 — hyphens and underscores must be treated as equivalent
    in field comparison. Specs use both ``side-effects`` and
    ``side_effects``; without normalization a real catch on the
    hyphen form is silently rejected."""

    def test_hyphens_become_underscores(self) -> None:
        self.assertEqual(_normalize_field_name("side-effects"), "side_effects")
        self.assertEqual(_normalize_field_name("processing-input"), "processing_input")

    def test_lowercase(self) -> None:
        self.assertEqual(_normalize_field_name("Processing"), "processing")
        self.assertEqual(_normalize_field_name("Side-Effects"), "side_effects")

    def test_underscore_passes_through(self) -> None:
        self.assertEqual(_normalize_field_name("side_effects"), "side_effects")

    def test_none_passes_through(self) -> None:
        self.assertIsNone(_normalize_field_name(None))


class TestAnchorNormalizer(TestCase):
    def test_full_form(self) -> None:
        out = normalize_anchor("FR-3.processing@line62")
        self.assertEqual(out["fr_id"], "FR-3")
        self.assertEqual(out["field"], "processing")
        self.assertEqual(out["line"], 62)

    def test_hyphenated_field(self) -> None:
        # Linus H2 reproducer — ``FR-3.side-effects`` previously truncated
        # to ``side`` because the regex only matched [A-Za-z_]+.
        out = normalize_anchor("FR-3.side-effects@line17")
        self.assertEqual(out["fr_id"], "FR-3")
        self.assertEqual(out["field"], "side-effects")
        self.assertEqual(out["line"], 17)

    def test_hyphenated_field_no_line(self) -> None:
        out = normalize_anchor("FR-3.side-effects")
        self.assertEqual(out["fr_id"], "FR-3")
        self.assertEqual(out["field"], "side-effects")

    def test_field_and_line_optional(self) -> None:
        out = normalize_anchor("FR-3")
        self.assertEqual(out["fr_id"], "FR-3")
        self.assertNotIn("field", out)
        self.assertNotIn("line", out)

    def test_field_no_line(self) -> None:
        out = normalize_anchor("FR-3.side_effects")
        self.assertEqual(out["fr_id"], "FR-3")
        self.assertEqual(out["field"], "side_effects")
        self.assertNotIn("line", out)

    def test_line_no_field(self) -> None:
        out = normalize_anchor("FR-3@line17")
        self.assertEqual(out["fr_id"], "FR-3")
        self.assertEqual(out["line"], 17)
        self.assertNotIn("field", out)

    def test_ac_id(self) -> None:
        out = normalize_anchor("AC-24")
        self.assertEqual(out["ac_id"], "AC-24")

    def test_ec_id(self) -> None:
        out = normalize_anchor("EC-7")
        self.assertEqual(out["ec_id"], "EC-7")

    def test_empty(self) -> None:
        out = normalize_anchor("")
        self.assertEqual(out["raw"], "")
        self.assertNotIn("fr_id", out)


class TestPromptBuilder(TestCase):
    def test_validator_prompt_includes_full_agent_def_and_barrier(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        fixture = fixtures["T1-real-fr3"]
        system, stdin = build_validator_prompt(fixture)
        # Full agent definition in system-append (production-faithful
        # per Neo correction #1; iter-2 attempted trimming but
        # regressed T2 and T3 catch rates so reverted).
        self.assertIn("Cross-Section Contradiction Procedure", system)
        self.assertIn("Pass 1", system)
        self.assertIn("Pass 2", system)
        self.assertIn("Pass 3", system)
        # Other agent procedures present (full agent.md, not the
        # trimmed slice).
        self.assertIn("Precision Analysis", system)
        self.assertIn("Output Format", system)
        # Barrier prompt (verbatim from barriers.py:189-197).
        self.assertIn("INFORMATION BARRIER", system)
        self.assertIn(VALIDATOR_BARRIER_PROMPT, system)
        # Postgres adapter addendum injected when fixture declares
        # active_adapters: ["postgres"].
        addendum_text = POSTGRES_VALIDATOR_ADDENDUM.read_text(encoding="utf-8")
        self.assertIn(addendum_text[:200], system)
        # Stdin contains the fixture content.
        self.assertIn(fixture.md_content, stdin)
        # Mandatory-steps imperative directive (Session 2 iteration-1).
        self.assertIn("MANDATORY STEPS", stdin)
        self.assertIn("Pass 1", stdin)
        self.assertIn("Pass 2", stdin)
        self.assertIn("Pass 3", stdin)
        self.assertIn("contradictions: []", stdin)

    def test_validator_prompt_skips_postgres_addendum_when_inactive(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        # T1-synthetic-audit declares active_adapters: [].
        fixture = fixtures["T1-synthetic-audit"]
        self.assertEqual(fixture.active_adapters, [])
        system, _ = build_validator_prompt(fixture)
        addendum_text = POSTGRES_VALIDATOR_ADDENDUM.read_text(encoding="utf-8")
        # First 200 chars of postgres addendum should NOT be in system —
        # this fixture has no postgres surface.
        self.assertNotIn(addendum_text[:200], system)

    def test_validator_prompt_emits_consistency_marker(self) -> None:
        fixture = next(iter(load_fixtures()))
        _, stdin = build_validator_prompt(fixture)
        self.assertIn("consistency=PASS", stdin)
        self.assertIn("consistency=FAIL", stdin)

    def test_densifier_prompt_extracts_step_45_section(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        fixture = fixtures["T1-real-fr3"]
        system, stdin = build_densifier_prompt(fixture)
        # Step 4.5 procedure heading is in system.
        self.assertIn("### Step 4.5:", system)
        # SSOT typology block (sentinel) is inside the extracted section.
        self.assertIn("BEGIN-CONTRADICTION-TYPOLOGY", system)
        self.assertIn("END-CONTRADICTION-TYPOLOGY", system)
        # Step 5 (the next section) MUST NOT appear — the slice should
        # stop exclusive of "### Step 5:".
        self.assertNotIn("### Step 5:", system)
        # Stdin frames as Step-3-just-finished (Neo correction #5).
        self.assertIn("Steps 1-4", stdin)
        self.assertIn("Step 3 emitted", stdin)
        # Output schema directive (the harness wants a parseable JSON
        # block).
        self.assertIn("```json", stdin)

    def test_step_45_section_extraction_idempotent(self) -> None:
        # The slice is content-addressable — calling twice gives the same
        # bytes. Catches accidental re-introduction of state in the
        # extractor.
        a = _extract_step_45_section()
        b = _extract_step_45_section()
        self.assertEqual(a, b)


class TestSSOTSync(TestCase):
    """Both surfaces' system prompts must include the SSOT block byte-identical.

    Mirrors ``tests/unit/test_consistency_status.py::TestTypologySSOTSync`` —
    that test pins the SSOT inside ``agents/spec-validator.md`` and the
    SKILL file. This test pins the SSOT inside the harness-built prompts.
    Drift defeats the harness-vs-production isomorphism (Neo correction #1
    sub-bullet).
    """

    _BEGIN = "<!-- BEGIN-CONTRADICTION-TYPOLOGY -->"
    _END = "<!-- END-CONTRADICTION-TYPOLOGY -->"

    def _block(self, text: str) -> str:
        s = text.index(self._BEGIN)
        e = text.index(self._END) + len(self._END)
        return text[s:e]

    def test_canonical_block_is_in_validator_prompt(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        fixture = fixtures["T1-real-fr3"]
        canonical = self._block(TYPOLOGY_FILE.read_text(encoding="utf-8"))
        system, _ = build_validator_prompt(fixture)
        self.assertEqual(self._block(system), canonical)

    def test_canonical_block_is_in_densifier_prompt(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        fixture = fixtures["T1-real-fr3"]
        canonical = self._block(TYPOLOGY_FILE.read_text(encoding="utf-8"))
        system, _ = build_densifier_prompt(fixture)
        self.assertEqual(self._block(system), canonical)


class TestExtractResultText(TestCase):
    def test_envelope_unwraps(self) -> None:
        envelope = json.dumps({"result": "hello world", "type": "result"})
        self.assertEqual(extract_result_text(envelope), "hello world")

    def test_no_result_field_returns_raw(self) -> None:
        envelope = json.dumps({"other": "field"})
        self.assertEqual(extract_result_text(envelope), envelope)

    def test_non_json_returns_raw(self) -> None:
        self.assertEqual(extract_result_text("plain text"), "plain text")

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(extract_result_text(""), "")
        self.assertEqual(extract_result_text("   \n  "), "")


class TestParseValidatorOutput(TestCase):
    _LEDGER_YAML = """
spec: T1-real-fr3
fr_ledgers:
  - fr_id: FR-3
    claims:
      - id: FR-3.c1
        subject: "baseline migration"
        predicate: creates
        polarity: negative
        object: "tables"
        anchor: "FR-3.processing@line15"
      - id: FR-3.c2
        subject: "baseline migration"
        predicate: creates
        polarity: positive
        object: "__drizzle_migrations table"
        anchor: "FR-3.side_effects@line17"
contradictions:
  - claim_a: FR-3.c1
    claim_b: FR-3.c2
    type: T1
    type_name: entity_creation_polarity_mismatch
    discriminator: "__drizzle_migrations table existence"
"""

    def test_extracts_typed_contradictions_and_resolves_anchors(self) -> None:
        text = f"```yaml\n{self._LEDGER_YAML.strip()}\n```\nconsistency=FAIL\n"
        result = parse_validator_output(text)
        self.assertEqual(len(result), 1)
        c = result[0]
        self.assertEqual(c["type"], "T1")
        self.assertEqual(c["_anchor_a"], "FR-3.processing@line15")
        self.assertEqual(c["_anchor_b"], "FR-3.side_effects@line17")

    def test_empty_contradictions_list(self) -> None:
        text = "```yaml\nspec: x\nfr_ledgers: []\ncontradictions: []\n```\n"
        result = parse_validator_output(text)
        self.assertEqual(result, [])

    def test_no_yaml_fence_returns_empty(self) -> None:
        self.assertEqual(parse_validator_output("no yaml here"), [])

    def test_skips_unrelated_yaml_fences(self) -> None:
        text = (
            "```yaml\nfoo: bar\n```\n\n"  # First fence has no contradictions key
            f"```yaml\n{self._LEDGER_YAML.strip()}\n```\n"
        )
        result = parse_validator_output(text)
        self.assertEqual(len(result), 1)


class TestParseDensifierOutput(TestCase):
    _STEP_45_JSON = json.dumps(
        {
            "step": "4.5",
            "spec_name": "T1-real-fr3",
            "checks_performed": 5,
            "contradictions": [
                {
                    "ac_id": "AC-24",
                    "ac_text": "AC-24 text",
                    "parent_fr": "FR-3",
                    "conflicting_anchor": "FR-3.processing@line15",
                    "conflicting_text": "creates no tables and applies no policies",
                    "type": "T1",
                    "type_name": "entity_creation_polarity_mismatch",
                    "discriminator": "__drizzle_migrations table existence",
                    "refinement_check": "AC-24 asserts presence; FR-3 asserts absence — not refinement",
                }
            ],
            "advisory_only": [],
        },
        indent=2,
    )

    def test_extracts_step_45_block(self) -> None:
        text = f"```json\n{self._STEP_45_JSON}\n```\n"
        result = parse_densifier_output(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "T1")
        self.assertEqual(result[0]["conflicting_anchor"], "FR-3.processing@line15")

    def test_skips_non_step_45_json(self) -> None:
        bogus = json.dumps({"step": "3", "contradictions": [{"type": "T1"}]})
        text = f"```json\n{bogus}\n```\n"
        self.assertEqual(parse_densifier_output(text), [])

    def test_no_json_fence_returns_empty(self) -> None:
        self.assertEqual(parse_densifier_output("no json here"), [])


class TestEvaluateRunTruePositive(TestCase):
    def setUp(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        self.t1 = fixtures["T1-real-fr3"]

    def _validator_entry(self, type_value: str = "T1", line: int = 17) -> dict:
        return {
            "claim_a": "FR-3.c1",
            "claim_b": "FR-3.c2",
            "type": type_value,
            "type_name": "entity_creation_polarity_mismatch",
            "discriminator": "__drizzle_migrations table existence",
            "_anchor_a": f"FR-3.processing@line15",
            "_anchor_b": f"FR-3.side_effects@line{line}",
        }

    def test_caught_when_anchor_and_slot_match(self) -> None:
        result = evaluate_run(self.t1, [self._validator_entry()], "validator")
        self.assertTrue(result.caught)
        self.assertEqual(result.slot, "T1")
        self.assertFalse(result.fp)

    def test_caught_with_lineless_anchor(self) -> None:
        # Real-world LLM behavior: validator often emits anchors without
        # line numbers (``FR-3.processing`` not ``FR-3.processing@line15``).
        # Match is at the fr_id+field level; line precision is reported
        # separately via ``line_precision`` on RunEvaluation.
        entry = {
            "claim_a": "FR-3.c1",
            "claim_b": "FR-3.c2",
            "type": "T1",
            "type_name": "entity_creation_polarity_mismatch",
            "discriminator": "__drizzle_migrations existence",
            "_anchor_a": "FR-3.processing",
            "_anchor_b": "FR-3.side_effects",
        }
        result = evaluate_run(self.t1, [entry], "validator")
        self.assertTrue(result.caught)
        self.assertIsNone(result.line_precision)

    def test_caught_with_out_of_range_line_records_precision_failure(self) -> None:
        # Densifier sometimes echoes the SSOT worked example's literal
        # @line62 regardless of the fixture's actual line numbers. The
        # match still passes (correct fr_id + field + slot + discriminator),
        # but ``line_precision`` is False so the report can surface the
        # regression.
        entry = {
            "claim_a": "FR-3.c1",
            "claim_b": "FR-3.c2",
            "type": "T1",
            "type_name": "entity_creation_polarity_mismatch",
            "discriminator": "...",
            "_anchor_a": "FR-3.processing@line99",
            "_anchor_b": "FR-3.side_effects@line99",
        }
        result = evaluate_run(self.t1, [entry], "validator")
        self.assertTrue(result.caught)
        self.assertFalse(result.line_precision)

    def test_not_caught_when_wrong_field(self) -> None:
        # Wrong FR field — anchor is FR-3.input which is not in the
        # fixture's expected fields ["processing", "side_effects"].
        entry = {
            "claim_a": "FR-3.c1",
            "claim_b": "FR-3.c2",
            "type": "T1",
            "type_name": "entity_creation_polarity_mismatch",
            "discriminator": "...",
            "_anchor_a": "FR-3.input",
            "_anchor_b": "FR-3.input",
        }
        result = evaluate_run(self.t1, [entry], "validator")
        self.assertFalse(result.caught)

    def test_not_caught_when_slot_wrong(self) -> None:
        result = evaluate_run(
            self.t1, [self._validator_entry(type_value="T4")], "validator"
        )
        self.assertFalse(result.caught)

    def test_densifier_entry_caught(self) -> None:
        entry = {
            "ac_id": "AC-24",
            "parent_fr": "FR-3",
            "conflicting_anchor": "FR-3.processing@line15",
            "type": "T1",
            "type_name": "entity_creation_polarity_mismatch",
            "discriminator": "__drizzle_migrations table existence",
        }
        result = evaluate_run(self.t1, [entry], "densifier")
        self.assertTrue(result.caught)
        self.assertEqual(result.slot, "T1")

    def test_caught_when_model_emits_hyphen_field_form(self) -> None:
        # Linus H2 — fixture pattern declares ``side_effects`` but the
        # model emitted ``side-effects``. Normalization must accept it.
        entry = {
            "claim_a": "FR-3.c1",
            "claim_b": "FR-3.c2",
            "type": "T1",
            "type_name": "entity_creation_polarity_mismatch",
            "discriminator": "__drizzle_migrations existence",
            "_anchor_a": "FR-3.processing@line15",
            "_anchor_b": "FR-3.side-effects@line17",  # hyphen, not underscore
        }
        result = evaluate_run(self.t1, [entry], "validator")
        self.assertTrue(result.caught)


class TestEvaluateRunRefinement(TestCase):
    def setUp(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        self.refinement = fixtures["refinement-idempotent"]

    def test_no_contradictions_emitted_is_pass(self) -> None:
        result = evaluate_run(self.refinement, [], "validator")
        self.assertFalse(result.caught)
        self.assertFalse(result.fp)

    def test_any_emitted_contradiction_is_false_positive(self) -> None:
        spurious = {
            "claim_a": "x",
            "claim_b": "y",
            "type": "T1",
            "type_name": "entity_creation_polarity_mismatch",
            "discriminator": "spurious",
            "_anchor_a": "FR-1.processing@line15",
            "_anchor_b": "AC-1",
        }
        result = evaluate_run(self.refinement, [spurious], "validator")
        self.assertTrue(result.caught)
        self.assertTrue(result.fp)


class TestEvaluateRunBorderline(TestCase):
    def setUp(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        self.borderline = fixtures["T4-borderline-entity"]

    def _entry(self, type_value: str) -> dict:
        return {
            "claim_a": "FR-1.c1",
            "claim_b": "FR-1.c2",
            "type": type_value,
            "type_name": "wire_contract_shape_mismatch" if type_value == "T5" else "any",
            "discriminator": "registration endpoint creates tenants outside withTenantDb",
            "_anchor_a": "FR-1.processing@line15",
            "_anchor_b": "AC-2",
        }

    def test_t1_accepted(self) -> None:
        r = evaluate_run(self.borderline, [self._entry("T1")], "validator")
        self.assertTrue(r.caught)
        self.assertEqual(r.slot, "T1")

    def test_t4_accepted(self) -> None:
        r = evaluate_run(self.borderline, [self._entry("T4")], "validator")
        self.assertTrue(r.caught)
        self.assertEqual(r.slot, "T4")

    def test_t2_rejected_outside_permitted_set(self) -> None:
        r = evaluate_run(self.borderline, [self._entry("T2")], "validator")
        self.assertFalse(r.caught)


class TestAggregator(TestCase):
    def setUp(self) -> None:
        fixtures = {f.fixture_id: f for f in load_fixtures()}
        self.t1 = fixtures["T1-real-fr3"]
        self.refinement = fixtures["refinement-idempotent"]
        self.borderline = fixtures["T4-borderline-entity"]

    def _t1_run(self, *, caught: bool = True, slot: str = "T1") -> RunEvaluation:
        return RunEvaluation(
            caught=caught,
            slot=slot if caught else None,
            type_name="entity_creation_polarity_mismatch" if caught else None,
            discriminator="__drizzle_migrations table existence",
            anchors=[],
            fp=False,
        )

    def test_5_of_5_passes(self) -> None:
        runs = [self._t1_run() for _ in range(5)]
        agg = aggregate_fixture(self.t1, "validator", runs)
        self.assertTrue(agg.passed)
        self.assertEqual(agg.catches, 5)
        self.assertTrue(agg.slot_stable)

    def test_4_of_5_passes(self) -> None:
        runs = [self._t1_run() for _ in range(4)] + [self._t1_run(caught=False)]
        agg = aggregate_fixture(self.t1, "validator", runs)
        self.assertTrue(agg.passed)
        self.assertEqual(agg.catches, 4)

    def test_3_of_5_fails(self) -> None:
        runs = [self._t1_run() for _ in range(3)] + [self._t1_run(caught=False)] * 2
        agg = aggregate_fixture(self.t1, "validator", runs)
        self.assertFalse(agg.passed)
        self.assertIn("below ship threshold", agg.failure_reason)

    def test_5_of_5_with_slot_drift_fails(self) -> None:
        runs = (
            [self._t1_run(slot="T1") for _ in range(3)]
            + [self._t1_run(slot="T4") for _ in range(2)]
        )
        agg = aggregate_fixture(self.t1, "validator", runs)
        self.assertFalse(agg.passed)
        self.assertIn("slot instability", agg.failure_reason)

    def test_borderline_accepts_mixed_t1_t4(self) -> None:
        # Borderline fixture: T1 in 3 runs, T4 in 2 — both inside
        # permitted_slots, all 5 caught, discriminator stable.
        runs = (
            [
                RunEvaluation(
                    caught=True,
                    slot="T1",
                    type_name="entity_creation_polarity_mismatch",
                    discriminator="registration endpoint creates tenants outside withTenantDb",
                    anchors=[],
                    fp=False,
                )
                for _ in range(3)
            ]
            + [
                RunEvaluation(
                    caught=True,
                    slot="T4",
                    type_name="behavioral_exclusivity_violation",
                    discriminator="registration endpoint bypasses withTenantDb for tenants insert",
                    anchors=[],
                    fp=False,
                )
                for _ in range(2)
            ]
        )
        agg = aggregate_fixture(self.borderline, "validator", runs)
        self.assertTrue(agg.passed, agg.failure_reason)
        self.assertTrue(agg.discriminator_stable)

    def test_borderline_rejects_outside_permitted(self) -> None:
        runs = (
            [
                RunEvaluation(
                    caught=True,
                    slot="T1",
                    type_name="x",
                    discriminator="registration",
                    anchors=[],
                    fp=False,
                )
                for _ in range(4)
            ]
            + [
                RunEvaluation(
                    caught=True,
                    slot="T2",  # NOT in permitted_slots {T1, T4}
                    type_name="x",
                    discriminator="registration",
                    anchors=[],
                    fp=False,
                )
            ]
        )
        agg = aggregate_fixture(self.borderline, "validator", runs)
        self.assertFalse(agg.passed)
        self.assertIn("slot instability", agg.failure_reason)

    def test_refinement_zero_fps_passes(self) -> None:
        runs = [
            RunEvaluation(
                caught=False, slot=None, type_name=None, discriminator="",
                anchors=[], fp=False,
            )
            for _ in range(5)
        ]
        agg = aggregate_fixture(self.refinement, "validator", runs)
        self.assertTrue(agg.passed)
        self.assertEqual(agg.false_positives, 0)

    def test_refinement_one_fp_passes_at_cap(self) -> None:
        runs = [
            RunEvaluation(
                caught=True, slot="T1", type_name="x", discriminator="x",
                anchors=[], fp=True,
            )
        ] + [
            RunEvaluation(
                caught=False, slot=None, type_name=None, discriminator="",
                anchors=[], fp=False,
            )
            for _ in range(4)
        ]
        agg = aggregate_fixture(self.refinement, "validator", runs)
        self.assertTrue(agg.passed)
        self.assertEqual(agg.false_positives, 1)

    def test_refinement_two_fps_fails(self) -> None:
        runs = [
            RunEvaluation(
                caught=True, slot="T1", type_name="x", discriminator="x",
                anchors=[], fp=True,
            )
            for _ in range(2)
        ] + [
            RunEvaluation(
                caught=False, slot=None, type_name=None, discriminator="",
                anchors=[], fp=False,
            )
            for _ in range(3)
        ]
        agg = aggregate_fixture(self.refinement, "validator", runs)
        self.assertFalse(agg.passed)
        self.assertIn("false positives", agg.failure_reason)


class TestValidTypologySlotsMatchSSOT(TestCase):
    """The harness's slot whitelist must mirror the SSOT's T1-T5 set —
    same drift-detection discipline as
    ``test_consistency_status.test_valid_types_match_typology_ssot``."""

    def test_slot_set_matches(self) -> None:
        import re
        text = TYPOLOGY_FILE.read_text(encoding="utf-8")
        ssot_slots = set(re.findall(r"\*\*(T[1-9])\*\*", text))
        self.assertEqual(VALID_TYPOLOGY_SLOTS, ssot_slots)
