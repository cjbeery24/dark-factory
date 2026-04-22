"""Tests for the §6 EC ↔ §8a AC family-coverage precheck rule.

Motivated by pipeline run 4736bbd9 (2026-04-22): SHIP_WITH_GAPS promoted
a NO-SHIP while a scenario anchored to ``EC-4`` was failing stably.
EC-4 and AC-12 described the same fail-closed unset-GUC invariant, but
only AC-12 was listed under §8a, so the ``ac_blocking`` veto didn't
fire. The detector is FATAL — authors must either add the EC to §8a
or rewrite it so it doesn't duplicate the blocking invariant's
observable behavior.
"""

import unittest

from darkfactory.spec.blocking_ac_family import check_blocking_ac_family
from darkfactory.spec.spec_model import parse_spec_model


def _build_spec(*, ec_rows: str, ac_rows: str, non_neg_body: str) -> str:
    return (
        "## 6. Edge Cases\n\n"
        "| ID | Condition | Expected Behavior |\n"
        "|----|-----------|-------------------|\n"
        f"{ec_rows}\n"
        "\n"
        "## 8. Acceptance Criteria\n\n"
        "| ID | Criterion | Blocking |\n"
        "|----|-----------|:-:|\n"
        f"{ac_rows}\n"
        "\n"
        "## 8a. Non-Negotiable Acceptance Criteria\n\n"
        f"{non_neg_body}\n"
    )


class TestBlockingAcFamilyCanonical(unittest.TestCase):
    """Reproduce the exact 4736bbd9 EC-4 / AC-12 pattern."""

    def test_canonical_budget_brain_pattern_flags_ec4(self) -> None:
        text = _build_spec(
            ec_rows=(
                "| EC-3 | `request.tenantId` is undefined | `AppError` with code `TENANT_REQUIRED`, status 401 |\n"
                "| EC-4 | `app.tenant_id` GUC is unset (no `SET LOCAL` in transaction) | "
                "`current_setting('app.tenant_id', true)` returns nullish, "
                "`NULLIF('', '')::uuid` yields SQL `NULL`, RLS returns zero rows — fail-closed, no cast error |\n"
                "| EC-5 | Transaction fails mid-query | Transaction rolls back automatically |\n"
            ),
            ac_rows=(
                "| AC-6 | GIVEN undefined `request.tenantId` WHEN `request.withDb` runs THEN `AppError` with code `TENANT_REQUIRED` and status 401 | yes |\n"
                "| AC-12 | GIVEN no `SET LOCAL app.tenant_id` WHEN query runs THEN "
                "`current_setting('app.tenant_id', true)` returns nullish (empty string `''`), "
                "`NULLIF('', '')::uuid` yields SQL `NULL`, zero rows returned (fail-closed, no cast error) | yes |\n"
            ),
            non_neg_body=(
                "- **AC-6** — auth enforcement.\n"
                "- **AC-12** — fail-closed security backstop.\n"
            ),
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        ec_ids = {f.ec_id for f in findings}
        self.assertIn("EC-4", ec_ids)
        ec4 = next(f for f in findings if f.ec_id == "EC-4")
        self.assertEqual(ec4.blocking_ac_id, "AC-12")
        # Should find the long SQL fragments in shared tokens.
        self.assertTrue(any("NULLIF" in t for t in ec4.shared_tokens))

    def test_ec3_flagged_against_ac6_auth_enforcement(self) -> None:
        """EC-3 and AC-6 share ``request.tenantId``, ``AppError``, ``TENANT_REQUIRED``."""
        text = _build_spec(
            ec_rows=(
                "| EC-3 | `request.tenantId` is undefined when `request.withDb` is called "
                "| `AppError` thrown with code `TENANT_REQUIRED`, status 401 |\n"
            ),
            ac_rows=(
                "| AC-6 | GIVEN `request.tenantId` is undefined WHEN `request.withDb(callback)` is called "
                "THEN it throws `AppError` with code `TENANT_REQUIRED` and status 401 | yes |\n"
            ),
            non_neg_body="- **AC-6** — auth enforcement.\n",
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].ec_id, "EC-3")
        self.assertEqual(findings[0].blocking_ac_id, "AC-6")


class TestBlockingAcFamilyNegativeCases(unittest.TestCase):
    def test_disjoint_ec_and_ac_no_finding(self) -> None:
        """Pool exhaustion EC ↔ RLS AC share no distinctive tokens."""
        text = _build_spec(
            ec_rows=(
                "| EC-6 | Connection pool exhausted (all connections in use) "
                "| `postgres.js` queues the request; if `queue_timeout` exceeded, throws connection error |\n"
            ),
            ac_rows=(
                "| AC-7 | GIVEN `app.tenant_id` set to tenant A WHEN query runs "
                "THEN only rows with matching `tenant_id` are returned (`strict-rls`) | yes |\n"
            ),
            non_neg_body="- **AC-7** — core tenant-isolation invariant.\n",
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(findings, [])

    def test_ec_already_in_non_negotiable_skipped(self) -> None:
        """EC-4 is explicitly declared blocking — no finding."""
        text = _build_spec(
            ec_rows=(
                "| EC-4 | `app.tenant_id` GUC is unset | "
                "`current_setting('app.tenant_id', true)` returns nullish, "
                "`NULLIF('', '')::uuid` yields SQL `NULL`, zero rows |\n"
            ),
            ac_rows=(
                "| AC-12 | GIVEN no `SET LOCAL app.tenant_id` WHEN query runs THEN "
                "`current_setting('app.tenant_id', true)` returns nullish, "
                "`NULLIF('', '')::uuid` yields SQL `NULL`, zero rows | yes |\n"
            ),
            non_neg_body=(
                "- **AC-12** — fail-closed backstop.\n"
                "- **EC-4** — fail-closed invariant (also declared at EC-level for reviewer visibility).\n"
            ),
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(findings, [])

    def test_no_blocking_acs_silent_no_op(self) -> None:
        text = _build_spec(
            ec_rows=(
                "| EC-4 | `app.tenant_id` GUC is unset | "
                "`NULLIF('', '')::uuid` yields SQL `NULL` |\n"
            ),
            ac_rows=(
                "| AC-12 | GIVEN `SET LOCAL app.tenant_id` not set THEN "
                "`NULLIF('', '')::uuid` yields SQL `NULL` | no |\n"
            ),
            non_neg_body="Justification: pure refactor, no blocking invariants.\n",
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(findings, [])

    def test_missing_ec_table_silent_no_op(self) -> None:
        text = (
            "## 8. Acceptance Criteria\n\n"
            "| ID | Criterion | Blocking |\n"
            "|----|-----------|:-:|\n"
            "| AC-12 | GIVEN `SET LOCAL app.tenant_id` not set THEN "
            "`NULLIF('', '')::uuid` yields SQL `NULL` | yes |\n"
            "\n"
            "## 8a. Non-Negotiable Acceptance Criteria\n\n"
            "- **AC-12** — fail-closed backstop.\n"
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(findings, [])

    def test_missing_ac_table_silent_no_op(self) -> None:
        text = (
            "## 6. Edge Cases\n\n"
            "| ID | Condition | Expected Behavior |\n"
            "|----|-----------|-------------------|\n"
            "| EC-4 | GUC unset | zero rows |\n"
            "\n"
            "## 8a. Non-Negotiable Acceptance Criteria\n\n"
            "- **AC-12** — fail-closed backstop.\n"
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(findings, [])


class TestBlockingAcFamilyThresholds(unittest.TestCase):
    def test_single_short_shared_token_below_threshold(self) -> None:
        """One shared short token (< 20 chars) is not enough."""
        text = _build_spec(
            ec_rows=(
                "| EC-5 | Transaction rolls back mid-query "
                "| `SET LOCAL` is also rolled back |\n"
            ),
            ac_rows=(
                "| AC-12 | GIVEN `SET LOCAL app.tenant_id` not set WHEN query runs THEN "
                "fail-closed, zero rows returned | yes |\n"
            ),
            non_neg_body="- **AC-12** — fail-closed backstop.\n",
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(findings, [])

    def test_single_long_shared_token_triggers_finding(self) -> None:
        """One shared token >= 20 chars is enough for a finding."""
        text = _build_spec(
            ec_rows=(
                "| EC-4 | GUC unset | "
                "`current_setting('app.tenant_id', true)` returns nullish |\n"
            ),
            ac_rows=(
                "| AC-12 | GIVEN no `SET LOCAL` THEN "
                "`current_setting('app.tenant_id', true)` returns nullish | yes |\n"
            ),
            non_neg_body="- **AC-12** — fail-closed backstop.\n",
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].ec_id, "EC-4")

    def test_two_short_shared_tokens_triggers_finding(self) -> None:
        """Two short tokens (each < 20 chars) meet the >= 2 threshold."""
        text = _build_spec(
            ec_rows=(
                "| EC-3 | `request.tenantId` missing "
                "| throws `AppError` with code `TENANT_REQUIRED` |\n"
            ),
            ac_rows=(
                "| AC-6 | GIVEN `request.tenantId` undefined THEN "
                "`AppError` with code `TENANT_REQUIRED` | yes |\n"
            ),
            non_neg_body="- **AC-6** — auth enforcement.\n",
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].ec_id, "EC-3")
        self.assertEqual(findings[0].blocking_ac_id, "AC-6")


class TestBlockingAcFamilyIntegration(unittest.TestCase):
    def test_pairs_ec_with_best_matching_ac(self) -> None:
        """Multi-AC spec — EC pairs with the AC it shares the most tokens with."""
        text = _build_spec(
            ec_rows=(
                "| EC-4 | GUC unset | "
                "`current_setting('app.tenant_id', true)` returns nullish; "
                "`NULLIF('', '')::uuid` yields SQL `NULL`; zero rows |\n"
            ),
            ac_rows=(
                "| AC-7 | GIVEN `app.tenant_id` set THEN matching rows only | yes |\n"
                "| AC-12 | GIVEN no `SET LOCAL app.tenant_id` THEN "
                "`current_setting('app.tenant_id', true)` returns nullish, "
                "`NULLIF('', '')::uuid` yields SQL `NULL`, zero rows | yes |\n"
            ),
            non_neg_body=(
                "- **AC-7** — strict RLS invariant.\n"
                "- **AC-12** — fail-closed backstop.\n"
            ),
        )
        findings = check_blocking_ac_family(parse_spec_model(text), raw_text=text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].ec_id, "EC-4")
        # Best pairing is AC-12 (more shared tokens) not AC-7.
        self.assertEqual(findings[0].blocking_ac_id, "AC-12")


if __name__ == "__main__":
    unittest.main()
