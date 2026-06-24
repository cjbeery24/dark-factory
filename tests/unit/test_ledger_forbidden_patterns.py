"""Tests for deriving forbidden_patterns from the consistency ledger.

The extractor turns the validator's ``predicate == forbids`` claims that
name a code call into deterministic ``forbidden_patterns`` entries. The
load-bearing discriminator is the no-space-before-paren rule (``migrate(``
is a call; ``requests (status 401)`` is a prose parenthetical).

Grounded in REAL ledger bytes: the F-1c (database-foundation) ledger
emitted by the validator carries
``object: "migrate() programmatic helper invocation"`` /
``raw: "MUST NOT invoke the migrate(...) programmatic helper"`` — note
NO backticks survive, so extraction cannot lean on author code-formatting.
"""

from __future__ import annotations

import unittest

from darkfactory.pipeline.ledger_forbidden_patterns import (
    _anchor_id,
    _call_identifiers,
    derive_forbidden_patterns_from_ledger,
)


def _claim(predicate, obj, raw, anchor):
    return {
        "id": "x",
        "subject": "s",
        "predicate": predicate,
        "polarity": "negative" if predicate == "forbids" else "positive",
        "object": obj,
        "raw": raw,
        "anchor": anchor,
    }


def _ledger(*claims):
    return {"spec": "t", "fr_ledgers": [{"fr_id": "FR-1", "claims": list(claims)}]}


class TestAnchorId(unittest.TestCase):
    def test_strips_processing_suffix(self):
        self.assertEqual(_anchor_id("FR-1.processing@line39"), "FR-1")

    def test_bare_ac_ec(self):
        self.assertEqual(_anchor_id("AC-7"), "AC-7")
        self.assertEqual(_anchor_id("EC-3"), "EC-3")

    def test_unrecognised_returns_none(self):
        self.assertIsNone(_anchor_id("NFR-2.processing"))  # not FR/AC/EC
        self.assertIsNone(_anchor_id(""))
        self.assertIsNone(_anchor_id(None))
        self.assertIsNone(_anchor_id("line39"))


class TestCallIdentifiers(unittest.TestCase):
    def test_extracts_empty_and_ellipsis_calls(self):
        self.assertEqual(list(_call_identifiers("invoke migrate(...) helper")), ["migrate"])
        self.assertEqual(list(_call_identifiers("the migrate() helper")), ["migrate"])

    def test_no_space_paren_only(self):
        # Prose parenthetical (space before paren) is NOT a call.
        self.assertEqual(list(_call_identifiers("rejects requests (status 401)")), [])

    def test_real_args_rejected(self):
        # Illustrative calls with REAL args are prose, not symbol refs
        # (Linus C1). These would otherwise wall implementers calling
        # common library fns for unrelated reasons.
        self.assertEqual(list(_call_identifiers("MUST NOT use cache(key)")), [])
        self.assertEqual(list(_call_identifiers("never fetch(url) directly")), [])
        self.assertEqual(list(_call_identifiers("avoid process(input) here")), [])
        self.assertEqual(list(_call_identifiers("no migrate(db) on startup")), [])

    def test_dedupe_first_seen_order(self):
        text = "migrate() and migrate(...) and push()"
        self.assertEqual(list(_call_identifiers(text)), ["migrate", "push"])

    def test_keyword_stoplist(self):
        self.assertEqual(list(_call_identifiers("MUST NOT return() or if()")), [])

    def test_empty(self):
        self.assertEqual(list(_call_identifiers("")), [])


class TestDeriveForbiddenPatterns(unittest.TestCase):
    def test_real_f1c_migrate_claim(self):
        # The actual F-1c ledger bytes (no backticks in raw).
        ledger = _ledger(
            _claim(
                "forbids",
                "migrate() programmatic helper invocation",
                "MUST NOT invoke the migrate(...) programmatic helper",
                "FR-1.processing@line24",
            )
        )
        result = derive_forbidden_patterns_from_ledger(ledger)
        self.assertEqual(result, {"FR-1": [r"re:\bmigrate\s*\("]})

    def test_prose_forbids_yields_nothing(self):
        # bb spec-5's "observing another tenant's rows" forbids claim —
        # a behavioral MUST-NOT with no code call → no pattern, no false
        # positive (the gate can't pattern-match a behavior anyway).
        ledger = _ledger(
            _claim(
                "forbids",
                "observing another tenant's rows on the users table",
                "no request may observe another tenant's rows.",
                "FR-1.processing@line39",
            )
        )
        self.assertEqual(derive_forbidden_patterns_from_ledger(ledger), {})

    def test_object_first_raw_fallback(self):
        # F-1c's other migrate forbids: object names no call ("drizzle-kit
        # migrate invocation at startup"), so we fall back to raw, which
        # carries the symbol-ref form ("migrate(...)").
        ledger = _ledger(
            _claim(
                "forbids",
                "drizzle-kit migrate invocation at startup",
                "the server startup path MUST NOT invoke the migrate(...) helper",
                "FR-1.processing@line22",
            )
        )
        self.assertEqual(
            derive_forbidden_patterns_from_ledger(ledger),
            {"FR-1": [r"re:\bmigrate\s*\("]},
        )

    def test_prose_with_real_arg_calls_yields_nothing(self):
        # Behavioral MUST-NOT whose prose happens to contain call-shaped
        # illustrations with real args (Linus C1 adversarial set).
        ledger = _ledger(
            _claim(
                "forbids",
                "reading another tenant's rows",
                "a handler MUST NOT call fetch(url) or cache(key) cross-tenant",
                "FR-1.processing@line9",
            )
        )
        self.assertEqual(derive_forbidden_patterns_from_ledger(ledger), {})

    def test_non_forbids_claims_ignored(self):
        ledger = _ledger(
            _claim("reads", "token()", "reads token() value", "FR-1.processing@line5"),
            _claim("creates", "build()", "creates build() output", "FR-1.processing@line6"),
        )
        self.assertEqual(derive_forbidden_patterns_from_ledger(ledger), {})

    def test_anchor_keyed_and_deduped(self):
        ledger = {
            "spec": "t",
            "fr_ledgers": [
                {
                    "fr_id": "FR-2",
                    "claims": [
                        _claim("forbids", "migrate()", "MUST NOT call migrate(db)",
                               "FR-2.processing@line10"),
                        _claim("forbids", "migrate(...) helper", "never again",
                               "FR-2.processing@line11"),
                    ],
                }
            ],
        }
        result = derive_forbidden_patterns_from_ledger(ledger)
        # Same call across two claims on FR-2 → one deduped pattern.
        self.assertEqual(result, {"FR-2": [r"re:\bmigrate\s*\("]})

    def test_malformed_inputs_return_empty(self):
        self.assertEqual(derive_forbidden_patterns_from_ledger(None), {})
        self.assertEqual(derive_forbidden_patterns_from_ledger({}), {})
        self.assertEqual(derive_forbidden_patterns_from_ledger({"fr_ledgers": "x"}), {})
        self.assertEqual(
            derive_forbidden_patterns_from_ledger(
                {"fr_ledgers": [{"claims": "nope"}]}
            ),
            {},
        )

    def test_claim_without_recognised_anchor_skipped(self):
        ledger = _ledger(
            _claim("forbids", "migrate()", "MUST NOT migrate()", "NFR-1.processing")
        )
        self.assertEqual(derive_forbidden_patterns_from_ledger(ledger), {})

    def test_emitted_pattern_is_consumable_regex(self):
        # The emitted ``re:`` pattern must compile and match a real call
        # line but not a comment-mention without parens.
        import re
        ledger = _ledger(
            _claim("forbids", "migrate()", "MUST NOT invoke migrate()",
                   "FR-1.processing@line1")
        )
        pattern = derive_forbidden_patterns_from_ledger(ledger)["FR-1"][0]
        self.assertTrue(pattern.startswith("re:"))
        body = pattern[len("re:"):]
        self.assertIsNotNone(re.search(body, "await migrate(db);"))
        self.assertIsNotNone(re.search(body, "migrate (db)"))  # \s* allows space in SOURCE
        self.assertIsNone(re.search(body, "// see the migration guide"))


if __name__ == "__main__":
    unittest.main()
