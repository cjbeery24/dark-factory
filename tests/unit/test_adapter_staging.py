"""Unit tests for ``darkfactory.pipeline.adapter_staging``.

The staging function is the seam that actually makes the wire-contract
helpers visible to the test-engineer. Without these tests, a regression
in the copy logic would silently ship scenarios with no helpers — the
test-engineer would hand-roll the wire predicates again (exactly the
2026-04-16 failure mode this whole track was built to prevent).

Coverage:
- A fully-staged adapter produces the expected filesystem layout.
- Re-staging is idempotent (second call overwrites, no stale files).
- Adapters returning ``None`` contribute nothing.
- Adapters returning non-existent paths emit a warning, do NOT raise.
- CONTRACTS_STAGED events carry the adapter name, src/dest, and file count.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from darkfactory.pipeline.adapter_staging import stage_adapter_contracts
from tests.helpers import TempDirTestCase


class _FakeAdapter:
    """Minimal adapter shape for staging tests — we only need
    ``name``, ``contracts_dir()``, and ``spec_triggers()``. Not a
    subclass of EcosystemAdapter because staging never touches the
    ABC's abstract methods."""

    def __init__(
        self,
        name: str,
        contracts: Path | None,
        *,
        triggers: bool = True,
    ) -> None:
        self.name = name
        self._contracts = contracts
        self._triggers = triggers

    def contracts_dir(self) -> Path | None:
        return self._contracts

    def spec_triggers(self, spec_text: str) -> bool:
        # Minimal stand-in for the real ABC predicate. Tests that don't
        # care about gating leave the default (True). Tests exercising
        # the gate construct an adapter with ``triggers=False``.
        return self._triggers


class TestStageAdapterContracts(TempDirTestCase):
    def _make_src(self, name: str) -> Path:
        src = self.tmp_dir / "src" / name
        src.mkdir(parents=True)
        (src / "index.ts").write_text("export const x = 1;")
        (src / "guc.ts").write_text("// helper")
        return src

    def test_stages_adapter_contracts_under_named_subdir(self) -> None:
        src = self._make_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapters = [_FakeAdapter("postgres", src)]

        staged = stage_adapter_contracts(scenarios, adapters)

        self.assertEqual(staged, ["postgres"])
        dest = scenarios / "_contracts" / "postgres"
        self.assertTrue((dest / "index.ts").is_file())
        self.assertTrue((dest / "guc.ts").is_file())

    def test_restaging_is_idempotent_and_removes_stale_files(self) -> None:
        """If the adapter's source tree has changed between runs, a
        stale file from the previous stage must not leak through."""
        src = self._make_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapters = [_FakeAdapter("postgres", src)]

        stage_adapter_contracts(scenarios, adapters)

        # Simulate an adapter that drops a file between runs.
        (src / "guc.ts").unlink()
        (src / "isolation.ts").write_text("// new helper")

        stage_adapter_contracts(scenarios, adapters)

        dest = scenarios / "_contracts" / "postgres"
        self.assertFalse(
            (dest / "guc.ts").exists(),
            "re-stage must remove files the adapter no longer ships",
        )
        self.assertTrue((dest / "isolation.ts").is_file())

    def test_adapter_without_contracts_dir_is_skipped(self) -> None:
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapters = [_FakeAdapter("typescript-node", None)]

        staged = stage_adapter_contracts(scenarios, adapters)

        self.assertEqual(staged, [])
        self.assertFalse((scenarios / "_contracts").exists())

    def test_nonexistent_contracts_dir_warns_does_not_raise(self) -> None:
        """A packaging bug (contracts_dir() returning a missing path)
        must NOT abort scenario-generation — we log a warning so the
        operator sees it, but the pipeline continues. Scenarios can
        still run against the spec; they just cannot use that adapter's
        helpers."""
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        missing = self.tmp_dir / "does-not-exist"
        adapters = [_FakeAdapter("postgres", missing)]

        with self.assertLogs(
            "darkfactory.pipeline.adapter_staging", level="WARNING",
        ) as ctx:
            staged = stage_adapter_contracts(scenarios, adapters)

        self.assertEqual(staged, [])
        self.assertTrue(
            any("is not a directory" in msg for msg in ctx.output),
            f"expected warning about non-directory; got {ctx.output}",
        )

    def test_emits_contracts_staged_event_per_adapter(self) -> None:
        src = self._make_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        events_file = self.tmp_dir / "events.jsonl"
        adapters = [_FakeAdapter("postgres", src)]

        stage_adapter_contracts(
            scenarios, adapters,
            spec="my-spec", run_id="runX",
            events_file=events_file,
        )

        lines = [
            json.loads(ln)
            for ln in events_file.read_text().splitlines()
            if ln.strip()
        ]
        staged_events = [
            ln for ln in lines if ln.get("event") == "CONTRACTS_STAGED"
        ]
        self.assertEqual(len(staged_events), 1)
        payload = staged_events[0].get("data", {})
        self.assertEqual(payload.get("adapter"), "postgres")
        self.assertEqual(payload.get("file_count"), 2)
        self.assertEqual(staged_events[0].get("spec"), "my-spec")
        self.assertEqual(staged_events[0].get("run_id"), "runX")

    def test_mixed_adapters_one_staged_one_skipped(self) -> None:
        """With multiple active adapters, only those with contracts get
        staged and only those get CONTRACTS_STAGED events."""
        src = self._make_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapters = [
            _FakeAdapter("typescript-node", None),
            _FakeAdapter("postgres", src),
        ]

        staged = stage_adapter_contracts(scenarios, adapters)

        self.assertEqual(staged, ["postgres"])
        self.assertFalse((scenarios / "_contracts" / "typescript-node").exists())
        self.assertTrue((scenarios / "_contracts" / "postgres" / "index.ts").is_file())

    def test_spec_text_gate_skips_adapter_when_triggers_false(self) -> None:
        """When ``spec_text`` is provided AND an adapter's
        ``spec_triggers(spec_text)`` returns False, the adapter's
        contracts are NOT staged — even if the adapter is in the
        ``adapters`` list (project-active) and has a real
        ``contracts_dir``. Closes the substrate-clean-spec coupling
        case (Phase 5 Stage 1 / 2026-04-25 echo-endpoint smoke)."""
        src = self._make_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter("postgres", src, triggers=False)

        with self.assertLogs(
            "darkfactory.pipeline.adapter_staging", level="INFO",
        ) as ctx:
            staged = stage_adapter_contracts(
                scenarios, [adapter],
                spec_text="this echo spec touches no database",
            )

        self.assertEqual(staged, [])
        self.assertFalse((scenarios / "_contracts" / "postgres").exists())
        self.assertTrue(
            any("spec_triggers did not fire" in msg for msg in ctx.output),
            f"expected info log about skipped adapter; got {ctx.output}",
        )

        # Sanity: same call WITHOUT spec_text still stages (legacy
        # caller compatibility — the gate is opt-in per call).
        staged2 = stage_adapter_contracts(scenarios, [adapter])
        self.assertEqual(staged2, ["postgres"])
        self.assertTrue((scenarios / "_contracts" / "postgres" / "index.ts").is_file())


class TestStagingInvokedFromOrchestrator(unittest.TestCase):
    def test_staging_call_appears_inside_step_scenario_generation(self) -> None:
        """The call must live inside ``_step_scenario_generation`` (not
        in the outer pipeline flow) so any ``regen`` rmtree has already
        run before staging — otherwise we would stage into a directory
        about to be wiped. The only cheap way to pin this invariant
        without spinning a real pipeline is to grep the orchestrator
        source for the call-site location.
        """
        from darkfactory.pipeline import orchestrator
        src = Path(orchestrator.__file__).read_text()

        marker = "def _step_scenario_generation("
        idx = src.find(marker)
        self.assertNotEqual(idx, -1, "_step_scenario_generation not found")
        body = src[idx:]

        self.assertIn("stage_adapter_contracts(", body, (
            "stage_adapter_contracts must be called inside "
            "_step_scenario_generation so it runs after any regen rmtree"
        ))
