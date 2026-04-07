"""Phase 4.5.4 — defense-in-depth source-leak scan."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.audit.source_leak_scan import (
    scan_agent_output_for_source_leaks,
)


def _setup(d: Path) -> None:
    (d / "src" / "api").mkdir(parents=True)
    (d / "src" / "api" / "handlers.py").write_text("def x(): ...\n")
    (d / "src" / "api" / "models.py").write_text("class M: ...\n")
    (d / "src" / "node_modules").mkdir()
    (d / "src" / "node_modules" / "should_skip.py").write_text("# skip\n")


def test_clean_output_returns_no_matches() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        _setup(d)
        result = scan_agent_output_for_source_leaks(
            "I generated scenarios per the spec.", d, ["src"],
        )
        assert result.matched_paths == []
        assert result.files_scanned == 2  # node_modules skipped


def test_path_match_in_output_flags_leak() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        _setup(d)
        output = "I peeked at src/api/handlers.py to see the shape."
        result = scan_agent_output_for_source_leaks(output, d, ["src"])
        assert "src/api/handlers.py" in result.matched_paths


def test_multiple_matches() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        _setup(d)
        output = "see src/api/handlers.py and src/api/models.py"
        result = scan_agent_output_for_source_leaks(output, d, ["src"])
        assert sorted(result.matched_paths) == [
            "src/api/handlers.py",
            "src/api/models.py",
        ]


def test_empty_source_dirs_returns_clean() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        result = scan_agent_output_for_source_leaks("anything", d, [])
        assert result.matched_paths == []
        assert result.files_scanned == 0


def test_missing_source_dir_skipped() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        result = scan_agent_output_for_source_leaks(
            "no leaks here", d, ["nonexistent"],
        )
        assert result.matched_paths == []


def test_excluded_spec_text_suppresses_match() -> None:
    # The agent learning a path from its spec input is not a leak.
    with TemporaryDirectory() as t:
        d = Path(t)
        _setup(d)
        output = "I generated a scenario for src/api/handlers.py"
        spec = "FR-1: handlers in src/api/handlers.py must return 200"
        result = scan_agent_output_for_source_leaks(
            output, d, ["src"], excluded_text=spec,
        )
        assert result.matched_paths == []


def test_excluded_text_partial_only_some_matches_remain() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        _setup(d)
        output = "see src/api/handlers.py and src/api/models.py"
        spec = "FR-1: src/api/handlers.py is the entrypoint"
        result = scan_agent_output_for_source_leaks(
            output, d, ["src"], excluded_text=spec,
        )
        # handlers.py was in the spec; models.py was not.
        assert result.matched_paths == ["src/api/models.py"]


def test_skips_non_source_extensions() -> None:
    with TemporaryDirectory() as t:
        d = Path(t)
        (d / "src").mkdir()
        (d / "src" / "config.json").write_text("{}")
        (d / "src" / "README.md").write_text("# x")
        result = scan_agent_output_for_source_leaks(
            "src/config.json", d, ["src"],
        )
        assert result.matched_paths == []
        assert result.files_scanned == 0
