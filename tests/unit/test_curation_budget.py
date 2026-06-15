"""Curation-budget guard — the metric from the scalability/curation policy
(docs/plans/00-RESET-PLAN.md "Generalization thesis & curation policy",
Neo+Codex consensus 2026-06-15).

These tests do NOT enforce a maximum — they PIN the current count of BESPOKE,
hand-curated, stack-specific rules so that ADDING one fails CI until the baseline
is deliberately bumped here. That makes curation growth VISIBLE and INTENTIONAL
(the goal is "catch silent growth", not "zero"), and the bump PR is the place to
justify it against the decision rule: prefer (1) live ground-truth gate →
(2) maintained AST/SAST tool → (3) convert an LLM finding to a deterministic
check → (4) a bespoke rule ONLY when none of the above can.

What counts as BESPOKE curation (the budgeted axis): hand-written security
regexes, substrate-rule triggers, and mechanism-token patterns. Deterministic
checks that derive from GROUND TRUTH (e.g. sql_lint/sql_tag_syntax via the pglast
AST, the TS adapter checks that defer to tsc, catalog reads in verify_substrate)
are NOT bespoke-gotcha curation and are intentionally NOT counted here.

DOWNWARD changes are welcome (e.g. the planned SAST swap should REDUCE the
security-pattern count toward 0 as Semgrep/CodeQL take over). When that lands,
lower SECURITY_PATTERN_BUDGET accordingly.
"""
from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from darkfactory.review.patterns import SECURITY_PATTERNS

_PG = Path(__file__).resolve().parents[2] / "darkfactory" / "adapters" / "postgres"

# --- Baselines (bump DELIBERATELY, with justification, per the curation policy) ---
SECURITY_PATTERN_BUDGET = 15   # py 5 / node 5 / go 3 / rust 2 — SAST-swap target (should shrink)
SUBSTRATE_RULE_BUDGET = 3      # custom_guc / rls_policy / tenant_isolation — adapter contracts
MECHANISM_TOKEN_BUDGET = 14    # postgres mechanism-vs-observable blocklist


class TestCurationBudget(unittest.TestCase):
    def test_security_pattern_count_pinned(self) -> None:
        total = sum(len(v) for v in SECURITY_PATTERNS.values())
        self.assertEqual(
            total, SECURITY_PATTERN_BUDGET,
            f"Bespoke SECURITY_PATTERNS count changed ({total} vs budget "
            f"{SECURITY_PATTERN_BUDGET}). Curation budget (00-RESET-PLAN.md): adding a "
            f"hand-written security regex must be DELIBERATE — prefer a maintained SAST "
            f"rule (Semgrep/CodeQL) over a bespoke pattern. If intentional, bump "
            f"SECURITY_PATTERN_BUDGET and justify in the PR. (Downward = good, e.g. SAST swap.)",
        )

    def test_substrate_rule_count_pinned(self) -> None:
        rules = tomllib.loads((_PG / "substrate-rules.toml").read_text()).get("rule", [])
        self.assertEqual(
            len(rules), SUBSTRATE_RULE_BUDGET,
            f"Substrate-rule count changed ({len(rules)} vs {SUBSTRATE_RULE_BUDGET}). These are "
            f"adapter contracts feeding live verify_substrate — legitimate, but new ones still "
            f"count as curation: bump SUBSTRATE_RULE_BUDGET + justify per the curation policy.",
        )

    def test_mechanism_token_count_pinned(self) -> None:
        tokens = tomllib.loads((_PG / "mechanism-tokens.toml").read_text()).get("token", [])
        self.assertEqual(
            len(tokens), MECHANISM_TOKEN_BUDGET,
            f"Mechanism-token count changed ({len(tokens)} vs {MECHANISM_TOKEN_BUDGET}). Bespoke "
            f"spec-hygiene patterns — adding one is curation: bump MECHANISM_TOKEN_BUDGET + justify.",
        )
