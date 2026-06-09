"""Tests for darkfactory/benchmark/battery.py (pure parse/validate)."""

from __future__ import annotations

import unittest

from tests.helpers import TempDirTestCase
from darkfactory.errors import ConfigError
from darkfactory.benchmark.battery import parse_battery, load_battery


class TestParseBattery(TempDirTestCase):
    def _target_toml(self, **over) -> str:
        path = over.get("path", str(self.tmp_dir))
        stack = over.get("stack", "typescript-postgres")
        reset = over.get("reset_cmd", "git reset --hard HEAD")
        specs = over.get("specs", '["spec-a"]')
        runs = over.get("runs", 2)
        extra = over.get("extra", "")
        return (
            f"runs = {runs}\n"
            f"[[target]]\n"
            f'path = "{path}"\n'
            f'stack = "{stack}"\n'
            f'reset_cmd = "{reset}"\n'
            f"specs = {specs}\n"
            f"{extra}"
        )

    def test_valid_battery(self) -> None:
        b = parse_battery(self._target_toml())
        self.assertEqual(b.runs, 2)
        self.assertEqual(len(b.targets), 1)
        t = b.targets[0]
        self.assertEqual(t.stack, "typescript-postgres")
        self.assertEqual(t.specs, ["spec-a"])
        self.assertEqual(t.setup_cmd, "")

    def test_runs_defaults_to_one(self) -> None:
        toml = self._target_toml().replace("runs = 2\n", "")
        self.assertEqual(parse_battery(toml).runs, 1)

    def test_runs_must_be_positive(self) -> None:
        with self.assertRaises(ConfigError):
            parse_battery(self._target_toml(runs=0))

    def test_no_targets_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_battery("runs = 1\n")

    def test_missing_path_raises(self) -> None:
        toml = 'runs=1\n[[target]]\nstack="ts"\nreset_cmd="x"\nspecs=["s"]\n'
        with self.assertRaises(ConfigError):
            parse_battery(toml)

    def test_nonexistent_path_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_battery(self._target_toml(path="/no/such/path/xyz"))

    def test_empty_stack_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_battery(self._target_toml(stack=""))

    def test_missing_reset_cmd_raises(self) -> None:
        # Reset is mandatory — a benchmark that doesn't reset measures drift.
        with self.assertRaises(ConfigError):
            parse_battery(self._target_toml(reset_cmd=""))

    def test_empty_specs_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_battery(self._target_toml(specs="[]"))

    def test_invalid_toml_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_battery("this is = = not toml [[[")

    def test_setup_cmd_optional(self) -> None:
        b = parse_battery(self._target_toml(extra='setup_cmd = "echo hi"\n'))
        self.assertEqual(b.targets[0].setup_cmd, "echo hi")

    def test_enterprise_warning_detected(self) -> None:
        # A target whose config enables enterprise gets flagged (exit 1/2 ambiguous).
        cfg = self.tmp_dir / ".dark-factory" / "config.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("enterprise_enabled = true\n")
        b = parse_battery(self._target_toml())
        self.assertTrue(b.targets[0].enterprise_warning)

    def test_enterprise_warning_absent_by_default(self) -> None:
        b = parse_battery(self._target_toml())
        self.assertFalse(b.targets[0].enterprise_warning)

    def test_run_timeout_optional_defaults_none(self) -> None:
        self.assertIsNone(parse_battery(self._target_toml()).run_timeout_s)

    def test_run_timeout_parsed(self) -> None:
        b = parse_battery("run_timeout_s = 5400\n" + self._target_toml())
        self.assertEqual(b.run_timeout_s, 5400)

    def test_run_timeout_invalid_raises(self) -> None:
        with self.assertRaises(ConfigError):
            parse_battery("run_timeout_s = 0\n" + self._target_toml())

    def test_run_timeout_bool_rejected(self) -> None:
        # bool subclasses int — `true` must not silently become a 1s timeout.
        with self.assertRaises(ConfigError):
            parse_battery("run_timeout_s = true\n" + self._target_toml())

    def test_runs_bool_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            parse_battery(self._target_toml(runs="true"))

    def test_load_battery_missing_file(self) -> None:
        with self.assertRaises(ConfigError):
            load_battery(self.tmp_dir / "nope.toml")


if __name__ == "__main__":
    unittest.main()
