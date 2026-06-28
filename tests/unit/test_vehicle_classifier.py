"""Phase-1 coverage-floor vehicle classifier (suggest-only) + its rendering.

Pins: the classifier maps a focused LLM reply to a VehicleSuggestion with a
DETERMINISTIC trust tier; it is fail-safe (any subprocess failure / missing
claude / bad output → ABSTAIN, never raises into the floor-abort path); and the
floor remediation renders a non-abstain suggestion (with the subprocess template
for `subprocess`) while ABSTAIN falls back to the static menu.
"""
from __future__ import annotations

import json
import unittest

from darkfactory.scenarios.coverage import CoverageGap, render_floor_remediation
from darkfactory.scenarios.vehicle_classifier import (
    ABSTAIN,
    CONDITIONAL,
    PRESERVING,
    REDUCING,
    UNSURE,
    WAIVER,
    VehicleSuggestion,
    classify_floor_anchors,
)
from darkfactory.utils.subprocess_runner import RunResult


def _runner(reply, *, exit_code=0, timed_out=False, envelope=True, raise_exc=None,
            raw_stdout=None):
    """Build a fake subprocess runner returning one canned reply.

    `reply` is the {vehicle, reason} object the model 'returns'. With
    envelope=True it is wrapped in claude's `{"result": "<json string>"}`
    output-format-json envelope; with envelope=False the reply IS the raw stdout.
    """
    def fake(cmd, timeout=None):
        if raise_exc is not None:
            raise raise_exc
        if raw_stdout is not None:
            stdout = raw_stdout
        elif envelope:
            stdout = json.dumps({"result": json.dumps(reply)})
        else:
            stdout = json.dumps(reply)
        return RunResult(exit_code=exit_code, stdout=stdout, timed_out=timed_out)
    return fake


def _one(anchor_text, runner):
    return classify_floor_anchors(
        [("AC-1", anchor_text)], model="m", runner=runner
    )["AC-1"]


class ClassifierTierMappingTests(unittest.TestCase):
    def test_each_vehicle_maps_to_its_deterministic_tier(self):
        cases = {
            "runtime-boundary": PRESERVING,
            "substrate-covers": CONDITIONAL,
            "subprocess": REDUCING,
            "untestable": WAIVER,
            "rescope": WAIVER,
        }
        for vehicle, tier in cases.items():
            with self.subTest(vehicle=vehicle):
                s = _one("AC-1: ...", _runner({"vehicle": vehicle, "reason": "r"}))
                self.assertEqual(s.vehicle, vehicle)
                self.assertEqual(s.tier, tier)
                self.assertFalse(s.abstained)

    def test_explicit_abstain(self):
        s = _one("AC-1: ...", _runner({"vehicle": ABSTAIN, "reason": "unsure"}))
        self.assertTrue(s.abstained)
        self.assertEqual(s.tier, UNSURE)

    def test_unknown_vehicle_becomes_abstain(self):
        s = _one("AC-1: ...", _runner({"vehicle": "frobnicate", "reason": "x"}))
        self.assertTrue(s.abstained)

    def test_raw_reply_without_envelope_parses(self):
        s = _one("AC-1: ...", _runner({"vehicle": "subprocess", "reason": "r"},
                                       envelope=False))
        self.assertEqual(s.vehicle, "subprocess")

    def test_reply_with_surrounding_prose_parses(self):
        raw = json.dumps({"result": 'Sure!\n{"vehicle":"untestable","reason":"x"} done'})
        s = _one("AC-1: ...", _runner(None, raw_stdout=raw))
        self.assertEqual(s.vehicle, "untestable")


class ClassifierFailSafeTests(unittest.TestCase):
    """ANY failure must degrade to ABSTAIN, never raise into the abort path."""

    def test_claude_not_found_abstains(self):
        s = _one("AC-1: ...", _runner(None, raise_exc=FileNotFoundError()))
        self.assertTrue(s.abstained)
        self.assertIn("not found", s.reason)

    def test_arbitrary_subprocess_error_abstains(self):
        s = _one("AC-1: ...", _runner(None, raise_exc=RuntimeError("boom")))
        self.assertTrue(s.abstained)

    def test_timeout_abstains(self):
        s = _one("AC-1: ...", _runner({"vehicle": "subprocess"}, timed_out=True))
        self.assertTrue(s.abstained)

    def test_nonzero_exit_abstains(self):
        s = _one("AC-1: ...", _runner({"vehicle": "subprocess"}, exit_code=1))
        self.assertTrue(s.abstained)

    def test_unparseable_output_abstains(self):
        s = _one("AC-1: ...", _runner(None, raw_stdout="not json at all"))
        self.assertTrue(s.abstained)

    def test_malformed_result_object_abstains_not_raises(self):
        # A runner returning an object missing .exit_code/.stdout must ABSTAIN
        # (self-contained fail-safe), never AttributeError out of the classifier.
        def bad_runner(cmd, timeout=None):
            return object()
        s = _one("AC-1: ...", bad_runner)
        self.assertTrue(s.abstained)

    def test_non_dict_json_stdout_abstains(self):
        for raw in ("[1, 2, 3]", "42", '"just a string"'):
            with self.subTest(raw=raw):
                s = _one("AC-1: ...", _runner(None, raw_stdout=raw))
                self.assertTrue(s.abstained)

    def test_envelope_result_non_string_abstains(self):
        # {"result": {...}} — result is not a str → no usable vehicle → ABSTAIN.
        raw = json.dumps({"result": {"vehicle": "subprocess"}})
        s = _one("AC-1: ...", _runner(None, raw_stdout=raw))
        self.assertTrue(s.abstained)

    def test_envelope_result_decodes_to_non_dict_abstains(self):
        # envelope valid, `result` IS a string, but the inner string decodes to a
        # non-dict (list / number) → no dict-with-vehicle → ABSTAIN (Codex B3).
        for inner in ("[1, 2, 3]", "42", '"plain"'):
            with self.subTest(inner=inner):
                raw = json.dumps({"result": inner})
                s = _one("AC-1: ...", _runner(None, raw_stdout=raw))
                self.assertTrue(s.abstained)

    def test_multiple_objects_first_with_vehicle_wins(self):
        raw = json.dumps({"result": '{"note":"hi"} {"vehicle":"subprocess","reason":"r"}'})
        s = _one("AC-1: ...", _runner(None, raw_stdout=raw))
        self.assertEqual(s.vehicle, "subprocess")

    def test_balanced_stray_object_before_real_one_is_skipped(self):
        # a balanced non-vehicle object before the real one → scanner skips it.
        raw = json.dumps({"result": '{"thinking":"x"} {"vehicle":"untestable","reason":"r"}'})
        s = _one("AC-1: ...", _runner(None, raw_stdout=raw))
        self.assertEqual(s.vehicle, "untestable")

    def test_unbalanced_braces_safely_abstain(self):
        # an UNBALANCED outer brace cannot be recovered → safe ABSTAIN.
        raw = json.dumps({"result": 'log {incomplete and {"vehicle":"untestable"}'})
        s = _one("AC-1: ...", _runner(None, raw_stdout=raw))
        self.assertTrue(s.abstained)

    def test_non_string_vehicle_reason_coerced(self):
        # LLM returns non-string types — coerced, not crashed (→ unknown → ABSTAIN).
        raw = json.dumps({"result": '{"vehicle": 5, "reason": ["a"]}'})
        s = _one("AC-1: ...", _runner(None, raw_stdout=raw))
        self.assertTrue(s.abstained)  # "5" is not a known vehicle

    def test_every_input_anchor_present_in_output(self):
        out = classify_floor_anchors(
            [("AC-1", "x"), ("EC-2", "y")], model="m",
            runner=_runner({"vehicle": "subprocess", "reason": "r"}),
        )
        self.assertEqual(set(out), {"AC-1", "EC-2"})


class RenderWithSuggestionTests(unittest.TestCase):
    def _gaps(self):
        return [
            CoverageGap(anchor_id="AC-1", reason="Spawns a build subprocess.",
                        category="spec-vehicle-mismatch"),
            CoverageGap(anchor_id="AC-2", reason="Some boundary thing.",
                        category="spec-vehicle-mismatch"),
        ]

    def test_subprocess_suggestion_renders_tier_and_template(self):
        sug = {"AC-1": VehicleSuggestion("AC-1", "subprocess", REDUCING, "CLI build")}
        out = render_floor_remediation(self._gaps(), ["AC-1"], None, sug)
        self.assertIn("SUGGESTED vehicle: `subprocess`", out)
        self.assertIn("REDUCES verification", out)        # the tier caption
        self.assertIn("covers: [AC-1]", out)              # the template
        self.assertIn("Advisory only", out)

    def test_preserving_suggestion_has_no_template(self):
        sug = {"AC-2": VehicleSuggestion("AC-2", "runtime-boundary", PRESERVING, "env")}
        out = render_floor_remediation(self._gaps(), ["AC-2"], None, sug)
        self.assertIn("SUGGESTED vehicle: `runtime-boundary`", out)
        self.assertNotIn("```yaml", out)  # template only for subprocess

    def test_all_abstain_suppresses_note_and_matches_menu(self):
        # Every anchor abstains (e.g. claude absent) → no SUGGESTED line, no
        # abstain note; identical to the suggestions=None render (Linus C2).
        sug = {
            "AC-1": VehicleSuggestion("AC-1", ABSTAIN, UNSURE, "x"),
            "AC-2": VehicleSuggestion("AC-2", ABSTAIN, UNSURE, "x"),
        }
        with_sug = render_floor_remediation(self._gaps(), ["AC-1", "AC-2"], None, sug)
        without = render_floor_remediation(self._gaps(), ["AC-1", "AC-2"], None)
        self.assertEqual(with_sug, without)
        self.assertNotIn("no confident vehicle suggestion", with_sug)

    def test_mixed_abstain_shows_note_only_when_some_real(self):
        sug = {
            "AC-1": VehicleSuggestion("AC-1", "subprocess", REDUCING, "CLI"),
            "AC-2": VehicleSuggestion("AC-2", ABSTAIN, UNSURE, "x"),
        }
        out = render_floor_remediation(self._gaps(), ["AC-1", "AC-2"], None, sug)
        self.assertIn("SUGGESTED vehicle: `subprocess`", out)
        self.assertIn("no confident vehicle suggestion", out)
        self.assertIn("vehicle: subprocess", out)  # static menu still present

    def test_no_suggestions_is_backcompat(self):
        # suggestions=None → identical shape to the pre-Phase-1 menu render.
        out = render_floor_remediation(self._gaps(), ["AC-1", "AC-2"], None)
        self.assertNotIn("SUGGESTED vehicle", out)
        self.assertIn("AC-1: Spawns a build subprocess.", out)

    def test_menu_carries_three_tier_framing(self):
        out = render_floor_remediation(self._gaps(), ["AC-1"], None)
        self.assertIn("PRESERVES verification", out)
        self.assertIn("REDUCES verification", out)
        self.assertIn("WAIVES verification", out)


if __name__ == "__main__":
    unittest.main()
