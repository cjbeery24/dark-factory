"""Phase 5.1 — post-ship archive tagging + shipped.jsonl row append."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from darkfactory.pipeline.post_ship_archive import (
    record_shipped_spec,
    tag_archived_scenarios,
)
from darkfactory.plan.state import load_shipped, shipped_log_path


def _archive(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / ".dark-factory" / "shipped-scenarios" / "my-spec"
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")
    return d


def test_tags_markdown_scenario(tmp_path: Path) -> None:
    d = _archive(tmp_path, {"01.scenarios.md": "# Scenario\nbody\n"})
    n = tag_archived_scenarios(d, "my-spec", "feature")
    assert n == 1
    body = (d / "01.scenarios.md").read_text()
    assert body.startswith(
        "<!-- spec_origin: my-spec -->\n<!-- spec_type: feature -->\n"
    )
    assert "# Scenario\nbody\n" in body


def test_tags_typescript_scenario(tmp_path: Path) -> None:
    d = _archive(tmp_path, {"02.test.ts": "import {x} from 'y';\n"})
    tag_archived_scenarios(d, "my-spec", "feature")
    body = (d / "02.test.ts").read_text()
    assert body.startswith("// spec_origin: my-spec\n// spec_type: feature\n")


def test_mixed_extensions(tmp_path: Path) -> None:
    d = _archive(tmp_path, {
        "a.scenarios.md": "x\n",
        "b.test.ts": "y\n",
        "c.py": "z\n",
    })
    n = tag_archived_scenarios(d, "spec", "bugfix")
    assert n == 3
    assert (d / "a.scenarios.md").read_text().startswith("<!-- spec_origin: spec -->")
    assert (d / "b.test.ts").read_text().startswith("// spec_origin: spec")
    assert (d / "c.py").read_text().startswith("# spec_origin: spec")


def test_idempotent_on_rerun(tmp_path: Path) -> None:
    d = _archive(tmp_path, {"01.md": "body\n"})
    tag_archived_scenarios(d, "spec", "feature")
    first = (d / "01.md").read_text()
    # Second call must not re-prepend.
    tag_archived_scenarios(d, "spec", "feature")
    assert (d / "01.md").read_text() == first


def test_unknown_extension_skipped(tmp_path: Path) -> None:
    d = _archive(tmp_path, {"weird.xyz": "contents\n"})
    n = tag_archived_scenarios(d, "spec", "feature")
    assert n == 0
    # Untouched.
    assert (d / "weird.xyz").read_text() == "contents\n"


def test_hidden_files_skipped(tmp_path: Path) -> None:
    d = _archive(tmp_path, {".registry-meta": "spec=x\n", "01.md": "body\n"})
    n = tag_archived_scenarios(d, "spec", "feature")
    assert n == 1
    assert (d / ".registry-meta").read_text() == "spec=x\n"


def test_sentinel_does_not_false_positive_on_prose(tmp_path: Path) -> None:
    # Regression: an earlier version scanned the first N chars for the
    # substring "spec_origin:" and would refuse to tag any scenario whose
    # body documented the post-ship header format. The sentinel must be an
    # exact first-line match against the rendered header.
    body = (
        "# Scenario\n"
        "When post-ship archive writes `spec_origin: foo`, ...\n"
    )
    d = _archive(tmp_path, {"doc.md": body})
    n = tag_archived_scenarios(d, "my-spec", "feature")
    assert n == 1
    out = (d / "doc.md").read_text()
    assert out.startswith("<!-- spec_origin: my-spec -->\n<!-- spec_type: feature -->\n")
    assert body in out


def test_shebang_preserved(tmp_path: Path) -> None:
    d = _archive(tmp_path, {"run.sh": "#!/usr/bin/env bash\necho hi\n"})
    n = tag_archived_scenarios(d, "spec", "feature")
    assert n == 1
    out = (d / "run.sh").read_text()
    # Shebang must remain on line 1 for the kernel to honor it.
    assert out.startswith(
        "#!/usr/bin/env bash\n# spec_origin: spec\n# spec_type: feature\necho hi\n"
    )


def test_non_utf8_file_skipped(tmp_path: Path) -> None:
    d = tmp_path / ".dark-factory" / "shipped-scenarios" / "my-spec"
    d.mkdir(parents=True)
    raw = b"\xff\xfe not utf-8\n"
    (d / "binary.md").write_bytes(raw)
    n = tag_archived_scenarios(d, "spec", "feature")
    assert n == 0
    # Original bytes untouched — we must not corrupt unknown encodings.
    assert (d / "binary.md").read_bytes() == raw


def test_record_shipped_appends_row(tmp_path: Path) -> None:
    row = record_shipped_spec(
        tmp_path,
        spec_id="my-spec",
        spec_type="feature",
        spec_origin="my-spec",
    )
    assert row is not None
    assert row.spec_id == "my-spec"
    assert row.spec_type == "feature"
    assert row.scenario_archive_path == ".dark-factory/shipped-scenarios/my-spec"
    assert row.env_hash_at_ship  # non-empty — hash_view of empty contract
    rows = load_shipped(tmp_path)
    assert len(rows) == 1
    assert rows[0].spec_id == "my-spec"


def test_record_shipped_refactor_accepted(tmp_path: Path) -> None:
    # Regression: ShippedSpec previously rejected spec_type="refactor",
    # silently dropping refactor specs from the archive. Phase 5.1 must
    # accept every spec_type the pipeline runs.
    row = record_shipped_spec(
        tmp_path,
        spec_id="cleanup",
        spec_type="refactor",
        spec_origin="cleanup",
    )
    assert row is not None
    assert row.spec_type == "refactor"
    rows = load_shipped(tmp_path)
    assert rows[0].spec_type == "refactor"


def test_record_shipped_multiple_rows(tmp_path: Path) -> None:
    record_shipped_spec(tmp_path, spec_id="a", spec_type="bootstrap", spec_origin="a")
    record_shipped_spec(tmp_path, spec_id="b", spec_type="feature", spec_origin="b")
    rows = load_shipped(tmp_path)
    assert [r.spec_id for r in rows] == ["a", "b"]
    # Durable on disk, newline-delimited.
    raw = shipped_log_path(tmp_path).read_text()
    assert raw.count("\n") == 2
