"""Phase-0 subprocess-contract gate.

A `vehicle: subprocess` anchor is REMOVED from scenario + holdout scoring (the
same `excluded` set as @untestable, orchestrator.py:727). Its ONLY possible
verifier is a Tier-1 command contract. Before this gate, command contracts were
a WARNING, not a gate, so an author could mark an AC subprocess and ship it
verified by NOTHING — the floor's fail-closed promise inverted to fail-open for
that anchor (Neo Phase-0 live-hole finding). These tests pin:

  1. `is_behavioral_command` — a command contract must assert an OUTCOME
     attributable to a claim (a failure on bad input, a file side effect),
     not merely that the command exited 0.
  2. `check_subprocess_contract_blockers` — every subprocess-excluded anchor
     must be NAMED in the `covers:` of a behavioral command contract (or covered
     via its FR id), else REVISE.
  3. The `Contract.covers` field round-trips through `parse_contracts`.

Open Linus/Codex question, recorded here: the FR-fallback (a behavioral
contract `fr: FR-1` covers subprocess anchor `FR-1`) is the one looser
allowance — the architect's command contracts are FR-keyed today, so without it
every subprocess FR would need an explicit `covers:`. It does NOT let an
FR-keyed contract cover a child AC implicitly (AC-3 has no intrinsic FR map);
only an explicit `covers: [AC-3]` does.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.spec.contracts import (
    Contract,
    is_behavioral_command,
    parse_contracts,
)
from darkfactory.spec.precheck import check_subprocess_contract_blockers, run_precheck


def _cmd(then: dict, ctype: str = "command") -> Contract:
    return Contract(
        id="C1", fr="FR-1", type=ctype, given="g", when={"command": "x"}, then=then
    )


class IsBehavioralCommandTests(unittest.TestCase):
    def test_non_command_is_not_behavioral(self):
        # http/import/file_exists are not command contracts — out of scope here.
        c = Contract(id="C", fr="FR-1", type="http", given="g", when={}, then={})
        self.assertFalse(is_behavioral_command(c))

    def test_bare_exit_zero_no_then_is_vacuous(self):
        # `npm run build` exits 0 — proves the command ran, asserts no outcome.
        self.assertFalse(is_behavioral_command(_cmd({"exit_code": 0})))

    def test_nonzero_exit_is_behavioral(self):
        # a SPECIFIC failure on a specific (bad) input is an outcome.
        self.assertTrue(is_behavioral_command(_cmd({"exit_code": 1})))

    def test_file_with_pattern_is_behavioral(self):
        self.assertTrue(
            is_behavioral_command(
                _cmd({"exit_code": 0, "file": "dist/index.html", "pattern": "<title>"})
            )
        )

    def test_file_with_trivial_pattern_is_not_behavioral(self):
        # ".*" matches anything — constrains nothing.
        for trivial in (".*", "", "[\\s\\S]*", "(?s).*"):
            with self.subTest(pattern=trivial):
                self.assertFalse(
                    is_behavioral_command(
                        _cmd({"exit_code": 0, "file": "f", "pattern": trivial})
                    )
                )

    def test_file_with_empty_matching_pattern_is_not_behavioral(self):
        # `a*` matches the empty string -> matches every input -> trivial.
        self.assertFalse(
            is_behavioral_command(_cmd({"exit_code": 0, "file": "f", "pattern": "a*"}))
        )

    def test_json_parse_requires_path(self):
        self.assertTrue(
            is_behavioral_command(
                _cmd({"file": "out.json", "parse": "json", "path": "$.ok"})
            )
        )
        self.assertFalse(
            is_behavioral_command(_cmd({"file": "out.json", "parse": "json"}))
        )

    def test_malformed_pattern_is_not_behavioral(self):
        # an invalid regex cannot be relied on to constrain -> fail closed.
        self.assertFalse(
            is_behavioral_command(_cmd({"exit_code": 0, "file": "f", "pattern": "([a"}))
        )

    def test_negative_match_assertion_is_not_behavioral(self):
        # matches: false + an improbable pattern is always-green -> vacuous
        # (Linus C1). A real "string is absent" claim earns credit via exit_code.
        self.assertFalse(
            is_behavioral_command(
                _cmd({"file": "f", "pattern": "zzz_never_appears", "matches": False})
            )
        )

    def test_negative_match_with_nonzero_exit_is_behavioral(self):
        # a nonzero exit_code is a real outcome regardless of matches direction.
        self.assertTrue(
            is_behavioral_command(
                _cmd({"exit_code": 1, "file": "f", "pattern": "x", "matches": False})
            )
        )

    def test_metachar_only_patterns_are_not_behavioral(self):
        # patterns that match every non-empty file assert "non-empty", not
        # content -> not behavioral (Codex B3 / Linus Q1).
        for meta in (".+", r"\b", r"\S", "(?=.)", ".{5}", r"\w+", "[\\s\\S]+"):
            with self.subTest(pattern=meta):
                self.assertFalse(
                    is_behavioral_command(
                        _cmd({"exit_code": 0, "file": "f", "pattern": meta})
                    )
                )

    def test_literal_content_patterns_are_behavioral(self):
        # patterns carrying a literal token assert specific content.
        for lit in ("<title>", "zzz_never", "a{3}", "VITE_API_URL", "[0-9]+abc"):
            with self.subTest(pattern=lit):
                self.assertTrue(
                    is_behavioral_command(
                        _cmd({"exit_code": 0, "file": "f", "pattern": lit})
                    )
                )

    def test_universal_alternation_bypass_is_not_behavioral(self):
        # a literal OR'd with a universal branch matches every input -> vacuous,
        # even though it carries a literal token (Codex B3).
        for univ in (r"a|[\s\S]", r"(?:never)|[\s\S]", r"x|[\w\W]", r"foo|[^]"):
            with self.subTest(pattern=univ):
                self.assertFalse(
                    is_behavioral_command(
                        _cmd({"exit_code": 0, "file": "AGENTS.md", "pattern": univ})
                    )
                )

    def test_hex_range_near_universal_is_not_behavioral(self):
        # a hex char-range spanning all-but-one byte is universal; its \xNN digits
        # must NOT be miscounted as literal content (Codex B3 round 2).
        for univ in (r"[\x00-\x09\x0b-\xff]", r"[\x01-\xff]"):
            with self.subTest(pattern=univ):
                self.assertFalse(
                    is_behavioral_command(
                        _cmd({"exit_code": 0, "file": "AGENTS.md", "pattern": univ})
                    )
                )

    def test_literal_digit_class_is_behavioral(self):
        # a genuine bounded class with literal boundaries still asserts content.
        self.assertTrue(
            is_behavioral_command(
                _cmd({"exit_code": 0, "file": "f", "pattern": "version [0-9]"})
            )
        )

    def test_bounded_multiline_pattern_is_behavioral(self):
        # a universal class BOUNDED by literals is a real assertion (matches none
        # of the single-char probes) -> kept.
        self.assertTrue(
            is_behavioral_command(
                _cmd({"exit_code": 0, "file": "f", "pattern": r"BEGIN[\s\S]*END"})
            )
        )


# --- spec-markdown fixtures for the gate (parse_contracts reads real markdown) ---

def _spec(contracts_yaml: str) -> str:
    return (
        "# Spec\n\n## Acceptance Criteria\n\n"
        "```yaml\n"
        "contracts:\n"
        f"{contracts_yaml}"
        "```\n"
    )


_BEHAVIORAL_AC1 = (
    "  - id: C1\n"
    "    fr: FR-1\n"
    "    type: command\n"
    "    given: apps/web set up\n"
    "    when:\n"
    "      command: pnpm --filter web build\n"
    "    then:\n"
    "      exit_code: 0\n"
    "      file: apps/web/dist/index.html\n"
    "      pattern: <title>\n"
    "    covers: [AC-1]\n"
)

_VACUOUS_AC1 = (
    "  - id: C1\n"
    "    fr: FR-1\n"
    "    type: command\n"
    "    given: apps/web set up\n"
    "    when:\n"
    "      command: pnpm --filter web build\n"
    "    then:\n"
    "      exit_code: 0\n"
    "    covers: [AC-1]\n"
)


class CheckSubprocessContractBlockersTests(unittest.TestCase):
    def test_no_subprocess_anchors_is_clean(self):
        self.assertEqual(check_subprocess_contract_blockers(_spec(_BEHAVIORAL_AC1), []), [])

    def test_uncovered_subprocess_anchor_blocks(self):
        # AC-1 marked subprocess, NO contract at all -> verified by nothing.
        spec = "# Spec\n\n## Acceptance Criteria\n\n(no contracts)\n"
        blockers = check_subprocess_contract_blockers(spec, ["AC-1"])
        self.assertEqual(len(blockers), 1)
        self.assertIn("AC-1", blockers[0])
        self.assertIn("subprocess", blockers[0])

    def test_behavioral_contract_covers_anchor_is_clean(self):
        self.assertEqual(
            check_subprocess_contract_blockers(_spec(_BEHAVIORAL_AC1), ["AC-1"]), []
        )

    def test_vacuous_contract_does_not_cover_anchor(self):
        # a bare exit_code:0 command names AC-1 in covers but is NOT behavioral.
        blockers = check_subprocess_contract_blockers(_spec(_VACUOUS_AC1), ["AC-1"])
        self.assertEqual(len(blockers), 1)
        self.assertIn("AC-1", blockers[0])

    def test_fr_fallback_covers_subprocess_fr_anchor(self):
        # subprocess marker on FR-1 itself; a behavioral contract fr: FR-1
        # (no explicit covers) is allowed to cover it.
        yaml_no_covers = _BEHAVIORAL_AC1.replace("    covers: [AC-1]\n", "")
        self.assertEqual(
            check_subprocess_contract_blockers(_spec(yaml_no_covers), ["FR-1"]), []
        )

    def test_fr_keyed_contract_does_not_implicitly_cover_child_ac(self):
        # contract fr: FR-1 with no covers must NOT cover a child AC-1.
        yaml_no_covers = _BEHAVIORAL_AC1.replace("    covers: [AC-1]\n", "")
        blockers = check_subprocess_contract_blockers(_spec(yaml_no_covers), ["AC-1"])
        self.assertEqual(len(blockers), 1)

    def test_case_insensitive_anchor_match(self):
        spec = _spec(_BEHAVIORAL_AC1.replace("covers: [AC-1]", "covers: [ac-1]"))
        self.assertEqual(check_subprocess_contract_blockers(spec, ["AC-1"]), [])

    def test_partial_coverage_blocks_only_uncovered(self):
        blockers = check_subprocess_contract_blockers(
            _spec(_BEHAVIORAL_AC1), ["AC-1", "AC-2"]
        )
        self.assertEqual(len(blockers), 1)
        self.assertIn("AC-2", blockers[0])


class ContractCoversFieldTests(unittest.TestCase):
    def test_covers_round_trips_through_parse(self):
        result = parse_contracts(_spec(_BEHAVIORAL_AC1))
        self.assertEqual(len(result.contracts), 1, result.errors)
        self.assertEqual(result.contracts[0].covers, ["AC-1"])

    def test_covers_defaults_empty_and_normalises_upper(self):
        yaml_lower = _BEHAVIORAL_AC1.replace("covers: [AC-1]", "covers: [ac-1, ec-2]")
        result = parse_contracts(_spec(yaml_lower))
        self.assertEqual(result.contracts[0].covers, ["AC-1", "EC-2"])

    def test_contract_without_covers_defaults_empty(self):
        yaml_no_covers = _BEHAVIORAL_AC1.replace("    covers: [AC-1]\n", "")
        result = parse_contracts(_spec(yaml_no_covers))
        self.assertEqual(result.contracts[0].covers, [])


# --- end-to-end run_precheck wiring (the gate must FATAL, both paths) --------

_SUBPROC_SPEC_UNCOVERED = """# S

## 2. Functional Requirements
### FR-2: cli

## 8. Acceptance Criteria
| ID | Criterion | Vehicle | Blocking |
|----|-----------|---------|----------|
| AC-1 | GIVEN PORT='abc' WHEN server starts THEN it calls process.exit(1) | subprocess | yes |
"""

_COVERING_CONTRACT = """
```yaml
contracts:
  - id: C1
    fr: FR-2
    type: command
    given: a bad PORT value
    when:
      command: PORT=abc node server.js
    then:
      exit_code: 1
    covers: [AC-1]
```
"""


class RunPrecheckWiringTests(unittest.TestCase):
    def test_uncovered_subprocess_anchor_is_fatal(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "spec.md"
            p.write_text(_SUBPROC_SPEC_UNCOVERED, encoding="utf-8")
            r = run_precheck(p)
        # The deterministic testability check must have flagged AC-1 subprocess.
        self.assertIn("AC-1", r.anchor_manifest.subprocess_ac_ids)
        # ... and with no covering contract it is FATAL (not merely advisory) on
        # BOTH paths (orchestrator + validate both hard-stop on fatal).
        self.assertTrue(r.fatal)
        self.assertFalse(r.passed)
        self.assertTrue(any("AC-1" in b for b in r.subprocess_contract_blockers))

    def test_behavioral_contract_clears_the_fatal(self):
        spec = _SUBPROC_SPEC_UNCOVERED + _COVERING_CONTRACT
        with TemporaryDirectory() as d:
            p = Path(d) / "spec.md"
            p.write_text(spec, encoding="utf-8")
            r = run_precheck(p)
        self.assertIn("AC-1", r.anchor_manifest.subprocess_ac_ids)
        self.assertEqual(r.subprocess_contract_blockers, [])


if __name__ == "__main__":
    unittest.main()
