"""Tests for ``TypescriptNodeAdapter.check_workspace_consistency`` (M2).

The B-ii hook moves the pnpm workspace-script consistency check off a
direct core call onto an additive ``EcosystemAdapter`` hook. The TS
adapter reads the root ``package.json`` scripts map and resolves which
scripts are genuinely required per workspace member (a root-level
aggregating script like ``eslint .`` requires nothing per-member; a
recursively-delegating one like ``pnpm -r run typecheck`` requires the
script in every member).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.adapters.base import WorkspaceCheckResult
from darkfactory.adapters.typescript.adapter import TypescriptNodeAdapter


class TestCheckWorkspaceConsistency(unittest.TestCase):
    def _make_monorepo(
        self,
        tmp: Path,
        root_scripts: dict[str, str],
        members: dict[str, dict],
    ) -> None:
        """Lay down a root package.json + pnpm-workspace.yaml + members."""
        (tmp / "package.json").write_text(
            json.dumps({"name": "root", "scripts": root_scripts}),
            encoding="utf-8",
        )
        (tmp / "pnpm-workspace.yaml").write_text(
            "packages:\n  - 'apps/*'\n  - 'packages/*'\n",
            encoding="utf-8",
        )
        for rel, pkg in members.items():
            d = tmp / rel
            d.mkdir(parents=True, exist_ok=True)
            (d / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

    def test_bb_scenario_lint_non_recursive_passes(self) -> None:
        """The bb scenario: root ``lint: "eslint ."`` (non-recursive,
        runs once at root) + ``typecheck: "pnpm -r run typecheck"``
        (recursive). Members define ``typecheck`` but NOT ``lint``. The
        check must PASS — lint is not required per-member.
        """
        with TemporaryDirectory() as td:
            tmp = Path(td)
            self._make_monorepo(
                tmp,
                root_scripts={
                    "lint": "eslint .",
                    "typecheck": "pnpm -r run typecheck",
                },
                members={
                    "apps/api": {
                        "name": "api",
                        "scripts": {"typecheck": "tsc --noEmit"},
                    },
                    "packages/shared": {
                        "name": "shared",
                        "scripts": {"typecheck": "tsc --noEmit"},
                    },
                },
            )
            adapter = TypescriptNodeAdapter(project_dir=tmp)
            result = adapter.check_workspace_consistency(
                tmp,
                lint_cmd="pnpm lint",
                typecheck_cmd="pnpm typecheck",
                test_cmd="",
            )
            self.assertIsInstance(result, WorkspaceCheckResult)
            self.assertTrue(result.passed, result.errors)

    def test_member_missing_recursive_script_fails(self) -> None:
        """A member genuinely missing the recursively-required script
        (``typecheck``) must FAIL — that is the real misconfiguration
        the check exists to catch.
        """
        with TemporaryDirectory() as td:
            tmp = Path(td)
            self._make_monorepo(
                tmp,
                root_scripts={
                    "lint": "eslint .",
                    "typecheck": "pnpm -r run typecheck",
                },
                members={
                    "apps/api": {
                        "name": "api",
                        "scripts": {"typecheck": "tsc --noEmit"},
                    },
                    # packages/shared is missing the recursive 'typecheck'.
                    "packages/shared": {
                        "name": "shared",
                        "scripts": {"build": "tsc"},
                    },
                },
            )
            adapter = TypescriptNodeAdapter(project_dir=tmp)
            result = adapter.check_workspace_consistency(
                tmp,
                lint_cmd="pnpm lint",
                typecheck_cmd="pnpm typecheck",
                test_cmd="",
            )
            self.assertFalse(result.passed)
            self.assertEqual(
                {pkg for pkg, _ in result.errors}, {"packages/shared"},
            )
            missing = dict(result.errors)["packages/shared"]
            self.assertIn("typecheck", missing)
            self.assertNotIn("lint", missing)

    def test_no_workspace_yaml_passes(self) -> None:
        """A non-monorepo project (no pnpm-workspace.yaml) passes — the
        underlying walker self-guards on the workspace manifest."""
        with TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "package.json").write_text(
                json.dumps({"name": "solo", "scripts": {"lint": "eslint ."}}),
                encoding="utf-8",
            )
            adapter = TypescriptNodeAdapter(project_dir=tmp)
            result = adapter.check_workspace_consistency(
                tmp, "pnpm lint", "pnpm typecheck", "pnpm test",
            )
            self.assertTrue(result.passed, result.errors)

    def test_missing_root_package_json_is_conservative(self) -> None:
        """No root package.json → root_scripts={} → derive falls back to
        require-in-all, so a member missing ``lint`` FAILS (conservative,
        never a crash)."""
        with TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "pnpm-workspace.yaml").write_text(
                "packages:\n  - 'apps/*'\n", encoding="utf-8",
            )
            d = tmp / "apps/api"
            d.mkdir(parents=True)
            (d / "package.json").write_text(
                json.dumps({"name": "api", "scripts": {"build": "tsc"}}),
                encoding="utf-8",
            )
            adapter = TypescriptNodeAdapter(project_dir=tmp)
            result = adapter.check_workspace_consistency(
                tmp, "pnpm lint", "", "",
            )
            self.assertFalse(result.passed)
            self.assertIn("apps/api", {pkg for pkg, _ in result.errors})

    def test_invalid_root_package_json_does_not_crash(self) -> None:
        """A malformed root package.json is swallowed (root_scripts={}),
        not raised — the read guard must never crash the pre-flight."""
        with TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "package.json").write_text("{ not json", encoding="utf-8")
            (tmp / "pnpm-workspace.yaml").write_text(
                "packages:\n  - 'apps/*'\n", encoding="utf-8",
            )
            d = tmp / "apps/api"
            d.mkdir(parents=True)
            (d / "package.json").write_text(
                json.dumps({"name": "api", "scripts": {"lint": "eslint"}}),
                encoding="utf-8",
            )
            adapter = TypescriptNodeAdapter(project_dir=tmp)
            # Must not raise.
            result = adapter.check_workspace_consistency(
                tmp, "pnpm lint", "", "",
            )
            self.assertIsInstance(result, WorkspaceCheckResult)


if __name__ == "__main__":
    unittest.main()
