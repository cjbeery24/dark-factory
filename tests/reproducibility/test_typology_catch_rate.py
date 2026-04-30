"""LLM-invoking reproducibility tests for the contradiction-typology surfaces.

Marked with ``@pytest.mark.llm``. Default test runs (``pytest`` with no
``-m`` flag) honor the ``addopts = "-m 'not llm'"`` setting in
``pyproject.toml`` and SKIP these tests.

Run explicitly:

    pytest tests/reproducibility/test_typology_catch_rate.py -m llm

Each parametrized case is one (fixture × surface) pair. The case runs N
invocations (default 5) and asserts the SHIP criterion: ≥4/5 catch rate
with same typology slot for TP fixtures, ≤1/5 false positives for
refinement counter-examples.

This file does NOT generate the catch-rate report — that's the job of
``run_sweep.py``. This file is the per-fixture "did the catch rate hold
on this run" verifier; the sweep CLI is what writes the audit artefact.
"""

from __future__ import annotations

import os

import pytest

from tests.reproducibility.harness import (
    Fixture,
    claude_available,
    load_fixtures,
    run_fixture_sweep,
)


_FIXTURES = load_fixtures()
_SURFACES = ("validator", "densifier")
_PARAMS = [(f, s) for f in _FIXTURES for s in _SURFACES]


def _id(case: tuple[Fixture, str]) -> str:
    fixture, surface = case
    return f"{fixture.fixture_id}-{surface}"


@pytest.mark.llm
@pytest.mark.parametrize("case", _PARAMS, ids=_id)
def test_fixture_catch_rate(case: tuple[Fixture, str]) -> None:
    fixture, surface = case
    if not claude_available():
        pytest.skip("claude CLI not on PATH; cannot run LLM reproducibility test")
    runs_env = os.getenv("DF_REPRO_RUNS")
    runs = int(runs_env) if runs_env else 5
    aggregate = run_fixture_sweep(fixture, surface, runs=runs)
    if not aggregate.passed:
        # Surface enough detail for the report to be reconstructed
        # without re-running.
        per_run_summary = "\n".join(
            f"  run {i + 1}: caught={r.caught} slot={r.slot} "
            f"discriminator={r.discriminator!r} fp={r.fp} notes={r.notes!r}"
            for i, r in enumerate(aggregate.per_run)
        )
        pytest.fail(
            f"{fixture.fixture_id} on {surface}: {aggregate.failure_reason}\n"
            f"catches={aggregate.catches}/{aggregate.runs} "
            f"fps={aggregate.false_positives} "
            f"slots={aggregate.slots_caught}\n{per_run_summary}"
        )
