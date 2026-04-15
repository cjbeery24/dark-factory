"""Unit tests for the Sprint 6 #2 Python + FastAPI adapter stub.

The shared adapter-contract battery at
``tests/adapters/test_adapter_contract.py`` already pins ABC-level
conformance. This file covers the concrete behavior the harness can't
reach:

- Detection predicate: requires both a Python manifest *and* a
  FastAPI mention; activating on just ``pyproject.toml`` would
  collide with future ``python-django`` / ``python-library`` adapters.
- Stub correctness: ``precheck`` returns ``[]``, not a lie;
  ``proposal_rehearsal_typecheck`` returns ``None`` (opt-out) rather
  than ``[]`` (which would mean "ran, found nothing").
- Quality-gate dispatch threads ``commands.*`` through without
  inventing Python command shapes — the architect is what knows
  those via the addendum.
- Graceful degradation on unreadable files, missing project_dir.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from darkfactory.adapters.python_fastapi import PythonFastApiAdapter
from darkfactory.spec.manifest import ArchitectureManifest
from tests.helpers import TempDirTestCase


class TestDetectionRequiresFastApiMention(TempDirTestCase):
    """The FastAPI-mention requirement is the whole point of scoping this
    adapter narrowly — a future ``python-django`` adapter must be able
    to coexist without detection conflict."""

    def test_pyproject_with_fastapi_dep_detects(self) -> None:
        (self.tmp_dir / "pyproject.toml").write_text(
            '[project]\n'
            'name = "svc"\n'
            'dependencies = ["fastapi>=0.110", "uvicorn"]\n'
        )
        self.assertTrue(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_requirements_txt_with_fastapi_detects(self) -> None:
        (self.tmp_dir / "requirements.txt").write_text(
            "fastapi==0.110.0\n"
            "uvicorn[standard]\n"
        )
        self.assertTrue(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_requirements_txt_with_fastapi_extras_detects(self) -> None:
        """``fastapi[all]`` is a common install shape — must still match."""
        (self.tmp_dir / "requirements.txt").write_text(
            "fastapi[all]>=0.110\n"
        )
        self.assertTrue(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_setup_py_with_fastapi_detects(self) -> None:
        (self.tmp_dir / "setup.py").write_text(
            'from setuptools import setup\n'
            'setup(install_requires=["fastapi", "uvicorn"])\n'
        )
        self.assertTrue(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_pyproject_without_fastapi_does_not_detect(self) -> None:
        """A plain Python library / CLI / Django project must NOT match
        — that's a different adapter's job."""
        (self.tmp_dir / "pyproject.toml").write_text(
            '[project]\n'
            'name = "lib"\n'
            'dependencies = ["requests", "pydantic"]\n'
        )
        self.assertFalse(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_fastapi_in_comment_does_not_detect(self) -> None:
        """A comment mentioning FastAPI must not trip detection — the
        regex requires a dependency-shape (version, extras, quote,
        whitespace) after the package name."""
        (self.tmp_dir / "requirements.txt").write_text(
            "# fastapi is great but we don't use it\n"
            "django==5.0\n"
        )
        self.assertFalse(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_fastapi_in_inline_comment_does_not_detect(self) -> None:
        """Regression: a Django project with an inline comment mentioning
        FastAPI (e.g. 'requests # installs fastapi as transitive') must
        not be claimed by this adapter. The detection-conflict argument
        with a future python-django adapter depends on this."""
        (self.tmp_dir / "requirements.txt").write_text(
            "django==5.0\n"
            "requests # installs fastapi as a transitive dependency\n"
        )
        self.assertFalse(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_fastapi_in_optional_dependencies_detects(self) -> None:
        """``[project.optional-dependencies]`` is a first-class declaration
        of use; a project that optionally depends on FastAPI is still a
        FastAPI project as far as this adapter's scope goes. Pinned so a
        future refactor can't silently drop it."""
        (self.tmp_dir / "pyproject.toml").write_text(
            '[project]\n'
            'name = "svc"\n'
            'dependencies = ["pydantic"]\n'
            '\n'
            '[project.optional-dependencies]\n'
            'api = ["fastapi>=0.110", "uvicorn"]\n'
        )
        self.assertTrue(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_fastapi_in_poetry_dev_dependencies_detects(self) -> None:
        """``[tool.poetry.dev-dependencies]`` is still a declaration of
        use (tests, scaffolding). Same reasoning as optional-dependencies
        — pin so it doesn't regress."""
        (self.tmp_dir / "pyproject.toml").write_text(
            '[tool.poetry]\n'
            'name = "svc"\n'
            '\n'
            '[tool.poetry.dev-dependencies]\n'
            'fastapi = "^0.110"\n'
        )
        self.assertTrue(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_unrelated_name_containing_fastapi_does_not_detect(self) -> None:
        """``not-fastapi`` or ``my-fastapi-plugin`` (with hyphenation
        preventing a word boundary before 'fastapi') must not match —
        the regex requires a whitespace / quote / bracket / comma
        before the name."""
        (self.tmp_dir / "requirements.txt").write_text(
            "not-fastapi-like==1.0\n"
        )
        self.assertFalse(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_no_python_manifest_does_not_detect(self) -> None:
        """No manifest at all → no detection, even if Python source
        exists."""
        (self.tmp_dir / "app.py").write_text("from fastapi import FastAPI\n")
        self.assertFalse(PythonFastApiAdapter().detect(self.tmp_dir))

    def test_missing_project_dir_returns_false(self) -> None:
        nonexistent = self.tmp_dir / "does_not_exist"
        self.assertFalse(PythonFastApiAdapter().detect(nonexistent))

    def test_project_dir_is_file_returns_false(self) -> None:
        """Defensive: if a caller passes a file path by mistake, don't
        raise into the registry loop."""
        file_path = self.tmp_dir / "not_a_dir"
        file_path.write_text("")
        self.assertFalse(PythonFastApiAdapter().detect(file_path))


class TestStubPrecheckReturnsEmpty(TempDirTestCase):
    """Precheck returns ``[]`` — the honest stub. A future real
    implementation can add findings; today we don't lie."""

    def test_returns_empty_list(self) -> None:
        from darkfactory.spec.spec_model import parse_spec_model

        spec_text = (
            "# Spec\n\n"
            "**Spec ID:** test\n\n"
            "## 1 Problem & Scope\n\nTest.\n\n"
            "## 2 Functional Requirements\n\n"
            "- FR-1: Test.\n\n"
            "## 3 Acceptance Criteria\n\n"
            "- AC-1: Test.\n"
        )
        spec = parse_spec_model(spec_text)
        findings = PythonFastApiAdapter().precheck(spec, self.tmp_dir)
        self.assertEqual(findings, [])


class TestStubConsistencyReturnsEmpty(unittest.TestCase):
    """No baseline consistency rules — project-hints supply those."""

    def test_base_returns_empty(self) -> None:
        self.assertEqual(PythonFastApiAdapter().consistency_rules(), [])

    def test_extra_rules_pass_through(self) -> None:
        import re
        from darkfactory.architecture.consistency import ConsistencyRule

        rule = ConsistencyRule(
            name="test-rule",
            env_paths=("commands.test",),
            spec_patterns=(re.compile(r"pytest"),),
            forbidden_env_patterns=(re.compile(r"unittest"),),
            description="test",
        )
        adapter = PythonFastApiAdapter(extra_consistency_rules=[rule])
        self.assertEqual(adapter.consistency_rules(), [rule])


class TestArchitectAddendum(unittest.TestCase):
    def test_addendum_non_empty(self) -> None:
        addendum = PythonFastApiAdapter().architect_addendum()
        self.assertTrue(addendum.strip())
        # Spot-check Python-specific content (not TS content).
        self.assertIn("FastAPI", addendum)
        self.assertIn("pyproject.toml", addendum)

    def test_addendum_never_mentions_node_or_pnpm(self) -> None:
        """Regression guard: the addendum must not leak TS/Node tokens.
        The whole reason this adapter exists is to keep Python specs
        from receiving Node guidance."""
        addendum = PythonFastApiAdapter().architect_addendum()
        lower = addendum.lower()
        for token in ("pnpm", "tsconfig", "vitest", "package.json", "npx"):
            self.assertNotIn(
                token, lower,
                f"Python addendum leaked Node token {token!r}",
            )


class TestQualityGateDispatch(unittest.TestCase):
    def test_threads_commands_from_manifest(self) -> None:
        manifest = ArchitectureManifest()
        manifest.runtime.amendment_ops = {
            "commands.install": "uv sync",
            "commands.test": "pytest apps/api/tests",
        }
        cmds = PythonFastApiAdapter().quality_gate_dispatch(manifest)
        self.assertEqual(cmds.install, "uv sync")
        self.assertEqual(cmds.test, "pytest apps/api/tests")
        # Unspecified phases default to empty string (the "skip phase" sentinel).
        self.assertEqual(cmds.build, "")
        self.assertEqual(cmds.lint, "")
        self.assertEqual(cmds.typecheck, "")

    def test_joins_list_commands_with_double_ampersand(self) -> None:
        """Matches the TypeScript adapter's `_op_str` semantics — a
        future architect that emits compound commands as a list must
        not silently drop them."""
        manifest = ArchitectureManifest()
        manifest.runtime.amendment_ops = {
            "commands.install": ["pip install -e .", "pip install pytest"],
        }
        cmds = PythonFastApiAdapter().quality_gate_dispatch(manifest)
        self.assertEqual(
            cmds.install, "pip install -e . && pip install pytest"
        )


class TestProposalRehearsalOptOut(unittest.TestCase):
    def test_returns_none(self) -> None:
        """Opt-out sentinel: distinct from '[]' which means 'ran, found
        nothing'. Future Python proposal-typecheck would return a list."""
        result = PythonFastApiAdapter().proposal_rehearsal_typecheck(Path("/tmp"))
        self.assertIsNone(result)


class TestRegistryIntegration(unittest.TestCase):
    """A5 registry-level smoke test — proves the one-line REGISTRY
    addition actually wires the adapter into the pipeline's dispatch
    surface. The contract harness already proves the adapter satisfies
    the ABC; this test proves it's reachable from the public API."""

    def test_registered_under_declared_name(self) -> None:
        from darkfactory.adapters import REGISTRY, get_adapter_class

        self.assertIn("python-fastapi", REGISTRY)
        cls = get_adapter_class("python-fastapi")
        self.assertIs(cls, PythonFastApiAdapter)

    def test_available_names_includes_python_fastapi(self) -> None:
        from darkfactory.adapters import available_adapter_names

        self.assertIn("python-fastapi", available_adapter_names())


if __name__ == "__main__":
    unittest.main()
