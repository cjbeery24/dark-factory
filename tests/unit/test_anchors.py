"""Unit tests for Phase 6 anchor-file staging (darkfactory.pipeline.anchors)."""

from __future__ import annotations

import unittest
from pathlib import Path

from darkfactory.pipeline.anchors import AnchorPaths, apply_anchors
from tests.helpers import TempDirTestCase


class TestAnchorPaths(unittest.TestCase):
    def test_any_set_false_when_all_none(self) -> None:
        self.assertFalse(AnchorPaths().any_set())

    def test_any_set_true_when_one_set(self) -> None:
        self.assertTrue(
            AnchorPaths(manifest=Path("/x.yaml")).any_set()
        )
        self.assertTrue(
            AnchorPaths(scenarios=Path("/dir")).any_set()
        )
        self.assertTrue(
            AnchorPaths(environment=Path("/env.toml")).any_set()
        )


class TestApplyAnchors(TempDirTestCase):
    def _project(self) -> Path:
        p = self.tmp_dir / "proj"
        (p / ".dark-factory").mkdir(parents=True)
        return p

    def test_noop_when_no_anchors_set(self) -> None:
        project = self._project()
        # Should not raise and should not create any extra files.
        apply_anchors(AnchorPaths(), project, "demo")
        self.assertFalse((project / ".dark-factory" / "architecture").exists())

    def test_anchor_manifest_copies_file(self) -> None:
        project = self._project()
        src = self.tmp_dir / "prior-manifest.yaml"
        src.write_text("key: value\n", encoding="utf-8")
        apply_anchors(
            AnchorPaths(manifest=src), project, "demo",
        )
        dest = (
            project / ".dark-factory" / "architecture" / "demo-manifest.yaml"
        )
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_text(), "key: value\n")

    def test_anchor_scenarios_copies_directory_recursively(self) -> None:
        project = self._project()
        src = self.tmp_dir / "prior-scenarios"
        src.mkdir()
        (src / "a.test.ts").write_text("// a", encoding="utf-8")
        (src / "sub").mkdir()
        (src / "sub" / "b.test.ts").write_text("// b", encoding="utf-8")
        apply_anchors(AnchorPaths(scenarios=src), project, "demo")
        dest_root = project / ".dark-factory" / "scenarios" / "demo"
        self.assertTrue((dest_root / "a.test.ts").exists())
        self.assertTrue((dest_root / "sub" / "b.test.ts").exists())
        self.assertEqual(
            (dest_root / "a.test.ts").read_text(), "// a",
        )

    def test_anchor_scenarios_overwrites_existing(self) -> None:
        project = self._project()
        existing = project / ".dark-factory" / "scenarios" / "demo"
        existing.mkdir(parents=True)
        (existing / "stale.test.ts").write_text("stale", encoding="utf-8")
        src = self.tmp_dir / "prior-scenarios"
        src.mkdir()
        (src / "fresh.test.ts").write_text("fresh", encoding="utf-8")
        apply_anchors(AnchorPaths(scenarios=src), project, "demo")
        # Stale file must be gone after overwrite.
        self.assertFalse((existing / "stale.test.ts").exists())
        self.assertTrue((existing / "fresh.test.ts").exists())

    def test_anchor_environment_copies_file(self) -> None:
        project = self._project()
        src = self.tmp_dir / "prior-env.toml"
        src.write_text("[build]\n", encoding="utf-8")
        apply_anchors(AnchorPaths(environment=src), project, "demo")
        dest = (
            project / ".dark-factory" / "architecture" / "environment.toml"
        )
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_text(), "[build]\n")

    def test_missing_manifest_source_raises_before_staging(self) -> None:
        project = self._project()
        # Use a real env source so we can verify it was NOT staged because
        # the pre-flight existence check fires before the staging loop.
        env_src = self.tmp_dir / "env.toml"
        env_src.write_text("x=1\n", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            apply_anchors(
                AnchorPaths(
                    manifest=self.tmp_dir / "does-not-exist.yaml",
                    environment=env_src,
                ),
                project, "demo",
            )
        self.assertFalse(
            (project / ".dark-factory" / "architecture" / "environment.toml").exists()
        )

    def test_scenarios_must_be_directory(self) -> None:
        project = self._project()
        src = self.tmp_dir / "not-a-dir.txt"
        src.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            apply_anchors(AnchorPaths(scenarios=src), project, "demo")

    def test_progress_callback_invoked_per_anchor(self) -> None:
        project = self._project()
        m_src = self.tmp_dir / "m.yaml"
        m_src.write_text("", encoding="utf-8")
        e_src = self.tmp_dir / "e.toml"
        e_src.write_text("", encoding="utf-8")
        messages: list[str] = []
        apply_anchors(
            AnchorPaths(manifest=m_src, environment=e_src),
            project, "demo",
            progress=messages.append,
        )
        # manifest + substrate-warning + environment (no sibling
        # substrate-manifests dir so the warning path fires for manifest).
        self.assertTrue(any("[V-anchor] manifest" in m for m in messages))
        self.assertTrue(any("[V-anchor] environment" in m for m in messages))

    def test_anchor_manifest_auto_seeds_substrate_siblings(self) -> None:
        """--anchor-manifest with sibling substrate-manifests/ dir copies
        every file in it into the project's architecture/substrate-manifests/.
        """
        project = self._project()
        # Simulate an archived run layout:
        # <anchor_dir>/
        #   demo-manifest.yaml   <-- anchor source
        #   substrate-manifests/
        #     drizzle.yaml
        #     vitest.yaml
        anchor_dir = self.tmp_dir / "archived"
        anchor_dir.mkdir()
        manifest_src = anchor_dir / "demo-manifest.yaml"
        manifest_src.write_text("manifest: 1\n", encoding="utf-8")
        substrate_src = anchor_dir / "substrate-manifests"
        substrate_src.mkdir()
        (substrate_src / "drizzle.yaml").write_text("name: drizzle\n", encoding="utf-8")
        (substrate_src / "vitest.yaml").write_text("name: vitest\n", encoding="utf-8")

        apply_anchors(AnchorPaths(manifest=manifest_src), project, "demo")

        substrate_dest = (
            project / ".dark-factory" / "architecture" / "substrate-manifests"
        )
        self.assertTrue(substrate_dest.is_dir())
        self.assertEqual(
            (substrate_dest / "drizzle.yaml").read_text(), "name: drizzle\n"
        )
        self.assertEqual(
            (substrate_dest / "vitest.yaml").read_text(), "name: vitest\n"
        )

    def test_anchor_manifest_warns_when_substrate_sibling_missing(self) -> None:
        """--anchor-manifest without a sibling substrate-manifests/ dir emits
        a warning via the progress callback but does not raise. The pipeline
        proceeds; the architect may still succeed if substrates can be
        re-derived.
        """
        project = self._project()
        manifest_src = self.tmp_dir / "solo-manifest.yaml"
        manifest_src.write_text("manifest: 1\n", encoding="utf-8")
        messages: list[str] = []
        apply_anchors(
            AnchorPaths(manifest=manifest_src), project, "demo",
            progress=messages.append,
        )
        # No substrate-manifests dir created when the sibling was absent.
        self.assertFalse(
            (
                project / ".dark-factory" / "architecture" / "substrate-manifests"
            ).exists()
        )
        self.assertTrue(
            any("WARNING" in m and "substrate-manifests" in m for m in messages),
            f"expected a substrate warning in messages, got: {messages!r}",
        )

    def test_anchor_manifest_substrate_ignores_subdirectories(self) -> None:
        """Only top-level files are copied. Subdirectories under the
        substrate-manifests source are not recursed — substrate manifests
        are produced as flat YAML files by design.
        """
        project = self._project()
        anchor_dir = self.tmp_dir / "archived"
        anchor_dir.mkdir()
        manifest_src = anchor_dir / "demo-manifest.yaml"
        manifest_src.write_text("", encoding="utf-8")
        substrate_src = anchor_dir / "substrate-manifests"
        substrate_src.mkdir()
        (substrate_src / "flat.yaml").write_text("ok\n", encoding="utf-8")
        nested = substrate_src / "nested"
        nested.mkdir()
        (nested / "deep.yaml").write_text("nope\n", encoding="utf-8")

        apply_anchors(AnchorPaths(manifest=manifest_src), project, "demo")

        dest = project / ".dark-factory" / "architecture" / "substrate-manifests"
        self.assertTrue((dest / "flat.yaml").exists())
        self.assertFalse((dest / "nested").exists())


if __name__ == "__main__":
    unittest.main()
