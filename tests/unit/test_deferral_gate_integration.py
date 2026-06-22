"""Integration test for the substrate-deferral validator GATE in
``_step_spec_validation``.

This drives the REAL invoke → extract_agent_text → parse → raise path with a
genuine ``AgentResult`` — the coverage the deterministic-only unit tests lacked,
which let a `extract_agent_text(_dres)` (should be `_dres.output`) crash ship
behind a green suite. Linus caught it; this locks it.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from darkfactory.docker.barriers import configure_barriers
from darkfactory.errors import DarkFactoryError
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import SpecRun
from darkfactory.pipeline.orchestrator import _step_spec_validation

# A spec that MEETS the deferral precondition: a verifiable=false substrate
# primitive + a Blocking (Non-Negotiable) AC. ``F`` marks the toml fence so the
# triple-backticks don't terminate this docstring-free constant awkwardly.
_F = "```"
SPEC = f"""\
# Spec
<!-- dark-factory-metadata
feature-id: F-1
spec_type: feature
-->

## 8. Acceptance Criteria

| ID | Criterion | Blocking |
|----|-----------|:-:|
| AC-1 | GIVEN rows for tenants A and B WHEN A queries THEN only tenant A's rows are returned | yes |

## 8a. Non-Negotiable Acceptance Criteria

- **AC-1** — tenant-isolation invariant.

## Substrate Primitives

{_F}toml
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
{_F}
"""


class DeferralGateIntegration(unittest.TestCase):
    def _project(self, tmp: str) -> Path:
        p = Path(tmp)
        (p / ".dark-factory" / "specs").mkdir(parents=True)
        (p / ".dark-factory" / "reviews").mkdir(parents=True)
        (p / ".dark-factory" / "specs" / "s.md").write_text(SPEC)
        # Passing status so validate_spec_result reaches the deferral gate.
        (p / ".dark-factory" / "reviews" / "s-validation.status").write_text(
            "verdict=PASS\nscore=100\ndivergence=LOW\nblockers=0\nconsistency=PASS\n"
        )
        return p

    def _run(self, agent_result: AgentResult) -> None:
        with TemporaryDirectory() as tmp:
            project = self._project(tmp)
            config = PipelineConfig(test_cmd="pytest")
            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
                return_value=agent_result,
            ):
                # reuse_validation=True skips the MAIN validator, so the only
                # invoke_agent call is the focused deferral check.
                _step_spec_validation(
                    SpecRun(spec_name="s"), config,
                    configure_barriers(config.source_dirs), project,
                    active_adapters=[], reuse_validation=True, dry_run=False,
                )

    def _agent(self, result_text: str, exit_code: int = 0) -> AgentResult:
        # Real AgentResult shaped like the CLI --output-format=json envelope.
        envelope = '{"result": ' + repr(result_text).replace("'", '"') + "}"
        return AgentResult(
            role=AgentRole.SPEC_VALIDATOR, exit_code=exit_code, output=envelope
        )

    def test_gate_fires_on_flagged_anchor(self):
        """A flagged Blocking anchor → DarkFactoryError (the gate). Also proves
        the extract_agent_text(_dres.output) path does not crash (C1 lock)."""
        with self.assertRaises(DarkFactoryError) as ctx:
            self._run(self._agent('["AC-1"]'))
        self.assertIn("deferral", str(ctx.exception).lower())

    def test_gate_persists_findings_report_for_skill(self):
        """When the gate fires it MUST persist the deferral-consistency report
        the /df-rescope-deferred skill consumes — else the validate→skill loop
        is broken (the gap the validator gate originally had vs the floor)."""
        import json
        from darkfactory.spec.deferral_check import deferral_report_path
        with TemporaryDirectory() as tmp:
            project = self._project(tmp)
            config = PipelineConfig(test_cmd="pytest")
            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
                return_value=self._agent('["AC-1"]'),
            ):
                with self.assertRaises(DarkFactoryError):
                    _step_spec_validation(
                        SpecRun(spec_name="s"), config,
                        configure_barriers(config.source_dirs), project,
                        active_adapters=[], reuse_validation=True, dry_run=False,
                    )
            report = deferral_report_path(
                project / ".dark-factory" / "reviews", "s"
            )
            self.assertTrue(report.exists(), "gate did not persist the report")
            data = json.loads(report.read_text())
            anchors = {f["anchor"] for f in data["findings"]}
            kinds = {f["kind"] for f in data["findings"]}
            self.assertEqual(anchors, {"AC-1"})
            # Each flagged anchor gets BOTH actions so the skill de-blocks AND
            # marks it @untestable.
            self.assertEqual(
                kinds, {"BLOCKING_OVER_DEFERRED", "LIVE_SCENARIO_OVER_DEFERRED"}
            )

    def test_gate_passes_on_empty_array(self):
        """A clean spec (LLM returns []) → no raise (returns through)."""
        self._run(self._agent("[]"))  # must not raise

    def test_gate_fails_open_on_agent_error(self):
        """A focused-check agent failure (exit!=0) → fail OPEN (no raise); the
        coverage-floor backstop catches it later."""
        self._run(self._agent("", exit_code=1))  # must not raise


if __name__ == "__main__":
    unittest.main()


# Spec with a DEFAULT-VERIFY primitive whose infra is deferred → elicitation gate.
_ELICIT_SPEC = f"""\
# Spec
<!-- dark-factory-metadata
feature-id: F-1
spec_type: feature
-->

## 3. Functional Requirements

- FR-1: the app.tenant_id GUC is set per transaction.

## Dependencies & Out of Scope

- Entity schemas (tenant tables) — defined by F-2 onward.

## Substrate Primitives

{_F}toml
[[tenant_isolation]]
tenant_table = "tenants"
{_F}
"""


class ElicitationGateIntegration(unittest.TestCase):
    def _project(self, tmp: str) -> Path:
        p = Path(tmp)
        (p / ".dark-factory" / "specs").mkdir(parents=True)
        (p / ".dark-factory" / "reviews").mkdir(parents=True)
        (p / ".dark-factory" / "specs" / "s.md").write_text(_ELICIT_SPEC)
        (p / ".dark-factory" / "reviews" / "s-validation.status").write_text(
            "verdict=PASS\nscore=100\ndivergence=LOW\nblockers=0\nconsistency=PASS\n"
        )
        return p

    def _agent(self, result_text: str, exit_code: int = 0) -> AgentResult:
        envelope = '{"result": ' + repr(result_text).replace("'", '"') + "}"
        return AgentResult(
            role=AgentRole.SPEC_VALIDATOR, exit_code=exit_code, output=envelope
        )

    def _run(self, agent_result: AgentResult, project: Path) -> None:
        config = PipelineConfig(test_cmd="pytest")
        with patch(
            "darkfactory.pipeline.orchestrator.invoke_agent",
            return_value=agent_result,
        ):
            _step_spec_validation(
                SpecRun(spec_name="s"), config,
                configure_barriers(config.source_dirs), project,
                active_adapters=[], reuse_validation=True, dry_run=False,
            )

    def test_elicitation_fires_on_default_verify_deferred_primitive(self):
        """A default-verify primitive whose infra is deferred → DarkFactoryError
        (elicitation gate), and persists a MISSING_VERIFIABLE_FALSE report. Also
        the C1-class lock: proves extract_agent_text(_eres.output) doesn't crash."""
        import json
        from darkfactory.spec.deferral_check import deferral_report_path
        with TemporaryDirectory() as tmp:
            project = self._project(tmp)
            with self.assertRaises(DarkFactoryError) as ctx:
                self._run(self._agent('["tenant_isolation"]'), project)
            self.assertIn("elicitation", str(ctx.exception).lower())
            report = deferral_report_path(project / ".dark-factory" / "reviews", "s")
            self.assertTrue(report.exists())
            data = json.loads(report.read_text())
            assert {f["kind"] for f in data["findings"]} == {"MISSING_VERIFIABLE_FALSE"}
            assert {f["primitive"] for f in data["findings"]} == {"tenant_isolation"}

    def test_elicitation_passes_when_llm_flags_nothing(self):
        with TemporaryDirectory() as tmp:
            self._run(self._agent("[]"), self._project(tmp))  # must not raise

    def test_elicitation_fails_open_on_agent_error(self):
        with TemporaryDirectory() as tmp:
            self._run(self._agent("", exit_code=1), self._project(tmp))  # no raise
