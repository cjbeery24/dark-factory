"""Phase 4.2 — gate policy resolver."""

import pytest

from darkfactory.pipeline.gate_policy import resolve_gate_policy


class TestResolveGatePolicy:
    def test_bootstrap_iter1_is_advisory(self):
        # A bootstrap spec is literally the work of getting a server
        # running. Demanding smoke success on iter1 would create a
        # chicken-and-egg failure — advisory is the whole point.
        assert resolve_gate_policy("bootstrap", 1) == "advisory"

    def test_bootstrap_iter2_is_fatal(self):
        # By iter2 the Implementer has had one pass at the bootstrap;
        # smoke must come up. No more leniency.
        assert resolve_gate_policy("bootstrap", 2) == "fatal"

    def test_bootstrap_iter3_is_fatal(self):
        assert resolve_gate_policy("bootstrap", 3) == "fatal"

    def test_feature_iter1_is_fatal(self):
        # Features are built on top of an already-running environment.
        # No excuse for smoke failing.
        assert resolve_gate_policy("feature", 1) == "fatal"

    def test_feature_iter2_is_fatal(self):
        assert resolve_gate_policy("feature", 2) == "fatal"

    def test_bugfix_iter1_is_fatal(self):
        # Bugfixes are similar to features — the environment exists.
        assert resolve_gate_policy("bugfix", 1) == "fatal"

    def test_refactor_iter1_is_fatal(self):
        assert resolve_gate_policy("refactor", 1) == "fatal"

    def test_unknown_spec_type_is_fatal(self):
        # Fail closed: only explicit bootstrap earns leniency. An empty
        # or unknown spec_type defaults to fatal so a parser regression
        # doesn't silently let every spec skip the smoke gate on iter1.
        assert resolve_gate_policy("", 1) == "fatal"
        assert resolve_gate_policy("nonsense", 1) == "fatal"

    def test_iteration_zero_or_negative_is_fatal(self):
        # Defensive: iteration loop is 1-indexed. Anything less is a
        # caller bug and must not accidentally trigger advisory.
        assert resolve_gate_policy("bootstrap", 0) == "fatal"
        assert resolve_gate_policy("bootstrap", -1) == "fatal"
