"""Tests for the ``dark-factory hints`` CLI subcommands (B3, Sprint 4).

Covers the four subcommands (list, lint, add, init) at the CLI-boundary
level — we drive them through ``darkfactory.cli.main`` rather than
calling the handlers directly so argparse wiring is exercised.

Writer-level contracts (atomic rewrite, round-trip validation,
legacy-migration refusal, surgical --force replacement) are tested
here too because they're trivially observable from the outside and a
separate ``test_project_hints_writer.py`` would duplicate the setup
fixtures. If the writer grows enough to need its own tests, we'll
split at that point.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import tomllib

from tests.helpers import TempDirTestCase

from darkfactory.cli import main


class HintsCLITestCase(TempDirTestCase):
    """Base: ``tmp_dir`` is a project-root with no .dark-factory yet."""

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        """Invoke main() and capture (exit_code, stdout, stderr).

        main() calls ``sys.exit`` — we catch it, unwrap, and return the
        code alongside captured streams so tests can assert on both.
        """
        full = ["--project-root", str(self.tmp_dir), *args]
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                main(full)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    @property
    def hints_path(self) -> Path:
        return self.tmp_dir / ".dark-factory" / "project-hints.toml"


class TestHintsList(HintsCLITestCase):
    def test_list_missing_file_guides_user(self) -> None:
        code, out, _ = self.run_cli("hints", "list")
        self.assertEqual(code, 0)
        self.assertIn("no project-hints.toml", out)
        self.assertIn("hints init", out)

    def test_list_preserves_comments_and_ordering(self) -> None:
        # Hand-write a file with comments + a deliberate key ordering
        # that tomli_w would NOT produce. `list` must print verbatim.
        content = (
            "# top-level comment\n"
            "[detected]\n"
            "language = \"node\"\n"
            "\n"
            "[[policy.consistency_rules]]\n"
            "# why this rule exists\n"
            "name = \"foo\"\n"
            "env_paths = [\"commands.dev\"]\n"
        )
        self.hints_path.parent.mkdir(parents=True)
        self.hints_path.write_text(content, encoding="utf-8")

        code, out, _ = self.run_cli("hints", "list")
        self.assertEqual(code, 0)
        # Verbatim: comments + ordering preserved.
        self.assertEqual(out, content)


class TestHintsLint(HintsCLITestCase):
    def test_lint_missing_file_is_not_a_failure(self) -> None:
        code, out, _ = self.run_cli("hints", "lint")
        # A project without policy is valid; lint should exit 0 and
        # guide the user toward init — not FAIL-loudly.
        self.assertEqual(code, 0)
        self.assertIn("no project-hints.toml", out)

    def test_lint_pass_reports_summary(self) -> None:
        self.hints_path.parent.mkdir(parents=True)
        self.hints_path.write_text(
            "[detected]\nlanguage = \"node\"\n"
            "[[policy.consistency_rules]]\n"
            "name = \"r1\"\n"
            "env_paths = [\"commands.dev\"]\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("hints", "lint")
        self.assertEqual(code, 0)
        self.assertIn("PASS", out)
        self.assertIn("consistency_rules=1", out)
        self.assertIn("language=node", out)

    def test_lint_fail_surfaces_config_error(self) -> None:
        self.hints_path.parent.mkdir(parents=True)
        # Rule without required 'name' → ConfigError from loader.
        self.hints_path.write_text(
            "[[policy.consistency_rules]]\n"
            "env_paths = [\"commands.dev\"]\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("hints", "lint")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertIn("missing required", out)

    def test_lint_invalid_toml(self) -> None:
        self.hints_path.parent.mkdir(parents=True)
        self.hints_path.write_text("[policy.\n", encoding="utf-8")
        code, out, _ = self.run_cli("hints", "lint")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)


class TestHintsAdd(HintsCLITestCase):
    def test_add_creates_file_and_writes_rule(self) -> None:
        # Use =-form for a value that starts with `--` so argparse
        # doesn't try to interpret it as another option.
        code, out, _ = self.run_cli(
            "hints", "add",
            "--name", "no-watch",
            "--spec-pattern=--watch",
            "--env-path", "commands.dev",
            "--description", "ban watch",
        )
        self.assertEqual(code, 0, out)
        self.assertTrue(self.hints_path.exists())
        # Re-parse and confirm the rule landed at policy.consistency_rules.
        doc = tomllib.loads(self.hints_path.read_text(encoding="utf-8"))
        rules = doc["policy"]["consistency_rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["name"], "no-watch")
        self.assertEqual(rules[0]["env_paths"], ["commands.dev"])
        self.assertEqual(rules[0]["description"], "ban watch")

    def test_add_rejects_missing_env_path(self) -> None:
        # Silently-inert rules are a footgun; CLI rejects before write.
        code, _out, err = self.run_cli(
            "hints", "add",
            "--name", "r",
            "--spec-pattern", "x",
        )
        self.assertEqual(code, 2)
        self.assertIn("--env-path is required", err)
        self.assertFalse(self.hints_path.exists())

    def test_add_rejects_duplicate_name(self) -> None:
        self.run_cli(
            "hints", "add", "--name", "r", "--env-path", "commands.dev",
        )
        code, _out, err = self.run_cli(
            "hints", "add", "--name", "r", "--env-path", "commands.setup",
        )
        # Exit code 1 (not 2): the arguments are valid, the operation
        # just collides with existing state. Keep the error class
        # distinct from CLI-arg errors per exit-code convention.
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertIn("--force", err)

    def test_add_force_preserves_comments_outside_replaced_rule(self) -> None:
        # The central point of the surgical --force: all other content
        # (top-level comments, sibling rule stanzas, per-rule comments
        # on rules we aren't touching) MUST survive. A naive
        # tomli_w round-trip would destroy them.
        self.hints_path.parent.mkdir(parents=True)
        self.hints_path.write_text(
            "# top-level policy comment\n"
            "[detected]\nlanguage = \"node\"\n"
            "\n"
            "[[policy.consistency_rules]]\n"
            "# keep-me comment on the sibling rule\n"
            "name = \"keep\"\n"
            "env_paths = [\"commands.dev\"]\n"
            "\n"
            "[[policy.consistency_rules]]\n"
            "name = \"replace-me\"\n"
            "env_paths = [\"commands.dev\"]\n"
            "description = \"original\"\n",
            encoding="utf-8",
        )
        code, _out, _err = self.run_cli(
            "hints", "add",
            "--name", "replace-me", "--env-path", "commands.test",
            "--description", "updated",
            "--force",
        )
        self.assertEqual(code, 0)
        after = self.hints_path.read_text(encoding="utf-8")
        # The top-level comment survives.
        self.assertIn("# top-level policy comment", after)
        # The comment on the OTHER rule survives.
        self.assertIn("# keep-me comment on the sibling rule", after)
        # The replacement landed.
        doc = tomllib.loads(after)
        by_name = {r["name"]: r for r in doc["policy"]["consistency_rules"]}
        self.assertEqual(by_name["replace-me"]["description"], "updated")
        self.assertEqual(by_name["replace-me"]["env_paths"], ["commands.test"])
        # The sibling rule's payload is unchanged.
        self.assertEqual(by_name["keep"]["env_paths"], ["commands.dev"])

    def test_add_force_overwrites(self) -> None:
        self.run_cli(
            "hints", "add",
            "--name", "r", "--env-path", "commands.dev",
            "--description", "original",
        )
        code, _out, _err = self.run_cli(
            "hints", "add",
            "--name", "r", "--env-path", "commands.setup",
            "--description", "updated",
            "--force",
        )
        self.assertEqual(code, 0)
        doc = tomllib.loads(self.hints_path.read_text(encoding="utf-8"))
        rules = doc["policy"]["consistency_rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["description"], "updated")
        self.assertEqual(rules[0]["env_paths"], ["commands.setup"])

    def test_add_preserves_existing_comments(self) -> None:
        # Pre-seed a file with comments; append a rule; verify comments
        # survive. This is the whole point of the append-only path.
        self.hints_path.parent.mkdir(parents=True)
        seed = (
            "# Project policy — see ADAPTER-GOVERNANCE.md\n"
            "[detected]\n"
            "language = \"node\"\n"
            "\n"
            "[[policy.consistency_rules]]\n"
            "# seed rule\n"
            "name = \"seed\"\n"
            "env_paths = [\"commands.dev\"]\n"
        )
        self.hints_path.write_text(seed, encoding="utf-8")

        code, _out, _err = self.run_cli(
            "hints", "add",
            "--name", "fresh", "--env-path", "commands.test",
        )
        self.assertEqual(code, 0)
        after = self.hints_path.read_text(encoding="utf-8")
        self.assertIn("# Project policy", after)  # top-level comment preserved
        self.assertIn("# seed rule", after)       # per-rule comment preserved
        # Both rules present.
        doc = tomllib.loads(after)
        names = {r["name"] for r in doc["policy"]["consistency_rules"]}
        self.assertEqual(names, {"seed", "fresh"})

    def test_add_rule_name_must_be_non_empty(self) -> None:
        # Empty --name is an arg-level footgun (writes silently-inert
        # rules, bypasses duplicate detection). Rejected at the CLI
        # layer so we never touch disk — exit 2 (argparse convention).
        code, _out, err = self.run_cli(
            "hints", "add", "--name", "", "--env-path", "commands.dev",
        )
        self.assertEqual(code, 2)
        self.assertIn("--name must be non-empty", err)
        self.assertFalse(self.hints_path.exists())

    def test_add_rejects_empty_list_values(self) -> None:
        # Empty-string entries in repeatable arg lists round-trip into
        # malformed rule stanzas. Reject at the CLI boundary.
        code, _out, err = self.run_cli(
            "hints", "add",
            "--name", "r", "--env-path", "commands.dev",
            "--spec-pattern", "",
        )
        self.assertEqual(code, 2)
        self.assertIn("--spec-pattern", err)

    def test_add_bad_existing_toml_is_clear(self) -> None:
        self.hints_path.parent.mkdir(parents=True)
        self.hints_path.write_text("not = valid = toml\n", encoding="utf-8")
        code, _out, err = self.run_cli(
            "hints", "add", "--name", "r", "--env-path", "commands.dev",
        )
        self.assertEqual(code, 1)
        self.assertIn("malformed TOML", err)
        self.assertIn("hints lint", err)


class TestHintsInit(HintsCLITestCase):
    def test_init_non_interactive_writes_empty_policy(self) -> None:
        code, out, _ = self.run_cli("hints", "init", "--non-interactive")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.hints_path.exists())
        doc = tomllib.loads(self.hints_path.read_text(encoding="utf-8"))
        self.assertIn("policy", doc)
        # Empty policy section is deliberate: the CLI prints guidance.
        self.assertIn("hints add", out)

    def test_init_preserves_existing_detected(self) -> None:
        # Confirm init doesn't clobber [detected] written by
        # `dark-factory init` earlier in the onboarding flow.
        self.hints_path.parent.mkdir(parents=True)
        self.hints_path.write_text(
            "[detected]\nlanguage = \"node\"\n"
            "package_manager = \"pnpm\"\n",
            encoding="utf-8",
        )
        code, _out, _err = self.run_cli("hints", "init", "--non-interactive")
        self.assertEqual(code, 0)
        doc = tomllib.loads(self.hints_path.read_text(encoding="utf-8"))
        self.assertEqual(doc["detected"]["language"], "node")
        self.assertEqual(doc["detected"]["package_manager"], "pnpm")

    def test_init_refuses_to_overwrite_existing_policy(self) -> None:
        self.hints_path.parent.mkdir(parents=True)
        self.hints_path.write_text(
            "[[policy.consistency_rules]]\n"
            "name = \"existing\"\n"
            "env_paths = [\"commands.dev\"]\n",
            encoding="utf-8",
        )
        code, _out, err = self.run_cli("hints", "init", "--non-interactive")
        self.assertEqual(code, 1)
        self.assertIn("already has a [policy]", err)
        # File untouched.
        self.assertIn("existing", self.hints_path.read_text(encoding="utf-8"))

    def test_init_force_overwrites_existing_policy(self) -> None:
        self.hints_path.parent.mkdir(parents=True)
        self.hints_path.write_text(
            "[[policy.consistency_rules]]\n"
            "name = \"existing\"\n"
            "env_paths = [\"commands.dev\"]\n",
            encoding="utf-8",
        )
        code, _out, _err = self.run_cli(
            "hints", "init", "--non-interactive", "--force",
        )
        self.assertEqual(code, 0)
        # With an empty interactive policy, --force drops the old rule.
        doc = tomllib.loads(self.hints_path.read_text(encoding="utf-8"))
        self.assertEqual(
            doc.get("policy", {}).get("consistency_rules", []),
            [],
        )


class TestWriterRollback(HintsCLITestCase):
    """Writer contract: on validation failure, the previous bytes are
    restored so automation callers (C2 remediation) see a consistent
    on-disk state after an error.

    We exercise the rollback by wedging ``load_project_hints`` into
    failing post-write — any ConfigError triggers the restore branch.
    """

    def test_rollback_restores_previous_bytes_on_validation_failure(
        self,
    ) -> None:
        # Seed a known-good file first.
        self.run_cli(
            "hints", "add", "--name", "seed", "--env-path", "commands.dev",
        )
        self.assertTrue(self.hints_path.exists())
        before = self.hints_path.read_bytes()

        # Monkey-patch the writer's validator to simulate a post-write
        # schema failure. The writer should restore `before` bytes.
        from darkfactory.architecture import project_hints_writer
        from darkfactory.errors import ConfigError

        def _fail(_project_dir):
            raise ConfigError("synthetic validation failure")

        original = project_hints_writer.load_project_hints
        project_hints_writer.load_project_hints = _fail
        try:
            code, _out, err = self.run_cli(
                "hints", "add",
                "--name", "second", "--env-path", "commands.test",
            )
        finally:
            project_hints_writer.load_project_hints = original

        self.assertEqual(code, 1)
        self.assertIn("synthetic validation failure", err)
        # Bytes are bit-for-bit identical after the rollback — the
        # new rule never ended up on disk.
        self.assertEqual(self.hints_path.read_bytes(), before)

    def test_rollback_removes_greenfield_file_on_validation_failure(
        self,
    ) -> None:
        # Greenfield (no prior file) + validation failure → file must
        # be removed, not left as broken bytes.
        from darkfactory.architecture import project_hints_writer
        from darkfactory.errors import ConfigError

        def _fail(_project_dir):
            raise ConfigError("synthetic validation failure")

        original = project_hints_writer.load_project_hints
        project_hints_writer.load_project_hints = _fail
        try:
            code, _out, _err = self.run_cli(
                "hints", "add",
                "--name", "r", "--env-path", "commands.dev",
            )
        finally:
            project_hints_writer.load_project_hints = original

        self.assertEqual(code, 1)
        self.assertFalse(self.hints_path.exists())


class TestHintsLegacyRefusal(HintsCLITestCase):
    """Writer contract: refuse to write when a legacy hints.json is live.

    The loader's `synthesized_from_legacy` flag documents that consumers
    that persist MUST check the flag before writing. A bare write would
    produce a project-hints.toml that silently shadows the legacy
    [detected] facts (the loader prefers the TOML) and strand the JSON.
    """

    def _seed_legacy(self) -> None:
        legacy_dir = self.tmp_dir / ".dark-factory" / "architecture"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "hints.json").write_text('{"language": "node"}\n')

    def test_add_refuses_when_legacy_pair_present(self) -> None:
        self._seed_legacy()
        code, _out, err = self.run_cli(
            "hints", "add", "--name", "r", "--env-path", "commands.dev",
        )
        self.assertEqual(code, 1)
        self.assertIn("legacy", err)
        self.assertIn("init --migrate-hints", err)
        # Writer refused — no project-hints.toml was created.
        self.assertFalse(self.hints_path.exists())

    def test_init_refuses_when_legacy_pair_present(self) -> None:
        self._seed_legacy()
        code, _out, err = self.run_cli("hints", "init", "--non-interactive")
        self.assertEqual(code, 1)
        self.assertIn("legacy", err)
        self.assertFalse(self.hints_path.exists())

    def test_add_allowed_after_project_hints_exists(self) -> None:
        # Once project-hints.toml exists alongside legacy files, the
        # loader prefers the TOML and legacy is already shadowed — no
        # further harm from writes. The guard only fires before the
        # migration.
        self._seed_legacy()
        self.hints_path.parent.mkdir(parents=True, exist_ok=True)
        self.hints_path.write_text(
            "[detected]\nlanguage = \"node\"\n", encoding="utf-8",
        )
        code, _out, _err = self.run_cli(
            "hints", "add", "--name", "r", "--env-path", "commands.dev",
        )
        self.assertEqual(code, 0)


class TestHintsNoSubcommand(HintsCLITestCase):
    def test_bare_hints_shows_usage(self) -> None:
        # Usage error → exit 2 (matches argparse convention).
        code, out, _ = self.run_cli("hints")
        self.assertEqual(code, 2)
        self.assertIn("list", out)
        self.assertIn("lint", out)
        self.assertIn("add", out)
        self.assertIn("init", out)


if __name__ == "__main__":
    unittest.main()
