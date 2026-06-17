"""Tests for the pnpm workspace script consistency checker.

Covers both the command-string parser (``derive_required_scripts``) and
the workspace walker (``check_workspace_scripts``). The fix under test
distinguishes commands scoped to a single package from commands that
should apply to every workspace member.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.adapters.typescript.workspace_scripts import (
    check_workspace_scripts,
    derive_required_scripts,
)


class TestDeriveRequiredScripts(unittest.TestCase):
    """Parsing ``(script_name, scoped_path | None)`` from gate commands."""

    def test_cd_prefix_scopes_to_path(self) -> None:
        scripts = derive_required_scripts(lint_cmd="cd apps/api && pnpm lint")
        self.assertEqual(scripts, frozenset({("lint", "apps/api")}))

    def test_bare_pnpm_is_unscoped(self) -> None:
        scripts = derive_required_scripts(lint_cmd="pnpm lint")
        self.assertEqual(scripts, frozenset({("lint", None)}))

    def test_recursive_flag_overrides_cd_prefix(self) -> None:
        """``cd X && pnpm -r lint`` is still workspace-wide."""
        scripts = derive_required_scripts(lint_cmd="cd apps/api && pnpm -r lint")
        self.assertEqual(scripts, frozenset({("lint", None)}))

    def test_recursive_long_flag_overrides_cd_prefix(self) -> None:
        scripts = derive_required_scripts(
            lint_cmd="cd apps/api && pnpm --recursive lint",
        )
        self.assertEqual(scripts, frozenset({("lint", None)}))

    def test_filter_flag_equals_form_overrides_cd_prefix(self) -> None:
        """``--filter=<val>`` (equals form) is eaten by the flag regex."""
        scripts = derive_required_scripts(
            lint_cmd="cd apps/api && pnpm --filter=@pkg/foo lint",
        )
        self.assertEqual(scripts, frozenset({("lint", None)}))

    def test_filter_flag_two_token_form_scoped_package_value(self) -> None:
        """``pnpm --filter @pkg/foo lint`` — the two-token form with a
        scoped-package value. Previously silently fell through to
        defaults because ``@pkg/foo`` broke the script-name regex; the
        preprocessing strip now removes the flag-value pair so ``lint``
        is captured correctly as unscoped.
        """
        scripts = derive_required_scripts(
            lint_cmd="pnpm --filter @pkg/foo lint",
        )
        self.assertEqual(scripts, frozenset({("lint", None)}))

    def test_filter_flag_two_token_form_plain_value(self) -> None:
        scripts = derive_required_scripts(lint_cmd="pnpm --filter api lint")
        self.assertEqual(scripts, frozenset({("lint", None)}))

    def test_pnpm_run_scoped(self) -> None:
        scripts = derive_required_scripts(test_cmd="cd apps/api && pnpm run test")
        self.assertEqual(scripts, frozenset({("test", "apps/api")}))

    def test_empty_commands_return_defaults(self) -> None:
        scripts = derive_required_scripts()
        names = {name for name, _ in scripts}
        scopes = {scope for _, scope in scripts}
        self.assertEqual(names, {"lint", "test", "typecheck"})
        self.assertEqual(scopes, {None})  # all defaults are unscoped

    def test_multiple_commands_each_scoped_independently(self) -> None:
        scripts = derive_required_scripts(
            lint_cmd="cd apps/api && pnpm lint",
            typecheck_cmd="pnpm -r typecheck",
            test_cmd="cd packages/shared && pnpm test",
        )
        self.assertEqual(
            scripts,
            frozenset({
                ("lint", "apps/api"),
                ("typecheck", None),
                ("test", "packages/shared"),
            }),
        )

    def test_skips_non_script_subcommands(self) -> None:
        """``pnpm install`` etc. are not script invocations."""
        scripts = derive_required_scripts(lint_cmd="pnpm install")
        # Falls back to defaults because no real script was parsed.
        self.assertIn(("lint", None), scripts)

    def test_pnpm_exec_not_captured_as_script(self) -> None:
        """``pnpm exec eslint .`` — exec is not a script."""
        scripts = derive_required_scripts(lint_cmd="pnpm exec eslint .")
        # ``exec`` must not appear as a captured script name.
        self.assertNotIn("exec", {name for name, _ in scripts})

    def test_pnpm_run_with_recursive_flag_captures_script(self) -> None:
        """``pnpm run -r lint`` must capture ``lint``, not ``run``.

        Previous parser backtracked and captured ``run`` as the script
        name. The improved regex eats flags on both sides of an optional
        ``run`` so the real script name is captured; ``run`` is also in
        the blocklist as a defensive backstop.
        """
        scripts = derive_required_scripts(lint_cmd="pnpm run -r lint")
        self.assertEqual(scripts, frozenset({("lint", None)}))
        self.assertNotIn("run", {name for name, _ in scripts})

    def test_unhandled_scoping_flag_logged(self) -> None:
        """``pnpm -C <path>`` is logged and the captured set is sane.

        Previously the parser captured ``apps`` (the path prefix) as a
        "script" — worse than the original bug. The preprocessing strip
        removes ``-C apps/api`` before script extraction so ``lint`` is
        captured instead. The scope is still None (we don't honor the
        ``-C`` scoping) but at least we don't propagate a garbage name.
        """
        with self.assertLogs(
            "darkfactory.adapters.typescript.workspace_scripts", level="WARNING",
        ) as cm:
            scripts = derive_required_scripts(lint_cmd="pnpm -C apps/api lint")
        self.assertTrue(
            any("unrecognized scoping form" in msg for msg in cm.output),
            cm.output,
        )
        # Must capture the real script name, not the path fragment.
        names = {name for name, _ in scripts}
        self.assertIn("lint", names)
        self.assertNotIn("apps", names)
        # Must treat as unscoped — warning says we're not honoring -C.
        self.assertTrue(all(scope is None for _, scope in scripts), scripts)

    def test_dash_dir_scoping_flag_logged(self) -> None:
        """``pnpm --dir <path>`` receives the same treatment as ``-C``."""
        with self.assertLogs(
            "darkfactory.adapters.typescript.workspace_scripts", level="WARNING",
        ) as cm:
            scripts = derive_required_scripts(
                lint_cmd="pnpm --dir packages/shared lint",
            )
        self.assertTrue(
            any("unrecognized scoping form" in msg for msg in cm.output),
            cm.output,
        )
        names = {name for name, _ in scripts}
        self.assertIn("lint", names)
        self.assertNotIn("packages", names)

    def test_cd_path_trailing_slash_normalized(self) -> None:
        """``cd apps/api/ && ...`` must match ``apps/api`` in member paths."""
        scripts = derive_required_scripts(lint_cmd="cd apps/api/ && pnpm lint")
        self.assertEqual(scripts, frozenset({("lint", "apps/api")}))

    def test_cd_path_dot_prefix_normalized(self) -> None:
        """``cd ./apps/api && ...`` must match ``apps/api`` in member paths."""
        scripts = derive_required_scripts(lint_cmd="cd ./apps/api && pnpm lint")
        self.assertEqual(scripts, frozenset({("lint", "apps/api")}))


class TestDeriveRequiredScriptsRootResolution(unittest.TestCase):
    """``root_scripts`` resolution: a bare ``pnpm <name>`` requires per-member
    scripts only when the ROOT script body delegates/opaque; a self-contained
    root-level body (``eslint .``) requires nothing."""

    def test_root_level_aggregator_dropped(self) -> None:
        # eslint ./tsc -b/biome check . — self-contained root-level → no member req.
        for body in ("eslint .", "tsc -b", "biome check .", "eslint . && tsc -b"):
            with self.subTest(body=body):
                self.assertEqual(
                    derive_required_scripts(
                        lint_cmd="pnpm lint", root_scripts={"lint": body},
                    ),
                    frozenset(),
                )

    def test_bb_scenario_lint_not_required(self) -> None:
        # Root eslint . (root-level) + recursive typecheck → only typecheck per-member.
        self.assertEqual(
            derive_required_scripts(
                lint_cmd="pnpm lint", typecheck_cmd="pnpm typecheck",
                root_scripts={
                    "lint": "eslint .",
                    "typecheck": "pnpm -r --parallel run typecheck",
                },
            ),
            frozenset({("typecheck", None)}),
        )

    def test_pnpm_recursive_root_body_requires_members(self) -> None:
        self.assertEqual(
            derive_required_scripts(
                lint_cmd="pnpm lint", root_scripts={"lint": "pnpm -r run lint"},
            ),
            frozenset({("lint", None)}),
        )

    def test_opaque_and_delegating_runners_require_members(self) -> None:
        # turbo/nx/lerna delegate; make/node/bash are opaque — all fail-closed
        # to require-in-all (a member missing the script is a real misconfig).
        for body in (
            "turbo run lint", "nx run-many -t lint", "lerna run lint",
            "make lint", "node scripts/lint-all.mjs", "concurrently 'eslint .'",
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    derive_required_scripts(
                        lint_cmd="pnpm lint", root_scripts={"lint": body},
                    ),
                    frozenset({("lint", None)}),
                )

    def test_absent_root_script_is_conservative(self) -> None:
        self.assertEqual(
            derive_required_scripts(lint_cmd="pnpm lint", root_scripts={}),
            frozenset({("lint", None)}),
        )

    def test_none_root_scripts_keeps_legacy_behavior(self) -> None:
        self.assertEqual(
            derive_required_scripts(lint_cmd="pnpm lint"),
            frozenset({("lint", None)}),
        )

    def test_command_level_recursion_not_dropped_by_root_body(self) -> None:
        # `pnpm -r lint` itself recurses; a non-recursive same-named root body
        # must NOT cause it to be dropped.
        self.assertEqual(
            derive_required_scripts(
                lint_cmd="pnpm -r lint", root_scripts={"lint": "eslint ."},
            ),
            frozenset({("lint", None)}),
        )

    def test_all_root_level_returns_empty_not_default(self) -> None:
        # All commands parsed but dropped as root-level → empty, NOT the
        # test/lint/typecheck default (which would re-introduce the false-fail).
        self.assertEqual(
            derive_required_scripts(
                lint_cmd="pnpm lint", root_scripts={"lint": "eslint ."},
            ),
            frozenset(),
        )


class TestCheckWorkspaceScriptsScoping(unittest.TestCase):
    """End-to-end check: scoped scripts only affect the matching member."""

    def _make_project(self, tmp: Path, packages: dict[str, dict]) -> None:
        (tmp / "pnpm-workspace.yaml").write_text(
            "packages:\n" + "\n".join(f"  - '{p}'" for p in packages),
            encoding="utf-8",
        )
        for rel, pkg_json in packages.items():
            member_dir = tmp / rel
            member_dir.mkdir(parents=True, exist_ok=True)
            (member_dir / "package.json").write_text(
                json.dumps(pkg_json), encoding="utf-8",
            )

    def test_scoped_script_not_required_in_siblings(self) -> None:
        """The regression: ``cd apps/api && pnpm lint`` must not demand
        ``lint`` in ``packages/shared`` or ``apps/web``.
        """
        with TemporaryDirectory() as td:
            tmp = Path(td)
            self._make_project(tmp, {
                "apps/api": {"name": "api", "scripts": {"lint": "eslint"}},
                "apps/web": {"name": "web", "scripts": {"build": "tsc"}},
                "packages/shared": {"name": "shared", "scripts": {"build": "tsc"}},
            })
            result = check_workspace_scripts(
                tmp, frozenset({("lint", "apps/api")}),
            )
            self.assertTrue(result.passed, result.errors)

    def test_scoped_script_still_enforced_on_its_own_package(self) -> None:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            self._make_project(tmp, {
                "apps/api": {"name": "api", "scripts": {"build": "tsc"}},
                # apps/api is missing the scoped 'lint' script
            })
            result = check_workspace_scripts(
                tmp, frozenset({("lint", "apps/api")}),
            )
            self.assertFalse(result.passed)
            self.assertEqual(result.errors, [("apps/api", ["lint"])])

    def test_unscoped_script_required_in_all(self) -> None:
        """Unscoped scripts must still trip the check when missing anywhere."""
        with TemporaryDirectory() as td:
            tmp = Path(td)
            self._make_project(tmp, {
                "apps/api": {"name": "api", "scripts": {"lint": "eslint"}},
                "apps/web": {"name": "web", "scripts": {"build": "tsc"}},
            })
            result = check_workspace_scripts(
                tmp, frozenset({("lint", None)}),
            )
            self.assertFalse(result.passed)
            self.assertEqual(
                {pkg for pkg, _ in result.errors}, {"apps/web"},
            )

    def test_mixed_scoped_and_unscoped(self) -> None:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            self._make_project(tmp, {
                "apps/api": {
                    "name": "api",
                    "scripts": {"lint": "eslint", "typecheck": "tsc --noEmit"},
                },
                "packages/shared": {
                    "name": "shared",
                    "scripts": {"typecheck": "tsc --noEmit"},
                    # no 'lint' — must pass because lint is scoped to api
                },
            })
            result = check_workspace_scripts(
                tmp, frozenset({
                    ("lint", "apps/api"),
                    ("typecheck", None),
                }),
            )
            self.assertTrue(result.passed, result.errors)

    def test_empty_scripts_short_circuits(self) -> None:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            self._make_project(tmp, {
                "apps/api": {"name": "api", "scripts": {}},
            })
            result = check_workspace_scripts(tmp, frozenset())
            self.assertTrue(result.passed)

    def test_no_workspace_yaml_short_circuits(self) -> None:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            result = check_workspace_scripts(
                tmp, frozenset({("lint", None)}),
            )
            self.assertTrue(result.passed)


if __name__ == "__main__":
    unittest.main()
