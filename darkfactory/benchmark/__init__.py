"""Benchmark runner + scorecard (Sprint 1 Part B).

A from-clean-state, multi-stack batch runner over ``dark-factory run``. Given a
battery config it runs each (target, spec, stack) N times and emits one minimal
JSONL scorecard row per run: {spec, stack, target, run, outcome, exit_code,
cost_usd, iterations, run_id, wall_s, ts}.

Deliberately small: it RECORDS rows, it does not analyze them. Determinism,
cost-means, and spec-vs-impl attribution are post-hoc queries over the JSONL,
not features of the runner.
"""
