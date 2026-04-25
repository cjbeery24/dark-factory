"""Unit tests for ``darkfactory.spec.negative_space``.

Coverage targets:
- The four §4a list blocks parse cleanly when present.
- §5b.test_server parses when present, defaults when absent.
- A spec with no §4a returns empty findings with zero parse errors.
- Malformed YAML or wrong shape produces a parse_errors entry but
  doesn't raise.
- Unknown top-level keys are ignored (forward-compatibility).
"""
from __future__ import annotations

import unittest

from darkfactory.spec.negative_space import (
    extract_negative_space,
)


_FULL_SPEC = """\
# Feature Specification: Test

## 1. Problem Statement

stuff

## 4a. Negative Space, Invariants, and Lifecycle

### forbidden_commands

```yaml
forbidden_commands:
  - pattern: '\\bdrizzle-kit\\s+push\\b'
    reason: "Spec mandates CLI migrations"
    applies_to: [commands.setup]
  - pattern: 'rm -rf /'
    reason: "Disallowed cleanup"
    applies_to: [commands.install, commands.setup]
```

### forbidden_test_shapes

```yaml
forbidden_test_shapes:
  - pattern: 'connection\\s*`SELECT current_setting'
    reason: "peer client cannot read transaction GUC"
    applies_to: [scenarios, holdouts]
```

### ecosystem_invariants

```yaml
ecosystem_invariants:
  - name: "rls-via-migration"
    invariant: "RLS in SQL files only"
    enforcement: architect.amend_check
```

### required_lifecycle_sequences

```yaml
required_lifecycle_sequences:
  - name: "migrate-needs-journal"
    sequence: ["seed_journal", "drizzle-kit migrate"]
    rationale: "drizzle-kit migrate hangs without journal"
    enforcement: substrate-agent
```

## 5. Interface Contract

stuff

## 5b. Wire Contracts

### test_server

```yaml
test_server:
  command: "cd apps/api && pnpm start"
  base_url: "http://localhost:3000"
  startup_timeout_seconds: 15
```

## 6. Edge Cases

stuff
"""


class TestExtractNegativeSpace(unittest.TestCase):
    def test_full_spec_parses_all_blocks(self) -> None:
        f = extract_negative_space(_FULL_SPEC)
        self.assertEqual(f.parse_errors, [])

        # forbidden_commands
        self.assertEqual(len(f.forbidden_commands), 2)
        self.assertEqual(
            f.forbidden_commands[0].pattern, r"\bdrizzle-kit\s+push\b",
        )
        self.assertEqual(
            f.forbidden_commands[0].applies_to, ["commands.setup"],
        )
        self.assertEqual(
            f.forbidden_commands[1].pattern, "rm -rf /",
        )

        # forbidden_test_shapes
        self.assertEqual(len(f.forbidden_test_shapes), 1)
        self.assertIn("scenarios", f.forbidden_test_shapes[0].applies_to)

        # ecosystem_invariants
        self.assertEqual(len(f.ecosystem_invariants), 1)
        self.assertEqual(f.ecosystem_invariants[0].name, "rls-via-migration")
        self.assertEqual(
            f.ecosystem_invariants[0].enforcement, "architect.amend_check",
        )

        # required_lifecycle_sequences
        self.assertEqual(len(f.required_lifecycle_sequences), 1)
        self.assertEqual(
            f.required_lifecycle_sequences[0].sequence,
            ["seed_journal", "drizzle-kit migrate"],
        )

        # test_server
        self.assertEqual(f.test_server.command, "cd apps/api && pnpm start")
        self.assertEqual(f.test_server.base_url, "http://localhost:3000")
        self.assertEqual(f.test_server.startup_timeout_seconds, 15)

    def test_spec_without_section_4a_returns_empty(self) -> None:
        text = "# Title\n\n## 1. Problem Statement\n\nstuff\n"
        f = extract_negative_space(text)
        self.assertEqual(f.forbidden_commands, [])
        self.assertEqual(f.forbidden_test_shapes, [])
        self.assertEqual(f.ecosystem_invariants, [])
        self.assertEqual(f.required_lifecycle_sequences, [])
        self.assertEqual(f.test_server.command, "")
        self.assertEqual(f.test_server.base_url, "")
        # Default timeout when block absent.
        self.assertEqual(f.test_server.startup_timeout_seconds, 10)
        self.assertEqual(f.parse_errors, [])

    def test_malformed_yaml_records_error_does_not_raise(self) -> None:
        text = """\
## 4a. Negative Space, Invariants, and Lifecycle

```yaml
forbidden_commands:
  - pattern: 'oops
    reason: unclosed string
```
"""
        f = extract_negative_space(text)
        self.assertEqual(f.forbidden_commands, [])
        self.assertEqual(len(f.parse_errors), 1)
        self.assertIn("YAML parse error", f.parse_errors[0])

    def test_wrong_shape_records_error(self) -> None:
        text = """\
## 4a. Negative Space, Invariants, and Lifecycle

```yaml
forbidden_commands: "not a list"
```
"""
        f = extract_negative_space(text)
        self.assertEqual(f.forbidden_commands, [])
        self.assertTrue(any(
            "expected list" in err for err in f.parse_errors
        ))

    def test_missing_pattern_in_forbidden_command_skipped(self) -> None:
        text = """\
## 4a. Negative Space

```yaml
forbidden_commands:
  - reason: "missing pattern key entirely"
  - pattern: "valid-one"
    reason: "ok"
```
"""
        f = extract_negative_space(text)
        # First entry skipped; second kept.
        self.assertEqual(len(f.forbidden_commands), 1)
        self.assertEqual(f.forbidden_commands[0].pattern, "valid-one")
        self.assertTrue(any(
            "missing or non-string `pattern`" in err
            for err in f.parse_errors
        ))

    def test_unknown_top_level_keys_ignored(self) -> None:
        text = """\
## 4a. Negative Space

```yaml
forbidden_commands:
  - pattern: "x"
    reason: "y"
some_future_field:
  whatever: 1
```
"""
        f = extract_negative_space(text)
        # Unknown key is silently ignored — not an error.
        self.assertEqual(f.parse_errors, [])
        self.assertEqual(len(f.forbidden_commands), 1)

    def test_test_server_partial_declaration_ok(self) -> None:
        text = """\
## 5b. Wire Contracts

```yaml
test_server:
  base_url: "http://localhost:3000"
```
"""
        f = extract_negative_space(text)
        self.assertEqual(f.test_server.command, "")
        self.assertEqual(f.test_server.base_url, "http://localhost:3000")
        self.assertEqual(f.test_server.startup_timeout_seconds, 10)
        self.assertEqual(f.parse_errors, [])

    def test_test_server_invalid_timeout_records_error(self) -> None:
        text = """\
## 5b. Wire Contracts

```yaml
test_server:
  command: "x"
  base_url: "http://localhost:3000"
  startup_timeout_seconds: "not-a-number"
```
"""
        f = extract_negative_space(text)
        # Falls back to default and records error.
        self.assertEqual(f.test_server.startup_timeout_seconds, 10)
        self.assertTrue(any(
            "must be an integer" in err for err in f.parse_errors
        ))

    def test_blocks_in_unrelated_sections_ignored(self) -> None:
        text = """\
## 1. Problem Statement

```yaml
forbidden_commands:
  - pattern: "wrong-section"
    reason: "this should be ignored"
```

## 4a. Negative Space

[empty]
"""
        f = extract_negative_space(text)
        # YAML in §1 must NOT be parsed as negative-space.
        self.assertEqual(f.forbidden_commands, [])
        self.assertEqual(f.parse_errors, [])


if __name__ == "__main__":
    unittest.main()
