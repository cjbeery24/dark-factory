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

if __name__ == "__main__":
    unittest.main()
