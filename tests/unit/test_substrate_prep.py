"""Unit tests for ``darkfactory.pipeline.substrate_prep``.

Phase 5 Stage 3 (2026-04-25). The orchestrator-side wrapper around the
substrate-owner agent: parses agent JSON output, validates emitted
commands via ``validate_pre_setup_entry()``, drives bounded retries.

These tests cover the deterministic pieces (parse + validate) without
mocking the full agent invocation. Live-agent integration testing
happens via the experiment trial harness, not here.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from darkfactory.pipeline.substrate_prep import (
    ConsideredPrecondition,
    SubstratePrepResult,
    _parse_agent_output,
    _validate_commands,
)
from tests.helpers import TempDirTestCase


class TestParseAgentOutput(TempDirTestCase):
    def _write(self, doc: dict) -> Path:
        p = self.tmp_dir / "substrate-prep.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def test_well_formed_emit_payload_parses(self) -> None:
        path = self._write({
            "schema_version": 1,
            "commands": [{
                "step": "seed_journal",
                "command": "mkdir -p x && [ -f y ] || echo {} > y",
                "rationale": "from spec §4a",
                "fires_when": "y absent",
            }],
            "considered": [{
                "precondition": "Migration journal required",
                "decision": "emit",
                "rationale": "lockfile shows drizzle-kit",
            }],
            "no_op": False,
            "evidence": {"tools_planned": ["drizzle-kit"]},
        })
        commands, considered, no_op, errs = _parse_agent_output(path)
        self.assertEqual(errs, [])
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["step"], "seed_journal")
        self.assertEqual(len(considered), 1)
        self.assertEqual(considered[0].decision, "emit")
        self.assertFalse(no_op)

    def test_no_op_payload_parses(self) -> None:
        path = self._write({
            "schema_version": 1,
            "commands": [],
            "considered": [{
                "precondition": "Migration journal required",
                "decision": "skip",
                "rationale": "no migration tool in lockfile",
            }],
            "no_op": True,
            "evidence": {"tools_planned": []},
        })
        commands, considered, no_op, errs = _parse_agent_output(path)
        self.assertEqual(errs, [])
        self.assertTrue(no_op)
        self.assertEqual(commands, [])
        self.assertEqual(len(considered), 1)
        self.assertEqual(considered[0].decision, "skip")

    def test_missing_file_returns_error(self) -> None:
        commands, considered, no_op, errs = _parse_agent_output(
            self.tmp_dir / "does-not-exist.json"
        )
        self.assertEqual(commands, [])
        self.assertEqual(considered, [])
        self.assertFalse(no_op)
        self.assertEqual(len(errs), 1)
        self.assertIn("did not write output file", errs[0])

    def test_invalid_json_returns_error(self) -> None:
        p = self.tmp_dir / "broken.json"
        p.write_text("{not json", encoding="utf-8")
        commands, considered, no_op, errs = _parse_agent_output(p)
        self.assertEqual(commands, [])
        self.assertEqual(len(errs), 1)
        self.assertIn("failed to read/parse", errs[0])

    def test_command_missing_command_field_skipped(self) -> None:
        path = self._write({
            "commands": [
                {"step": "x", "command": "valid one"},
                {"step": "y"},  # missing command
            ],
            "considered": [],
            "no_op": False,
        })
        commands, _considered, _no_op, errs = _parse_agent_output(path)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["command"], "valid one")
        self.assertTrue(any("missing or empty `command`" in e for e in errs))

    def test_considered_invalid_decision_records_error(self) -> None:
        path = self._write({
            "commands": [],
            "considered": [{
                "precondition": "x",
                "decision": "maybe",  # invalid — only emit/skip
                "rationale": "y",
            }],
            "no_op": True,
        })
        _commands, considered, _no_op, errs = _parse_agent_output(path)
        self.assertEqual(considered, [])
        self.assertTrue(any("must be 'emit' or 'skip'" in e for e in errs))

    def test_considered_missing_precondition_skipped(self) -> None:
        path = self._write({
            "commands": [],
            "considered": [{"decision": "skip", "rationale": "x"}],
            "no_op": True,
        })
        _commands, considered, _no_op, errs = _parse_agent_output(path)
        self.assertEqual(considered, [])
        self.assertTrue(any("missing `precondition`" in e for e in errs))

    def test_top_level_array_rejected(self) -> None:
        p = self.tmp_dir / "array.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        _commands, _considered, _no_op, errs = _parse_agent_output(p)
        self.assertTrue(any("must be a JSON object" in e for e in errs))

    def test_unknown_top_level_keys_ignored(self) -> None:
        path = self._write({
            "schema_version": 1,
            "commands": [],
            "considered": [],
            "no_op": True,
            "evidence": {},
            "future_field": "ignore me",
        })
        _commands, _considered, no_op, errs = _parse_agent_output(path)
        self.assertEqual(errs, [])
        self.assertTrue(no_op)


class TestValidateCommands(unittest.TestCase):
    def test_clean_commands_pass(self) -> None:
        cmds = [
            {"step": "a", "command": "mkdir -p x", "rationale": "", "fires_when": ""},
            {"step": "b", "command": "[ -f y ] || echo z > y", "rationale": "", "fires_when": ""},
        ]
        passing, rejections = _validate_commands(cmds, attempt=1)
        self.assertEqual(len(passing), 2)
        self.assertEqual(rejections, [])

    def test_outer_bash_c_wrap_rejected(self) -> None:
        cmds = [{
            "step": "a",
            "command": "bash -c 'echo hi'",
            "rationale": "", "fires_when": "",
        }]
        passing, rejections = _validate_commands(cmds, attempt=1)
        self.assertEqual(passing, [])
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].step, "a")
        self.assertIn("bash", rejections[0].rule.lower())

    def test_inline_semicolon_rejected(self) -> None:
        cmds = [{
            "step": "a",
            "command": "echo a; echo b",
            "rationale": "", "fires_when": "",
        }]
        _passing, rejections = _validate_commands(cmds, attempt=1)
        self.assertEqual(len(rejections), 1)
        self.assertIn(";", rejections[0].rule)

    def test_command_substitution_rejected(self) -> None:
        cmds = [{
            "step": "a",
            "command": "echo $(date)",
            "rationale": "", "fires_when": "",
        }]
        _passing, rejections = _validate_commands(cmds, attempt=1)
        self.assertEqual(len(rejections), 1)

    def test_mixed_pass_and_fail(self) -> None:
        cmds = [
            {"step": "good", "command": "mkdir -p x", "rationale": "", "fires_when": ""},
            {"step": "bad", "command": "echo $(date)", "rationale": "", "fires_when": ""},
            {"step": "good2", "command": "[ -f y ] || touch y", "rationale": "", "fires_when": ""},
        ]
        passing, rejections = _validate_commands(cmds, attempt=2)
        self.assertEqual(len(passing), 2)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0].step, "bad")
        self.assertEqual(rejections[0].attempt, 2)


class TestSubstratePrepResultDataclass(unittest.TestCase):
    def test_default_construction(self) -> None:
        r = SubstratePrepResult()
        self.assertEqual(r.commands, [])
        self.assertEqual(r.rejections, [])
        self.assertEqual(r.considered, [])
        self.assertFalse(r.no_op)
        self.assertFalse(r.retries_exhausted)
        self.assertEqual(r.attempts, 0)


if __name__ == "__main__":
    unittest.main()
