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
    ``name``, ``contracts_dir()``, ``project_contracts_dir()``, and
    ``spec_triggers()``. Not a subclass of EcosystemAdapter because
    staging never touches the ABC's abstract methods.

    ``project_contracts`` (Phase 3 B9d) follows the same shape as
    ``contracts``: an absolute path or ``None``. In production the ABC
    default reads ``project_dir/.dark-factory/contracts/<name>/``; for
    tests the path is supplied directly so the test author has full
    control over where project helpers live.
    """

    def __init__(
        self,
        name: str,
        contracts: Path | None,
        *,
        triggers: bool = True,
        project_contracts: Path | None = None,
    ) -> None:
        self.name = name
        self._contracts = contracts
        self._project_contracts = project_contracts
        self._triggers = triggers

    def contracts_dir(self) -> Path | None:
        return self._contracts

    def project_contracts_dir(self, project_dir: Path) -> Path | None:
        # Tests pass an absolute path via the constructor and ignore the
        # ``project_dir`` arg. Production adapters use the ABC default,
        # which keys on ``project_dir``.
        return self._project_contracts

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


class TestProjectContractsStaging(TempDirTestCase):
    """ROADMAP.md Phase 3 B9d: project-side staging.

    Pins the path-namespacing invariant and the four cases the spec
    calls out (framework-only, project-only, both present, neither).
    Plus the collision-impossibility check that rejects a framework
    adapter shipping a child named ``project``.
    """

    def _make_framework_src(self, name: str) -> Path:
        src = self.tmp_dir / "framework" / name
        src.mkdir(parents=True)
        (src / "index.ts").write_text(
            "export const universal = 1;", encoding="utf-8",
        )
        (src / "guc.ts").write_text(
            "// universal wire-contract", encoding="utf-8",
        )
        return src

    def _make_project_src(self, name: str) -> Path:
        src = self.tmp_dir / "project-contracts" / name
        src.mkdir(parents=True)
        (src / "tenant.ts").write_text(
            "// project convention: app.tenant_id", encoding="utf-8",
        )
        return src

    def test_project_helpers_land_under_project_subdir(self) -> None:
        """Both framework and project helpers present. Framework lands
        flat under ``_contracts/<adapter>/``; project lands under
        ``_contracts/<adapter>/project/``. Path-namespaced — collisions
        impossible because the two trees occupy disjoint paths."""
        fw = self._make_framework_src("postgres")
        proj = self._make_project_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter("postgres", fw, project_contracts=proj)

        staged = stage_adapter_contracts(
            scenarios, [adapter],
            project_dir=self.tmp_dir,  # arg required to enable project staging
        )

        self.assertEqual(staged, ["postgres"])
        # Framework helpers at the root (flat).
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "index.ts").is_file()
        )
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "guc.ts").is_file()
        )
        # Project helpers under the ``project/`` subdir.
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "project" / "tenant.ts").is_file()
        )
        # No file collision possible — different paths.

    def test_project_only_no_framework(self) -> None:
        """Adapter has no framework helpers (``contracts_dir()`` is
        None) but the project ships its own. Project staging still
        happens — the framework's absence does not block project-side
        adoption."""
        proj = self._make_project_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter("postgres", None, project_contracts=proj)

        staged = stage_adapter_contracts(
            scenarios, [adapter],
            project_dir=self.tmp_dir,
        )

        # Framework return value is empty (None contracts → not staged).
        # Project still copied. The split list signals "this adapter
        # had no framework helpers" which is observable via the empty
        # return AND the absence of framework files at the dest root.
        self.assertEqual(staged, [])
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "project" / "tenant.ts").is_file()
        )
        # No framework files at the parent — the parent only exists
        # because copytree created it for the project subdir.
        flat_files = [
            p for p in (scenarios / "_contracts" / "postgres").iterdir()
            if p.is_file()
        ]
        self.assertEqual(flat_files, [])

    def test_framework_only_no_project(self) -> None:
        """Status quo: adapter ships framework helpers but the project
        has no project-side helpers. ``project_dir`` may be supplied
        but ``project_contracts_dir()`` returns None — silently
        skipped. The framework path still lands; the result matches the
        pre-B9d behavior."""
        fw = self._make_framework_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter("postgres", fw, project_contracts=None)

        staged = stage_adapter_contracts(
            scenarios, [adapter],
            project_dir=self.tmp_dir,
        )

        self.assertEqual(staged, ["postgres"])
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "index.ts").is_file()
        )
        # No project subdir (nothing to stage).
        self.assertFalse(
            (scenarios / "_contracts" / "postgres" / "project").exists()
        )

    def test_both_empty_is_a_no_op(self) -> None:
        """Adapter ships nothing (universal-three-helper retirement
        case from Phase 5 B9c-extension). Staging is a no-op for this
        adapter. The empty return signals it cleanly."""
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter("postgres", None, project_contracts=None)

        staged = stage_adapter_contracts(
            scenarios, [adapter],
            project_dir=self.tmp_dir,
        )

        self.assertEqual(staged, [])
        self.assertFalse((scenarios / "_contracts").exists())

    def test_legacy_caller_without_project_dir_skips_project_staging(self) -> None:
        """Back-compat: callers that don't pass ``project_dir`` get the
        pre-B9d behavior. Framework staging still runs; project staging
        is silently skipped even if the adapter would have produced
        project helpers. This protects existing test harnesses and any
        third-party consumer of ``stage_adapter_contracts``."""
        fw = self._make_framework_src("postgres")
        proj = self._make_project_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter("postgres", fw, project_contracts=proj)

        # No project_dir argument → legacy path.
        staged = stage_adapter_contracts(scenarios, [adapter])

        self.assertEqual(staged, ["postgres"])
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "index.ts").is_file()
        )
        self.assertFalse(
            (scenarios / "_contracts" / "postgres" / "project").exists(),
            "legacy callers must not see project staging without opt-in",
        )

    def test_path_namespace_collision_rejected(self) -> None:
        """A framework adapter that ships a child named ``project``
        would collide with the namespacing invariant. Staging refuses
        loudly rather than silently overwriting either side. This is
        an authoring-time assertion: an adapter author who writes
        ``contracts/<adapter>/project/foo.ts`` discovers the error on
        the first run, not on the first project that defines its own
        helpers. ``DarkFactoryError`` (not ``ValueError``) — this is
        a configuration/authoring error, consistent with the rest of
        the codebase's error taxonomy.
        """
        from darkfactory.errors import DarkFactoryError

        fw = self._make_framework_src("postgres")
        # Plant a colliding child.
        (fw / "project").mkdir()
        (fw / "project" / "uh_oh.ts").write_text(
            "// framework misuse", encoding="utf-8",
        )
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter("postgres", fw)

        with self.assertRaises(DarkFactoryError) as ctx:
            stage_adapter_contracts(
                scenarios, [adapter],
                project_dir=self.tmp_dir,
            )
        self.assertIn("project", str(ctx.exception))
        self.assertIn("postgres", str(ctx.exception))
        # Even without project_dir, the framework adapter still has the
        # bad child — staging must still reject it (the invariant is
        # path-shape, not call-site).
        with self.assertRaises(DarkFactoryError):
            stage_adapter_contracts(scenarios, [adapter])

    def test_project_staging_idempotent_clears_stale(self) -> None:
        """Re-running the project stage with a different source file
        set must not leak stale files. Symmetric to the framework-side
        idempotency invariant."""
        fw = self._make_framework_src("postgres")
        proj = self._make_project_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter("postgres", fw, project_contracts=proj)

        stage_adapter_contracts(
            scenarios, [adapter], project_dir=self.tmp_dir,
        )

        (proj / "tenant.ts").unlink()
        (proj / "billing.ts").write_text(
            "// new project helper", encoding="utf-8",
        )

        stage_adapter_contracts(
            scenarios, [adapter], project_dir=self.tmp_dir,
        )

        self.assertFalse(
            (scenarios / "_contracts" / "postgres" / "project" / "tenant.ts").exists(),
            "stale project file must be removed on re-stage",
        )
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "project" / "billing.ts").is_file()
        )

    def test_spec_triggers_gate_suppresses_both_paths(self) -> None:
        """When ``spec_triggers(spec_text)`` returns False, neither
        framework nor project helpers stage. Project helpers being
        semantically tied to the framework adapter, they share the
        same gating decision (a substrate-clean spec doesn't need
        either)."""
        fw = self._make_framework_src("postgres")
        proj = self._make_project_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter(
            "postgres", fw, project_contracts=proj, triggers=False,
        )

        staged = stage_adapter_contracts(
            scenarios, [adapter],
            spec_text="echo spec, no database",
            project_dir=self.tmp_dir,
        )

        self.assertEqual(staged, [])
        self.assertFalse(
            (scenarios / "_contracts" / "postgres").exists(),
            "spec_triggers gate must suppress both framework AND project staging",
        )

    def test_project_staging_emits_event(self) -> None:
        """Project staging emits a ``CONTRACTS_STAGED`` event with
        ``staging_kind="project"`` (B9d adds the kind field to the
        event payload). Distinguishable downstream without requiring
        path-suffix parsing on ``dest``.
        """
        proj = self._make_project_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        events_file = self.tmp_dir / "events.jsonl"
        adapter = _FakeAdapter("postgres", None, project_contracts=proj)

        stage_adapter_contracts(
            scenarios, [adapter],
            spec="my-spec", run_id="r1",
            events_file=events_file,
            project_dir=self.tmp_dir,
        )

        lines = [
            json.loads(ln) for ln in events_file.read_text().splitlines()
            if ln.strip()
        ]
        staged_events = [
            ln for ln in lines if ln.get("event") == "CONTRACTS_STAGED"
        ]
        self.assertEqual(len(staged_events), 1)
        payload = staged_events[0].get("data", {})
        self.assertEqual(payload.get("staging_kind"), "project")
        self.assertEqual(payload.get("adapter"), "postgres")
        self.assertEqual(payload.get("file_count"), 1)
        # Path suffix corroborates without being load-bearing.
        self.assertTrue(payload.get("dest", "").endswith("/project"))

    def test_both_paths_emit_two_events_with_kind_field(self) -> None:
        """Single call with framework + project both present emits
        two CONTRACTS_STAGED events: one with ``staging_kind="framework"``
        (dest at the adapter root), one with ``staging_kind="project"``
        (dest under ``/project``). Pinning event order isn't part of
        the contract — pin the set."""
        fw = self._make_framework_src("postgres")
        proj = self._make_project_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        events_file = self.tmp_dir / "events.jsonl"
        adapter = _FakeAdapter("postgres", fw, project_contracts=proj)

        stage_adapter_contracts(
            scenarios, [adapter],
            spec="my-spec", run_id="r1",
            events_file=events_file,
            project_dir=self.tmp_dir,
        )

        lines = [
            json.loads(ln) for ln in events_file.read_text().splitlines()
            if ln.strip()
        ]
        kinds = sorted(
            ln["data"]["staging_kind"]
            for ln in lines
            if ln.get("event") == "CONTRACTS_STAGED"
        )
        self.assertEqual(kinds, ["framework", "project"])

    def test_framework_missing_project_present_still_stages_project(self) -> None:
        """Behavior change introduced by B9d: when ``contracts_dir()``
        returns a path that doesn't exist, the framework path is
        skipped with a warning AND project staging still proceeds.
        Pre-B9d there was no project staging path; post-B9d the
        warning-and-continue behavior must not block project helpers
        from landing — a project that ships its own contracts for an
        adapter whose framework helpers are missing on this host is
        a legitimate (B9a-deprecation-window) use case."""
        missing_fw = self.tmp_dir / "does-not-exist"
        proj = self._make_project_src("postgres")
        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter(
            "postgres", missing_fw, project_contracts=proj,
        )

        with self.assertLogs(
            "darkfactory.pipeline.adapter_staging", level="WARNING",
        ) as ctx:
            staged = stage_adapter_contracts(
                scenarios, [adapter],
                project_dir=self.tmp_dir,
            )

        # Framework didn't stage (path missing) → not in the return
        # list, but the warning fired.
        self.assertEqual(staged, [])
        self.assertTrue(
            any("not a directory" in msg for msg in ctx.output),
            f"expected framework path warning; got {ctx.output}",
        )
        # Project still staged.
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "project" / "tenant.ts").is_file()
        )

    def test_multi_adapter_mixed_project_helpers(self) -> None:
        """Two adapters in one call: one ships project helpers, one
        doesn't. Both reach project staging logic; only the one with
        project_contracts_dir() returning a real dir lands a
        ``project/`` subdir. Pins the mixed-iteration regression."""
        fw_a = self._make_framework_src("postgres")
        fw_b = self.tmp_dir / "framework" / "typescript-node"
        fw_b.mkdir(parents=True)
        (fw_b / "index.ts").write_text("export {};")

        proj_a = self._make_project_src("postgres")
        # No project helpers for typescript-node.

        scenarios = self.tmp_dir / "scenarios" / "my-spec"
        adapters = [
            _FakeAdapter("postgres", fw_a, project_contracts=proj_a),
            _FakeAdapter("typescript-node", fw_b, project_contracts=None),
        ]

        staged = stage_adapter_contracts(
            scenarios, adapters,
            project_dir=self.tmp_dir,
        )

        self.assertEqual(sorted(staged), ["postgres", "typescript-node"])
        # Postgres has framework + project subtree.
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "guc.ts").is_file()
        )
        self.assertTrue(
            (scenarios / "_contracts" / "postgres" / "project" / "tenant.ts").is_file()
        )
        # Typescript-node has framework only — no project subdir.
        self.assertTrue(
            (scenarios / "_contracts" / "typescript-node" / "index.ts").is_file()
        )
        self.assertFalse(
            (scenarios / "_contracts" / "typescript-node" / "project").exists()
        )


def _make_minimal_concrete_adapter(adapter_name: str):
    """Build a minimal concrete subclass of ``EcosystemAdapter`` with
    every abstract method stubbed. Tests of the ABC's *default*
    methods use this so the inheritance path actually executes — a
    SimpleNamespace would silently pass even if the ABC default were
    broken in a way that depended on inheritance machinery.
    """
    from darkfactory.adapters.base import EcosystemAdapter

    class _MinimalAdapter(EcosystemAdapter):
        # ``name`` is exposed as an abstract property on the ABC; a
        # plain class attribute satisfies the descriptor protocol that
        # ``__abstractmethods__`` checks at instantiation time.
        name = adapter_name

        def detect(self) -> bool:
            return False

        def detection_signals(self) -> tuple[str, ...]:
            return ()

        def architect_addendum(self) -> str:
            return ""

        def scenario_architect_addendum(self) -> str:
            return ""

        def spec_validator_addendum(self) -> str:
            return ""

        def consistency_rules(self):
            return []

        def precheck(self, spec):
            return []

        def proposal_rehearsal_typecheck(self, *args, **kwargs):
            return None

        def quality_gate_dispatch(self, *args, **kwargs):
            return None

        def verify_substrate(self, *args, **kwargs):
            return None

    return _MinimalAdapter()


class TestProjectContractsDirABCDefault(TempDirTestCase):
    """Pin the ABC default behavior of
    ``EcosystemAdapter.project_contracts_dir(project_dir)``: returns
    ``<project>/.dark-factory/contracts/<adapter_name>/`` if the
    directory exists, else None.

    Uses a concrete subclass (not ``SimpleNamespace``) so the ABC
    inheritance path actually executes — guards against the ABC adding
    an ``__init__`` precondition or descriptor that a duck-typed test
    would silently bypass.
    """

    def test_returns_path_when_dir_exists(self) -> None:
        adapter = _make_minimal_concrete_adapter("postgres")
        contracts = self.tmp_dir / ".dark-factory" / "contracts" / "postgres"
        contracts.mkdir(parents=True)
        result = adapter.project_contracts_dir(self.tmp_dir)
        self.assertEqual(result, contracts)

    def test_returns_none_when_dir_missing(self) -> None:
        adapter = _make_minimal_concrete_adapter("postgres")
        # No directory created.
        result = adapter.project_contracts_dir(self.tmp_dir)
        self.assertIsNone(result)

    def test_returns_none_when_path_is_a_file(self) -> None:
        adapter = _make_minimal_concrete_adapter("postgres")
        contracts_parent = self.tmp_dir / ".dark-factory" / "contracts"
        contracts_parent.mkdir(parents=True)
        # Plant a regular file at the expected directory location.
        (contracts_parent / "postgres").write_text("not a dir")
        result = adapter.project_contracts_dir(self.tmp_dir)
        self.assertIsNone(result)

    def test_path_keys_on_adapter_name(self) -> None:
        # Same project_dir, different adapter names → different paths.
        for name in ("postgres", "typescript-node", "python-fastapi"):
            adapter = _make_minimal_concrete_adapter(name)
            (self.tmp_dir / ".dark-factory" / "contracts" / name).mkdir(
                parents=True, exist_ok=True,
            )
            result = adapter.project_contracts_dir(self.tmp_dir)
            self.assertEqual(
                result,
                self.tmp_dir / ".dark-factory" / "contracts" / name,
            )


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
