"""LLM-invocation runner for the postgres adapter's retire_after auto-
eval workflow. ROADMAP.md Phase 3 B9b — third commit (foundation in
``e107479``, harness in ``8306fbc``, trigger prompts in this commit).

Pure host-side function: takes a helper name, a model identifier, and
a round count; invokes ``claude -p --output-format=json --max-turns 1``
N times with the helper's vanilla trigger prompt; collects the model's
textual outputs; hands the list to ``evaluate_fixture`` from the
harness; returns an :class:`EvalResult`.

What this module does NOT do:

* Persist artifacts under ``.dark-factory/audit/`` or anywhere else —
  the EvalResult is returned to the caller, who chooses whether to
  emit it on stdout or write it to a flag-supplied path. Path B
  decision (commit 3, neo-reviewed): auto-eval is a maintainer-only
  artifact with no per-project semantics, so writing under any
  project's ``.dark-factory/audit/`` is the wrong layer. The CLI
  subcommand mirrors ``_cmd_export_otel``'s
  stdout-with-optional-``--out`` shape.
* Open PRs or auto-merge anything — that lands in commit 4 (GitHub
  Actions workflow consuming the runner's output).
* Retry on subprocess failures. Fail-loud-on-first-round-failure is
  the chosen policy (B9b commit 3 design): a failed round means the
  evaluation cost the caller money for a result we can't trust, and
  hiding the failure under a soft-fail soft-bias would let
  silent-network-flake conditions tilt verdicts toward retire.
  Callers wrap with retry as needed.

**Authorization-model triggers (AUTHORIZATION-MODEL.md):**

Trigger #1 (audience escalation): NO. The runner produces an
``EvalResult`` and returns it to its caller (CLI). No string crosses
into a barrier-restricted reader's view; the runner does not write to
any directory on disk.

Trigger #2 (changed-surfaces declaration): NO. ``barriers.py``
untouched, ``quality-gate.sh`` report rows untouched,
``scenario_lint.py`` untouched, no ``.dark-factory/`` artifact.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from darkfactory.adapters.postgres.retire_after_eval import (
    EvalResult,
    evaluate_fixture,
)
from darkfactory.adapters.postgres.retire_after_fixtures import (
    BY_HELPER_NAME as _FIXTURES_BY_NAME,
)
from darkfactory.adapters.postgres.retire_after_trigger_prompts import (
    BY_HELPER_NAME as _TRIGGER_PROMPTS_BY_NAME,
)
from darkfactory.utils.subprocess_runner import RunResult
from darkfactory.utils.subprocess_runner import run as _default_subprocess_runner

log = logging.getLogger(__name__)


# Wrapping preamble for the trigger prompt body. Frames the eval as a
# vitest-test authoring task without naming any helper or
# adapter-specific primitive — the per-helper trigger body owns the
# spec content. The preamble's job is just to put the model in
# "writing one test file" mode rather than "explaining" or "asking
# clarifying questions."
_PROMPT_PREAMBLE = (
    "You are writing a single vitest test file in TypeScript. The "
    "acceptance criterion is described below. Emit a complete test "
    "snippet ready to drop into a `tests/` directory, including any "
    "necessary `import` statements. Output only the test code, no "
    "surrounding explanation.\n\nAcceptance criterion:\n"
)


# Subprocess-runner injection point for tests. The default is the
# real ``subprocess_runner.run``; tests pass a stub that returns
# canned ``RunResult`` instances. Defined as a Callable rather than a
# Protocol because the only caller, ``run_auto_eval`` below, drives
# the call site directly — a Protocol would just add a layer over
# plain function dispatch.
SubprocessRunner = Callable[..., RunResult]


def _render_prompt(prompt_body: str) -> str:
    return _PROMPT_PREAMBLE + prompt_body


def _claude_command(prompt: str, model: str) -> list[str]:
    return [
        "claude", "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--max-turns", "1",
    ]


def run_auto_eval(
    helper_name: str,
    model: str,
    rounds: int,
    *,
    runner: SubprocessRunner | None = None,
    timeout: int = 120,
) -> EvalResult:
    """Run the auto-eval workflow for one helper at one model.

    For each of N ``rounds``, invoke ``claude -p`` with the helper's
    vanilla trigger prompt, parse the JSON output to extract the
    model's textual reply, and accumulate the replies. Hand the list
    of N replies to :func:`evaluate_fixture` and return the result.

    Raises ``ValueError`` if ``rounds < 1`` or ``helper_name`` is
    unknown to the framework's universal-helper registries.

    Raises ``RuntimeError`` if any round's subprocess fails (non-zero
    exit, timeout, or unparseable JSON output) — see module docstring
    for the fail-loud rationale.

    The ``runner`` parameter is the subprocess-runner injection point
    used by tests; production callers omit it and get
    ``subprocess_runner.run`` via call-time module lookup (the
    function-default-at-definition-time binding would defeat
    ``unittest.mock.patch`` at the module attribute level). ``timeout``
    is the per-round subprocess timeout in seconds (default 120).
    """
    if rounds < 1:
        raise ValueError(f"rounds must be >= 1, got {rounds}")

    if runner is None:
        # Module-level lookup at CALL time so tests can patch
        # ``_default_subprocess_runner`` and have the change take
        # effect. Function defaults bind at definition time, so
        # ``runner=_default_subprocess_runner`` in the signature
        # would not respect the patch.
        runner = _default_subprocess_runner

    if helper_name not in _TRIGGER_PROMPTS_BY_NAME:
        known = sorted(_TRIGGER_PROMPTS_BY_NAME)
        raise ValueError(
            f"unknown helper {helper_name!r} — no TriggerPrompt registered. "
            f"Known: {known}"
        )
    # No second ``helper_name not in _FIXTURES_BY_NAME`` check — the
    # bidirectional invariant test
    # (``test_b9b_trigger_prompts.test_every_trigger_prompt_has_a_fixture``)
    # makes that branch structurally unreachable. Two lookups for the
    # same set membership is paranoia, not engineering (Linus 3b
    # review).

    trigger = _TRIGGER_PROMPTS_BY_NAME[helper_name]
    fixture = _FIXTURES_BY_NAME[helper_name]
    rendered = _render_prompt(trigger.prompt_body)

    outputs: list[str] = []
    for round_idx in range(rounds):
        log.info(
            "auto-eval %s @ %s — round %d/%d",
            helper_name, model, round_idx + 1, rounds,
        )
        result = runner(
            _claude_command(rendered, model),
            timeout=timeout,
        )
        if result.timed_out:
            raise RuntimeError(
                f"round {round_idx + 1}/{rounds} timed out after "
                f"{timeout}s — increase --timeout or check claude availability"
            )
        if result.exit_code != 0:
            raise RuntimeError(
                f"round {round_idx + 1}/{rounds} subprocess failed "
                f"(exit_code={result.exit_code}, "
                f"stderr={result.stderr[:200]!r})"
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"round {round_idx + 1}/{rounds} JSON-parse failed: {exc} "
                f"(stdout[:200]={result.stdout[:200]!r})"
            ) from exc
        text = data.get("result")
        if not isinstance(text, str):
            # Include the full key list so a maintainer hitting a
            # claude-CLI output-format schema drift can diagnose
            # without re-running. ``data`` is dict-of-Any from
            # ``json.loads``; we're pinning to an undocumented
            # internal field of an external CLI. The key list is the
            # cheapest forward-compatibility hedge (Linus 3b review).
            keys = list(data.keys()) if isinstance(data, dict) else []
            raise RuntimeError(
                f"round {round_idx + 1}/{rounds} JSON output missing "
                f"'result' string field (got {type(text).__name__}, "
                f"keys={keys})"
            )
        outputs.append(text)

    return evaluate_fixture(fixture, outputs)
