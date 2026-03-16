"""Tests for DF_PYTHON_HANDLES_AB behavior in quality-gate.sh and satisfaction.sh."""

import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
QUALITY_GATE_SH = SCRIPTS_DIR / "quality-gate.sh"
SATISFACTION_SH = SCRIPTS_DIR / "lib" / "satisfaction.sh"
SCENARIOS_SH = SCRIPTS_DIR / "lib" / "scenarios.sh"


def _run_filter_via_find(files: list[str], handles_ab: str) -> list[str]:
    """
    Create a temp fixture dir with the given files, source scenarios.sh to get
    canonical _DF_FORMAT_PATTERNS_* arrays, run the actual DF_PYTHON_HANDLES_AB
    filtering logic, and return basenames of surviving files.

    The script is written to a temp file (not passed via -c) to avoid Python
    f-string / bash ${...} escaping conflicts.
    """
    import os
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create fixture files
        for name in files:
            Path(tmpdir, name).write_text("")

        # Build the bash script using concatenation — no f-string interpolation
        # of bash variable syntax like ${arr[@]+"${arr[@]}"}.
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
            'if [[ "${DF_PYTHON_HANDLES_AB:-0}" == "1" ]]; then',
            "    declare -A _ab_set=()",
            "    while IFS= read -r _ab_f; do",
            '        _ab_set["$_ab_f"]=1',
            "    done < <(find " + D + ' -type f \\( \\',
            '        "${_DF_FORMAT_PATTERNS_HTTP[@]}" -o \\',
            '        "${_DF_FORMAT_PATTERNS_JOURNEY[@]}" \\',
            "    \\) 2>/dev/null)",
            "",
            "    _filtered=()",
            '    for _f in "${files[@]+"${files[@]}"}"; do',
            '        if [[ -z "${_ab_set[$_f]+x}" ]]; then',
            '            _filtered+=("$_f")',
            "        fi",
            "    done",
            '    files=("${_filtered[@]+"${_filtered[@]}"}")',
            "fi",
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

        env = dict(os.environ)
        env["DF_PYTHON_HANDLES_AB"] = handles_ab
        result = subprocess.run(
            ["bash", script_path], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, (
            f"bash failed (DF_PYTHON_HANDLES_AB={handles_ab!r}):\n"
            f"{result.stderr}\n--- script ---\n{script_content}"
        )
        output = result.stdout.strip()
        return [x for x in output.split("\n") if x and x != Path(script_path).name]


class TestQualityGateShGuards(unittest.TestCase):
    """Verify the DF_PYTHON_HANDLES_AB guards are present in quality-gate.sh."""

    def _content(self) -> str:
        return QUALITY_GATE_SH.read_text()

    def test_http_server_start_guarded(self):
        content = self._content()
        self.assertIn('DF_PYTHON_HANDLES_AB:-0}" != "1"', content)

    def test_delegation_message_present(self):
        content = self._content()
        self.assertIn("delegated to Python orchestrator", content)

    def test_ab_delegated_flag_set(self):
        content = self._content()
        self.assertIn("_DF_AB_DELEGATED=true", content)

    def test_skip_row_suppressed_when_delegated(self):
        content = self._content()
        idx_skip = content.find("No recognized formats in")
        self.assertGreater(idx_skip, 0)
        # The if-block guarding the SKIP row must reference _DF_AB_DELEGATED
        preceding = content[:idx_skip]
        self.assertIn("_DF_AB_DELEGATED", preceding)

    def test_http_runner_guarded(self):
        content = self._content()
        idx_runner = content.find("http-scenario-runner.py")
        self.assertGreater(idx_runner, 0, "http-scenario-runner.py not found")
        preceding = content[:idx_runner]
        self.assertIn("DF_PYTHON_HANDLES_AB", preceding)

    def test_journey_runner_guarded(self):
        content = self._content()
        idx_runner = content.find("journey-runner.py")
        self.assertGreater(idx_runner, 0, "journey-runner.py not found")
        preceding = content[:idx_runner]
        self.assertIn("DF_PYTHON_HANDLES_AB", preceding)


class TestSatisfactionShFiltering(unittest.TestCase):
    """Verify satisfaction.sh filters A/B files when DF_PYTHON_HANDLES_AB=1.

    Tests source the actual scenarios.sh so canonical _DF_FORMAT_PATTERNS_* arrays
    drive the filtering — not a copy of the logic.
    """

    def test_filters_http_and_journey_when_flag_set(self):
        files = ["01-contract.test.py", "api.scenarios.md", "login.journey.md"]
        remaining = _run_filter_via_find(files, "1")
        self.assertIn("01-contract.test.py", remaining)
        self.assertNotIn("api.scenarios.md", remaining)
        self.assertNotIn("login.journey.md", remaining)

    def test_no_filtering_when_flag_is_zero(self):
        files = ["01-contract.test.py", "api.scenarios.md", "login.journey.md"]
        remaining = _run_filter_via_find(files, "0")
        self.assertEqual(sorted(remaining), sorted(files))

    def test_no_filtering_when_flag_empty(self):
        # DF_PYTHON_HANDLES_AB="" → ${:-0} → "0" → no filtering
        files = ["api.scenarios.md", "test_01.py"]
        remaining = _run_filter_via_find(files, "")
        self.assertIn("api.scenarios.md", remaining)
        self.assertIn("test_01.py", remaining)

    def test_only_ab_files_becomes_empty(self):
        files = ["api.scenarios.md", "login.journey.md"]
        remaining = _run_filter_via_find(files, "1")
        self.assertEqual(remaining, [])

    def test_executable_files_pass_through(self):
        files = ["test_01_contract.py", "02-happy.test.py"]
        remaining = _run_filter_via_find(files, "1")
        self.assertEqual(sorted(remaining), sorted(files))

    def test_satisfaction_sh_uses_canonical_patterns(self):
        """satisfaction.sh must reference canonical arrays, not hardcoded extensions."""
        content = SATISFACTION_SH.read_text()
        self.assertIn("DF_PYTHON_HANDLES_AB", content)
        self.assertIn("_DF_FORMAT_PATTERNS_HTTP", content)
        self.assertIn("_DF_FORMAT_PATTERNS_JOURNEY", content)
        self.assertNotIn("*.scenarios.md|*.journey.md", content)


if __name__ == "__main__":
    unittest.main()
