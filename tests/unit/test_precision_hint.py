"""Tests for darkfactory/architecture/precision_hint.py."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase

from darkfactory.architecture.precision_hint import compose_precision_hint


def _write_status(
    project_dir: Path, spec_name: str, divergence: str | None = None
) -> None:
    status_dir = project_dir / ".dark-factory" / "reviews"
    status_dir.mkdir(parents=True, exist_ok=True)
    lines = ["verdict=PASS", "score=100", "blockers=0"]
    if divergence is not None:
        lines.append(f"divergence={divergence}")
    (status_dir / f"{spec_name}-validation.status").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


class TestComposePrecisionHint(TempDirTestCase):
    def test_medium_writes_file_and_returns_text(self) -> None:
        _write_status(self.tmp_dir, "demo", "MEDIUM")
        out = compose_precision_hint(self.tmp_dir, "demo")
        self.assertIn("PRECISION ADVISORY", out)
        self.assertIn("MEDIUM", out)
        hint_file = (
            self.tmp_dir
            / ".dark-factory"
            / "architecture"
            / "demo-precision-hint.md"
        )
        self.assertTrue(hint_file.exists())
        self.assertIn("Precision Analysis", hint_file.read_text(encoding="utf-8"))

    def test_medium_hint_references_spec_validation_report(self) -> None:
        _write_status(self.tmp_dir, "demo", "MEDIUM")
        out = compose_precision_hint(self.tmp_dir, "demo")
        self.assertIn("demo-validation.md", out)

    def test_low_returns_empty_and_clears_stale_hint(self) -> None:
        # Prior run wrote a hint; current run is LOW, so the hint must clear.
        arch_dir = self.tmp_dir / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True, exist_ok=True)
        stale = arch_dir / "demo-precision-hint.md"
        stale.write_text("stale", encoding="utf-8")
        _write_status(self.tmp_dir, "demo", "LOW")
        self.assertEqual(compose_precision_hint(self.tmp_dir, "demo"), "")
        self.assertFalse(stale.exists())

    def test_high_returns_empty(self) -> None:
        # HIGH is already blocked/warned in detection._parse_status_file;
        # the hint module stays silent so the advisory doesn't compete.
        _write_status(self.tmp_dir, "demo", "HIGH")
        self.assertEqual(compose_precision_hint(self.tmp_dir, "demo"), "")

    def test_missing_status_returns_empty(self) -> None:
        self.assertEqual(compose_precision_hint(self.tmp_dir, "demo"), "")

    def test_missing_divergence_key_returns_empty(self) -> None:
        _write_status(self.tmp_dir, "demo", divergence=None)
        self.assertEqual(compose_precision_hint(self.tmp_dir, "demo"), "")

    def test_lowercase_medium_is_normalised(self) -> None:
        _write_status(self.tmp_dir, "demo", "medium")
        out = compose_precision_hint(self.tmp_dir, "demo")
        self.assertIn("PRECISION ADVISORY", out)


if __name__ == "__main__":
    unittest.main()
