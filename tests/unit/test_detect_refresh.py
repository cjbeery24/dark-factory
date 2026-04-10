"""Structural test: autodetect delta hook placement in iteration loop."""

from __future__ import annotations


class TestHookCallSitePlacement:
    """The autodetect delta hook fires once per full iteration, not per pre-gate
    retry. This is enforced by code structure: _autodetect_delta_hook is called at
    a single fixed point in run_iteration_loop, outside the pre-gate retry loop.
    This test verifies that structural invariant by inspecting the source."""

    def test_hook_called_once_outside_pregate_loop(self) -> None:
        import inspect
        from darkfactory.pipeline import iteration
        source = inspect.getsource(iteration.run_iteration_loop)
        # Exactly one call site.
        assert source.count("_autodetect_delta_hook") == 1, (
            "_autodetect_delta_hook should appear exactly once in run_iteration_loop"
        )
        assert "for pre_gate_attempt in range" in source, (
            "pre-gate retry loop not found in run_iteration_loop — update this test if the loop was refactored"
        )
        # The hook call appears before the pre-gate block, not inside it.
        hook_pos = source.index("_autodetect_delta_hook")
        pregate_loop_pos = source.index("for pre_gate_attempt in range")
        assert hook_pos < pregate_loop_pos, (
            "_autodetect_delta_hook must appear before the pre-gate retry loop, not inside it"
        )
