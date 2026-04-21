"""Codebase-level invariants for the iteration-loop terminal exits.

Run d717e915 aborted at preflight and left an incomplete per-run
archive because ``iteration.py`` raised bare ``DarkFactoryError`` at
the preflight-persisted and scenario-infrastructure sites. The outer
catch in ``orchestrator.py`` was ``(HardAbort, BudgetExceeded)``, so
those raises propagated past ``_handle_abort`` and no
``final/meta.json`` was ever written.

Two fixes went in:

1. The catch was broadened to ``(DarkFactoryError, BudgetExceeded)``
   so the archive handler runs for any abort-path raise.
2. The two terminal raises in ``iteration.py`` were converted to
   ``HardAbort`` and paired with ``emit_preflight_violation`` so
   Monitor tails see a structured event instead of a stdout line.

These tests lock in the second fix at the pattern level. A future
engineer who adds a new terminal abort to ``iteration.py`` and reaches
for bare ``DarkFactoryError`` will fail this test with a pointed
message about the ``HardAbort`` + event convention.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ITERATION_PY = (
    Path(__file__).resolve().parents[2]
    / "darkfactory"
    / "pipeline"
    / "iteration.py"
)
ORCHESTRATOR_PY = (
    Path(__file__).resolve().parents[2]
    / "darkfactory"
    / "pipeline"
    / "orchestrator.py"
)


class TestIterationTerminalRaises(unittest.TestCase):
    """Iteration-loop aborts must use ``HardAbort`` + an event."""

    def test_no_bare_dark_factory_error_raises_in_iteration(self) -> None:
        # ``iteration.py`` catches ``DarkFactoryError`` raised from
        # deeper modules in three retry loops, but must never *raise*
        # ``DarkFactoryError`` itself — every terminal abort in the
        # iteration loop must be ``HardAbort`` so it also carries a
        # ``PREFLIGHT_VIOLATION`` event upstream.
        source = ITERATION_PY.read_text(encoding="utf-8")
        hits = [
            (i, line)
            for i, line in enumerate(source.splitlines(), start=1)
            if re.search(r"\braise\s+DarkFactoryError\b", line)
        ]
        self.assertEqual(
            hits, [],
            msg=(
                "iteration.py must not `raise DarkFactoryError` — use "
                "`HardAbort` with a paired `emit_preflight_violation`. "
                "See run d717e915 post-mortem and "
                "darkfactory/pipeline/iteration.py history at the "
                "preflight-persisted and scenario-infrastructure "
                f"raise sites. Offending lines: {hits}"
            ),
        )

    def test_orchestrator_catch_routes_dark_factory_error(self) -> None:
        # The outer catch in ``run_single_spec`` must include
        # ``DarkFactoryError`` (not just ``HardAbort``) so an
        # accidentally-bare raise still reaches ``_handle_abort`` and
        # the run archive captures ``final/meta.json``. Enforce the
        # token-level presence so a future refactor that narrows the
        # catch trips this test before silently regressing archive
        # bookkeeping.
        source = ORCHESTRATOR_PY.read_text(encoding="utf-8")
        self.assertIn(
            "except (DarkFactoryError, BudgetExceeded)",
            source,
            msg=(
                "orchestrator.py run_single_spec must catch "
                "(DarkFactoryError, BudgetExceeded) so every abort-"
                "path raise — HardAbort or bare DarkFactoryError — "
                "routes through _handle_abort. Run d717e915 showed "
                "the narrow (HardAbort, BudgetExceeded) catch "
                "bypasses the archive handler."
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
