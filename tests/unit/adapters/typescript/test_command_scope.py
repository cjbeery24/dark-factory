"""Tests for M1 command-scope validation (Sprint 5 workspace anchor).

The fix: anchor command-scope consistency on the project's REAL declared
workspaces (drift-proof, machine-authoritative) when available, falling back to
source_dirs only when no workspaces are detected. This removes the false-positive
where the architect correctly targets the spec's workspace but the configured
source_dirs has drifted to a different one.
"""

from __future__ import annotations

import unittest

from darkfactory.adapters.typescript.adapter import TypescriptNodeAdapter


class TestCommandScopeWorkspaceAnchor(unittest.TestCase):
    def setUp(self) -> None:
        self.a = TypescriptNodeAdapter()

    def _codes(self, command, source_dirs, workspaces=None):
        return [f.code for f in self.a.validate_command_scope(
            command, source_dirs, workspaces=workspaces,
        )]

    # --- the regression: drifted source_dirs must NOT false-positive ---------
    def test_drifted_source_dirs_with_workspace_anchor_is_clean(self) -> None:
        # The exact failing case: spec in packages/shared, but source_dirs
        # drifted to apps/api/src. With the real workspaces as anchor, the
        # architect's correct `cd packages/shared` is consistent.
        self.assertEqual(
            self._codes(
                "cd packages/shared && pnpm install",
                ["apps/api/src"],
                workspaces=["apps/api", "apps/web", "packages/shared"],
            ),
            [],
        )

    def test_command_to_non_workspace_still_violates(self) -> None:
        # The guard still catches a command targeting a NON-declared workspace
        # (the real bug M1 exists for) — workspace anchor is not circular.
        self.assertEqual(
            self._codes(
                "cd apps/legacy && pnpm install",
                ["apps/api/src"],
                workspaces=["apps/api", "packages/shared"],
            ),
            ["monorepo-scope-mismatch"],
        )

    def test_parent_of_workspaces_violates(self) -> None:
        # `cd packages` is NOT a declared workspace and spans packages/shared +
        # packages/other — a cross-workspace command M1 must catch. The ancestor
        # relation is rejected for workspace anchors (Codex P1).
        self.assertEqual(
            self._codes(
                "cd packages && pnpm install",
                ["apps/api/src"],
                workspaces=["packages/shared", "packages/other"],
            ),
            ["monorepo-scope-mismatch"],
        )

    def test_source_dirs_fallback_allows_ancestor(self) -> None:
        # In the source_dirs fallback (no workspaces), a target that is an
        # ANCESTOR of a source_dir is still consistent (`apps/api` ⊇ apps/api/src).
        self.assertEqual(
            self._codes("cd apps/api && pnpm build", ["apps/api/src"]),
            [],
        )

    def test_subdir_of_workspace_is_consistent(self) -> None:
        self.assertEqual(
            self._codes(
                "cd packages/shared/src && pnpm build",
                ["apps/api/src"],
                workspaces=["packages/shared"],
            ),
            [],
        )

    def test_pnpm_filter_path_anchored_on_workspaces(self) -> None:
        self.assertEqual(
            self._codes(
                "pnpm --filter ./packages/shared test",
                ["apps/api/src"],
                workspaces=["packages/shared"],
            ),
            [],
        )

    # --- fallback: no workspaces detected → source_dirs behavior (unchanged) --
    def test_fallback_to_source_dirs_when_no_workspaces(self) -> None:
        # No workspaces (non-monorepo): drift against source_dirs still violates.
        self.assertEqual(
            self._codes("cd packages/shared && pnpm install", ["apps/api/src"]),
            ["monorepo-scope-mismatch"],
        )

    def test_fallback_correct_source_dirs_is_clean(self) -> None:
        self.assertEqual(
            self._codes("cd packages/shared && pnpm install", ["packages/shared/src"]),
            [],
        )

    # --- guards ---------------------------------------------------------------
    def test_empty_command_clean(self) -> None:
        self.assertEqual(self._codes("", ["apps/api/src"], workspaces=["apps/api"]), [])

    def test_no_anchors_at_all_skips(self) -> None:
        # Neither source_dirs nor workspaces → nothing to validate against.
        self.assertEqual(self._codes("cd whatever && pnpm i", [], workspaces=[]), [])

    def test_command_with_no_scope_target_clean(self) -> None:
        # A plain command with no cd/--filter target is always clean.
        self.assertEqual(
            self._codes("pnpm install", ["apps/api/src"], workspaces=["packages/shared"]),
            [],
        )


if __name__ == "__main__":
    unittest.main()
