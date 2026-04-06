"""Regression tests for `dark-factory validate` behavior around stale pipeline state.

Plan: docs/plans/validate-fresh-state.md

`_cmd_validate` must never block on a pre-existing iteration state file whose
`spec_hash` differs from the current spec content. Validate is read-only,
step-1-only, and has no `--fresh` flag of its own, so a hard block from the
shared resume path is unactionable. The fix: `_cmd_validate` passes
`fresh=True` to `run_pipeline`, bypassing `_try_auto_resume` entirely.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.helpers import TempDirTestCase, make_project


SPEC_BODY = "# Spec\n\nSome content.\n"


def _make_spec(project: Path, name: str, body: str = SPEC_BODY) -> Path:
    spec_file = project / ".dark-factory" / "specs" / f"{name}.md"
    spec_file.write_text(body, encoding="utf-8")
    return spec_file


def _write_state(project: Path, name: str, spec_hash: str) -> Path:
    state_dir = project / ".dark-factory" / "state" / name
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "spec_hash": spec_hash,
                "completed_step": 5,
                "iteration": 2,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    return state_file


def _call_validate(project: Path, spec: str, run_pipeline_mock: MagicMock) -> None:
    """Invoke `_cmd_validate` with enough mocks to avoid real I/O beyond the
    spec and state files under `project`."""
    from darkfactory import cli as cli_mod

    # Minimal precheck result: passed, no ambiguities.
    precheck_result = MagicMock()
    precheck_result.passed = True
    precheck_result.ambiguities = []
    precheck_result.summary = ""

    fake_config = MagicMock()

    args = argparse.Namespace(spec=spec, dry_run=False, ci=True)

    with patch.object(cli_mod, "_load_config", return_value=fake_config), \
         patch("darkfactory.config.profiles.resolve_profile", return_value=fake_config), \
         patch("darkfactory.spec.precheck.run_precheck", return_value=precheck_result), \
         patch("darkfactory.pipeline.orchestrator.run_pipeline", run_pipeline_mock), \
         patch("darkfactory.scenarios.detection.validate_spec_result", return_value=(True, "ok")):
        try:
            cli_mod._cmd_validate(args, project)
        except SystemExit as exc:
            # _cmd_validate always calls sys.exit(exit_code) at the end. Exit
            # code 0 is normal completion; anything else is a real failure
            # the test should not silently swallow.
            assert exc.code == 0, f"_cmd_validate exited non-zero: {exc.code}"


class TestValidateFreshState(TempDirTestCase):
    """Validate must proceed regardless of stale pipeline state."""

    def setUp(self) -> None:
        super().setUp()
        self.project = make_project(self.tmp_dir)
        self.spec_name = "myspec"
        self.spec_file = _make_spec(self.project, self.spec_name)
        self.current_hash = hashlib.sha256(self.spec_file.read_bytes()).hexdigest()

    def _precheck_runs_path(self) -> Path:
        return (
            self.project / ".dark-factory" / "state" / self.spec_name / "precheck_runs.json"
        )

    # --------- Case A: stale state with mismatched hash ---------

    def test_case_a_stale_state_mismatched_hash_proceeds(self) -> None:
        """Prior pipeline state with a different spec_hash must NOT block validate."""
        stale_hash = "deadbeef" * 8  # deliberately != current_hash
        state_file = _write_state(self.project, self.spec_name, stale_hash)
        original_state_bytes = state_file.read_bytes()

        run_pipeline_mock = MagicMock(return_value=0)

        # Must not raise DarkFactoryError.
        _call_validate(self.project, self.spec_name, run_pipeline_mock)

        # run_pipeline was called, and with fresh=True.
        self.assertEqual(run_pipeline_mock.call_count, 1)
        kwargs = run_pipeline_mock.call_args.kwargs
        self.assertTrue(
            kwargs.get("fresh") is True,
            f"_cmd_validate must pass fresh=True to run_pipeline; got kwargs={kwargs}",
        )
        self.assertEqual(kwargs.get("stop_after_step"), 1)

        # The iteration state file was not touched by validate itself (run_pipeline
        # is mocked, so nothing writes to it).
        self.assertEqual(state_file.read_bytes(), original_state_bytes)

        # precheck_runs.json was written with count=1 and the new hash.
        runs_path = self._precheck_runs_path()
        self.assertTrue(runs_path.exists())
        data = json.loads(runs_path.read_text(encoding="utf-8"))
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["spec_hash"], self.current_hash)

    # --------- Case B: no prior state ---------

    def test_case_b_no_prior_state_proceeds(self) -> None:
        run_pipeline_mock = MagicMock(return_value=0)
        _call_validate(self.project, self.spec_name, run_pipeline_mock)

        self.assertEqual(run_pipeline_mock.call_count, 1)
        self.assertTrue(run_pipeline_mock.call_args.kwargs.get("fresh") is True)

        runs_path = self._precheck_runs_path()
        self.assertTrue(runs_path.exists())
        data = json.loads(runs_path.read_text(encoding="utf-8"))
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["spec_hash"], self.current_hash)

    # --------- Case C: matching hash, state preserved ---------

    def test_case_c_matching_hash_does_not_clobber_state(self) -> None:
        state_file = _write_state(self.project, self.spec_name, self.current_hash)
        original_state_bytes = state_file.read_bytes()

        run_pipeline_mock = MagicMock(return_value=0)
        _call_validate(self.project, self.spec_name, run_pipeline_mock)

        self.assertEqual(run_pipeline_mock.call_count, 1)
        self.assertTrue(run_pipeline_mock.call_args.kwargs.get("fresh") is True)

        # Iteration state file untouched by _cmd_validate (run_pipeline is mocked).
        self.assertEqual(state_file.read_bytes(), original_state_bytes)


if __name__ == "__main__":
    unittest.main()
