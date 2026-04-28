"""Tests for darkfactory.pipeline.substrate_phase — verifier-container dispatcher."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.helpers import TempDirTestCase, make_project

from darkfactory.pipeline.substrate_phase import (
    SubstrateResult,
    _build_verifier_docker_args,
    _merge,
    _parse_verifier_output,
    run_substrate_verification,
)
from darkfactory.utils.subprocess_runner import RunResult


class _FakeAdapter:
    """Stand-in for a real EcosystemAdapter — only ``name`` is read."""
    def __init__(self, name: str) -> None:
        self.name = name


class TestBuildVerifierDockerArgs(unittest.TestCase):
    """Verifier args should keep the container minimal: no project mount."""

    def test_no_project_mount(self) -> None:
        args = _build_verifier_docker_args(
            spec_path=Path("/host/spec.md"),
            manifest_path=Path("/host/manifest.toml"),
            sidecar_env={},
            network=None,
            run_id="r1",
        )
        # The whole point of the peer-container architecture: project is
        # NOT mounted. If a future refactor adds /project here, the
        # security argument for option 1 vs option (c) collapses.
        joined = " ".join(args)
        self.assertNotIn(":/project", joined)
        self.assertNotIn("/project:", joined)

    def test_pythonpath_set(self) -> None:
        args = _build_verifier_docker_args(
            spec_path=Path("/host/spec.md"),
            manifest_path=Path("/host/m.toml"),
            sidecar_env={},
            network=None,
            run_id="r1",
        )
        self.assertIn("PYTHONPATH=/opt/dark-factory", args)

    def test_network_attached_when_provided(self) -> None:
        args = _build_verifier_docker_args(
            spec_path=Path("/host/spec.md"),
            manifest_path=Path("/host/m.toml"),
            sidecar_env={},
            network="df-r1-execution",
            run_id="r1",
        )
        idx = args.index("--network")
        self.assertEqual(args[idx + 1], "df-r1-execution")

    def test_network_omitted_when_none(self) -> None:
        args = _build_verifier_docker_args(
            spec_path=Path("/host/spec.md"),
            manifest_path=Path("/host/m.toml"),
            sidecar_env={},
            network=None,
            run_id="r1",
        )
        self.assertNotIn("--network", args)

    def test_sidecar_env_passed(self) -> None:
        args = _build_verifier_docker_args(
            spec_path=Path("/host/spec.md"),
            manifest_path=Path("/host/m.toml"),
            sidecar_env={"DATABASE_URL": "postgres://pg:5432/db"},
            network=None,
            run_id="r1",
        )
        self.assertIn("DATABASE_URL=postgres://pg:5432/db", args)


class TestBuildVerifierDockerArgsWithFiles(TempDirTestCase):
    """Mount behaviour depends on file existence on the host."""

    def test_spec_always_mounted(self) -> None:
        spec = self.tmp_dir / "spec.md"
        spec.write_text("# spec")
        args = _build_verifier_docker_args(
            spec_path=spec,
            manifest_path=self.tmp_dir / "missing.toml",
            sidecar_env={},
            network=None,
            run_id="r1",
        )
        self.assertIn(f"{spec}:/spec.md:ro", args)

    def test_manifest_mounted_when_exists(self) -> None:
        spec = self.tmp_dir / "spec.md"
        spec.write_text("# spec")
        manifest = self.tmp_dir / "manifest.toml"
        manifest.write_text("spec_id = 'x'")
        args = _build_verifier_docker_args(
            spec_path=spec,
            manifest_path=manifest,
            sidecar_env={},
            network=None,
            run_id="r1",
        )
        self.assertIn(f"{manifest}:/manifest.toml:ro", args)

    def test_manifest_not_mounted_when_missing(self) -> None:
        """Missing manifest must surface as path-doesn't-exist inside the container.

        verify_substrate is responsible for distinguishing
        substrate-manifest-missing (rules fired) from None
        (legitimate skip); the dispatcher must NOT synthesise a
        fake manifest mount.
        """
        spec = self.tmp_dir / "spec.md"
        spec.write_text("# spec")
        args = _build_verifier_docker_args(
            spec_path=spec,
            manifest_path=self.tmp_dir / "missing.toml",
            sidecar_env={},
            network=None,
            run_id="r1",
        )
        self.assertNotIn("/manifest.toml:ro", " ".join(args))


class TestParseVerifierOutput(unittest.TestCase):
    def test_pass(self) -> None:
        rr = RunResult(exit_code=0, stdout='{"status": "pass", "findings": []}\n', stderr="")
        result = _parse_verifier_output("postgres", rr)
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.findings, [])

    def test_skip(self) -> None:
        rr = RunResult(exit_code=0, stdout='{"status": "skip", "findings": []}\n', stderr="")
        result = _parse_verifier_output("postgres", rr)
        self.assertEqual(result.status, "skip")

    def test_fail_with_findings(self) -> None:
        payload = {
            "status": "fail",
            "findings": [{"severity": "error", "code": "x", "message": "y", "path": ""}],
        }
        rr = RunResult(exit_code=0, stdout=json.dumps(payload) + "\n", stderr="")
        result = _parse_verifier_output("postgres", rr)
        self.assertEqual(result.status, "fail")
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0]["code"], "x")

    def test_crash_synthesises_finding(self) -> None:
        rr = RunResult(exit_code=2, stdout="", stderr="boom")
        result = _parse_verifier_output("postgres", rr)
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.findings[0]["code"], "substrate-verifier-crashed")

    def test_timeout_distinct_from_crash(self) -> None:
        """Exit 124 (coreutils timeout) gets its own code, not the generic crash one."""
        rr = RunResult(exit_code=124, stdout="", stderr="", timed_out=True)
        result = _parse_verifier_output("postgres", rr)
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.findings[0]["code"], "substrate-verifier-timeout")

    def test_unparseable_output_synthesises_finding(self) -> None:
        rr = RunResult(exit_code=0, stdout="not json at all\n", stderr="")
        result = _parse_verifier_output("postgres", rr)
        self.assertEqual(result.status, "fail")
        self.assertEqual(
            result.findings[0]["code"], "substrate-verifier-output-unparseable",
        )

    def test_pre_envelope_stdout_noise_is_unparseable(self) -> None:
        """Entry script redirects stdout→stderr before any import.

        Anything appearing on real stdout before the envelope is a
        violation of that discipline (a future bug worth surfacing).
        Last-line parsing would silently mask it; we instead require
        the whole stdout blob to be the envelope.
        """
        stdout = "WARN: something\n" + json.dumps({"status": "pass", "findings": []}) + "\n"
        rr = RunResult(exit_code=0, stdout=stdout, stderr="")
        result = _parse_verifier_output("postgres", rr)
        self.assertEqual(result.status, "fail")
        self.assertEqual(
            result.findings[0]["code"], "substrate-verifier-output-unparseable",
        )


class TestMerge(unittest.TestCase):
    def test_skip_plus_skip_is_skip(self) -> None:
        target = SubstrateResult()
        _merge(target, SubstrateResult(status="skip"))
        self.assertEqual(target.status, "skip")

    def test_pass_overrides_skip(self) -> None:
        target = SubstrateResult(status="skip")
        _merge(target, SubstrateResult(status="pass"))
        self.assertEqual(target.status, "pass")

    def test_fail_overrides_pass(self) -> None:
        target = SubstrateResult(status="pass")
        _merge(target, SubstrateResult(status="fail", findings=[{"x": 1}]))
        self.assertEqual(target.status, "fail")
        self.assertEqual(target.findings, [{"x": 1}])

    def test_pass_does_not_override_fail(self) -> None:
        target = SubstrateResult(status="fail", findings=[{"x": 1}])
        _merge(target, SubstrateResult(status="pass"))
        self.assertEqual(target.status, "fail")

    def test_findings_unioned(self) -> None:
        target = SubstrateResult(status="fail", findings=[{"a": 1}])
        _merge(target, SubstrateResult(status="fail", findings=[{"b": 2}]))
        self.assertEqual(target.findings, [{"a": 1}, {"b": 2}])


class TestRunSubstrateVerification(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        make_project(self.tmp_dir)
        spec = self.tmp_dir / ".dark-factory" / "specs" / "x.md"
        spec.write_text("# spec")
        self.config = MagicMock()
        self.config.egress_filtering = False
        self.config.services = []

    def test_dry_run_writes_skip_envelope_no_container(self) -> None:
        with patch(
            "darkfactory.pipeline.substrate_phase.execute_container",
        ) as mock_exec:
            result = run_substrate_verification(
                adapters=[_FakeAdapter("postgres")],
                spec_name="x",
                config=self.config,
                run_id="r1",
                project_dir=self.tmp_dir,
                sidecar_env={},
                network=None,
                dry_run=True,
            )
        mock_exec.assert_not_called()
        self.assertEqual(result.status, "skip")
        out = self.tmp_dir / ".dark-factory" / "reports" / "x-substrate-findings.json"
        self.assertTrue(out.is_file())
        data = json.loads(out.read_text())
        self.assertEqual(data["status"], "skip")

    def test_no_adapters_writes_skip_envelope(self) -> None:
        with patch(
            "darkfactory.pipeline.substrate_phase.execute_container",
        ) as mock_exec:
            result = run_substrate_verification(
                adapters=[], spec_name="x", config=self.config, run_id="r1",
                project_dir=self.tmp_dir, sidecar_env={}, network=None,
            )
        mock_exec.assert_not_called()
        self.assertEqual(result.status, "skip")

    def test_missing_spec_skips(self) -> None:
        # Delete the spec we created in setUp.
        (self.tmp_dir / ".dark-factory" / "specs" / "x.md").unlink()
        with patch(
            "darkfactory.pipeline.substrate_phase.execute_container",
        ) as mock_exec:
            result = run_substrate_verification(
                adapters=[_FakeAdapter("postgres")],
                spec_name="x", config=self.config, run_id="r1",
                project_dir=self.tmp_dir, sidecar_env={}, network=None,
            )
        mock_exec.assert_not_called()
        self.assertEqual(result.status, "skip")

    def test_pass_envelope_persisted(self) -> None:
        ok = RunResult(
            exit_code=0,
            stdout='{"status": "pass", "findings": []}\n',
            stderr="",
        )
        with patch(
            "darkfactory.pipeline.substrate_phase.execute_container",
            return_value=ok,
        ):
            result = run_substrate_verification(
                adapters=[_FakeAdapter("postgres")],
                spec_name="x", config=self.config, run_id="r1",
                project_dir=self.tmp_dir, sidecar_env={}, network=None,
            )
        self.assertEqual(result.status, "pass")
        out = self.tmp_dir / ".dark-factory" / "reports" / "x-substrate-findings.json"
        data = json.loads(out.read_text())
        self.assertEqual(data["status"], "pass")

    def test_fail_envelope_persisted(self) -> None:
        payload = {
            "status": "fail",
            "findings": [{"severity": "error", "code": "x", "message": "y", "path": ""}],
        }
        rr = RunResult(exit_code=0, stdout=json.dumps(payload) + "\n", stderr="")
        with patch(
            "darkfactory.pipeline.substrate_phase.execute_container",
            return_value=rr,
        ):
            result = run_substrate_verification(
                adapters=[_FakeAdapter("postgres")],
                spec_name="x", config=self.config, run_id="r1",
                project_dir=self.tmp_dir, sidecar_env={}, network=None,
            )
        self.assertEqual(result.status, "fail")
        out = self.tmp_dir / ".dark-factory" / "reports" / "x-substrate-findings.json"
        data = json.loads(out.read_text())
        self.assertEqual(data["status"], "fail")
        self.assertEqual(len(data["findings"]), 1)

    def test_dispatcher_crash_writes_fail_envelope(self) -> None:
        """A crash in the in-quality_gate dispatcher must surface as FAIL.

        Logging-and-swallowing would let the renderer's absent-file
        branch render FAIL anyway, but a pre-written envelope with a
        specific code is strictly better — the reviewer sees *what*
        broke instead of just "no findings file".

        ``active_adapters`` is now threaded from the caller (per the
        adapter-list-threaded-from-orchestrator rule); the prior
        ``load_active_adapters`` re-call inside ``_run_substrate_phase``
        was removed when the propagation gap was fixed.
        """
        from darkfactory.pipeline.quality_gate import _run_substrate_phase

        with patch(
            "darkfactory.pipeline.substrate_phase.run_substrate_verification",
            side_effect=RuntimeError("kaboom"),
        ):
            _run_substrate_phase(
                spec_name="x", config=self.config, run_id="r1",
                project_dir=self.tmp_dir, sidecar_env={}, dry_run=False,
                active_adapters=[_FakeAdapter("postgres")],
            )
        out = self.tmp_dir / ".dark-factory" / "reports" / "x-substrate-findings.json"
        self.assertTrue(out.is_file())
        data = json.loads(out.read_text())
        self.assertEqual(data["status"], "fail")
        self.assertEqual(data["findings"][0]["code"], "substrate-dispatcher-crashed")
        self.assertIn("kaboom", data["findings"][0]["message"])

    def test_standalone_fallback_writes_fail_envelope_on_adapter_resolution_error(
        self,
    ) -> None:
        """Linus follow-up #2 contract: when ``run_quality_gate`` is
        called without an explicit ``active_adapters`` (the standalone
        ``dark-factory gate`` debug path), it loads + activates locally.
        If that resolution fails, the fallback MUST write a fail
        envelope (code ``substrate-fallback-adapter-resolution-failed``)
        rather than silently degrading to SKIP.

        Mirrors the dispatcher-crash pattern (``test_dispatcher_crash_
        writes_fail_envelope``): an explicit fail envelope tells the
        reviewer/oracle *what* broke instead of leaving them with a
        bare table-row "SKIP" that hides the breakage.

        Patching ``load_project_hints`` to raise simulates a
        misconfigured project (corrupt hints, missing file in a fresh
        clone, etc.). The orchestrator path is unaffected because it
        always passes an explicit ``active_adapters`` list.
        """
        from darkfactory.pipeline.quality_gate import run_quality_gate

        # We can't run the full ``run_quality_gate`` (it spawns Docker
        # containers, builds gate commands, etc.) — patch the gate
        # launch and the hints loader so the fallback runs in isolation.
        # ``dry_run=True`` is the lightest path through; combined with
        # the patches below, the fallback's exception handler is the
        # only substantive code that executes.
        with patch(
            "darkfactory.architecture.project_hints.load_project_hints",
            side_effect=RuntimeError("hints corrupt"),
        ), patch(
            "darkfactory.pipeline.quality_gate._launch_gate_container",
        ) as mock_launch:
            # Make _launch_gate_container return a stub so dry_run path
            # doesn't matter; we just need run_quality_gate to reach
            # the fallback before bailing.
            mock_launch.return_value = type(
                "_R", (), {
                    "exit_code": 0, "stdout": "", "stderr": "",
                },
            )()
            # Narrow except (per Linus follow-up): downstream of the
            # fallback, ``run_quality_gate`` calls ``check_language_image``
            # which raises ``ConfigError`` because we haven't stubbed
            # the Docker image check (the test's MagicMock config
            # doesn't satisfy that). Catching ``ConfigError`` specifically
            # lets the fallback's OWN error paths (an OSError from
            # envelope-write, anything else inside
            # ``_write_substrate_dispatcher_fail``) propagate as real
            # test failures instead of being silently swallowed under
            # the misleading "fallback didn't write envelope" assertion.
            from darkfactory.errors import ConfigError
            try:
                run_quality_gate(
                    spec_name="x",
                    config=self.config,
                    run_id="r1",
                    project_dir=self.tmp_dir,
                    dry_run=True,
                    # active_adapters omitted → fallback runs.
                )
            except ConfigError:
                # Expected: downstream Docker-image check can't find
                # the language image because the config is mocked.
                # The envelope was already written by the fallback
                # handler before we got here.
                pass

        out = (
            self.tmp_dir / ".dark-factory" / "reports"
            / "x-substrate-findings.json"
        )
        # The envelope should have been written by the fallback's
        # exception handler. If this assertion fires, the fallback
        # silently degraded to SKIP — reintroducing the failure mode
        # Linus flagged for fix.
        self.assertTrue(
            out.is_file(),
            "fallback did not write substrate-findings envelope on "
            "adapter resolution failure",
        )
        data = json.loads(out.read_text())
        self.assertEqual(data["status"], "fail")
        self.assertEqual(
            data["findings"][0]["code"],
            "substrate-fallback-adapter-resolution-failed",
        )
        self.assertIn("hints corrupt", data["findings"][0]["message"])

    def test_threaded_adapters_dispatch_to_run_substrate_verification(self) -> None:
        """Pins the propagation contract: ``_run_substrate_phase`` MUST
        forward its ``active_adapters`` argument to
        ``run_substrate_verification`` verbatim — no re-load, no
        filtering. This is the regression test for the 2026-04-28 gap
        where the in-substrate-phase ``load_active_adapters`` re-call
        discarded the orchestrator's spec-triggered activation set,
        silently SKIPping postgres verification on every spec whose
        hints didn't already declare postgres.

        Setup: passes a sentinel adapter list with a name not in any
        registry. Asserts ``run_substrate_verification`` is invoked with
        exactly that list — proving no re-derivation happened.
        """
        from darkfactory.pipeline.quality_gate import _run_substrate_phase

        sentinel_adapter = _FakeAdapter("sentinel-not-in-registry")
        with patch(
            "darkfactory.pipeline.substrate_phase.run_substrate_verification",
        ) as mock_run:
            _run_substrate_phase(
                spec_name="x", config=self.config, run_id="r1",
                project_dir=self.tmp_dir, sidecar_env={}, dry_run=False,
                active_adapters=[sentinel_adapter],
            )
        mock_run.assert_called_once()
        self.assertEqual(
            mock_run.call_args.kwargs["adapters"], [sentinel_adapter]
        )

    def test_two_adapters_aggregated(self) -> None:
        responses = iter([
            RunResult(
                exit_code=0,
                stdout='{"status": "pass", "findings": []}\n',
                stderr="",
            ),
            RunResult(
                exit_code=0,
                stdout='{"status": "fail", "findings": [{"severity":"error","code":"c","message":"m","path":""}]}\n',
                stderr="",
            ),
        ])
        with patch(
            "darkfactory.pipeline.substrate_phase.execute_container",
            side_effect=lambda *a, **kw: next(responses),
        ):
            result = run_substrate_verification(
                adapters=[_FakeAdapter("postgres"), _FakeAdapter("typescript-node")],
                spec_name="x", config=self.config, run_id="r1",
                project_dir=self.tmp_dir, sidecar_env={}, network=None,
            )
        # fail wins over pass
        self.assertEqual(result.status, "fail")
        self.assertEqual(len(result.findings), 1)


if __name__ == "__main__":
    unittest.main()
