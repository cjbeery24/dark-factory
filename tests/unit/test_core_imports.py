"""Verify that core pipeline modules don't import enterprise modules at the top level.

Runs the import probe in a subprocess so the in-process module state seen by
sibling tests is untouched. The previous in-process implementation called
`del sys.modules[...]` on `darkfactory.deployment.*` then reloaded the
orchestrator, which left other test files (e.g. `test_environment.py`) holding
stale bindings to the pre-reload module objects — `@patch("darkfactory.deployment.environment.run")`
then patched the *new* module while the function under test still called into
the *old* one, producing 50+ unrelated full-suite failures.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest


_ENTERPRISE_PREFIXES = (
    "darkfactory.deployment",
    "darkfactory.intelligence.anomaly",
    "darkfactory.intelligence.trust",
    "darkfactory.intelligence.calibrate",
    "darkfactory.scenarios.regression",
    "darkfactory.events.export_otel",
    "darkfactory.pipeline.evaluator",
)


def _probe(import_target: str) -> list[str]:
    """Import `import_target` in a clean subprocess; return enterprise modules loaded."""
    prefixes_repr = repr(_ENTERPRISE_PREFIXES)
    script = textwrap.dedent(f"""
        import sys
        import {import_target}  # noqa: F401
        prefixes = {prefixes_repr}
        loaded = sorted(m for m in sys.modules if any(m.startswith(p) for p in prefixes))
        for m in loaded:
            print(m)
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return [line for line in result.stdout.splitlines() if line]


class TestCoreImportIsolation(unittest.TestCase):
    """Importing orchestrator and iteration must NOT pull in enterprise modules."""

    def test_orchestrator_import(self) -> None:
        loaded = _probe("darkfactory.pipeline.orchestrator")
        self.assertEqual(loaded, [], f"Enterprise modules imported at top level: {loaded}")

    def test_iteration_import(self) -> None:
        loaded = _probe("darkfactory.pipeline.iteration")
        self.assertEqual(loaded, [], f"Enterprise modules imported at top level: {loaded}")


if __name__ == "__main__":
    unittest.main()
