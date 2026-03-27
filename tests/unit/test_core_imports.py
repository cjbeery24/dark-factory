"""Verify that core pipeline modules don't import enterprise modules at the top level."""

from __future__ import annotations

import importlib
import sys
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


def _enterprise_modules_loaded() -> list[str]:
    return [m for m in sys.modules if any(m.startswith(p) for p in _ENTERPRISE_PREFIXES)]


class TestCoreImportIsolation(unittest.TestCase):
    """Importing orchestrator and iteration must NOT pull in enterprise modules."""

    def _purge_enterprise(self) -> None:
        for m in _enterprise_modules_loaded():
            del sys.modules[m]

    def test_orchestrator_import(self) -> None:
        self._purge_enterprise()
        import darkfactory.pipeline.orchestrator  # noqa: F401
        importlib.reload(darkfactory.pipeline.orchestrator)
        loaded = _enterprise_modules_loaded()
        self.assertEqual(loaded, [], f"Enterprise modules imported at top level: {loaded}")

    def test_iteration_import(self) -> None:
        self._purge_enterprise()
        import darkfactory.pipeline.iteration  # noqa: F401
        importlib.reload(darkfactory.pipeline.iteration)
        loaded = _enterprise_modules_loaded()
        self.assertEqual(loaded, [], f"Enterprise modules imported at top level: {loaded}")


if __name__ == "__main__":
    unittest.main()
