"""Tests for ``_step_te_pre_acceptance`` (ROADMAP.md Phase 2 B1).

Pins the host-side §4a TE pre-acceptance check:

- retry budget bookkeeping (off-by-one, exhaustion path),
- phase-transition state machine (sentinel/no-files skip, clean pass,
  retry-then-clean, budget-exhausted abort),
- classification taxonomy (HardAbort prefix
  ``scenario_author_retry_exhausted:`` so the orchestrator-level
  ``(DarkFactoryError, BudgetExceeded)`` catch routes through
  ``_handle_abort`` and ``analyze_trial.py``'s arbitrary-string
  classifier accepts the new key),
- audit log writes per-attempt under
  ``.dark-factory/audit/te-acceptance-<spec>-iter<n>.json`` (Implementer-
  masked dir per ``barriers.py``).

Mocks ``invoke_agent`` so no Docker container starts. The TE retry
behavior is simulated by writing/clearing scenario files inside the
mock callback.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from darkfactory.errors import HardAbort
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import SpecRun
from darkfactory.pipeline.orchestrator import _step_te_pre_acceptance


_VIOLATING_TS = """
import { test } from "vitest";
test("[AC-1] uses banned pattern", async () => {
  const x = "foo_bar_baz_qux_quux_violation";
});
"""

_CLEAN_TS = """
import { test } from "vitest";
test("[AC-1] is clean", async () => {
  const x = "ok";
});
"""

_SPEC_WITH_FORBIDDEN_SHAPE = """\
# Demo Spec

## 1. Goal
Whatever.

## 4a. Negative Space

```yaml
forbidden_test_shapes:
  - pattern: "foo_bar_baz_qux_quux_violation"
    reason: "Test fixture pattern only — must not appear in real scenarios"
```
"""

_TIER1_CLEAN = """
import { test } from "vitest";
test("[FR-1] tier1 contract", async () => {});
"""


def _make_project(root: Path, spec_name: str = "demo") -> tuple[Path, Path]:
    """Build a minimal project tree and return (project_dir, scenarios_dir)."""
    df = root / ".dark-factory"
    (df / "specs").mkdir(parents=True)
    (df / "scenarios" / spec_name).mkdir(parents=True)
    (df / "audit").mkdir(parents=True)
    spec_file = df / "specs" / f"{spec_name}.md"
    spec_file.write_text(_SPEC_WITH_FORBIDDEN_SHAPE, encoding="utf-8")
    return root, df / "scenarios" / spec_name


def _spec_run(spec_name: str = "demo") -> SpecRun:
    return SpecRun(
        spec_name=spec_name, run_id="run123abc456",
        spec_hash="h", spec_type="feature",
    )


# ---------- skip paths ----------------------------------------------------


class TestSkipPaths(unittest.TestCase):
    def test_no_scenario_files_skips_lint(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir, _scen = _make_project(Path(tmp))
            spec_run = _spec_run()
            config = PipelineConfig(te_pre_acceptance_max_retries=2)

            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
            ) as mock_invoke:
                _step_te_pre_acceptance(
                    spec_run, config, {}, project_dir,
                    events_file=None, audit_path=None, dry_run=False,
                )
            # No retry invocation, no audit file (early return).
            mock_invoke.assert_not_called()
            audit_dir = project_dir / ".dark-factory" / "audit"
            self.assertEqual(
                list(audit_dir.glob("te-acceptance-*")),
                [],
            )

    def test_sentinel_skips_lint(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir, scen_dir = _make_project(Path(tmp))
            (scen_dir / "SCENARIOS-NOT-APPLICABLE.md").write_text(
                "# Justification\n\nReason.\n", encoding="utf-8",
            )
            spec_run = _spec_run()
            config = PipelineConfig(te_pre_acceptance_max_retries=2)

            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
            ) as mock_invoke:
                _step_te_pre_acceptance(
                    spec_run, config, {}, project_dir,
                    events_file=None, audit_path=None, dry_run=False,
                )
            mock_invoke.assert_not_called()


# ---------- happy path ----------------------------------------------------


class TestCleanFirstAttempt(unittest.TestCase):
    def test_no_issues_returns_with_one_audit_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir, scen_dir = _make_project(Path(tmp))
            (scen_dir / "01.test.ts").write_text(_CLEAN_TS, encoding="utf-8")
            spec_run = _spec_run()
            config = PipelineConfig(te_pre_acceptance_max_retries=3)

            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
            ) as mock_invoke:
                _step_te_pre_acceptance(
                    spec_run, config, {}, project_dir,
                    events_file=None, audit_path=None, dry_run=False,
                )
            mock_invoke.assert_not_called()

            audit_files = sorted(
                (project_dir / ".dark-factory" / "audit").glob(
                    "te-acceptance-demo-iter*.json"
                )
            )
            self.assertEqual(len(audit_files), 1)
            self.assertTrue(audit_files[0].name.endswith("-iter0.json"))
            audit = json.loads(audit_files[0].read_text())
            self.assertEqual(audit["spec"], "demo")
            self.assertEqual(audit["attempt"], 0)
            self.assertEqual(audit["max_retries"], 3)
            self.assertEqual(audit["issue_count"], 0)
            self.assertEqual(audit["issues"], [])


# ---------- retry-then-clean ---------------------------------------------


class TestRetrySucceeds(unittest.TestCase):
    def test_one_retry_clears_violation(self) -> None:
        """Attempt 0 sees the violation; the TE retry rewrites the file
        clean; attempt 1 lint passes; phase returns without abort. Two
        audit entries (iter0 dirty, iter1 clean)."""
        with TemporaryDirectory() as tmp:
            project_dir, scen_dir = _make_project(Path(tmp))
            (scen_dir / "01.test.ts").write_text(_VIOLATING_TS, encoding="utf-8")
            spec_run = _spec_run()
            config = PipelineConfig(te_pre_acceptance_max_retries=2)

            invoke_calls: list[tuple] = []

            def _fake_invoke(role, prompt, **kwargs):
                invoke_calls.append((role, kwargs.get("phase")))
                # Simulate the TE rewriting the file clean. _clear_tier2_scenarios
                # has already removed the dirty file before this callback runs.
                (scen_dir / "01.test.ts").write_text(
                    _CLEAN_TS, encoding="utf-8",
                )
                return AgentResult(role=role, exit_code=0)

            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
                side_effect=_fake_invoke,
            ):
                _step_te_pre_acceptance(
                    spec_run, config, {}, project_dir,
                    events_file=None, audit_path=None, dry_run=False,
                )

            # Exactly one TE retry invocation (attempt 0 had hits, attempt 1 was clean).
            self.assertEqual(len(invoke_calls), 1)
            self.assertEqual(invoke_calls[0][0], AgentRole.TEST_ENGINEER)
            self.assertEqual(invoke_calls[0][1], "te_pre_acceptance_retry")

            audit_files = sorted(
                (project_dir / ".dark-factory" / "audit").glob(
                    "te-acceptance-demo-iter*.json"
                )
            )
            self.assertEqual(
                [f.name for f in audit_files],
                [
                    "te-acceptance-demo-iter0.json",
                    "te-acceptance-demo-iter1.json",
                ],
            )
            iter0 = json.loads(audit_files[0].read_text())
            iter1 = json.loads(audit_files[1].read_text())
            self.assertGreaterEqual(iter0["issue_count"], 1)
            self.assertEqual(iter1["issue_count"], 0)


# ---------- budget exhaustion --------------------------------------------


class TestBudgetExhausted(unittest.TestCase):
    def test_exhaustion_raises_hard_abort_with_classification(self) -> None:
        """All attempts hit; budget=2 → 1 initial + 2 retries = 3 lint checks
        and 2 TE invocations; raises HardAbort with the canonical
        classification keyword prefix."""
        with TemporaryDirectory() as tmp:
            project_dir, scen_dir = _make_project(Path(tmp))
            (scen_dir / "01.test.ts").write_text(_VIOLATING_TS, encoding="utf-8")
            spec_run = _spec_run()
            config = PipelineConfig(te_pre_acceptance_max_retries=2)

            invoke_count = [0]

            def _fake_invoke(role, prompt, **kwargs):
                invoke_count[0] += 1
                # TE "fixes" produce another violating file (the
                # adversarial case — TE never converges).
                (scen_dir / "01.test.ts").write_text(
                    _VIOLATING_TS, encoding="utf-8",
                )
                return AgentResult(role=role, exit_code=0)

            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
                side_effect=_fake_invoke,
            ):
                with self.assertRaises(HardAbort) as ctx:
                    _step_te_pre_acceptance(
                        spec_run, config, {}, project_dir,
                        events_file=None, audit_path=None, dry_run=False,
                    )

            # Classification keyword is the prefix; exact taxonomy
            # match is what analyze_trial.py and any future structured
            # error_code consumer rely on.
            msg = str(ctx.exception)
            self.assertTrue(
                msg.startswith("scenario_author_retry_exhausted:"),
                f"abort message must start with classification keyword, got {msg!r}",
            )
            # run_id propagation (HardAbort.run_id is the structured
            # remediation hint used by the CLI top-level catch).
            self.assertEqual(ctx.exception.run_id, "run123abc456")

            # Budget bookkeeping: max_retries=2 → 2 TE retry invocations
            # (initial check is host-side, no agent call).
            self.assertEqual(invoke_count[0], 2)

            # Audit per-attempt: max_retries+1 = 3 files (iter0/1/2).
            audit_files = sorted(
                (project_dir / ".dark-factory" / "audit").glob(
                    "te-acceptance-demo-iter*.json"
                )
            )
            self.assertEqual(
                [f.name for f in audit_files],
                [
                    "te-acceptance-demo-iter0.json",
                    "te-acceptance-demo-iter1.json",
                    "te-acceptance-demo-iter2.json",
                ],
            )

    def test_zero_retries_aborts_after_initial_check(self) -> None:
        """``max_retries=0`` means lint once, abort on any hit, no TE
        invocation. Pins the off-by-one boundary."""
        with TemporaryDirectory() as tmp:
            project_dir, scen_dir = _make_project(Path(tmp))
            (scen_dir / "01.test.ts").write_text(_VIOLATING_TS, encoding="utf-8")
            spec_run = _spec_run()
            config = PipelineConfig(te_pre_acceptance_max_retries=0)

            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
            ) as mock_invoke:
                with self.assertRaises(HardAbort) as ctx:
                    _step_te_pre_acceptance(
                        spec_run, config, {}, project_dir,
                        events_file=None, audit_path=None, dry_run=False,
                    )
            mock_invoke.assert_not_called()
            self.assertTrue(
                str(ctx.exception).startswith(
                    "scenario_author_retry_exhausted:"
                )
            )

    def test_retry_exit_code_surfaced_in_abort_message(self) -> None:
        """A non-zero TE retry exit_code is captured in the audit log
        under ``prior_te_retry_exit_code`` AND surfaced in the final
        abort message (so an ops-level investigation of the abort
        string distinguishes "TE refused to fix" from "TE crashed
        before emitting")."""
        with TemporaryDirectory() as tmp:
            project_dir, scen_dir = _make_project(Path(tmp))
            (scen_dir / "01.test.ts").write_text(_VIOLATING_TS, encoding="utf-8")
            spec_run = _spec_run()
            config = PipelineConfig(te_pre_acceptance_max_retries=1)

            def _fake_invoke(role, prompt, **kwargs):
                # TE crashed without writing anything — the offender
                # file was already cleared by _clear_tier2_scenarios,
                # so the next lint sees an empty scenarios dir.
                return AgentResult(role=role, exit_code=2)

            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
                side_effect=_fake_invoke,
            ):
                # No new files emitted → empty scenarios → second-attempt
                # has_scenario_files is False; but our check is at entry,
                # so we'd still try to lint an empty dir which yields
                # 0 issues. Re-write the violating file directly to
                # force the lint to fire on attempt 1 too.
                def _fake_invoke_keep_violation(role, prompt, **kwargs):
                    # Restore the violation so attempt 1 has hits.
                    (scen_dir / "01.test.ts").write_text(
                        _VIOLATING_TS, encoding="utf-8",
                    )
                    return AgentResult(role=role, exit_code=2)

                with patch(
                    "darkfactory.pipeline.orchestrator.invoke_agent",
                    side_effect=_fake_invoke_keep_violation,
                ):
                    with self.assertRaises(HardAbort) as ctx:
                        _step_te_pre_acceptance(
                            spec_run, config, {}, project_dir,
                            events_file=None, audit_path=None, dry_run=False,
                        )

            self.assertIn("exit_code=2", str(ctx.exception))
            iter1 = json.loads(
                (
                    project_dir / ".dark-factory" / "audit"
                    / "te-acceptance-demo-iter1.json"
                ).read_text()
            )
            # iter1 is the post-retry lint; its prior retry exit was 2.
            self.assertEqual(iter1["prior_te_retry_exit_code"], 2)


# ---------- tier-2 clearing between retries ------------------------------


class TestTier2Clearing(unittest.TestCase):
    def test_df_setup_preserved_between_retries(self) -> None:
        """Sprint 29 (Codex gate): the staged DB-reset gate-infra (_df_setup/)
        must survive the retry clear, exactly like _contracts/. It is staged
        ONCE before the TE generate/validate/regen retry loops; wiping it here
        would drop the reset from the final gate run on RETRIED postgres specs,
        reintroducing the Sprint-28 contamination bug."""
        from darkfactory.pipeline.orchestrator import _clear_tier2_scenarios
        with TemporaryDirectory() as tmp:
            scen = Path(tmp)
            (scen / "00-tier1-x.test.ts").write_text("x", encoding="utf-8")
            (scen / "01-tier2.test.ts").write_text("x", encoding="utf-8")
            (scen / "_contracts").mkdir()
            (scen / "_contracts" / "h.ts").write_text("x", encoding="utf-8")
            (scen / "_df_setup" / "postgres").mkdir(parents=True)
            (scen / "_df_setup" / "postgres" / "_df_reset.setup.ts").write_text(
                "x", encoding="utf-8",
            )
            _clear_tier2_scenarios(scen, tier1_prefix="00-tier1-")
            self.assertTrue((scen / "00-tier1-x.test.ts").exists())   # tier1 kept
            self.assertTrue((scen / "_contracts" / "h.ts").exists())  # contracts kept
            self.assertTrue(                                          # reset infra kept
                (scen / "_df_setup" / "postgres" / "_df_reset.setup.ts").exists()
            )
            self.assertFalse((scen / "01-tier2.test.ts").exists())   # tier2 cleared

    def test_tier1_preserved_tier2_cleared_between_retries(self) -> None:
        """``_clear_tier2_scenarios`` runs before each TE retry. Tier 1
        files (``00-tier1-*``) and ``_contracts/`` survive; everything
        else is removed so the TE rewrites against a clean slate."""
        with TemporaryDirectory() as tmp:
            project_dir, scen_dir = _make_project(Path(tmp))
            (scen_dir / "01.test.ts").write_text(_VIOLATING_TS, encoding="utf-8")
            (scen_dir / "00-tier1-contract.test.ts").write_text(
                _TIER1_CLEAN, encoding="utf-8",
            )
            (scen_dir / "_contracts").mkdir()
            (scen_dir / "_contracts" / "helper.ts").write_text("export {};")

            spec_run = _spec_run()
            config = PipelineConfig(te_pre_acceptance_max_retries=1)

            tier1_seen_during_retry: list[bool] = []
            offender_seen_during_retry: list[bool] = []
            contracts_seen_during_retry: list[bool] = []

            def _fake_invoke(role, prompt, **kwargs):
                # Snapshot scenarios_dir state at the moment the retry
                # runs (post-clear, pre-rewrite).
                tier1_seen_during_retry.append(
                    (scen_dir / "00-tier1-contract.test.ts").exists()
                )
                offender_seen_during_retry.append(
                    (scen_dir / "01.test.ts").exists()
                )
                contracts_seen_during_retry.append(
                    (scen_dir / "_contracts" / "helper.ts").exists()
                )
                # TE writes a clean replacement.
                (scen_dir / "01.test.ts").write_text(
                    _CLEAN_TS, encoding="utf-8",
                )
                return AgentResult(role=role, exit_code=0)

            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
                side_effect=_fake_invoke,
            ):
                _step_te_pre_acceptance(
                    spec_run, config, {}, project_dir,
                    events_file=None, audit_path=None, dry_run=False,
                )

            self.assertEqual(len(tier1_seen_during_retry), 1)
            # Tier 1 + _contracts must be intact at retry time.
            self.assertTrue(tier1_seen_during_retry[0])
            self.assertTrue(contracts_seen_during_retry[0])
            # The offender (Tier 2 file) must have been cleared before retry.
            self.assertFalse(offender_seen_during_retry[0])


# ---------- feature-flag gating ------------------------------------------


class TestFeatureFlagGating(unittest.TestCase):
    """ROADMAP.md Phase 2 risk mitigation: phase is OFF by default. A
    code-level test of the wiring would exercise the full ``run_pipeline``
    boundary; this lighter pin verifies the default config value, which
    is the rollback lever ("flip one config to disable")."""

    def test_te_pre_acceptance_check_default_off(self) -> None:
        config = PipelineConfig()
        self.assertFalse(config.te_pre_acceptance_check)

    def test_default_max_retries_is_three(self) -> None:
        config = PipelineConfig()
        self.assertEqual(config.te_pre_acceptance_max_retries, 3)


if __name__ == "__main__":
    unittest.main()
