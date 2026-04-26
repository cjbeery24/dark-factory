"""Tests for the B9b retire_after auto-eval workflow infrastructure
(.github/workflows/auto-eval-retire-after.yml +
scripts/auto_eval_summary.py).

Two test concerns:

1. **Path-existence pin (Linus B9b commit-4 review #7).** The PR-body
   manual-removal checklist lists framework files a maintainer must
   edit to retire a helper. A future framework reorg renaming or
   moving any of these silently breaks the PR's instructions on the
   next auto-eval run (months from now). Pin every literal path in
   ``MANUAL_REMOVAL_PATHS`` against the actual file structure here;
   reorg breaks CI immediately, not on the next model bump.

2. **Summary-script behavior.** ``auto_eval_summary.py`` reads
   per-helper EvalResult JSONs and produces step output, step
   summary, and (conditionally) a PR body. Tests feed canned JSONs
   covering the three-verdict closed vocabulary
   ('retire' | 'keep' | 'inconclusive') and assert each output.
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

# Import path: scripts/ is not a package, so we add it to sys.path
# explicitly. ``auto_eval_summary`` is the module name.
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import auto_eval_summary as aes  # noqa: E402


def _make_eval_result_json(
    helper_name: str,
    verdict: str,
    *,
    rounds: int = 5,
    foot_gun_hits: int = 0,
    helper_import_count: int = 0,
) -> dict:
    """Construct an EvalResult-shaped dict matching what
    ``dark-factory contracts auto-eval`` writes."""
    return {
        "schema_version": 1,
        "helper_name": helper_name,
        "rounds": rounds,
        "foot_gun_hits": foot_gun_hits,
        "helper_import_count": helper_import_count,
        "verdict": verdict,
        "per_round": [
            {"foot_gun_hit": False, "imports_helper": False}
            for _ in range(rounds)
        ],
    }


class TestManualRemovalPathsExist(unittest.TestCase):
    """Linus #7: every literal path in the PR-body checklist must
    point at a real framework file. The first entry uses a per-helper
    placeholder ``<helper-file>.ts`` and is checked by parent-dir
    existence + ``.ts`` content presence rather than literal lookup."""

    def test_first_entry_is_placeholder_and_parent_dir_has_ts_files(self) -> None:
        path_str, _instruction = aes.MANUAL_REMOVAL_PATHS[0]
        # Sanity: this entry is the per-helper placeholder.
        self.assertIn("<helper-file>", path_str)
        parent_dir = _REPO_ROOT / Path(path_str).parent
        self.assertTrue(
            parent_dir.is_dir(),
            f"manual-removal-checklist parent dir does not exist: {parent_dir}",
        )
        ts_files = list(parent_dir.glob("*.ts"))
        self.assertGreater(
            len(ts_files), 0,
            f"manual-removal-checklist parent has no .ts files: {parent_dir}",
        )

    def test_remaining_entries_point_at_real_files(self) -> None:
        """Every non-placeholder entry must resolve to an existing
        framework file. Catches a reorg that renames or moves any of
        these without updating the auto-eval summary script."""
        for path_str, instruction in aes.MANUAL_REMOVAL_PATHS[1:]:
            with self.subTest(path=path_str):
                full_path = _REPO_ROOT / path_str
                self.assertTrue(
                    full_path.exists(),
                    msg=(
                        f"manual-removal-checklist path missing: {path_str} "
                        f"(instruction: {instruction!r}). Either restore the "
                        "file or update MANUAL_REMOVAL_PATHS in "
                        "scripts/auto_eval_summary.py."
                    ),
                )

    def test_all_entries_have_non_empty_instruction(self) -> None:
        for path_str, instruction in aes.MANUAL_REMOVAL_PATHS:
            with self.subTest(path=path_str):
                self.assertGreater(len(instruction.strip()), 0)


class TestLoadResults(unittest.TestCase):
    def test_empty_dir_yields_empty_list(self, /) -> None:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            self.assertEqual(aes.load_results(Path(tmp)), [])

    def test_loads_and_sorts_by_helper_name(self) -> None:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "z.json").write_text(json.dumps(
                _make_eval_result_json("zHelper", "retire"),
            ))
            (tmp_path / "a.json").write_text(json.dumps(
                _make_eval_result_json("aHelper", "keep"),
            ))
            results = aes.load_results(tmp_path)
            # Sorted by file name (a.json before z.json), giving
            # alphabetical helper_name ordering for the typical case
            # where the file name matches the helper name.
            self.assertEqual([r.helper_name for r in results],
                             ["aHelper", "zHelper"])


class TestRenderStepSummary(unittest.TestCase):
    def test_summary_table_includes_every_helper(self) -> None:
        results = [
            aes.HelperResult("assertOne", "retire", 5, 0, 0),
            aes.HelperResult("assertTwo", "keep", 5, 1, 2),
            aes.HelperResult("assertThree", "inconclusive", 5, 0, 0),
        ]
        out = aes.render_step_summary(results, "claude-opus-4-7", "5")
        self.assertIn("assertOne", out)
        self.assertIn("assertTwo", out)
        self.assertIn("assertThree", out)
        self.assertIn("**retire**", out)
        self.assertIn("**keep**", out)
        self.assertIn("**inconclusive**", out)
        self.assertIn("claude-opus-4-7", out)

    def test_summary_includes_count_columns(self) -> None:
        results = [aes.HelperResult("h", "keep", 3, 1, 2)]
        out = aes.render_step_summary(results, "m", "3")
        self.assertIn("| 3 |", out)  # rounds
        self.assertIn("| 1 |", out)  # foot_gun_hits
        self.assertIn("| 2 |", out)  # helper_imports


class TestRenderPRBody(unittest.TestCase):
    def test_pr_body_lists_only_retired_helpers(self) -> None:
        retired = [
            aes.HelperResult("assertX", "retire", 5, 0, 0),
            aes.HelperResult("assertY", "retire", 5, 0, 0),
        ]
        body = aes.render_pr_body(retired, "claude-opus-4-7", "5")
        self.assertIn("assertX", body)
        self.assertIn("assertY", body)
        self.assertIn("Auto-eval verdict: retire", body)

    def test_pr_body_includes_manual_removal_checklist(self) -> None:
        retired = [aes.HelperResult("h", "retire", 5, 0, 0)]
        body = aes.render_pr_body(retired, "m", "5")
        for path_str, _instruction in aes.MANUAL_REMOVAL_PATHS:
            self.assertIn(path_str, body)

    def test_pr_body_references_workflow_artifact(self) -> None:
        retired = [aes.HelperResult("h", "retire", 5, 0, 0)]
        body = aes.render_pr_body(retired, "claude-opus-4-7", "5")
        self.assertIn("auto-eval-results-claude-opus-4-7", body)

    def test_pr_body_has_governance_rationale(self) -> None:
        """The 'why draft PR not auto-merge' section is the
        load-bearing reason this workflow doesn't mechanically
        remove. Pin its presence — a future maintainer trimming the
        body to be 'cleaner' would defeat the calibration framing."""
        retired = [aes.HelperResult("h", "retire", 5, 0, 0)]
        body = aes.render_pr_body(retired, "m", "5")
        self.assertIn("calibration", body.lower())
        self.assertIn("governance", body.lower())


class TestMainBehavior(unittest.TestCase):
    """End-to-end of ``auto_eval_summary.main()``: feed canned
    EvalResult JSONs, set env, capture outputs."""

    def _setup_env(
        self, tmp_path: Path,
        *, model: str = "claude-opus-4-7", rounds: str = "5",
    ) -> dict:
        gh_output = tmp_path / "GITHUB_OUTPUT"
        gh_summary = tmp_path / "GITHUB_STEP_SUMMARY"
        gh_output.touch()
        gh_summary.touch()
        return {
            "INPUT_MODEL": model,
            "INPUT_ROUNDS": rounds,
            "GITHUB_OUTPUT": str(gh_output),
            "GITHUB_STEP_SUMMARY": str(gh_summary),
        }

    def _run(self, args: list[str], env: dict) -> tuple[int, str]:
        out = io.StringIO()
        with patch.dict(os.environ, env, clear=False):
            with patch.object(sys, "argv", ["auto_eval_summary.py", *args]):
                with redirect_stdout(out):
                    code = aes.main()
        return code, out.getvalue()

    def test_no_retired_writes_no_pr_body(self) -> None:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            results_dir = tmp_path / "results"
            results_dir.mkdir()
            (results_dir / "h.json").write_text(json.dumps(
                _make_eval_result_json("h", "keep"),
            ))
            pr_body = tmp_path / "pr_body.md"
            env = self._setup_env(tmp_path)
            code, stdout = self._run(
                ["--results-dir", str(results_dir),
                 "--pr-body-out", str(pr_body)],
                env,
            )
            self.assertEqual(code, 0)
            self.assertFalse(pr_body.exists())
            self.assertIn("All helpers still load-bearing", stdout)
            # retired= line is empty
            with open(env["GITHUB_OUTPUT"]) as f:
                self.assertIn("retired=\n", f.read())

    def test_one_retired_writes_pr_body(self) -> None:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            results_dir = tmp_path / "results"
            results_dir.mkdir()
            (results_dir / "a.json").write_text(json.dumps(
                _make_eval_result_json("aRet", "retire"),
            ))
            (results_dir / "b.json").write_text(json.dumps(
                _make_eval_result_json("bKeep", "keep"),
            ))
            pr_body = tmp_path / "pr_body.md"
            env = self._setup_env(tmp_path)
            code, _ = self._run(
                ["--results-dir", str(results_dir),
                 "--pr-body-out", str(pr_body)],
                env,
            )
            self.assertEqual(code, 0)
            self.assertTrue(pr_body.exists())
            body = pr_body.read_text()
            self.assertIn("aRet", body)
            # bKeep is NOT in the PR body (only retired ones).
            self.assertNotIn("bKeep", body)
            with open(env["GITHUB_OUTPUT"]) as f:
                self.assertIn("retired=aRet\n", f.read())

    def test_step_summary_lists_all_helpers_regardless_of_verdict(self) -> None:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            results_dir = tmp_path / "results"
            results_dir.mkdir()
            for name, verdict in [
                ("aRet", "retire"),
                ("bKeep", "keep"),
                ("cInc", "inconclusive"),
            ]:
                (results_dir / f"{name}.json").write_text(json.dumps(
                    _make_eval_result_json(name, verdict),
                ))
            pr_body = tmp_path / "pr_body.md"
            env = self._setup_env(tmp_path)
            code, _ = self._run(
                ["--results-dir", str(results_dir),
                 "--pr-body-out", str(pr_body)],
                env,
            )
            self.assertEqual(code, 0)
            with open(env["GITHUB_STEP_SUMMARY"]) as f:
                summary = f.read()
            for name in ["aRet", "bKeep", "cInc"]:
                self.assertIn(name, summary)

    def test_missing_env_var_returns_2(self) -> None:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            results_dir = tmp_path / "results"
            results_dir.mkdir()
            pr_body = tmp_path / "pr_body.md"
            env_partial = {"INPUT_MODEL": "m"}  # missing the rest
            with patch.dict(os.environ, env_partial, clear=True):
                with patch.object(sys, "argv", [
                    "auto_eval_summary.py",
                    "--results-dir", str(results_dir),
                    "--pr-body-out", str(pr_body),
                ]):
                    code = aes.main()
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
