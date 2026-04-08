"""Phase 2.6b — step_begin enforcement test (option A+).

Two invariants protect the env-contract freshening contract from
regressing:

1. **Static (AST):** every agent-invoking step function in the pipeline
   begins with a ``step_begin(...)`` call as its first statement. The
   set of such functions is pinned below — adding a 10th step function
   without registering it here is a test failure.

2. **No off-list step functions:** any function in
   ``orchestrator.py`` / ``iteration.py`` that calls ``invoke_agent``
   (the only agent-invocation entry point) MUST appear in the pinned
   set. This catches the "someone added a new step function and forgot
   step_begin" regression — the failure mode the audit doc was written
   to prevent.

3. **Two-cycle registry:** running ``step_begin`` twice for the same
   ``(spec_id, step_id)`` clears the prior entry rather than appending.
   This is the property the iteration loop relies on.

We deliberately do NOT install a ``Config.__getattribute__`` wrapper.
Phase 2.3 freshens config in-place; readers don't know about steps by
design. A dynamic wrapper would re-introduce exactly the coupling 2.3
removed and would not match a notion of "active step" the registry
tracks. See docs/plans/2026-04-06-pipeline-resilience-rewrite.md
section 2.6 for the rationale.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from darkfactory.architecture.runtime import (
    _registry_snapshot,
    _reset_registry,
    step_begin,
)
from darkfactory.models.config import PipelineConfig

# (module-relative-path, function-name) — the closed set of agent-invoking
# step functions. Adding a new agent-invoking step requires editing this
# set AND wiring step_begin into it. _step_smoke_test is intentionally
# absent: it does not invoke any agent.
PINNED_STEP_FUNCTIONS: frozenset[tuple[str, str]] = frozenset({
    ("darkfactory/pipeline/orchestrator.py", "_step_spec_validation"),
    ("darkfactory/pipeline/orchestrator.py", "_step_architecture_lock"),
    ("darkfactory/pipeline/orchestrator.py", "_step_scenario_generation"),
    ("darkfactory/pipeline/orchestrator.py", "_invoke_qa_holdout"),
    ("darkfactory/pipeline/orchestrator.py", "_step_scenario_repair"),
    ("darkfactory/pipeline/orchestrator.py", "_enforce_coverage_floor"),
    ("darkfactory/pipeline/orchestrator.py", "_handle_escalation"),
    ("darkfactory/pipeline/iteration.py", "_step_implementation"),
    ("darkfactory/pipeline/iteration.py", "_step_quality_gate"),
    ("darkfactory/pipeline/iteration.py", "_step_evaluator"),
    ("darkfactory/pipeline/iteration.py", "_step_classify"),
    ("darkfactory/pipeline/iteration.py", "_step_review"),
    ("darkfactory/pipeline/iteration.py", "_step_oracle"),
})

# run_iteration_loop calls step_begin too, but inside the while loop (so
# that each iteration freshens). It is exempt from the first-statement
# check, AND from the off-list check below — its agent invocations are
# delegated to the _step_* helpers, which are themselves pinned.
LOOP_FUNCTIONS_EXEMPT_FROM_OFFLIST: frozenset[tuple[str, str]] = frozenset({
    ("darkfactory/pipeline/iteration.py", "run_iteration_loop"),
})

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse(rel: str) -> ast.Module:
    return ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))


def _all_function_defs(tree: ast.Module) -> list[ast.FunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def _calls_to(name: str, fn: ast.FunctionDef) -> list[ast.Call]:
    out: list[ast.Call] = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name) and f.id == name:
            out.append(n)
        elif isinstance(f, ast.Attribute) and f.attr == name:
            out.append(n)
    return out


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _first_executable_call_name(fn: ast.FunctionDef) -> str | None:
    """Return the name of the first call statement, skipping the docstring."""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # skip docstring
    if not body:
        return None
    stmt = body[0]
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        f = stmt.value.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
    return None


# ---------- (1) Each pinned function has step_begin as first statement -----


@pytest.mark.parametrize("rel,fn_name", sorted(PINNED_STEP_FUNCTIONS))
def test_pinned_function_starts_with_step_begin(rel: str, fn_name: str):
    tree = _parse(rel)
    fn = _find_function(tree, fn_name)
    assert fn is not None, f"{rel}::{fn_name} not found — update PINNED_STEP_FUNCTIONS"
    first = _first_executable_call_name(fn)
    assert first == "step_begin", (
        f"{rel}::{fn_name} must call step_begin(...) as its first executable "
        f"statement (got {first!r}). Phase 2.3 contract: every step boundary "
        f"freshens PipelineConfig from environment.toml."
    )


# ---------- (2) No off-list function in those modules calls invoke_agent ---


def test_no_off_list_function_invokes_agent():
    """Any function in orchestrator.py / iteration.py that calls
    ``invoke_agent`` must appear in PINNED_STEP_FUNCTIONS. Catches the
    'someone added a new step function and forgot step_begin' regression."""
    pinned_by_module: dict[str, set[str]] = {}
    for rel, name in PINNED_STEP_FUNCTIONS | LOOP_FUNCTIONS_EXEMPT_FROM_OFFLIST:
        pinned_by_module.setdefault(rel, set()).add(name)

    offenders: list[str] = []
    for rel in pinned_by_module:
        tree = _parse(rel)
        for fn in _all_function_defs(tree):
            if not _calls_to("invoke_agent", fn):
                continue
            if fn.name not in pinned_by_module[rel]:
                offenders.append(f"{rel}::{fn.name}")

    assert not offenders, (
        "These functions invoke_agent but are not in PINNED_STEP_FUNCTIONS. "
        "Either add them to the pinned set (and wire step_begin) or refactor "
        "the agent invocation into one of the existing step functions:\n  - "
        + "\n  - ".join(offenders)
    )


# ---------- (3) Two-cycle registry behavior --------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry()
    yield
    _reset_registry()


def test_loop_function_has_no_direct_agent_invocations():
    """``run_iteration_loop`` is exempt from the off-list check because it
    delegates to pinned ``_step_*`` helpers. Close the loophole: assert
    the loop body contains *zero* direct ``invoke_agent`` calls. If
    someone ever inlines an agent invocation into the loop, this fires."""
    for rel, fn_name in LOOP_FUNCTIONS_EXEMPT_FROM_OFFLIST:
        tree = _parse(rel)
        fn = _find_function(tree, fn_name)
        assert fn is not None, f"{rel}::{fn_name} not found"
        direct = _calls_to("invoke_agent", fn)
        assert not direct, (
            f"{rel}::{fn_name} contains {len(direct)} direct invoke_agent "
            f"call(s). Loop functions must delegate agent invocations to "
            f"pinned _step_* helpers so each iteration freshens config "
            f"via step_begin."
        )


def test_two_cycle_registry_clears_prior_entry(tmp_path):
    config = PipelineConfig()
    step_begin("spec-1", "implementer", config, tmp_path)
    snap1 = _registry_snapshot()
    assert len(snap1) == 1

    # Second cycle: same key. Phase 2.3 contract is that the prior entry
    # is replaced (counter resets to 1), not accumulated. Iteration loop
    # depends on this — otherwise the registry grows unbounded across
    # NO-SHIP retries.
    step_begin("spec-1", "implementer", config, tmp_path)
    snap2 = _registry_snapshot()
    assert len(snap2) == 1
    assert list(snap2.values())[0] == 1
