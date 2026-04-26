"""ROADMAP.md Phase 3 B9a v(N): project-starter templates for the
postgres adapter's convention helpers.

Pins the existence and shape of
``templates/contracts/postgres/project-starters/`` so a future commit
that accidentally deletes it (or removes a starter) fails the test
suite at PR time instead of breaking the v(N+1) deprecation path
silently.

Two surfaces tested:

1. **Existence + shape**: every convention helper named in the
   ROADMAP has a starter file under the templates dir; the README
   documents the universal/convention split.
2. **Round-trip staging**: when a developer copies the starter dir
   into their project's ``<project>/.dark-factory/contracts/postgres/``,
   ``stage_adapter_contracts`` (with the B9d ``project_dir`` parameter)
   produces the expected layout under
   ``_contracts/postgres/project/``. Pins the integration between the
   B9a starter shape and the B9d staging ABI.

A barrier-discipline assertion in
``test_no_framework_helper_in_starters`` defends against a future
v(N+1) deletion that gets reverted halfway and accidentally re-ships
a universal helper as a "starter" (which would re-introduce the
extends-merge surface ROADMAP §8 explicitly rejects).
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from darkfactory.pipeline.adapter_staging import stage_adapter_contracts
from tests.helpers import TempDirTestCase


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STARTERS_DIR = (
    _REPO_ROOT / "templates" / "contracts" / "postgres" / "project-starters"
)


# Convention helpers per ROADMAP.md Phase 3 §"Exit criteria" — the three
# helpers that move out of the framework (universal three remain).
_CONVENTION_HELPERS: tuple[tuple[str, str], ...] = (
    ("assertAppSetsTxGuc", "app_tx_guc.ts"),
    ("assertTenantSeesOwnRows", "isolation.ts"),
    ("assertTenantCannotSeeOtherRows", "isolation.ts"),
)

# Universal helpers — MUST NOT appear in the project starters; they
# stay live in the framework. Pinning the absence is the v(N+1)-safety
# check: if a future patch accidentally copies a universal helper into
# the starters dir (e.g., the migration script gets too greedy), this
# test catches it.
_UNIVERSAL_HELPERS: tuple[str, ...] = (
    "assertUnsetGucIsNullish",
    "assertTxGucRoundTrips",
    "assertRlsEnabled",
)


class TestStarterDirExistsAndShape(unittest.TestCase):
    def test_starter_dir_exists(self) -> None:
        self.assertTrue(
            _STARTERS_DIR.is_dir(),
            f"starter dir missing: {_STARTERS_DIR} — "
            "ROADMAP.md Phase 3 B9a v(N) ships convention helpers as "
            "starter templates here. Restore from git history if "
            "accidentally deleted.",
        )

    def test_readme_documents_universal_vs_convention_split(self) -> None:
        readme = _STARTERS_DIR / "README.md"
        self.assertTrue(readme.is_file(), f"missing {readme}")
        text = readme.read_text(encoding="utf-8")
        # Pin the names of all six helpers in the README so a future
        # rename is caught (the README is the documentation surface
        # downstream consumers grep for to understand the layout).
        for name in _UNIVERSAL_HELPERS:
            self.assertIn(
                name, text,
                f"README must mention universal helper {name!r} so "
                "users understand which side of the split it lives on",
            )
        for name, _file in _CONVENTION_HELPERS:
            self.assertIn(
                name, text,
                f"README must mention convention helper {name!r} so "
                "users understand which side of the split it lives on",
            )

    def test_index_exports_every_convention_helper(self) -> None:
        index = _STARTERS_DIR / "index.ts"
        self.assertTrue(index.is_file(), f"missing {index}")
        text = index.read_text(encoding="utf-8")
        for name, _file in _CONVENTION_HELPERS:
            self.assertIn(
                f"{name}", text,
                f"index.ts must re-export {name!r} so scenarios can "
                "import from the staging root without knowing the file "
                "split",
            )

    def test_index_relative_imports_carry_js_suffix(self) -> None:
        """Node16 / nodenext module resolution requires the ``.js``
        suffix on relative imports — even for ``.ts`` source files.
        Pin every relative import in ``index.ts`` carries the suffix
        so a future rewrite that drops it doesn't ship a broken staged
        tree downstream."""
        import re
        index = _STARTERS_DIR / "index.ts"
        text = index.read_text(encoding="utf-8")
        # Match the source spec on every ``from '<spec>'`` form.
        relative_imports = re.findall(
            r"""from\s+['"](\.\.?\/[^'"]+)['"]""",
            text,
        )
        self.assertGreater(
            len(relative_imports), 0,
            "index.ts should have at least one relative import",
        )
        for src in relative_imports:
            self.assertTrue(
                src.endswith(".js"),
                f"relative import {src!r} in index.ts must end with "
                ".js (node16/nodenext requires the suffix even on "
                ".ts source files)",
            )

    def test_each_convention_helper_has_a_file(self) -> None:
        for name, file in _CONVENTION_HELPERS:
            path = _STARTERS_DIR / file
            self.assertTrue(
                path.is_file(),
                f"starter file missing: {path} (helper {name})",
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                f"export async function {name}",
                text,
                f"{path} must export an async function named {name!r}",
            )

    def test_no_framework_helper_in_starters(self) -> None:
        """Defense-in-depth against a v(N+1)-deletion bug. If a future
        patch accidentally migrates a universal helper to the starter
        dir (e.g., a too-greedy ``git mv``), this test catches it
        before the change ships. The universal three are
        defined-by-name in the ROADMAP exit criteria — pinning their
        absence is the regression backstop.
        """
        # Combine all .ts files' bodies and check no universal helper
        # is exported there.
        all_text = ""
        for ts_file in _STARTERS_DIR.glob("*.ts"):
            all_text += ts_file.read_text(encoding="utf-8")
        for name in _UNIVERSAL_HELPERS:
            self.assertNotIn(
                f"export async function {name}",
                all_text,
                f"universal helper {name!r} appears in the starter dir "
                f"{_STARTERS_DIR} — this is a layering violation. "
                "Universal helpers stay in the framework "
                "(`darkfactory/adapters/postgres/contracts/ts/`).",
            )

    def test_starters_avoid_known_foot_gun_patterns(self) -> None:
        """Starters MUST NOT contain the patterns
        ``scripts/lib/scenario_lint.py`` flags as foot-guns:
        ``parameterized-set-local`` and ``peer-client-guc-read``. If a
        starter ships a foot-gun, every project that copies the starter
        ships the foot-gun, and the regression cascades through the
        v(N+1) deprecation surface.

        Reuses scenario_lint's compiled patterns directly (same module
        loader as ``test_b8_lint_row_leak_regression.py``) so a future
        regex tightening propagates here automatically.
        """
        import importlib.util
        import sys

        spec_path = (
            _REPO_ROOT / "scripts" / "lib" / "scenario_lint.py"
        )
        spec = importlib.util.spec_from_file_location(
            "scenario_lint", spec_path,
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("scenario_lint", mod)
        spec.loader.exec_module(mod)

        for ts_file in _STARTERS_DIR.glob("*.ts"):
            text = ts_file.read_text(encoding="utf-8")
            for name, pat in (
                ("parameterized-set-local", mod._PARAMETERIZED_SET_LOCAL_RE),
                ("peer-client-guc-read", mod._PEER_CLIENT_GUC_READ_RE),
            ):
                matches = list(pat.finditer(text))
                self.assertEqual(
                    matches, [],
                    f"starter {ts_file.name} contains a {name!r} "
                    f"foot-gun match: {[m.group(0) for m in matches]!r}. "
                    "Every project that copies this starter inherits "
                    "the foot-gun; fix the starter, do not let it "
                    "propagate.",
                )


class TestRoundTripStaging(TempDirTestCase):
    """Simulate the developer flow:
    1. Copy starters into ``<project>/.dark-factory/contracts/postgres/``.
    2. Run ``stage_adapter_contracts`` with ``project_dir`` set.
    3. Confirm the result lands under
       ``<scenarios_dir>/_contracts/postgres/project/`` — the path the
       test-engineer container imports from.
    """

    def _copy_starters(self, *, keep_readme: bool = True) -> Path:
        """Mirror what ``dark-factory contracts init <adapter>`` (P4)
        will do once it exists: copy the starters dir into the
        project's contracts area. Returns the project root.

        ``keep_readme=True`` reflects the literal user flow if a
        developer copies the directory verbatim (the most common
        case). ``keep_readme=False`` reflects the documented flow
        (README says "copy this directory's contents excluding this
        README"). Both are tested below to pin the staging behavior
        in either case.
        """
        project_dir = self.tmp_dir / "myproject"
        contracts_dir = project_dir / ".dark-factory" / "contracts" / "postgres"
        contracts_dir.parent.mkdir(parents=True)
        shutil.copytree(_STARTERS_DIR, contracts_dir, dirs_exist_ok=False)
        if not keep_readme:
            (contracts_dir / "README.md").unlink()
        return project_dir

    def test_starters_stage_into_project_subdir(self) -> None:
        from tests.unit.test_adapter_staging import _FakeAdapter

        project_dir = self._copy_starters(keep_readme=False)
        scenarios_dir = self.tmp_dir / "scenarios" / "my-spec"
        # Construct an adapter whose project_contracts_dir resolves to
        # the project's copied starter dir. The B9d ABC default would
        # do this naturally for a real PostgresAdapter; the fake mirrors
        # it explicitly so the test does not depend on the real
        # adapter's import graph.
        proj_contracts = (
            project_dir / ".dark-factory" / "contracts" / "postgres"
        )
        adapter = _FakeAdapter(
            "postgres",
            None,  # no framework helpers in this test — only project.
            project_contracts=proj_contracts,
        )

        stage_adapter_contracts(
            scenarios_dir, [adapter],
            project_dir=project_dir,
        )

        staged_root = scenarios_dir / "_contracts" / "postgres" / "project"
        self.assertTrue(
            (staged_root / "app_tx_guc.ts").is_file(),
            f"expected app_tx_guc.ts in {staged_root}",
        )
        self.assertTrue(
            (staged_root / "isolation.ts").is_file(),
            f"expected isolation.ts in {staged_root}",
        )
        self.assertTrue(
            (staged_root / "index.ts").is_file(),
            f"expected index.ts in {staged_root}",
        )

    def test_readme_leaks_into_scenarios_when_developer_copies_verbatim(self) -> None:
        """Pin the literal copy-the-whole-dir-verbatim flow's outcome.

        Many developers will run ``cp -r templates/contracts/postgres/
        project-starters/* <project>/.dark-factory/contracts/postgres/``
        without reading the README's "excluding this README"
        parenthetical. ``stage_adapter_contracts`` has no extension
        filter, so the README leaks into
        ``_contracts/postgres/project/README.md`` inside the staged
        scenarios tree.

        Decision (B9a v(N)): the leak is harmless. README is
        documentation; no scenario can import a Markdown file. The
        test pins the behavior so a future change that adds an
        extension filter (or, conversely, a future change that breaks
        the literal-copy flow) makes a deliberate visible decision
        instead of silently shipping a regression.
        """
        from tests.unit.test_adapter_staging import _FakeAdapter

        project_dir = self._copy_starters(keep_readme=True)
        scenarios_dir = self.tmp_dir / "scenarios" / "my-spec"
        adapter = _FakeAdapter(
            "postgres",
            None,
            project_contracts=(
                project_dir / ".dark-factory" / "contracts" / "postgres"
            ),
        )

        stage_adapter_contracts(
            scenarios_dir, [adapter],
            project_dir=project_dir,
        )

        # README ships into staged scenarios under the project subdir.
        readme = (
            scenarios_dir / "_contracts" / "postgres" / "project"
            / "README.md"
        )
        self.assertTrue(
            readme.is_file(),
            "v(N) decision: README leaks into staged scenarios on the "
            "literal copy-verbatim flow. Harmless (no Markdown import "
            "is possible) and pinned here so a future filter change "
            "is a deliberate, visible decision.",
        )

    def test_starters_coexist_with_framework_helpers(self) -> None:
        """The B9d path-namespacing invariant: framework and project
        helpers occupy disjoint paths. Pin it end-to-end with real
        starter content + a synthetic framework helper set.
        """
        from tests.unit.test_adapter_staging import _FakeAdapter

        project_dir = self._copy_starters(keep_readme=False)
        scenarios_dir = self.tmp_dir / "scenarios" / "my-spec"

        # Synthetic framework helpers (mimics the universal three).
        fw_dir = self.tmp_dir / "fake-framework" / "postgres"
        fw_dir.mkdir(parents=True)
        (fw_dir / "guc.ts").write_text(
            "export async function assertUnsetGucIsNullish() {}",
            encoding="utf-8",
        )
        (fw_dir / "rls.ts").write_text(
            "export async function assertRlsEnabled() {}",
            encoding="utf-8",
        )

        adapter = _FakeAdapter(
            "postgres",
            fw_dir,
            project_contracts=(
                project_dir / ".dark-factory" / "contracts" / "postgres"
            ),
        )

        stage_adapter_contracts(
            scenarios_dir, [adapter],
            project_dir=project_dir,
        )

        # Framework helpers at the flat level.
        self.assertTrue(
            (scenarios_dir / "_contracts" / "postgres" / "guc.ts").is_file()
        )
        self.assertTrue(
            (scenarios_dir / "_contracts" / "postgres" / "rls.ts").is_file()
        )
        # Project helpers under project/ subdir — disjoint from
        # framework root.
        self.assertTrue(
            (
                scenarios_dir / "_contracts" / "postgres" / "project"
                / "app_tx_guc.ts"
            ).is_file()
        )
        self.assertTrue(
            (
                scenarios_dir / "_contracts" / "postgres" / "project"
                / "isolation.ts"
            ).is_file()
        )


if __name__ == "__main__":
    unittest.main()
