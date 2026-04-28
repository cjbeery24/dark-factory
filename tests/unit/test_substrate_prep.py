"""Unit tests for ``darkfactory.pipeline.substrate_prep``.

Phase 5 Stage 3 (2026-04-25). The orchestrator-side wrapper around the
substrate-owner agent: parses agent JSON output, validates emitted
commands via ``validate_pre_setup_entry()``, drives bounded retries.

Schema-v2 (2026-04-28, Gap 1): the parser enforces a load-bearing
non-claim — ``claim`` field is required and MUST equal ``"none"``.
Schema-level encoding (vs. prompt-only directive) so a future model
refresh cannot quietly start asserting substrate verification. v1
emissions (which carried ``no_op: true`` as a verification claim)
are rejected outright by schema_version mismatch.

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
    ParsedAgentOutput,
    SubstratePrepResult,
    _parse_agent_output,
    _validate_commands,
)
from tests.helpers import TempDirTestCase


def _v2_payload(**overrides) -> dict:
    """Schema-v2 baseline. Override fields per test as needed.

    Note: ``stage_exit`` is NOT in the agent's emission schema. It's
    an orchestrator-only field on SubstratePrepResult — the agent
    has no channel to describe its own attempt status (that would
    reintroduce the verification-claim ambiguity that ``claim:
    "none"`` exists to prevent).
    """
    base = {
        "schema_version": 2,
        "commands_emitted": [],
        "files_seeded": [],
        "claim": "none",
        "considered": [],
        "evidence_inspected": {"tools_planned": []},
    }
    base.update(overrides)
    return base


class TestParseAgentOutput(TempDirTestCase):
    def _write(self, doc: dict) -> Path:
        p = self.tmp_dir / "substrate-prep.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def test_well_formed_emit_payload_parses(self) -> None:
        path = self._write(_v2_payload(
            commands_emitted=[{
                "step": "seed_journal",
                "command": "mkdir -p x && [ -f y ] || echo {} > y",
                "rationale": "from spec §4a",
                "fires_when": "y absent",
            }],
            files_seeded=["apps/api/src/db/migrations/meta/_journal.json"],
            considered=[{
                "precondition": "Migration journal required",
                "decision": "emit",
                "rationale": "lockfile shows drizzle-kit",
            }],
            evidence_inspected={"tools_planned": ["drizzle-kit"]},
        ))
        parsed = _parse_agent_output(path)
        self.assertEqual(parsed.parse_errors, [])
        self.assertEqual(len(parsed.commands), 1)
        self.assertEqual(parsed.commands[0]["step"], "seed_journal")
        self.assertEqual(
            parsed.files_seeded,
            ["apps/api/src/db/migrations/meta/_journal.json"],
        )
        self.assertEqual(len(parsed.considered), 1)
        self.assertEqual(parsed.considered[0].decision, "emit")

    def test_zero_commands_payload_parses(self) -> None:
        """Zero commands describes agent action, NOT a verification claim.
        Schema-v2 has no `no_op` field — empty commands_emitted is just
        empty commands_emitted."""
        path = self._write(_v2_payload(
            considered=[{
                "precondition": "Migration journal required",
                "decision": "skip",
                "rationale": "no migration tool in lockfile",
            }],
        ))
        parsed = _parse_agent_output(path)
        self.assertEqual(parsed.parse_errors, [])
        self.assertEqual(parsed.commands, [])
        self.assertEqual(parsed.files_seeded, [])
        self.assertEqual(len(parsed.considered), 1)
        self.assertEqual(parsed.considered[0].decision, "skip")

    def test_missing_file_returns_error(self) -> None:
        parsed = _parse_agent_output(self.tmp_dir / "does-not-exist.json")
        self.assertEqual(parsed.commands, [])
        self.assertEqual(parsed.files_seeded, [])
        self.assertEqual(parsed.considered, [])
        self.assertEqual(len(parsed.parse_errors), 1)
        self.assertIn("did not write output file", parsed.parse_errors[0])

    def test_invalid_json_returns_error(self) -> None:
        p = self.tmp_dir / "broken.json"
        p.write_text("{not json", encoding="utf-8")
        parsed = _parse_agent_output(p)
        self.assertEqual(parsed.commands, [])
        self.assertEqual(len(parsed.parse_errors), 1)
        self.assertIn("failed to read/parse", parsed.parse_errors[0])

    def test_command_missing_command_field_skipped(self) -> None:
        path = self._write(_v2_payload(
            commands_emitted=[
                {"step": "x", "command": "valid one"},
                {"step": "y"},  # missing command
            ],
        ))
        parsed = _parse_agent_output(path)
        self.assertEqual(len(parsed.commands), 1)
        self.assertEqual(parsed.commands[0]["command"], "valid one")
        self.assertTrue(
            any("missing or empty `command`" in e for e in parsed.parse_errors)
        )

    def test_considered_invalid_decision_records_error(self) -> None:
        path = self._write(_v2_payload(
            considered=[{
                "precondition": "x",
                "decision": "maybe",  # invalid — only emit/skip
                "rationale": "y",
            }],
        ))
        parsed = _parse_agent_output(path)
        self.assertEqual(parsed.considered, [])
        self.assertTrue(
            any("must be 'emit' or 'skip'" in e for e in parsed.parse_errors)
        )

    def test_considered_missing_precondition_skipped(self) -> None:
        path = self._write(_v2_payload(
            considered=[{"decision": "skip", "rationale": "x"}],
        ))
        parsed = _parse_agent_output(path)
        self.assertEqual(parsed.considered, [])
        self.assertTrue(
            any("missing `precondition`" in e for e in parsed.parse_errors)
        )

    def test_top_level_array_rejected(self) -> None:
        p = self.tmp_dir / "array.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        parsed = _parse_agent_output(p)
        self.assertTrue(
            any("must be a JSON object" in e for e in parsed.parse_errors)
        )

    def test_unknown_top_level_keys_ignored(self) -> None:
        path = self._write(_v2_payload(future_field="ignore me"))
        parsed = _parse_agent_output(path)
        self.assertEqual(parsed.parse_errors, [])
        self.assertEqual(parsed.commands, [])


class TestSchemaV2NonClaimEnforcement(TempDirTestCase):
    """Pin the load-bearing schema-level non-claim contract.

    Closes Gap 1 (2026-04-28 framework gap audit). v1 used ``no_op: true``
    to mean "I checked and the substrate is healthy" — that was a
    verification claim by the agent, encoded in the schema. v2 removes
    the claim from the schema entirely; the parser REJECTS any
    emission that asserts verification. Prompt-level "this agent does
    not verify" doesn't survive a model refresh; schema-level absence
    + a required ``claim: \"none\"`` discriminator does.
    """

    def _write(self, doc: dict) -> Path:
        p = self.tmp_dir / "substrate-prep.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def test_v1_schema_rejected_no_migration_shim(self) -> None:
        """v1 emissions carry the verification claim we explicitly removed.
        No backwards-compat — the retry loop re-prompts the agent which
        reads the v2 agent definition."""
        path = self._write({
            "schema_version": 1,
            "commands": [],
            "no_op": True,
            "considered": [],
            "evidence": {},
        })
        parsed = _parse_agent_output(path)
        self.assertTrue(
            any("schema_version must be 2" in e for e in parsed.parse_errors),
            f"v1 schema must be rejected; errs={parsed.parse_errors}",
        )

    def test_missing_claim_field_rejected(self) -> None:
        path = self._write({
            "schema_version": 2,
            "commands_emitted": [],
            "files_seeded": [],
            # `claim` deliberately omitted.
            "considered": [],
            "evidence_inspected": {},
        })
        parsed = _parse_agent_output(path)
        self.assertTrue(
            any("missing required `claim`" in e for e in parsed.parse_errors),
            f"missing claim must be rejected; errs={parsed.parse_errors}",
        )

    def test_claim_verified_rejected(self) -> None:
        """The exact failure mode this gap exists to prevent: an agent
        asserting `claim: \"verified\"` (or any non-`\"none\"` value).
        The parser MUST reject so the agent is re-prompted with the
        rule that fired. JSON ``null`` (Python ``None``) is in the
        forbidden set — a model writing ``null`` instead of "none"
        would otherwise slip past a naive ``or "none"`` coercion."""
        for forbidden in ("verified", "healthy", "complete", "ok", "pass", True, None):
            path = self._write(_v2_payload(claim=forbidden))
            parsed = _parse_agent_output(path)
            self.assertTrue(
                any("`claim` must be 'none'" in e for e in parsed.parse_errors),
                f"claim={forbidden!r} must be rejected; errs={parsed.parse_errors}",
            )

    def test_claim_none_accepted(self) -> None:
        path = self._write(_v2_payload(claim="none"))
        parsed = _parse_agent_output(path)
        self.assertEqual(parsed.parse_errors, [])

    def test_files_seeded_must_be_list_of_strings(self) -> None:
        # Non-list rejected.
        path = self._write(_v2_payload(files_seeded={"not": "a list"}))
        parsed = _parse_agent_output(path)
        self.assertTrue(
            any("`files_seeded` must be a list" in e for e in parsed.parse_errors)
        )

        # Non-string entry recorded as error, valid entries kept.
        path = self._write(_v2_payload(files_seeded=["valid/path", 42, ""]))
        parsed = _parse_agent_output(path)
        self.assertEqual(parsed.files_seeded, ["valid/path"])
        self.assertTrue(
            any("expected non-empty string" in e for e in parsed.parse_errors)
        )

    def test_v1_field_names_on_v2_payload_rejected(self) -> None:
        """A v2-tagged emission carrying v1 field names is the agent's
        prompt or training going stale. Silent acceptance with data loss
        (the v1 `commands` field dropped on the floor) is the exact
        failure mode this test prevents — we surface a parse error so
        the retry loop names the offending key. (Linus belated review
        S2: the prior version of this test pinned silent acceptance and
        was therefore lying about what it tested.)"""
        path = self._write({
            "schema_version": 2,
            "commands_emitted": [],
            "files_seeded": [],
            "claim": "none",
            "considered": [],
            "evidence_inspected": {},
            # v1 field names — must each trigger an error.
            "commands": [{"step": "x", "command": "mkdir -p y"}],
            "no_op": False,
            "evidence": {"tools_planned": []},
            "stage_exit": "clean",  # also v1 — orchestrator-only in v2
        })
        parsed = _parse_agent_output(path)
        for v1_key in ("commands", "no_op", "evidence", "stage_exit"):
            self.assertTrue(
                any(
                    f"unexpected v1-only field {v1_key!r}" in e
                    for e in parsed.parse_errors
                ),
                f"v1 key {v1_key!r} on v2 payload must trigger parse error; "
                f"errs={parsed.parse_errors}",
            )

    def test_null_lists_rejected(self) -> None:
        """JSON ``null`` for required collection fields must be rejected,
        not silently coerced to ``[]`` (which would let an agent elide
        command emission and have it read as 'zero commands, success').
        Linus belated review S3-nit on JSON null edge cases."""
        for null_field in ("commands_emitted", "files_seeded", "considered"):
            path = self._write(_v2_payload(**{null_field: None}))
            parsed = _parse_agent_output(path)
            self.assertTrue(
                any(
                    f"missing or null `{null_field}`" in e
                    for e in parsed.parse_errors
                ),
                f"null {null_field} must be rejected; errs={parsed.parse_errors}",
            )


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
        """The deliberate omission: no `no_op` field, no `verified`
        field. Empty `commands` is the default — meaning "no commands
        forwarded to the gate", NOT meaning "substrate verified clean".
        Only verify_substrate() answers the health question."""
        r = SubstratePrepResult()
        self.assertEqual(r.commands, [])
        self.assertEqual(r.files_seeded, [])
        self.assertEqual(r.stage_exit, "clean")
        self.assertEqual(r.rejections, [])
        self.assertEqual(r.considered, [])
        self.assertFalse(r.retries_exhausted)
        self.assertEqual(r.attempts, 0)
        # Pin the schema-level non-claim contract: there must NOT be
        # any field that lets a consumer read substrate-prep status as
        # a substrate-health proxy.
        self.assertFalse(
            hasattr(r, "no_op"),
            "SubstratePrepResult must not carry a no_op flag — that was "
            "the v1 verification-claim field removed in Gap 1 fix",
        )
        self.assertFalse(
            hasattr(r, "verified"),
            "SubstratePrepResult must not carry a verified flag — only "
            "verify_substrate() is authoritative for substrate health",
        )


class TestParsedAgentOutputDataclass(unittest.TestCase):
    def test_default_construction(self) -> None:
        """Replaces the prior 5-tuple return shape (Linus belated
        review S2). Adding a field is one line; throwaway underscores
        in tests collapse to one ignored field on the dataclass instance."""
        p = ParsedAgentOutput()
        self.assertEqual(p.commands, [])
        self.assertEqual(p.files_seeded, [])
        self.assertEqual(p.considered, [])
        self.assertEqual(p.parse_errors, [])
        # No stage_exit on the parsed-from-agent shape — orchestrator-
        # only field; agent has no channel to describe attempt status.
        self.assertFalse(
            hasattr(p, "stage_exit"),
            "ParsedAgentOutput must not carry stage_exit — that field is "
            "orchestrator-owned (Neo belated review #1: a half-agent / "
            "half-orchestrator field has the same writer ambiguity that "
            "bit no_op)",
        )


if __name__ == "__main__":
    unittest.main()
