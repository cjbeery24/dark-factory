"""Unit tests for ``_architect_amend_entry_points``.

Exercises the manifest preserve-and-extend contract in
``docs/dev/2026-04-15-coverage-gap-architect-amend.md`` §D3: every
pre-existing entry_point and every other top-level section must be
preserved, new entries' paths must resolve against ``file_map``, and
colliding specifiers must be rejected with the pre-amend manifest
restored on disk.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import SpecRun
from darkfactory.pipeline.orchestrator import _architect_amend_entry_points
from darkfactory.scenarios.coverage import (
    GAP_CATEGORY_ARCHITECT,
    CoverageGap,
)


BASE_MANIFEST = {
    "spec_hash": "abc123",
    # Canonical architect output: list of {path, action, ...} dicts.
    "file_map": [
        {"path": "apps/api/src/db/index.ts", "action": "create", "satisfies": ["FR-1"]},
        {"path": "apps/api/src/db/health-check.ts", "action": "create", "satisfies": ["FR-4"]},
    ],
    "commands": {"test": "pnpm test"},
    "runtime": {"language": "node"},
    "dependencies": [],
    "test_integration": {
        "entry_points": [
            {
                "path": "apps/api/src/db/index.ts",
                "specifier": "@budget/api/db",
                "exports": ["createDb"],
            },
        ],
    },
}


def _write_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _call_amend(
    tmp: Path,
    fake_agent: "callable[[dict], dict]",
    gaps: list[CoverageGap],
    exit_code: int = 0,
) -> tuple[str, str, list[str]]:
    """Helper: set up project dir, invoke _architect_amend_entry_points with
    a mocked architect that transforms the manifest via ``fake_agent``.

    ``fake_agent`` takes the pre-amend manifest dict and returns the
    post-amend manifest dict; the mock writes it to disk.
    """
    project_dir = tmp
    manifest_path = project_dir / ".dark-factory" / "architecture" / "spec-manifest.yaml"
    spec_file = project_dir / ".dark-factory" / "specs" / "spec.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        "# Spec\n\n## FR-4: Register database health check\nDescribe.\n",
        encoding="utf-8",
    )
    _write_manifest(manifest_path, BASE_MANIFEST)

    def _invoke_agent_mock(role, prompt, **kwargs):
        # Simulate the architect modifying the manifest on disk.
        if exit_code == 0:
            pre = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            post = fake_agent(pre)
            _write_manifest(manifest_path, post)
        return AgentResult(role=AgentRole.ARCHITECT, exit_code=exit_code)

    spec_run = SpecRun(spec_name="spec", run_id="r1", spec_hash="h", spec_type="feature")
    config = PipelineConfig()
    barriers = {}

    with patch(
        "darkfactory.pipeline.orchestrator.invoke_agent",
        side_effect=_invoke_agent_mock,
    ):
        return _architect_amend_entry_points(
            spec_run, config, barriers, project_dir,
            gaps=gaps,
            spec_file=spec_file,
            manifest_path=manifest_path,
            amend_number=1,
            events_file=None,
            audit_path=None,
            dry_run=False,
        )


class TestArchitectAmendHappyPath(unittest.TestCase):
    def test_adds_new_entry_point_success(self) -> None:
        with TemporaryDirectory() as tmp:
            def add_health_check(pre: dict) -> dict:
                post = dict(pre)
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/health-check.ts",
                            "specifier": "@budget/api/db/health-check",
                            "exports": ["checkDatabase"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "missing", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            outcome, reason, new_specs = _call_amend(
                Path(tmp), add_health_check, gaps,
            )
            self.assertEqual(outcome, "success")
            self.assertEqual(reason, "")
            self.assertEqual(new_specs, ["@budget/api/db/health-check"])

    def test_no_new_entries_is_no_progress(self) -> None:
        with TemporaryDirectory() as tmp:
            def identity(pre: dict) -> dict:
                return pre

            gaps = [CoverageGap(
                "FR-4", "missing", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            outcome, _, new_specs = _call_amend(Path(tmp), identity, gaps)
            self.assertEqual(outcome, "no_progress")
            self.assertEqual(new_specs, [])

    def test_new_entry_unrelated_to_requested_is_no_progress(self) -> None:
        with TemporaryDirectory() as tmp:
            def add_other(pre: dict) -> dict:
                post = dict(pre)
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/index.ts",
                            "specifier": "@budget/api/db/alt",
                            "exports": ["createDb"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "missing", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            outcome, _, new_specs = _call_amend(Path(tmp), add_other, gaps)
            self.assertEqual(outcome, "no_progress")
            self.assertEqual(new_specs, ["@budget/api/db/alt"])


class TestArchitectAmendValidation(unittest.TestCase):
    def _assert_pre_amend_restored(
        self, tmp: Path, fake_agent: "callable[[dict], dict]",
        gaps: list[CoverageGap],
        expected_substring: str,
    ) -> None:
        manifest_path = tmp / ".dark-factory" / "architecture" / "spec-manifest.yaml"
        outcome, reason, new_specs = _call_amend(tmp, fake_agent, gaps)
        self.assertEqual(outcome, "validation_failed")
        self.assertIn(expected_substring, reason)
        self.assertEqual(new_specs, [])
        # Pre-amend manifest is restored on disk.
        restored = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(restored, BASE_MANIFEST)

    def test_top_level_section_modified_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            def mutate_commands(pre: dict) -> dict:
                post = dict(pre)
                post["commands"] = {"test": "npm test"}  # changed
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/health-check.ts",
                            "specifier": "@new/spec",
                            "exports": ["checkDatabase"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            self._assert_pre_amend_restored(
                Path(tmp), mutate_commands, gaps,
                "top-level section `commands` was modified",
            )

    def test_added_off_schema_top_level_key_rejected(self) -> None:
        # Codex nit: an amend that ADDS a new off-schema top-level key (the
        # bb/TS drift shape, e.g. `files`) must be rejected and the pre-amend
        # manifest restored — the round-trip loop alone only checks pre keys.
        with TemporaryDirectory() as tmp:
            def add_drift_key(pre: dict) -> dict:
                post = dict(pre)
                post["files"] = [{"path": "x.ts", "action": "create"}]  # off-schema
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/health-check.ts",
                            "specifier": "@new/spec",
                            "exports": ["checkDatabase"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            self._assert_pre_amend_restored(
                Path(tmp), add_drift_key, gaps,
                "added non-canonical top-level key(s)",
            )

    def test_top_level_section_removed_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            def remove_runtime(pre: dict) -> dict:
                post = {k: v for k, v in pre.items() if k != "runtime"}
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/health-check.ts",
                            "specifier": "@new/spec",
                            "exports": ["checkDatabase"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            self._assert_pre_amend_restored(
                Path(tmp), remove_runtime, gaps,
                "top-level section `runtime` was removed",
            )

    def test_existing_entry_modified_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            def rewrite_existing(pre: dict) -> dict:
                post = dict(pre)
                post["test_integration"] = {
                    "entry_points": [
                        {
                            "path": "apps/api/src/db/index.ts",
                            "specifier": "@budget/api/db-RENAMED",  # changed!
                            "exports": ["createDb"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            self._assert_pre_amend_restored(
                Path(tmp), rewrite_existing, gaps,
                "pre-existing entry_point was modified or removed",
            )

    def test_new_entry_path_not_in_file_map_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            def add_unknown_path(pre: dict) -> dict:
                post = dict(pre)
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/nonexistent.ts",
                            "specifier": "@new/spec",
                            "exports": ["foo"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            self._assert_pre_amend_restored(
                Path(tmp), add_unknown_path, gaps,
                "not in file_map",
            )

    def test_specifier_collision_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            def collide_specifier(pre: dict) -> dict:
                post = dict(pre)
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/health-check.ts",
                            "specifier": "@budget/api/db",  # collision!
                            "exports": ["checkDatabase"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            self._assert_pre_amend_restored(
                Path(tmp), collide_specifier, gaps,
                "collides with existing",
            )

    def test_duplicate_specifier_within_amend_rejected(self) -> None:
        """Two new entries with the same specifier both collide with each
        other — the validator must refuse even though neither collides with
        a pre-existing entry."""
        with TemporaryDirectory() as tmp:
            def add_two_with_same_specifier(pre: dict) -> dict:
                post = dict(pre)
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/health-check.ts",
                            "specifier": "@budget/api/db/health-check",
                            "exports": ["checkDatabase"],
                        },
                        {
                            "path": "apps/api/src/db/index.ts",
                            "specifier": "@budget/api/db/health-check",  # dup!
                            "exports": ["createDb"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            self._assert_pre_amend_restored(
                Path(tmp), add_two_with_same_specifier, gaps,
                "duplicated within amend",
            )

    def test_missing_specifier_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            def add_without_specifier(pre: dict) -> dict:
                post = dict(pre)
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/health-check.ts",
                            "specifier": "",  # empty!
                            "exports": ["checkDatabase"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            self._assert_pre_amend_restored(
                Path(tmp), add_without_specifier, gaps,
                "empty or not a string",
            )

    def test_missing_path_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            def add_without_path(pre: dict) -> dict:
                post = dict(pre)
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "",
                            "specifier": "@new/spec",
                            "exports": ["x"],
                        },
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            self._assert_pre_amend_restored(
                Path(tmp), add_without_path, gaps,
                "empty or not a string",
            )

    def test_architect_exit_nonzero_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            def noop(pre: dict) -> dict:
                return pre

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            manifest_path = Path(tmp) / ".dark-factory" / "architecture" / "spec-manifest.yaml"
            outcome, reason, _ = _call_amend(
                Path(tmp), noop, gaps, exit_code=2,
            )
            self.assertEqual(outcome, "validation_failed")
            self.assertIn("exited with code 2", reason)
            # Pre-amend manifest preserved.
            restored = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(restored, BASE_MANIFEST)


class TestArchitectAmendFileMapShapes(unittest.TestCase):
    """file_map can be a list of ``{path, ...}`` dicts (canonical architect
    output) or a dict keyed by path (defensive fallback). Both must let a
    valid new entry through and both must reject a path not present."""

    def test_dict_form_file_map_accepts_valid_path(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest_path = tmp_path / ".dark-factory" / "architecture" / "spec-manifest.yaml"
            spec_file = tmp_path / ".dark-factory" / "specs" / "spec.md"
            spec_file.parent.mkdir(parents=True, exist_ok=True)
            spec_file.write_text("# Spec\n", encoding="utf-8")

            dict_manifest = {
                **BASE_MANIFEST,
                "file_map": {
                    "apps/api/src/db/index.ts": {"action": "create"},
                    "apps/api/src/db/health-check.ts": {"action": "create"},
                },
            }
            _write_manifest(manifest_path, dict_manifest)

            def add_hc(pre: dict) -> dict:
                post = dict(pre)
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/health-check.ts",
                            "specifier": "@budget/api/db/health-check",
                            "exports": ["checkDatabase"],
                        },
                    ],
                }
                return post

            def _invoke_agent_mock(role, prompt, **kwargs):
                pre = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                post = add_hc(pre)
                _write_manifest(manifest_path, post)
                return AgentResult(role=AgentRole.ARCHITECT, exit_code=0)

            spec_run = SpecRun(
                spec_name="spec", run_id="r1", spec_hash="h", spec_type="feature",
            )
            gaps = [CoverageGap(
                "FR-4", "missing", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            with patch(
                "darkfactory.pipeline.orchestrator.invoke_agent",
                side_effect=_invoke_agent_mock,
            ):
                outcome, reason, new_specs = _architect_amend_entry_points(
                    spec_run, PipelineConfig(), {}, tmp_path,
                    gaps=gaps, spec_file=spec_file, manifest_path=manifest_path,
                    amend_number=1, events_file=None, audit_path=None, dry_run=False,
                )
            self.assertEqual(outcome, "success")
            self.assertEqual(new_specs, ["@budget/api/db/health-check"])


class TestArchitectAmendKeyOrderTolerance(unittest.TestCase):
    def test_key_reorder_accepted(self) -> None:
        """Architect reorders top-level keys but preserves their values."""
        with TemporaryDirectory() as tmp:
            def reorder_keys(pre: dict) -> dict:
                # Rebuild the dict in different insertion order.
                reordered = {}
                for key in reversed(list(pre.keys())):
                    reordered[key] = pre[key]
                reordered["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {
                            "path": "apps/api/src/db/health-check.ts",
                            "specifier": "@budget/api/db/health-check",
                            "exports": ["checkDatabase"],
                        },
                    ],
                }
                return reordered

            gaps = [CoverageGap(
                "FR-4", "x", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            outcome, _, new_specs = _call_amend(Path(tmp), reorder_keys, gaps)
            self.assertEqual(outcome, "success")
            self.assertEqual(new_specs, ["@budget/api/db/health-check"])


class TestArchitectAmendAdditiveFileMap(unittest.TestCase):
    """Part C: the PRESERVE check allows ADDITIVE file_map entries. Adding an
    entry_point for a module not yet in file_map REQUIRES adding that module's
    file_map entry; a strict byte-equal file_map check made such amends (e.g.
    the AC-8 logger entry-point) impossible. Modify/remove of an existing entry
    is still rejected."""

    def test_additive_file_map_entry_for_new_module_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            def add_logger(pre: dict) -> dict:
                post = dict(pre)
                post["file_map"] = pre["file_map"] + [
                    {"path": "apps/api/src/common/logger.ts",
                     "action": "create", "satisfies": ["AC-8"]},
                ]
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {"path": "apps/api/src/common/logger.ts",
                         "specifier": "@budget/api/logger",
                         "exports": ["logger"]},
                    ],
                }
                return post

            gaps = [CoverageGap(
                "AC-8", "missing", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/common/logger.ts",
            )]
            outcome, reason, new_specs = _call_amend(Path(tmp), add_logger, gaps)
            self.assertEqual(outcome, "success", reason)
            self.assertEqual(new_specs, ["@budget/api/logger"])

    def test_modifying_existing_file_map_entry_still_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            def mutate_existing(pre: dict) -> dict:
                post = dict(pre)
                fm = [dict(e) for e in pre["file_map"]]
                fm[0]["action"] = "modify"  # mutate a pre-existing entry
                post["file_map"] = fm
                post["test_integration"] = {
                    "entry_points": pre["test_integration"]["entry_points"] + [
                        {"path": "apps/api/src/db/health-check.ts",
                         "specifier": "@budget/api/db/hc", "exports": ["x"]},
                    ],
                }
                return post

            gaps = [CoverageGap(
                "FR-4", "missing", GAP_CATEGORY_ARCHITECT,
                "apps/api/src/db/health-check.ts",
            )]
            outcome, reason, _ = _call_amend(Path(tmp), mutate_existing, gaps)
            self.assertEqual(outcome, "validation_failed")
            self.assertIn("file_map", reason)


if __name__ == "__main__":
    unittest.main()
