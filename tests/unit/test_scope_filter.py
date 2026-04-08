"""Unit tests for Phase 4.6c scope_filter — locked-denominator enforcement.

Fixtures derived from the Phase 4.5a iter2 run on
``2026-04-08-shared-parse-amount`` (see
``docs/pipeline-run-reports/2026-04-08-23-20-budget-brain-v2-shared-parse-amount.md``).
The 4 known stable failures from that run drive the gate criteria for
Phase 4.6c — these tests are the deterministic, no-pipeline-spend gate
that must pass before re-running F-U1 with the filter active.
"""

from __future__ import annotations

from pathlib import Path

from darkfactory.pipeline.scope_filter import (
    OutOfScopeFailure,
    ScopePartition,
    check_literal_input_membership,
    merge_partitions,
    partition_failing_by_scope,
)
from darkfactory.spec.anchors import AnchorManifest


# ---------------------------------------------------------------------------
# F-U1 fixture data — verbatim from the 4.5a iter2 run
# ---------------------------------------------------------------------------

F_U1_MANIFEST = AnchorManifest(
    fr_ids=["FR-1", "FR-2", "FR-3", "FR-4"],
    ac_ids=["AC-1", "AC-2", "AC-3", "AC-4", "AC-5", "AC-6", "AC-7", "AC-8"],
    ec_ids=[f"EC-{i}" for i in range(1, 13)],
)

# The 4 stable failures from iter2 of the 4.5a run.
F_U1_ITER2_FAILING = [
    "parseAmount adversarial scenarios > handles malformed comma placement [adversarial] 5ms",
    "parseAmount edge cases and boundaries > handles empty currency prefix followed by comma [AC-8] 4ms",
    "parseAmount edge cases and boundaries > handles multiple commas [AC-8] 0ms",
    "parseAmount edge cases and boundaries > handles comma at end [AC-8] 0ms",
]


# ---------------------------------------------------------------------------
# Rule 4.6c.1: AnchorManifest membership
# ---------------------------------------------------------------------------


class TestPartitionByScope:

    def test_no_anchor_tag_is_out_of_scope(self):
        result = partition_failing_by_scope(
            ["handles malformed comma placement [adversarial]"],
            F_U1_MANIFEST,
        )
        assert result.in_scope_failing == []
        assert len(result.out_of_scope_failing) == 1
        assert result.out_of_scope_failing[0].reason == "no_anchor"

    def test_known_anchor_tag_is_in_scope(self):
        result = partition_failing_by_scope(
            ["validates simple decimal [AC-1]"],
            F_U1_MANIFEST,
        )
        assert result.in_scope_failing == ["validates simple decimal [AC-1]"]
        assert result.out_of_scope_failing == []

    def test_unknown_anchor_tag_is_out_of_scope(self):
        result = partition_failing_by_scope(
            ["asserts something [AC-99]"],
            F_U1_MANIFEST,
        )
        assert result.in_scope_failing == []
        assert len(result.out_of_scope_failing) == 1
        assert "AC-99" in result.out_of_scope_failing[0].reason

    def test_fr_and_ec_tags_are_recognised(self):
        result = partition_failing_by_scope(
            ["covers [FR-1]", "covers [EC-7]"],
            F_U1_MANIFEST,
        )
        assert len(result.in_scope_failing) == 2
        assert result.out_of_scope_failing == []

    def test_4_5a_iter2_partition_catches_one_of_four(self):
        # Rule 4.6c.1 alone catches the [adversarial] one because it has
        # no anchor. The 3 [AC-8]-tagged comma scenarios pass through
        # because AC-8 IS in the manifest. Those need rule 4.6c.2.
        result = partition_failing_by_scope(F_U1_ITER2_FAILING, F_U1_MANIFEST)
        assert len(result.out_of_scope_failing) == 1
        assert result.out_of_scope_failing[0].reason == "no_anchor"
        assert "[adversarial]" in result.out_of_scope_failing[0].test_name
        assert len(result.in_scope_failing) == 3

    def test_empty_input(self):
        result = partition_failing_by_scope([], F_U1_MANIFEST)
        assert result.in_scope_failing == []
        assert result.out_of_scope_failing == []
        assert not result.has_changes()


# ---------------------------------------------------------------------------
# Rule 4.6c.2: literal-input membership against the spec
# ---------------------------------------------------------------------------


# Mini-spec text mirroring the structure of the F-U1 spec's §5.5 Wire
# Contracts and §6 Edge Cases — every legitimate literal input from the
# real F-U1 spec, in the same backtick convention.
F_U1_SPEC_TEXT = """
## 5.5. Wire Contracts (Examples)

| Input | Output |
|---|---|
| `""` | EMPTY |
| `"   "` | EMPTY |
| `"abc"` | NOT_A_NUMBER |
| `"1.234"` | TOO_MANY_DECIMALS |
| `"1.2.3"` | NOT_A_NUMBER |
| `"$1,234.56"` | cents 123456 |
| `"1"` | cents 100 |
| `"-50.25"` | cents -5025 |
| `"100000000000000.00"` | OUT_OF_RANGE |

## 6. Edge Cases

| ID | Condition |
|---|---|
| EC-1 | `raw === ""` |
| EC-3 | `raw === "$"` |
| EC-4 | `raw === "$$100"` |
| EC-5 | `raw === "1,234.56"` |
| EC-9 | `raw === "1e10"` |
| EC-11 | `raw === "\\u0000"` |
"""


def _write_scenario_file(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


def test_spec_literal_set_extracts_wire_contract_inputs(tmp_path: Path):
    from darkfactory.pipeline.scope_filter import _spec_literal_set
    literals = _spec_literal_set(F_U1_SPEC_TEXT)
    assert "1.234" in literals
    assert "$1,234.56" in literals
    assert "100000000000000.00" in literals
    assert "1,234.56" in literals
    assert "$$100" in literals
    # Things NOT in the spec
    assert "$,123" not in literals
    assert "1,,234.56" not in literals
    assert "1234," not in literals


class TestCheckLiteralInputMembership:

    def test_test_with_no_subject_call_is_in_scope(self, tmp_path: Path):
        _write_scenario_file(
            tmp_path,
            "00-tier1.test.ts",
            """
            import { parseAmount } from '../src/parse-amount';
            describe('x', () => {
              it('checks types only [AC-1]', () => {
                expect(typeof parseAmount).toBe('function');
              });
            });
            """,
        )
        result = check_literal_input_membership(
            ["checks types only [AC-1]"], F_U1_SPEC_TEXT, tmp_path,
        )
        assert result.in_scope_failing == ["checks types only [AC-1]"]
        assert result.out_of_scope_failing == []

    def test_test_with_in_spec_literal_is_in_scope(self, tmp_path: Path):
        _write_scenario_file(
            tmp_path,
            "01-contract.test.ts",
            """
            import { parseAmount } from '../src/parse-amount';
            describe('x', () => {
              it('parses dollar+commas [AC-1]', () => {
                const r = parseAmount("$1,234.56");
                expect(r.ok).toBe(true);
              });
            });
            """,
        )
        result = check_literal_input_membership(
            ["parses dollar+commas [AC-1]"], F_U1_SPEC_TEXT, tmp_path,
        )
        assert result.in_scope_failing == ["parses dollar+commas [AC-1]"]
        assert result.out_of_scope_failing == []

    def test_test_with_unspecified_literal_is_out_of_scope(self, tmp_path: Path):
        _write_scenario_file(
            tmp_path,
            "03-edge.test.ts",
            """
            import { parseAmount } from '../src/parse-amount';
            describe('x', () => {
              it('handles empty currency prefix followed by comma [AC-8]', () => {
                expect(() => {
                  const result = parseAmount("$,123");
                  expect(result.ok).toBe(false);
                }).not.toThrow();
              });
            });
            """,
        )
        result = check_literal_input_membership(
            ["handles empty currency prefix followed by comma [AC-8]"],
            F_U1_SPEC_TEXT,
            tmp_path,
        )
        assert result.in_scope_failing == []
        assert len(result.out_of_scope_failing) == 1
        assert "$,123" in result.out_of_scope_failing[0].reason

    def test_property_style_test_is_exempt(self, tmp_path: Path):
        _write_scenario_file(
            tmp_path,
            "03-edge.test.ts",
            """
            import { parseAmount } from '../src/parse-amount';
            describe('x', () => {
              it('property test: never throws [AC-8]', () => {
                const inputs = ["", " ", "\\u0000", "🚀", "∞", "NaN"];
                for (const input of inputs) {
                  expect(() => parseAmount(input)).not.toThrow();
                }
              });
            });
            """,
        )
        result = check_literal_input_membership(
            ["property test: never throws [AC-8]"], F_U1_SPEC_TEXT, tmp_path,
        )
        # 6 distinct literals → property exemption applies
        assert result.in_scope_failing == ["property test: never throws [AC-8]"]
        assert result.out_of_scope_failing == []

    def test_4_5a_iter2_three_comma_tests_caught(self, tmp_path: Path):
        # Mirror of the actual 03-edge-cases.test.ts content for the 3
        # [AC-8] comma tests that 4.6c.1 missed.
        _write_scenario_file(
            tmp_path,
            "03-edge-cases.test.ts",
            """
            import { parseAmount } from '../src/parse-amount';
            describe('parseAmount edge cases and boundaries', () => {
              it('handles empty currency prefix followed by comma [AC-8]', () => {
                expect(() => {
                  const result = parseAmount("$,123");
                  expect(result.ok).toBe(false);
                }).not.toThrow();
              });
              it('handles multiple commas [AC-8]', () => {
                expect(() => {
                  const result = parseAmount("1,,234.56");
                  expect(result.ok).toBe(false);
                }).not.toThrow();
              });
              it('handles comma at end [AC-8]', () => {
                expect(() => {
                  const result = parseAmount("1234,");
                  expect(result.ok).toBe(false);
                }).not.toThrow();
              });
            });
            """,
        )
        in_scope_after_4_6c_1 = [
            "handles empty currency prefix followed by comma [AC-8]",
            "handles multiple commas [AC-8]",
            "handles comma at end [AC-8]",
        ]
        result = check_literal_input_membership(
            in_scope_after_4_6c_1, F_U1_SPEC_TEXT, tmp_path,
        )
        assert result.in_scope_failing == []
        assert len(result.out_of_scope_failing) == 3
        reasons = [r.reason for r in result.out_of_scope_failing]
        assert any("$,123" in r for r in reasons)
        assert any("1,,234.56" in r for r in reasons)
        assert any("1234," in r for r in reasons)

    def test_legitimate_in_spec_literal_passes(self, tmp_path: Path):
        # AC-7 spec example: parseAmount("1e10") → NOT_A_NUMBER. The literal
        # "1e10" IS in the spec (EC-9), so this test is in-scope.
        _write_scenario_file(
            tmp_path,
            "02-error.test.ts",
            """
            import { parseAmount } from '../src/parse-amount';
            describe('errors', () => {
              it('rejects scientific notation [AC-7]', () => {
                const r = parseAmount("1e10");
                expect(r.ok).toBe(false);
              });
            });
            """,
        )
        result = check_literal_input_membership(
            ["rejects scientific notation [AC-7]"], F_U1_SPEC_TEXT, tmp_path,
        )
        assert result.in_scope_failing == ["rejects scientific notation [AC-7]"]
        assert result.out_of_scope_failing == []


# ---------------------------------------------------------------------------
# End-to-end: 4.6c.1 + 4.6c.2 composed catches all 4 known failures
# ---------------------------------------------------------------------------


def test_4_5a_iter2_full_pipeline_catches_all_four(tmp_path: Path):
    """The validation-gate test for Phase 4.6c.

    Expected: applying 4.6c.1 then 4.6c.2 to the 4 known iter2 failures
    reclassifies all 4 as out-of-scope, leaving the in-scope failing set
    empty. This is the deterministic gate the plan committed to.
    """
    _write_scenario_file(
        tmp_path,
        "03-edge-cases.test.ts",
        """
        import { parseAmount } from '../src/parse-amount';
        describe('parseAmount edge cases and boundaries', () => {
          it('handles empty currency prefix followed by comma [AC-8]', () => {
            expect(() => { const result = parseAmount("$,123"); expect(result.ok).toBe(false); }).not.toThrow();
          });
          it('handles multiple commas [AC-8]', () => {
            expect(() => { const result = parseAmount("1,,234.56"); expect(result.ok).toBe(false); }).not.toThrow();
          });
          it('handles comma at end [AC-8]', () => {
            expect(() => { const result = parseAmount("1234,"); expect(result.ok).toBe(false); }).not.toThrow();
          });
        });
        """,
    )
    _write_scenario_file(
        tmp_path,
        "04-adversarial.test.ts",
        """
        import { parseAmount } from '../src/parse-amount';
        describe('parseAmount adversarial scenarios', () => {
          it('handles malformed comma placement [adversarial]', () => {
            const result = parseAmount("1,2,3");
            expect(result.ok).toBe(false);
          });
        });
        """,
    )

    # Use just the test names without the trailing duration suffix from
    # vitest's output formatter (the iter_state.failing_test_names list
    # carries the it() string, not the formatted vitest line).
    failing = [
        "handles malformed comma placement [adversarial]",
        "handles empty currency prefix followed by comma [AC-8]",
        "handles multiple commas [AC-8]",
        "handles comma at end [AC-8]",
    ]

    # Step 1: 4.6c.1 — anchor manifest filter
    p1 = partition_failing_by_scope(failing, F_U1_MANIFEST)
    assert len(p1.out_of_scope_failing) == 1  # the [adversarial] one

    # Step 2: 4.6c.2 — literal input membership on the survivors
    p2 = check_literal_input_membership(
        p1.in_scope_failing, F_U1_SPEC_TEXT, tmp_path,
    )
    assert len(p2.out_of_scope_failing) == 3  # the 3 [AC-8] comma tests
    assert p2.in_scope_failing == []

    # Composed: all 4 caught, zero in-scope failing
    final = merge_partitions(p1, p2)
    assert len(final.out_of_scope_failing) == 4
    assert final.in_scope_failing == []
    assert final.has_changes()
