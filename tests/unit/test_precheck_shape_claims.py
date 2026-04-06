"""Tests for the shape-claim detector in spec/precheck.py.

The shape-claim detector blocks specs that mention a serialized
cross-boundary structure (JSON envelope, log line, stdout, etc.) without
pinning the literal shape with a fenced code block or a Wire Contracts
reference. Without it, two LLM agents reading the same spec each invent a
different shape and the resulting scenarios fight the implementation.
"""

import unittest

from darkfactory.spec.precheck import check_shape_claims_from_text


def _section(name: str, body: str) -> str:
    return f"## {name}\n\n{body}\n"


class TestShapeClaims(unittest.TestCase):
    # ----- Token detection -----

    def test_envelope_without_example_is_blocked(self) -> None:
        text = _section(
            "Functional Requirements",
            "FR-1: The server returns a JSON error envelope on all 4xx responses.",
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(len(findings), 1)
        self.assertIn(findings[0].matched_token.lower(), {"json", "envelope"})
        self.assertEqual(findings[0].section, "Functional Requirements")

    def test_log_format_requires_example(self) -> None:
        text = _section(
            "Error Handling",
            "All errors are written to stdout in the project log format.",
        )
        findings = check_shape_claims_from_text(text)
        # "stdout" and "log format" are both tokens; only one finding per line.
        self.assertEqual(len(findings), 1)

    def test_response_body_token(self) -> None:
        text = _section(
            "Functional Requirements",
            "FR-1: The response body includes a status field on success.",
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].matched_token.lower(), "response body")

    # ----- Satisfaction rules -----

    def test_satisfied_by_inline_fenced_block(self) -> None:
        text = (
            "## Functional Requirements\n\n"
            "FR-1: The server returns a JSON envelope on errors.\n\n"
            "```json\n"
            '{ "ok": false, "error": { "code": "STR" } }\n'
            "```\n"
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    def test_long_section_satisfied_by_top_fenced_block(self) -> None:
        # Section-membership rule: a fenced block at the top satisfies tokens
        # 60 lines later in the same section. Proves it's not a line window.
        body = (
            "Here is the canonical envelope shape:\n\n"
            "```json\n"
            '{ "ok": false, "error": { "code": "STR" } }\n'
            "```\n\n"
            + "Lorem ipsum filler line.\n" * 60
            + "FR-99: Returns a JSON envelope on errors.\n"
        )
        text = _section("Functional Requirements", body)
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    def test_satisfied_by_wire_contract_reference(self) -> None:
        text = (
            "## Functional Requirements\n\n"
            "FR-1: Errors return a JSON envelope. See Wire Contracts: Error Envelope.\n\n"
            "## Wire Contracts\n\n"
            "### Error Envelope\n\n"
            "```json\n"
            '{ "ok": false }\n'
            "```\n"
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    def test_unsatisfied_wire_contract_reference_to_unknown_name(self) -> None:
        text = (
            "## Functional Requirements\n\n"
            "FR-1: Errors return a JSON envelope. See Wire Contracts: Nonexistent.\n\n"
            "## Wire Contracts\n\n"
            "### Error Envelope\n\n"
            "```json\n"
            '{ "ok": false }\n'
            "```\n"
        )
        # FR section has no fence and reference is to an undefined name.
        findings = check_shape_claims_from_text(text)
        self.assertEqual(len(findings), 1)

    # ----- False-positive guards -----

    def test_word_boundary_jsonp_does_not_match(self) -> None:
        text = _section(
            "Functional Requirements",
            "FR-1: The server returns jsonp callbacks for legacy clients.",
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    def test_word_boundary_payloads_does_not_match(self) -> None:
        # "payload" is in the token list, but plural "payloads" should still
        # match because \b matches at the end-of-word boundary regardless.
        # Confirm word-boundary semantics: "payloads" should match (it does
        # contain "payload" as a prefix word, but \b at end of "payload" sees
        # "s" which is a word char, so the match is REJECTED). This test
        # documents the expected behavior.
        text = _section(
            "Functional Requirements",
            "FR-1: Large payloads are streamed in chunks.",
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    def test_prose_field_mention_does_not_trigger(self) -> None:
        # "field" is intentionally NOT in the token list. Prose like
        # "the response includes a `requestId` field" must not trigger.
        text = _section(
            "Functional Requirements",
            "FR-1: The response includes a requestId field on every request.",
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    def test_outside_shape_bearing_section_ignored(self) -> None:
        # Examples section can mention "json" freely without triggering.
        text = _section(
            "Examples",
            "Example 1: A JSON envelope is returned for the 400 case.",
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    def test_problem_statement_ignored(self) -> None:
        text = _section(
            "Problem Statement",
            "Today, the API returns inconsistent JSON envelopes across endpoints.",
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    # ----- Section coverage -----

    def test_acceptance_criteria_is_shape_bearing(self) -> None:
        text = _section(
            "Acceptance Criteria",
            "AC-1: GIVEN a 400 response WHEN the client parses it THEN the JSON envelope contains an error code.",
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(len(findings), 1)

    def test_interface_contract_is_shape_bearing(self) -> None:
        text = _section(
            "Interface Contract",
            "All errors return a JSON envelope with an error code.",
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(len(findings), 1)


class TestShapeClaimRegressionsAndDiagnostics(unittest.TestCase):
    def test_h3_nested_wire_contracts_recognized(self) -> None:
        # `### Wire Contracts` nested under another section must satisfy
        # the shape-claim check via the model — proves the wiring of
        # check_shape_claims to model.wire_contracts().
        text = (
            "## Functional Requirements\n\n"
            "FR-1: Errors return a JSON envelope. See Wire Contracts: Error Envelope.\n\n"
            "## Error Handling\n\n"
            "### Wire Contracts\n\n"
            "#### Error Envelope\n\n"
            "```json\n"
            '{"ok": false}\n'
            "```\n"
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    def test_sibling_section_fence_does_not_satisfy_other_section(self) -> None:
        # Regression for the section-containment fix: a fenced block under
        # `## Error Handling` must NOT satisfy a shape claim in the
        # `## Interface Contract` section.
        text = (
            "## Interface Contract\n\n"
            "All errors return a JSON envelope with an error code.\n\n"
            "## Error Handling\n\n"
            "```json\n"
            '{"ok": false}\n'
            "```\n"
        )
        findings = check_shape_claims_from_text(text)
        self.assertTrue(
            any(f.section == "Interface Contract" for f in findings),
            f"expected an unresolved shape claim in Interface Contract, got: {findings}",
        )

    def test_budget_brain_v2_regression_one_round(self) -> None:
        # Synthetic version of the 5-round trace: AC table with shape tokens,
        # no fence in AC, ### Wire Contracts nested under Error Handling,
        # AC rows referencing the contract by name. Must pass in 1 round.
        text = (
            "## Acceptance Criteria\n\n"
            "| ID | Description |\n"
            "|----|-------------|\n"
            "| AC-1 | GIVEN env WHEN start THEN stderr Wire Contracts: stderr-env-validation |\n"
            "| AC-2 | GIVEN req WHEN parse THEN log line Wire Contracts: stderr-env-validation |\n\n"
            "## Error Handling\n\n"
            "### Wire Contracts\n\n"
            "#### stderr-env-validation\n\n"
            "```json\n"
            '{"level": "error", "msg": "..."}\n'
            "```\n"
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(findings, [])

    def test_diagnostic_emitter_contracts_available(self) -> None:
        from darkfactory.spec.precheck import (
            ShapeClaimFinding,
            format_shape_claim_diagnostic,
        )
        from darkfactory.spec.spec_model import (
            Heading, Section, Table, TableRow,
        )
        section = Section(
            heading=Heading(line_number=10, level=2, text="Acceptance Criteria", raw="## Acceptance Criteria"),
            end_line=30,
            tables=[Table(line_number=12, end_line=20, rows=[TableRow(line_number=15, cells=["AC-1"], section_path=("Acceptance Criteria",))], section_path=("Acceptance Criteria",))],
        )
        finding = ShapeClaimFinding(
            section="Acceptance Criteria",
            line_number=15,
            matched_token="stderr",
            context="...",
            available_wire_contracts=["stderr-env-validation", "log-line-request"],
            would_break_table=True,
        )
        out = format_shape_claim_diagnostic(finding, section)
        self.assertIn("stderr-env-validation", out)
        self.assertIn("log-line-request", out)
        self.assertIn("AFTER line 20", out)
        self.assertIn("Do NOT place it between table rows", out)

    def test_diagnostic_emitter_no_contracts(self) -> None:
        from darkfactory.spec.precheck import (
            ShapeClaimFinding,
            format_shape_claim_diagnostic,
        )
        from darkfactory.spec.spec_model import Heading, Section
        section = Section(
            heading=Heading(line_number=1, level=2, text="Functional Requirements", raw="## Functional Requirements"),
            end_line=10,
        )
        finding = ShapeClaimFinding(
            section="Functional Requirements",
            line_number=5,
            matched_token="envelope",
            context="...",
            available_wire_contracts=[],
        )
        out = format_shape_claim_diagnostic(finding, section)
        self.assertNotIn("(A) RECOMMENDED", out)
        self.assertIn("(B)", out)
        self.assertIn("No Wire Contracts", out)

    def test_diagnostic_recommended_fix_produces_passing_spec(self) -> None:
        # Falsifier (branch B): synthetic spec fails. Call the diagnostic,
        # parse the "AFTER line N" number out of its output, splice a
        # fenced block at that line, re-run run_precheck, assert it passes.
        import re
        import tempfile
        from pathlib import Path

        from darkfactory.spec.precheck import (
            format_shape_claim_diagnostic,
            run_precheck,
        )
        from darkfactory.spec.spec_model import parse_spec_model

        text = (
            "## Functional Requirements\n\n"
            "FR-1: Errors return a JSON envelope.\n"
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(len(findings), 1)
        finding = findings[0]

        # Locate the corresponding Section object in the parsed model so we
        # can pass it to the diagnostic formatter.
        model = parse_spec_model(text)
        section = model.find_section(finding.section)
        assert section is not None
        diag = format_shape_claim_diagnostic(finding, section)

        m = re.search(r"AFTER line (\d+)", diag)
        assert m is not None, f"diagnostic missing 'AFTER line N':\n{diag}"
        after_line = int(m.group(1))

        # Splice a fenced JSON block directly after the recommended line.
        original_lines = text.split("\n")
        fence = ["", "```json", '{"ok": false}', "```", ""]
        spliced = original_lines[:after_line] + fence + original_lines[after_line:]
        fixed = "\n".join(spliced)

        # Shape-claim check alone must now pass.
        self.assertEqual(check_shape_claims_from_text(fixed), [])

        # And run_precheck (the public entrypoint) must not surface any
        # shape-claim findings either.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "spec.md"
            p.write_text(fixed, encoding="utf-8")
            result = run_precheck(p)
            self.assertEqual(result.shape_claims, [])

    def test_diagnostic_recommended_fix_branch_a_produces_passing_spec(self) -> None:
        # Falsifier (branch A): parse the "Wire Contracts: <name>"
        # recommendation from the diagnostic and apply it to an AC row.
        import re

        from darkfactory.spec.precheck import format_shape_claim_diagnostic
        from darkfactory.spec.spec_model import parse_spec_model

        text = (
            "## Acceptance Criteria\n\n"
            "| ID | Description |\n"
            "|----|-------------|\n"
            "| AC-1 | GIVEN a 400 WHEN parsed THEN JSON envelope returned |\n\n"
            "## Wire Contracts\n\n"
            "### Error Envelope\n\n"
            "```json\n"
            '{"ok": false}\n'
            "```\n"
        )
        findings = check_shape_claims_from_text(text)
        self.assertEqual(len(findings), 1)
        finding = findings[0]

        model = parse_spec_model(text)
        section = model.find_section(finding.section)
        assert section is not None
        diag = format_shape_claim_diagnostic(finding, section)

        m = re.search(r'"Wire Contracts:\s*([^"]+)"', diag)
        assert m is not None, f"diagnostic missing 'Wire Contracts: <name>':\n{diag}"
        contract_name = m.group(1).strip()

        # Append the contract reference to the AC-1 row.
        fixed = text.replace(
            "JSON envelope returned |",
            f"JSON envelope returned. Wire Contracts: {contract_name} |",
        )
        self.assertEqual(check_shape_claims_from_text(fixed), [])


if __name__ == "__main__":
    unittest.main()
