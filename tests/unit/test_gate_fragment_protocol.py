"""Tests for the fragment file protocol between quality-gate.sh and Python orchestrator.

Validates that:
- Bash always writes to format-specific fragment files (-executable.env, -satisfaction.env)
- A/B file filtering is unconditional (always filters HTTP/Journey from executable scoring)
- DF_PYTHON_ORCHESTRATED is the sole coordination flag (1 guard clause)
- Python accumulates fragments into canonical counts files
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
QUALITY_GATE_SH = SCRIPTS_DIR / "quality-gate.sh"
SATISFACTION_SH = SCRIPTS_DIR / "lib" / "satisfaction.sh"
SCENARIOS_SH = SCRIPTS_DIR / "lib" / "scenarios.sh"


def _run_filter_via_find(files: list[str]) -> list[str]:
    """
    Create a temp fixture dir with the given files, source scenarios.sh to get
    canonical _DF_FORMAT_PATTERNS_* arrays, run the unconditional A/B filtering
    logic, and return basenames of surviving files.
    """
    import os
    with tempfile.TemporaryDirectory() as tmpdir:
        for name in files:
            Path(tmpdir, name).write_text("")

        S = str(SCENARIOS_SH)
        D = tmpdir
        script_lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "source " + S,
            "",
            "files=()",
            "while IFS= read -r f; do",
            '    files+=("$f")',
            "done < <(find " + D + ' -type f \\( \\',
            '    "${_DF_FORMAT_PATTERNS_EXECUTABLE[@]}" -o \\',
            '    "${_DF_FORMAT_PATTERNS_HTTP[@]}" -o \\',
            '    "${_DF_FORMAT_PATTERNS_JOURNEY[@]}" \\',
            "\\) 2>/dev/null | sort)",
            "",
            # Unconditional A/B filtering (matches new satisfaction.sh behavior)
            "declare -A _ab_set=()",
            "while IFS= read -r _ab_f; do",
            '    _ab_set["$_ab_f"]=1',
            "done < <(find " + D + ' -type f \\( \\',
            '    "${_DF_FORMAT_PATTERNS_HTTP[@]}" -o \\',
            '    "${_DF_FORMAT_PATTERNS_JOURNEY[@]}" \\',
            "\\) 2>/dev/null)",
            "",
            "_filtered=()",
            'for _f in "${files[@]+"${files[@]}"}"; do',
            '    if [[ -z "${_ab_set[$_f]+x}" ]]; then',
            '        _filtered+=("$_f")',
            "    fi",
            "done",
            'files=("${_filtered[@]+"${_filtered[@]}"}")',
            "",
            'for f in "${files[@]+"${files[@]}"}"; do',
            '    basename "$f"',
            "done",
        ]
        script_content = "\n".join(script_lines) + "\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, dir=tmpdir
        ) as sf:
            sf.write(script_content)
            script_path = sf.name

        result = subprocess.run(
            ["bash", script_path], capture_output=True, text=True, env=os.environ.copy()
        )
        assert result.returncode == 0, (
            f"bash failed:\n{result.stderr}\n--- script ---\n{script_content}"
        )
        output = result.stdout.strip()
        return [x for x in output.split("\n") if x and x != Path(script_path).name]


class TestQualityGateOrchestrationFlag(unittest.TestCase):
    """Verify DF_PYTHON_ORCHESTRATED is the sole coordination flag in bash scripts."""

    def _qg_content(self) -> str:
        return QUALITY_GATE_SH.read_text()

    def test_old_flag_absent(self):
        """DF_PYTHON_HANDLES_AB must not appear anywhere in bash scripts."""
        qg = self._qg_content()
        sat = SATISFACTION_SH.read_text()
        self.assertNotIn("DF_PYTHON_HANDLES_AB", qg)
        self.assertNotIn("DF_PYTHON_HANDLES_AB", sat)

    def test_new_flag_used_in_quality_gate(self):
        content = self._qg_content()
        self.assertIn("DF_PYTHON_ORCHESTRATED", content)

    def test_delegation_message_present(self):
        content = self._qg_content()
        self.assertIn("delegated to Python orchestrator", content)

    def test_ab_delegated_flag_set(self):
        content = self._qg_content()
        self.assertIn("_DF_AB_DELEGATED=true", content)

    def test_skip_row_suppressed_when_delegated(self):
        content = self._qg_content()
        idx_skip = content.find("No recognized formats in")
        self.assertGreater(idx_skip, 0)
        preceding = content[:idx_skip]
        self.assertIn("_DF_AB_DELEGATED", preceding)

    def test_accumulation_guard_uses_new_flag(self):
        """The single accumulation guard must check DF_PYTHON_ORCHESTRATED."""
        content = self._qg_content()
        # Find the call site (not the function definition) — it's after
        # "Accumulate format-specific fragments" comment.
        idx_comment = content.find("Accumulate format-specific fragments")
        self.assertGreater(idx_comment, 0)
        block = content[idx_comment:idx_comment + 400]
        self.assertIn("DF_PYTHON_ORCHESTRATED", block)
        self.assertIn("_df_accumulate_format_counts", block)


class TestSatisfactionShFiltering(unittest.TestCase):
    """Verify satisfaction.sh unconditionally filters A/B files.

    Tests source the actual scenarios.sh so canonical _DF_FORMAT_PATTERNS_* arrays
    drive the filtering — not a copy of the logic.
    """

    def test_filters_http_and_journey(self):
        files = ["01-contract.test.py", "api.scenarios.md", "login.journey.md"]
        remaining = _run_filter_via_find(files)
        self.assertIn("01-contract.test.py", remaining)
        self.assertNotIn("api.scenarios.md", remaining)
        self.assertNotIn("login.journey.md", remaining)

    def test_only_ab_files_becomes_empty(self):
        files = ["api.scenarios.md", "login.journey.md"]
        remaining = _run_filter_via_find(files)
        self.assertEqual(remaining, [])

    def test_executable_files_pass_through(self):
        files = ["test_01_contract.py", "02-happy.test.py"]
        remaining = _run_filter_via_find(files)
        self.assertEqual(sorted(remaining), sorted(files))

    def test_satisfaction_sh_uses_canonical_patterns(self):
        """satisfaction.sh must reference canonical arrays, not hardcoded extensions."""
        content = SATISFACTION_SH.read_text()
        self.assertIn("_DF_FORMAT_PATTERNS_HTTP", content)
        self.assertIn("_DF_FORMAT_PATTERNS_JOURNEY", content)
        self.assertNotIn("*.scenarios.md|*.journey.md", content)

    def test_satisfaction_sh_no_flag_conditional_on_filtering(self):
        """Filtering must be unconditional — no DF_PYTHON_HANDLES_AB guard."""
        content = SATISFACTION_SH.read_text()
        self.assertNotIn("DF_PYTHON_HANDLES_AB", content)


if __name__ == "__main__":
    unittest.main()
