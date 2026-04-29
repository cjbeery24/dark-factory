"""Tests for darkfactory/spec/consistency_status.py and the orchestrator's
cross-section consistency gate (Phase 3.5b)."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tests.helpers import TempDirTestCase

from darkfactory.spec.consistency_status import (
    extract_contradictions,
    format_consistency_failure,
    ledger_has_claims,
    parse_consistency_ledger,
    read_consistency_status,
)


def _write_status(
    project_dir: Path,
    spec_name: str,
    *,
    consistency: str | None = None,
    extra_lines: tuple[str, ...] = (),
) -> Path:
    status_dir = project_dir / ".dark-factory" / "reviews"
    status_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "verdict=PASS",
        "score=100",
        "divergence=LOW",
        "blockers=0",
    ]
    lines.extend(extra_lines)
    if consistency is not None:
        lines.append(f"consistency={consistency}")
    status_file = status_dir / f"{spec_name}-validation.status"
    status_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status_file


def _write_ledger(
    project_dir: Path, spec_name: str, body: str,
) -> Path:
    reviews_dir = project_dir / ".dark-factory" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = reviews_dir / f"{spec_name}-consistency-ledger.yaml"
    ledger_path.write_text(body, encoding="utf-8")
    return ledger_path


# A canonical FR-3-shaped ledger fixture used across multiple tests.
_FR3_LEDGER_YAML = """\
spec: 2026-03-28-database-foundation
fr_ledgers:
  - fr_id: FR-3
    claims:
      - id: FR-3.c1
        subject: "baseline migration"
        predicate: creates
        polarity: negative
        object: "tables"
        anchor: "FR-3.processing@line62"
        raw: "The baseline migration itself creates no tables and applies no policies"
      - id: FR-3.c3
        subject: "baseline migration"
        predicate: creates
        polarity: positive
        object: "__drizzle_migrations table"
        anchor: "FR-3.side_effects@line70"
        raw: "The __drizzle_migrations tracking table is created in the database when the baseline migration is applied"
contradictions:
  - claim_a: FR-3.c1
    claim_b: FR-3.c3
    type: T1
    type_name: entity_creation_polarity_mismatch
    discriminator: "__drizzle_migrations table existence"
"""


class TestReadConsistencyStatus(TempDirTestCase):
    def test_pass_value(self) -> None:
        path = _write_status(self.tmp_dir, "demo", consistency="PASS")
        self.assertEqual(read_consistency_status(path), "PASS")

    def test_fail_value(self) -> None:
        path = _write_status(self.tmp_dir, "demo", consistency="FAIL")
        self.assertEqual(read_consistency_status(path), "FAIL")

    def test_lowercase_rejected_strict_case(self) -> None:
        # Contract: the validator agent emits exact-case PASS|FAIL.
        # Loose-input here would mask a case-drift regression in the
        # validator output, so the parser refuses lowercase.
        path = _write_status(self.tmp_dir, "demo", consistency="pass")
        self.assertIsNone(read_consistency_status(path))

    def test_missing_field_returns_none(self) -> None:
        # Back-compat: validator versions that don't emit consistency=
        # must not break the gate.
        path = _write_status(self.tmp_dir, "demo")
        self.assertIsNone(read_consistency_status(path))

    def test_missing_file_returns_none(self) -> None:
        path = self.tmp_dir / ".dark-factory" / "reviews" / "absent.status"
        self.assertIsNone(read_consistency_status(path))

    def test_unrecognized_value_returns_none(self) -> None:
        path = _write_status(self.tmp_dir, "demo", consistency="MAYBE")
        self.assertIsNone(read_consistency_status(path))


class TestParseConsistencyLedger(TempDirTestCase):
    def test_valid_ledger_returns_dict(self) -> None:
        path = _write_ledger(self.tmp_dir, "demo", _FR3_LEDGER_YAML)
        ledger = parse_consistency_ledger(path)
        self.assertIsNotNone(ledger)
        assert ledger is not None  # for type-checker
        self.assertEqual(ledger["spec"], "2026-03-28-database-foundation")
        self.assertEqual(len(ledger["fr_ledgers"]), 1)
        self.assertEqual(len(ledger["fr_ledgers"][0]["claims"]), 2)

    def test_missing_file_returns_none(self) -> None:
        path = self.tmp_dir / "absent.yaml"
        self.assertIsNone(parse_consistency_ledger(path))

    def test_malformed_yaml_returns_none(self) -> None:
        path = _write_ledger(self.tmp_dir, "demo", "not: valid: yaml: [unclosed")
        self.assertIsNone(parse_consistency_ledger(path))

    def test_top_level_list_returns_none(self) -> None:
        path = _write_ledger(self.tmp_dir, "demo", "- item1\n- item2\n")
        self.assertIsNone(parse_consistency_ledger(path))


class TestLedgerHasClaims(TestCase):
    def test_populated_ledger_true(self) -> None:
        ledger = {
            "fr_ledgers": [
                {"fr_id": "FR-1", "claims": [{"id": "FR-1.c1"}]},
            ],
        }
        self.assertTrue(ledger_has_claims(ledger))

    def test_empty_fr_ledgers_false(self) -> None:
        self.assertFalse(ledger_has_claims({"fr_ledgers": []}))

    def test_missing_fr_ledgers_false(self) -> None:
        self.assertFalse(ledger_has_claims({"spec": "x"}))

    def test_fr_ledgers_with_empty_claims_false(self) -> None:
        ledger = {
            "fr_ledgers": [
                {"fr_id": "FR-1", "claims": []},
                {"fr_id": "FR-2"},  # missing claims key
            ],
        }
        self.assertFalse(ledger_has_claims(ledger))

    def test_none_returns_false(self) -> None:
        self.assertFalse(ledger_has_claims(None))


class TestExtractContradictions(TestCase):
    def test_valid_types_match_typology_ssot(self) -> None:
        # SSOT discipline: the Python set must match the typology
        # markdown's T1-T5 rows verbatim. Drift breaks the gate
        # silently — extract_contradictions would drop a valid type
        # if the markdown added T6 without the set growing.
        from darkfactory.spec.consistency_status import _VALID_TYPES
        import re
        root = Path(__file__).resolve().parent.parent.parent
        ssot_text = (
            root / "darkfactory" / "contracts" / "contradiction_typology.md"
        ).read_text(encoding="utf-8")
        ssot_types = set(re.findall(r"\*\*(T[1-9])\*\*", ssot_text))
        self.assertEqual(ssot_types, _VALID_TYPES)

    def test_valid_t1_contradiction_preserved(self) -> None:
        ledger = {
            "contradictions": [
                {
                    "claim_a": "FR-3.c1", "claim_b": "FR-3.c3",
                    "type": "T1",
                    "type_name": "entity_creation_polarity_mismatch",
                    "discriminator": "__drizzle_migrations existence",
                },
            ],
        }
        out = extract_contradictions(ledger)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "T1")

    def test_out_of_vocab_type_dropped(self) -> None:
        # Defends the typology: open-vocabulary types are forbidden.
        ledger = {
            "contradictions": [
                {"claim_a": "a", "claim_b": "b", "type": "T9", "discriminator": "x"},
                {"claim_a": "c", "claim_b": "d", "type": "T1", "discriminator": "y"},
            ],
        }
        out = extract_contradictions(ledger)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "T1")

    def test_lowercase_type_normalized(self) -> None:
        ledger = {
            "contradictions": [
                {"claim_a": "a", "claim_b": "b", "type": "t2", "discriminator": "x"},
            ],
        }
        out = extract_contradictions(ledger)
        self.assertEqual(len(out), 1)

    def test_missing_contradictions_returns_empty(self) -> None:
        self.assertEqual(extract_contradictions({"spec": "x"}), [])
        self.assertEqual(extract_contradictions(None), [])


class TestFormatConsistencyFailure(TestCase):
    def test_failure_message_includes_anchors_and_discriminator(self) -> None:
        # Use the FR-3 fixture parsed via yaml.safe_load equivalent.
        import yaml
        ledger = yaml.safe_load(_FR3_LEDGER_YAML)
        contradictions = extract_contradictions(ledger)
        msg = format_consistency_failure(ledger, contradictions)
        # Anchor lookup substitutes the byte-addressable pointer.
        self.assertIn("FR-3.c1", msg)
        self.assertIn("FR-3.processing@line62", msg)
        self.assertIn("FR-3.c3", msg)
        self.assertIn("FR-3.side_effects@line70", msg)
        # Typology slot.
        self.assertIn("T1", msg)
        self.assertIn("entity_creation_polarity_mismatch", msg)
        # Discriminator.
        self.assertIn("__drizzle_migrations", msg)

    def test_failure_message_falls_back_when_anchor_missing(self) -> None:
        ledger = {
            "fr_ledgers": [
                {"fr_id": "FR-1", "claims": [{"id": "FR-1.c1"}]},
            ],
            "contradictions": [
                {
                    "claim_a": "FR-1.c1", "claim_b": "FR-1.c2",
                    "type": "T1",
                    "type_name": "entity_creation_polarity_mismatch",
                    "discriminator": "x",
                },
            ],
        }
        contradictions = extract_contradictions(ledger)
        msg = format_consistency_failure(ledger, contradictions)
        # FR-1.c2 has no claim entry so the formatter prints the bare ID.
        self.assertIn("FR-1.c2", msg)


class TestOrchestratorConsistencyGate(TempDirTestCase):
    """Wires the orchestrator's _step_spec_validation through the new
    consistency gate. Mirrors TestSpecValidationUnresolvedContractsRetry's
    setup: mock invoke_agent + validate_spec_result so the only path
    under test is the post-validate gate logic."""

    def _setup(self):
        from darkfactory.docker.barriers import configure_barriers
        from darkfactory.models.config import PipelineConfig
        from darkfactory.models.pipeline import SpecRun
        project = self.tmp_dir
        (project / ".dark-factory" / "specs").mkdir(parents=True)
        (project / ".dark-factory" / "specs" / "demo.md").write_text("# Spec\n")
        reviews = project / ".dark-factory" / "reviews"
        reviews.mkdir(parents=True)
        # Write a minimal valid validation report so the unresolved_contracts
        # check passes and we reach the new consistency gate.
        report_path = reviews / "demo-validation.md"
        report_path.write_text(
            "# Report\n\n```yaml\nunresolved_contracts: []\n```\n",
            encoding="utf-8",
        )
        spec_run = SpecRun(spec_name="demo")
        config = PipelineConfig(test_cmd="pytest")
        barriers = configure_barriers(config.source_dirs)
        return project, spec_run, config, barriers

    def _invoke(self, project, spec_run, config, barriers):
        from darkfactory.models.agent import AgentResult, AgentRole
        from darkfactory.pipeline.orchestrator import _step_spec_validation
        with patch(
            "darkfactory.pipeline.orchestrator.invoke_agent",
            return_value=AgentResult(role=AgentRole.SPEC_VALIDATOR, exit_code=0),
        ), patch(
            "darkfactory.pipeline.orchestrator.validate_spec_result",
            return_value=(True, "PASS"),
        ):
            _step_spec_validation(
                spec_run, config, barriers, project, dry_run=False,
                active_adapters=[],
            )

    def test_consistency_fail_raises_with_formatted_message(self) -> None:
        from darkfactory.errors import DarkFactoryError
        project, spec_run, config, barriers = self._setup()
        _write_status(project, "demo", consistency="FAIL")
        _write_ledger(project, "demo", _FR3_LEDGER_YAML)
        with self.assertRaises(DarkFactoryError) as ctx:
            self._invoke(project, spec_run, config, barriers)
        msg = str(ctx.exception)
        # Operator greppability: the literal status field appears in the message.
        self.assertIn("consistency=FAIL", msg)
        self.assertIn("cross-section", msg)
        self.assertIn("T1", msg)
        self.assertIn("entity_creation_polarity_mismatch", msg)
        self.assertIn("__drizzle_migrations", msg)
        # Anchors must reach the operator so they can navigate to the
        # offending spec lines.
        self.assertIn("FR-3.processing@line62", msg)
        self.assertIn("FR-3.side_effects@line70", msg)

    def test_consistency_fail_without_typed_contradictions_raises(self) -> None:
        # Validator said FAIL but the ledger has no typed contradictions
        # (or all were out-of-vocab and got dropped).
        from darkfactory.errors import DarkFactoryError
        project, spec_run, config, barriers = self._setup()
        _write_status(project, "demo", consistency="FAIL")
        _write_ledger(
            project, "demo",
            "spec: demo\nfr_ledgers:\n  - fr_id: FR-1\n    claims:\n      - id: FR-1.c1\n"
            "contradictions:\n"
            "  - claim_a: FR-1.c1\n    claim_b: FR-1.c2\n    type: T9\n    discriminator: x\n",
        )
        with self.assertRaises(DarkFactoryError) as ctx:
            self._invoke(project, spec_run, config, barriers)
        msg = str(ctx.exception)
        self.assertIn("consistency=FAIL", msg)
        self.assertIn("typed (T1-T5)", msg)

    def test_consistency_pass_without_ledger_raises_theater(self) -> None:
        # consistency=PASS but the validator never wrote the ledger —
        # theater protection.
        from darkfactory.errors import DarkFactoryError
        project, spec_run, config, barriers = self._setup()
        _write_status(project, "demo", consistency="PASS")
        # Intentionally do not write the ledger file.
        with self.assertRaises(DarkFactoryError) as ctx:
            self._invoke(project, spec_run, config, barriers)
        msg = str(ctx.exception)
        self.assertIn("consistency=PASS", msg)
        self.assertIn("never written", msg)
        self.assertIn("skipped the extraction pass", msg)

    def test_consistency_pass_with_populated_ledger_proceeds(self) -> None:
        project, spec_run, config, barriers = self._setup()
        _write_status(project, "demo", consistency="PASS")
        # Ledger with claims but no contradictions — the genuine PASS case.
        _write_ledger(
            project, "demo",
            "spec: demo\nfr_ledgers:\n  - fr_id: FR-1\n    claims:\n"
            "      - id: FR-1.c1\n        subject: x\n        predicate: creates\n"
            "        polarity: positive\n        object: y\n        anchor: AC-1\n"
            "contradictions: []\n",
        )
        # No exception expected.
        self._invoke(project, spec_run, config, barriers)

    def test_consistency_field_missing_proceeds_back_compat(self) -> None:
        # Older validator agent versions don't emit `consistency=`. The
        # gate must not break those flows.
        project, spec_run, config, barriers = self._setup()
        _write_status(project, "demo")  # no consistency key
        # No ledger written either.
        # No exception expected.
        self._invoke(project, spec_run, config, barriers)

    def test_consistency_pass_with_contradictions_in_ledger_raises(self) -> None:
        # Validator's status disagrees with its own evidence: PASS
        # status but the ledger lists a typed contradiction. Trust
        # the ledger; raise. Linus S2-2.
        from darkfactory.errors import DarkFactoryError
        project, spec_run, config, barriers = self._setup()
        _write_status(project, "demo", consistency="PASS")
        _write_ledger(project, "demo", _FR3_LEDGER_YAML)
        with self.assertRaises(DarkFactoryError) as ctx:
            self._invoke(project, spec_run, config, barriers)
        msg = str(ctx.exception)
        self.assertIn("consistency=PASS", msg)
        self.assertIn("disagrees with its own evidence", msg)

    def test_consistency_pass_with_empty_fr_less_ledger_proceeds(self) -> None:
        # FR-less spec (bug-fix or refactor) legitimately produces an
        # empty fr_ledgers + empty contradictions. The gate must not
        # raise spuriously. Linus S2-3 — distinguish "ledger present
        # but empty" from "ledger never written".
        project, spec_run, config, barriers = self._setup()
        _write_status(project, "demo", consistency="PASS")
        _write_ledger(
            project, "demo",
            "spec: demo\nfr_ledgers: []\ncontradictions: []\n",
        )
        # No exception expected.
        self._invoke(project, spec_run, config, barriers)

    def test_consistency_fail_with_missing_ledger_distinct_message(self) -> None:
        # Linus gap: FAIL + ledger never written should produce a
        # distinct error from FAIL + ledger present + no typed
        # contradictions.
        from darkfactory.errors import DarkFactoryError
        project, spec_run, config, barriers = self._setup()
        _write_status(project, "demo", consistency="FAIL")
        # Intentionally do not write the ledger.
        with self.assertRaises(DarkFactoryError) as ctx:
            self._invoke(project, spec_run, config, barriers)
        msg = str(ctx.exception)
        self.assertIn("consistency=FAIL", msg)
        self.assertIn("never written", msg)


class TestTypologySSOTSync(TestCase):
    """Both the spec-validator agent file and the df-densify-spec skill
    file MUST inline the typology block byte-identical to the SSOT at
    darkfactory/contracts/contradiction_typology.md.

    The block is delimited by sentinel HTML comments. Drift between the
    three files defeats the single-source-of-truth design — a future
    edit to one surface would silently diverge from the other.
    """

    _BEGIN = "<!-- BEGIN-CONTRADICTION-TYPOLOGY -->"
    _END = "<!-- END-CONTRADICTION-TYPOLOGY -->"

    def _extract_block(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        if self._BEGIN not in text:
            raise AssertionError(
                f"expected sentinel {self._BEGIN} in {path}, none found — "
                f"the typology block has been deleted or its sentinels "
                f"renamed; restore the inlined SSOT block"
            )
        if self._END not in text:
            raise AssertionError(
                f"expected sentinel {self._END} in {path}, none found — "
                f"the typology block is missing its closing sentinel"
            )
        start = text.index(self._BEGIN)
        end = text.index(self._END) + len(self._END)
        return text[start:end]

    def _repo_root(self) -> Path:
        # tests/unit/ → repo root is two levels up from this file.
        return Path(__file__).resolve().parent.parent.parent

    def test_validator_agent_inlines_canonical_typology(self) -> None:
        root = self._repo_root()
        canonical = self._extract_block(
            root / "darkfactory" / "contracts" / "contradiction_typology.md"
        )
        validator = self._extract_block(root / "agents" / "spec-validator.md")
        self.assertEqual(canonical, validator)

    def test_densifier_skill_inlines_canonical_typology(self) -> None:
        root = self._repo_root()
        canonical = self._extract_block(
            root / "darkfactory" / "contracts" / "contradiction_typology.md"
        )
        skill = self._extract_block(
            root / ".claude" / "skills" / "df-densify-spec" / "SKILL.md"
        )
        self.assertEqual(canonical, skill)


class TestDensifierSkillStructuralSentinels(TestCase):
    """Catch accidental deletion of Step 4.5 in a future skill rewrite.
    Asserts only the structural anchors (heading + JSON code fence
    presence), not prose content — prose can evolve, structure cannot."""

    def _skill_text(self) -> str:
        root = Path(__file__).resolve().parent.parent.parent
        return (root / ".claude" / "skills" / "df-densify-spec" / "SKILL.md").read_text(
            encoding="utf-8"
        )

    def test_step_4_5_heading_present(self) -> None:
        self.assertIn("### Step 4.5:", self._skill_text())

    def test_step_4_5_output_schema_block_present(self) -> None:
        text = self._skill_text()
        # The schema's discriminating fields — if these go missing the
        # JSON contract has been gutted.
        self.assertIn('"step": "4.5"', text)
        self.assertIn('"contradictions":', text)
        self.assertIn('"advisory_only":', text)
        self.assertIn('"discriminator":', text)
        self.assertIn('"refinement_check":', text)


class TestValidatorPromptIncludesConsistencyDirective(TestCase):
    """Source-level assert that the orchestrator's spec-validator prompt
    template includes the new consistency=PASS|FAIL field and the
    Cross-Section Contradiction Procedure directive. Catches accidental
    removal during future prompt edits."""

    def test_orchestrator_prompt_mentions_consistency_field(self) -> None:
        root = Path(__file__).resolve().parent.parent.parent
        text = (root / "darkfactory" / "pipeline" / "orchestrator.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("consistency=<PASS|FAIL>", text)
        self.assertIn("consistency-ledger.yaml", text)
        self.assertIn("Cross-Section Contradiction Procedure", text)
