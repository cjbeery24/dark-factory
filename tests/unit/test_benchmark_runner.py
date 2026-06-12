"""Tests for darkfactory/benchmark/runner.py (subprocess mocked; no Docker)."""

from __future__ import annotations

import json
import subprocess
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from tests.helpers import TempDirTestCase
from darkfactory.benchmark.battery import Battery, BatteryTarget
from darkfactory.benchmark.runner import run_battery


class TestRunBattery(TempDirTestCase):
    def _battery(self, specs=("spec-a",), runs=1, setup_cmd="") -> Battery:
        target = BatteryTarget(
            path=self.tmp_dir, stack="ts",
            reset_cmd="git reset --hard HEAD",
            specs=list(specs), setup_cmd=setup_cmd,
        )
        return Battery(runs=runs, targets=[target])

    def _fake(self, *, run_exit=0, write_registry=False, write_events=True,
              reset_exit=0, setup_exit=0, calls=None):
        df = self.tmp_dir / ".dark-factory"

        def fake_run(cmd, **kwargs):
            if calls is not None:
                calls.append(cmd)
            if isinstance(cmd, list) and cmd[:2] == ["dark-factory", "run"]:
                if write_events:
                    ev = df / "events.jsonl"
                    ev.parent.mkdir(parents=True, exist_ok=True)
                    term = "SPEC_STABLE_FAILURE" if run_exit == 5 else "SPEC_SHIPPED"
                    ev.write_text(
                        json.dumps({"event": "COST_SUMMARY", "run_id": "rX",
                                    "data": {"estimated_usd": 42.0}}) + "\n"
                        + json.dumps({"event": term, "run_id": "rX",
                                      "data": {"iterations": 2}}) + "\n"
                    )
                    # Real per-agent telemetry (the cost source — sonnet rates:
                    # 1000*3e-6 + 2000*15e-6 = 0.033).
                    tel = df / "reports" / "run-telemetry.jsonl"
                    tel.parent.mkdir(parents=True, exist_ok=True)
                    tel.write_text(json.dumps({
                        "run_id": "rX", "role": "spec-validator",
                        "model_id": "claude-sonnet-4-20250514",
                        "input_tokens": 1000, "output_tokens": 2000,
                        "cache_read_tokens": 0, "cache_creation_tokens": 0,
                        "wall_clock_ms": 1000,
                    }) + "\n")
                if write_registry:
                    reg = df / "reports" / f"{cmd[2]}-defect-registry.md"
                    reg.parent.mkdir(parents=True, exist_ok=True)
                    reg.write_text("# gaps\n")
                return CompletedProcess(cmd, returncode=run_exit, stdout="", stderr="")
            if isinstance(cmd, str) and cmd.startswith("git reset"):
                return CompletedProcess(cmd, returncode=reset_exit, stdout="",
                                        stderr="reset boom" if reset_exit else "")
            if isinstance(cmd, str) and cmd.startswith("df-setup"):
                return CompletedProcess(cmd, returncode=setup_exit, stdout="", stderr="")
            # docker cleanup shell commands
            return CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        return fake_run

    def _rows(self, out):
        return [json.loads(x) for x in out.read_text().strip().splitlines()]

    def test_stable_failure_row(self) -> None:
        out = self.tmp_dir / "sc.jsonl"
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(run_exit=5)):
            rc = run_battery(self._battery(), out_path=out, progress=lambda m: None)
        self.assertEqual(rc, 0)
        rows = self._rows(out)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "stable_failure")
        self.assertEqual(rows[0]["exit_code"], 5)
        self.assertEqual(rows[0]["iterations"], 2)
        # cost_usd is now REAL-telemetry-derived (cache-aware), not the events
        # COST_SUMMARY cap value (42.0). Sonnet: 1000*3e-6 + 2000*15e-6 = 0.033.
        self.assertAlmostEqual(rows[0]["cost_usd"], 0.033, places=6)
        self.assertEqual(rows[0]["output_tokens"], 2000)
        self.assertEqual(rows[0]["agent_invocations"], 1)
        self.assertEqual(rows[0]["model_id"], "claude-sonnet-4-20250514")

    def test_clean_ship_row(self) -> None:
        out = self.tmp_dir / "sc.jsonl"
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(run_exit=0, write_registry=False)):
            run_battery(self._battery(), out_path=out, progress=lambda m: None)
        self.assertEqual(self._rows(out)[0]["outcome"], "clean_ship")

    def test_ship_with_gaps_row(self) -> None:
        out = self.tmp_dir / "sc.jsonl"
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(run_exit=0, write_registry=True)):
            run_battery(self._battery(), out_path=out, progress=lambda m: None)
        self.assertEqual(self._rows(out)[0]["outcome"], "ship_with_gaps")

    def test_fresh_flag_passed(self) -> None:
        out = self.tmp_dir / "sc.jsonl"
        calls = []
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(run_exit=0, calls=calls)):
            run_battery(self._battery(), out_path=out, progress=lambda m: None)
        df_calls = [c for c in calls if isinstance(c, list) and c[:2] == ["dark-factory", "run"]]
        self.assertEqual(len(df_calls), 1)
        self.assertIn("--fresh", df_calls[0])
        self.assertEqual(df_calls[0][2], "spec-a")

    def test_events_wiped_before_run(self) -> None:
        # Pre-seed a stale events.jsonl; the run writes nothing. If the runner
        # wipes correctly, metrics come back None (not the stale $999 cost).
        ev = self.tmp_dir / ".dark-factory" / "events.jsonl"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text(json.dumps({"event": "COST_SUMMARY", "run_id": "stale",
                                  "data": {"estimated_usd": 999.0}}) + "\n")
        out = self.tmp_dir / "sc.jsonl"
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(run_exit=1, write_events=False)):
            run_battery(self._battery(), out_path=out, progress=lambda m: None)
        row = self._rows(out)[0]
        self.assertIsNone(row["cost_usd"])  # stale 999 was wiped
        self.assertIsNone(row["run_id"])

    def test_reset_failure_records_error_and_rc1(self) -> None:
        out = self.tmp_dir / "sc.jsonl"
        calls = []
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(reset_exit=1, calls=calls)):
            rc = run_battery(self._battery(), out_path=out, progress=lambda m: None)
        self.assertEqual(rc, 1)
        row = self._rows(out)[0]
        self.assertEqual(row["outcome"], "error")
        self.assertEqual(row["exit_code"], -1)
        # The pipeline must NOT have been invoked after a failed reset.
        self.assertFalse(any(isinstance(c, list) and c[:2] == ["dark-factory", "run"] for c in calls))

    def test_runs_multiple_emits_multiple_rows(self) -> None:
        out = self.tmp_dir / "sc.jsonl"
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(run_exit=0)):
            run_battery(self._battery(runs=3), out_path=out, progress=lambda m: None)
        rows = self._rows(out)
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["run"] for r in rows}, {1, 2, 3})

    def test_setup_cmd_runs_once_per_target(self) -> None:
        out = self.tmp_dir / "sc.jsonl"
        calls = []
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(run_exit=0, calls=calls)):
            run_battery(self._battery(specs=("a", "b"), runs=2, setup_cmd="df-setup x"),
                        out_path=out, progress=lambda m: None)
        setup_calls = [c for c in calls if c == "df-setup x"]
        self.assertEqual(len(setup_calls), 1)  # once per target, not per run
        self.assertEqual(len(self._rows(out)), 4)  # 2 specs x 2 runs

    def test_stale_registry_wiped_so_clean_ship(self) -> None:
        # The CLEAN_SHIP vs SHIP_WITH_GAPS discriminator is the load-bearing
        # invariant: a defect registry left by a PRIOR run must be wiped before
        # this run, so a fresh clean ship (exit 0, writes no registry) is
        # classified clean_ship, not ship_with_gaps.
        stale = self.tmp_dir / ".dark-factory" / "reports" / "spec-a-defect-registry.md"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("# stale gaps from a prior run\n")
        out = self.tmp_dir / "sc.jsonl"
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(run_exit=0, write_registry=False)):
            run_battery(self._battery(), out_path=out, progress=lambda m: None)
        self.assertEqual(self._rows(out)[0]["outcome"], "clean_ship")

    def test_stale_shipped_jsonl_wiped(self) -> None:
        # A prior benchmark ship must not leak via plan/shipped.jsonl.
        shipped = self.tmp_dir / ".dark-factory" / "plan" / "shipped.jsonl"
        shipped.parent.mkdir(parents=True, exist_ok=True)
        shipped.write_text('{"spec_id":"prior"}\n')
        out = self.tmp_dir / "sc.jsonl"
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(run_exit=0)):
            run_battery(self._battery(), out_path=out, progress=lambda m: None)
        self.assertFalse(shipped.exists(), "plan/shipped.jsonl must be wiped before each run")

    def test_timeout_records_error_and_continues(self) -> None:
        out = self.tmp_dir / "sc.jsonl"

        def fake(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["dark-factory", "run"]:
                raise subprocess.TimeoutExpired(cmd, 1)
            return CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        battery = self._battery()
        battery.run_timeout_s = 1
        with patch("darkfactory.benchmark.runner.subprocess.run", side_effect=fake):
            rc = run_battery(battery, out_path=out, progress=lambda m: None)
        self.assertEqual(rc, 0)  # a timeout is run data, not a runner-level failure
        row = self._rows(out)[0]
        self.assertEqual(row["outcome"], "error")
        self.assertEqual(row["exit_code"], -1)

    def test_dry_run_executes_nothing(self) -> None:
        out = self.tmp_dir / "sc.jsonl"
        calls = []
        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._fake(calls=calls)):
            rc = run_battery(self._battery(), out_path=out, dry_run=True, progress=lambda m: None)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertFalse(out.exists())


class TestFrameworkResetEnvContract(TempDirTestCase):
    """_framework_reset must reset the env-contract to the source pin: drop
    untracked architecture deltas (cross-spec pollution) but keep the tracked
    baseline. Real git; only the docker-cleanup shell calls are stubbed."""

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(self.tmp_dir),
            capture_output=True, check=True,
        )

    def _delta(self, name: str, body: str = "schema_version = 2\n"):
        p = (self.tmp_dir / ".dark-factory" / "architecture"
             / "environment.deltas" / name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        return p

    def _docker_stub(self):
        """Patch side_effect that runs git for real but stubs docker cleanup."""
        real_run = subprocess.run

        def side(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[:1] == ["git"]:
                return real_run(cmd, **kwargs)
            return CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        return side

    def _init_repo(self) -> None:
        self._git("init", "-q")
        self._git("config", "user.email", "t@t.t")
        self._git("config", "user.name", "t")

    def test_drops_untracked_deltas_keeps_tracked_baseline(self) -> None:
        from darkfactory.benchmark.runner import _framework_reset

        self._init_repo()
        # Tracked baseline env-contract (committed at the "source pin").
        base = self._delta("0001-bootstrap-from-detection.toml")
        env_toml = self.tmp_dir / ".dark-factory" / "architecture" / "environment.toml"
        env_toml.write_text("[commands]\nbuild = \"x\"\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "baseline")
        # Post-baseline UNTRACKED pollution from a prior spec (the drizzle leak),
        # plus an untracked stale manifest.
        bad_delta = self._delta(
            "0005-database-foundation.toml",
            "schema_version = 2\n# setup = drizzle-kit migrate\n",
        )
        bad_manifest = (self.tmp_dir / ".dark-factory" / "architecture"
                        / "database-foundation-manifest.yaml")
        bad_manifest.write_text("stale: true\n")

        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._docker_stub()):
            err = _framework_reset(self.tmp_dir)

        self.assertIsNone(err)
        self.assertFalse(bad_delta.exists(), "untracked sibling delta must be removed")
        self.assertFalse(bad_manifest.exists(), "untracked stale manifest must be removed")
        self.assertTrue(base.exists(), "tracked baseline delta must survive")
        self.assertTrue(env_toml.exists(), "tracked environment.toml must survive")

    def test_gitignored_dark_factory_still_cleaned(self) -> None:
        # The load-bearing case: the target gitignores .dark-factory/, so the
        # polluting deltas are ignored-untracked. Plain `git clean -fd` would
        # SKIP them and silently defeat the reset; `-fdx` must still remove them
        # while the force-added tracked baseline survives.
        from darkfactory.benchmark.runner import _framework_reset

        self._init_repo()
        (self.tmp_dir / ".gitignore").write_text(".dark-factory/\n")
        base = self._delta("0001-bootstrap-from-detection.toml")
        self._git("add", "-f", ".dark-factory/architecture/environment.deltas/"
                  "0001-bootstrap-from-detection.toml", ".gitignore")
        self._git("commit", "-qm", "baseline")
        # Ignored-untracked pollution.
        bad_delta = self._delta("0005-database-foundation.toml",
                                "# setup = drizzle-kit migrate\n")

        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._docker_stub()):
            err = _framework_reset(self.tmp_dir)

        self.assertIsNone(err)
        self.assertFalse(bad_delta.exists(),
                         "ignored-untracked delta must be removed (requires -x)")
        self.assertTrue(base.exists(), "force-added tracked baseline must survive")

    def test_git_clean_failure_is_surfaced_not_swallowed(self) -> None:
        # A defeated hermetic reset must be a loud runner error, not silent.
        from darkfactory.benchmark.runner import _framework_reset

        (self.tmp_dir / ".git").mkdir()  # satisfy the git-target guard
        self._delta("0005-database-foundation.toml")

        def side(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "clean"]:
                return CompletedProcess(cmd, returncode=128, stdout="",
                                        stderr="fatal: boom")
            return CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        with patch("darkfactory.benchmark.runner.subprocess.run", side_effect=side):
            err = _framework_reset(self.tmp_dir)

        self.assertIsNotNone(err)
        self.assertIn("128", err)

    def test_non_git_target_skips_clean_and_returns_none(self) -> None:
        # No .git → the reset_cmd owns hermeticity; skip silently (no error, no
        # raise). The non-git delta is left in place (not our job to clean).
        from darkfactory.benchmark.runner import _framework_reset

        delta = self._delta("0001-bootstrap-from-detection.toml")  # no git repo

        with patch("darkfactory.benchmark.runner.subprocess.run",
                   side_effect=self._docker_stub()):
            err = _framework_reset(self.tmp_dir)  # must not raise

        self.assertIsNone(err)
        self.assertTrue(delta.exists())


if __name__ == "__main__":
    unittest.main()
