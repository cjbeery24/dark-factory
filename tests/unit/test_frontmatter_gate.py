"""Phase 4.6 — validate-time spec frontmatter gate."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.spec.frontmatter_gate import check_spec_frontmatter_gate


def _write(d: Path, name: str, content: str) -> Path:
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


META = "<!-- dark-factory-metadata\nspec_type: {st}\n-->\n# Spec\n"


def test_passes_feature_spec() -> None:
    with TemporaryDirectory() as t:
        p = _write(Path(t), "f.md", META.format(st="feature"))
        ok, msg = check_spec_frontmatter_gate(p)
        assert ok and msg == ""


def test_missing_file() -> None:
    ok, msg = check_spec_frontmatter_gate(Path("/nonexistent/x.md"))
    assert not ok and "not found" in msg


def test_missing_spec_type() -> None:
    with TemporaryDirectory() as t:
        p = _write(Path(t), "f.md", "<!-- dark-factory-metadata\n-->\n# Spec\n")
        ok, msg = check_spec_frontmatter_gate(p)
        assert not ok and "spec_type" in msg


def test_invalid_spec_type() -> None:
    with TemporaryDirectory() as t:
        p = _write(Path(t), "f.md", META.format(st="nonsense"))
        ok, msg = check_spec_frontmatter_gate(p)
        assert not ok and "nonsense" in msg.lower() or "Invalid" in msg


def test_bugfix_without_reproduction_fails() -> None:
    with TemporaryDirectory() as t:
        p = _write(Path(t), "f.md", META.format(st="bugfix"))
        ok, msg = check_spec_frontmatter_gate(p)
        assert not ok and "Reproduction" in msg


def test_bugfix_with_reproduction_passes() -> None:
    with TemporaryDirectory() as t:
        body = META.format(st="bugfix") + "\n## Reproduction\n1. do x\n"
        p = _write(Path(t), "f.md", body)
        ok, msg = check_spec_frontmatter_gate(p)
        assert ok and msg == ""


def test_bugfix_with_steps_to_reproduce_header_passes() -> None:
    with TemporaryDirectory() as t:
        body = META.format(st="bugfix") + "\n## Steps to Reproduce\n1. x\n"
        p = _write(Path(t), "f.md", body)
        ok, msg = check_spec_frontmatter_gate(p)
        assert ok


def test_bugfix_with_fenced_reproduction_does_not_credit() -> None:
    # A `## Reproduction` line buried inside a fenced code block must NOT
    # satisfy the gate — that's a quoted snippet, not a real section.
    with TemporaryDirectory() as t:
        body = (
            META.format(st="bugfix")
            + "\n## Background\n```markdown\n## Reproduction\n1. x\n```\n"
        )
        p = _write(Path(t), "f.md", body)
        ok, msg = check_spec_frontmatter_gate(p)
        assert not ok and "Reproduction" in msg


def test_bootstrap_passes_without_reproduction() -> None:
    with TemporaryDirectory() as t:
        p = _write(Path(t), "f.md", META.format(st="bootstrap"))
        ok, msg = check_spec_frontmatter_gate(p)
        assert ok
