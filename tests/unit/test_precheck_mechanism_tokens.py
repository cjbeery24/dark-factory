"""Tests for mechanism-token integration in ``run_precheck`` (Proposal 4).

Verifies:
- The Postgres blocklist is applied to specs when postgres is in active_adapters.
- Block-severity matches set ``fatal=True`` and ``passed=False``.
- Warn-only matches surface in findings but don't gate the spec.
- The check is skipped for non-Postgres projects (no false positives).
- Summary output names the token, severity, and line so the spec-writer
  loop has enough context to fix each finding.
"""
from __future__ import annotations

import textwrap
import unittest

from tests.helpers import TempDirTestCase

from darkfactory.spec.precheck import run_precheck


def _write_hints(project_dir, adapters: list[str]) -> None:
    (project_dir / ".dark-factory").mkdir(parents=True, exist_ok=True)
    (project_dir / ".dark-factory" / "project-hints.toml").write_text(
        "[detected]\n"
        f"active_adapters = {adapters!r}\n"
        "schema_version = 1\n",
    )


_MINIMAL_SPEC_HEADER = """\
---
spec_type: feature
---

# Minimal spec

## 1. Problem Statement

Keep things working.

## 2. Functional Requirements

- FR-1: The system serves requests.

## 3. Non-Functional Requirements

- NFR-1: Availability.

## 4. Constraints

- None.

## 5. Interface Contract

```ts
export function foo(): void;
```

## 6. Edge Cases

- EC-1: An empty request returns an empty response.

## 7. Error Handling

- All errors return 500.

## 8. Acceptance Criteria

- AC-1: Given an empty request, when the endpoint is called, then the response is empty.

## 8a. Non-Negotiable Acceptance Criteria

- AC-1

## 9. Examples

- GET / → 200.

## 10. Dependencies & Out of Scope

- None.
"""


class TestPrecheckMechanismTokens(TempDirTestCase):
    """run_precheck gates on mechanism tokens when postgres is active."""

    def _spec(self, name: str, body: str):
        _write_hints(self.tmp_dir, ["typescript-node", "postgres"])
        spec_path = self.tmp_dir / ".dark-factory" / "specs" / f"{name}.md"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(body)
        return spec_path

    def test_clean_spec_does_not_flag_mechanism_tokens(self) -> None:
        spec = self._spec("clean", _MINIMAL_SPEC_HEADER)
        result = run_precheck(spec, project_dir=self.tmp_dir)
        self.assertEqual(result.mechanism_tokens, [])
        # The gate should not produce a mechanism-driven fatal.
        # (It may still fatal for other reasons on a minimal spec, but
        # not due to this new gate.)
        blocking = [f for f in result.mechanism_tokens if f.severity == "block"]
        self.assertEqual(blocking, [])

    def test_block_token_sets_fatal(self) -> None:
        body = _MINIMAL_SPEC_HEADER + textwrap.dedent("""
            ## Appendix: Implementation note

            Every request must issue `SET LOCAL app.tenant_id = <value>`
            so that row-level policies can read `current_setting('app.tenant_id')`.
        """)
        spec = self._spec("hot-spec", body)
        result = run_precheck(spec, project_dir=self.tmp_dir)
        self.assertTrue(result.mechanism_tokens)
        self.assertTrue(result.fatal)
        self.assertFalse(result.passed)
        names = {f.token_name for f in result.mechanism_tokens}
        # The content we injected triggers at least these three.
        self.assertIn("set_local", names)
        self.assertIn("app_guc_identifier", names)
        self.assertIn("current_setting_call", names)

    def test_warn_only_does_not_set_fatal(self) -> None:
        # drizzle is a warn-severity token: it surfaces in findings but
        # doesn't mark the spec fatal on its own.
        body = _MINIMAL_SPEC_HEADER + textwrap.dedent("""
            ## Appendix

            Use drizzle-orm for schema definition.
        """)
        spec = self._spec("warn-only", body)
        result = run_precheck(spec, project_dir=self.tmp_dir)
        drizzle = [f for f in result.mechanism_tokens if f.token_name == "drizzle_library"]
        self.assertEqual(len(drizzle), 1)
        self.assertEqual(drizzle[0].severity, "warn")
        # No block-severity finding → not fatal from mechanism-tokens.
        block_findings = [f for f in result.mechanism_tokens if f.severity == "block"]
        self.assertEqual(block_findings, [])
        # Depending on other minimal-spec issues, fatal could be False
        # here. What matters: mechanism tokens ALONE did not cause fatal.
        # If it IS fatal, sql_tag_syntax or blocking_ac_family is the
        # reason — not mechanism_tokens.
        if result.fatal:
            # Confirm the reason is one of the other fatal-producing
            # checks, not this one.
            self.assertFalse(
                any(f.severity == "block" for f in result.mechanism_tokens),
                "fatal must not be caused by warn-only mechanism tokens",
            )

    def test_skipped_when_postgres_not_active(self) -> None:
        # Set active_adapters WITHOUT postgres; the scanner must be a no-op
        # so a Python project using the word "drizzle" or "app.config" in
        # unrelated prose doesn't get false-blocked.
        _write_hints(self.tmp_dir, ["python-fastapi"])
        body = _MINIMAL_SPEC_HEADER + textwrap.dedent("""
            ## Appendix

            SET LOCAL and current_setting and app.tenant_id everywhere!
        """)
        spec_path = self.tmp_dir / ".dark-factory" / "specs" / "no-pg.md"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(body)
        result = run_precheck(spec_path, project_dir=self.tmp_dir)
        self.assertEqual(result.mechanism_tokens, [])

    def test_runs_when_project_dir_none(self) -> None:
        # No project_dir → fail-open to "run the gate" so isolated
        # fixture tests still exercise the check.
        body = _MINIMAL_SPEC_HEADER + "\n\nUse SET LOCAL everywhere.\n"
        spec_path = self.tmp_dir / ".dark-factory" / "specs" / "no-dir.md"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(body)
        result = run_precheck(spec_path, project_dir=None)
        self.assertTrue(result.mechanism_tokens)
        self.assertTrue(result.fatal)

    def test_summary_names_token_and_line(self) -> None:
        body = _MINIMAL_SPEC_HEADER + textwrap.dedent("""

            The app sets app.tenant_id per request.
        """)
        spec = self._spec("summary-spec", body)
        result = run_precheck(spec, project_dir=self.tmp_dir)
        # The summary must include the P4 header + the specific token
        # name so a /df-write-spec iteration can locate the problem.
        self.assertIn("Mechanism tokens (P4)", result.summary)
        self.assertIn("app_guc_identifier", result.summary)


if __name__ == "__main__":
    unittest.main()
