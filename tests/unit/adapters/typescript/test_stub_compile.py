"""A6 — Embedded-stub round-trip (tsc --noEmit against project).

Motivated by 2026-04-14 database-foundation escalation. See
``docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md``
(Sprint 2, A6).

Test surface:
- Extraction correctness (no tsc invocation needed).
- All no-op pathways log and return [].
- Scratch placement rejects escapes.
- Diagnostic parsing maps back to the originating stub's spec line.

The tsc subprocess is mocked throughout — we don't ship a TypeScript
toolchain with the test fixture set. One end-to-end integration test
against a real tsc install would be useful but belongs in an integration
suite, not unit.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.spec.spec_model import parse_spec_model
from darkfactory.adapters.typescript.stub_compile import (
    InterfaceStub,
    StubCompileFinding,
    _extract_filepath,
    _parse_tsc_diagnostics,
    _synthesize_tsconfig,
    _write_stubs_to_scratch,
    check_stub_roundtrip,
    extract_interface_stubs,
)


# ---------------------------------------------------------------------------
# Extraction — filepath comment parsing
# ---------------------------------------------------------------------------


def test_extract_filepath_accepts_standard_comment():
    fp, body = _extract_filepath("// apps/api/src/db/index.ts\nexport const db: Foo;\n")
    assert fp == "apps/api/src/db/index.ts"
    # Body must still have the same number of lines for tsc line-mapping.
    assert body.count("\n") == 1


def test_extract_filepath_accepts_scoped_path():
    fp, _ = _extract_filepath("// @app/api/src/foo.ts\nexport {};\n")
    assert fp == "@app/api/src/foo.ts"


def test_extract_filepath_accepts_dts_extension():
    fp, _ = _extract_filepath("// types/global.d.ts\ndeclare const X: number;\n")
    assert fp == "types/global.d.ts"


def test_extract_filepath_tolerates_blank_leading_lines():
    fp, _ = _extract_filepath("\n\n// apps/api/index.ts\nexport {};\n")
    assert fp == "apps/api/index.ts"


def test_extract_filepath_accepts_bare_filename():
    """``// foo.ts`` (no directory) is accepted and placed at the scratch root.

    This is unusual for real monorepo specs but it's not ambiguous —
    the stub lives at ``<scratch>/foo.ts``. Locking the current
    behavior makes any future tightening a deliberate change with a
    failing test in hand, rather than a silent regression.
    """
    fp, _ = _extract_filepath("// foo.ts\nexport {};\n")
    assert fp == "foo.ts"


def test_extract_filepath_rejects_non_comment_first_line():
    fp, body = _extract_filepath("export const db: Foo;\n// apps/api/index.ts\n")
    assert fp is None
    # Body unchanged when we bail.
    assert body.startswith("export const db")


def test_extract_filepath_rejects_wrong_extension():
    fp, _ = _extract_filepath("// apps/api/index.py\nexport {};\n")
    assert fp is None


# ---------------------------------------------------------------------------
# Extraction — spec model walk
# ---------------------------------------------------------------------------


def test_extract_stubs_only_from_interface_contract():
    markdown = (
        "# Spec\n\n"
        "## 2. Functional Requirements\n\n"
        "```typescript\n// wrong/section.ts\nexport {};\n```\n\n"
        "## 5. Interface Contract\n\n"
        "```typescript\n// right/file.ts\nexport const x: number;\n```\n"
    )
    model = parse_spec_model(markdown)
    stubs = extract_interface_stubs(model)
    assert [s.filepath for s in stubs] == ["right/file.ts"]


def test_extract_stubs_handles_numbered_heading():
    markdown = (
        "# Spec\n\n"
        "## 5. Interface Contract\n\n"
        "```typescript\n// apps/api/a.ts\nexport {};\n```\n"
    )
    model = parse_spec_model(markdown)
    stubs = extract_interface_stubs(model)
    assert len(stubs) == 1
    assert stubs[0].filepath == "apps/api/a.ts"


def test_extract_stubs_handles_unnumbered_heading():
    markdown = (
        "# Spec\n\n"
        "## Interface Contract\n\n"
        "```typescript\n// apps/api/a.ts\nexport {};\n```\n"
    )
    model = parse_spec_model(markdown)
    stubs = extract_interface_stubs(model)
    assert len(stubs) == 1


def test_extract_stubs_skips_non_ts_fences():
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```json\n{\"x\": 1}\n```\n"
        "```jsonc\n// comment\n```\n"
        "```sql\nSELECT 1;\n```\n"
    )
    model = parse_spec_model(markdown)
    assert extract_interface_stubs(model) == []


def test_extract_stubs_accepts_mts_cts():
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```mts\n// apps/api/mod.mts\nexport {};\n```\n"
        "```cts\n// apps/api/legacy.cts\nexport {};\n```\n"
    )
    model = parse_spec_model(markdown)
    paths = {s.filepath for s in extract_interface_stubs(model)}
    assert paths == {"apps/api/mod.mts", "apps/api/legacy.cts"}


def test_extract_stubs_skips_fence_without_filepath_comment(caplog):
    """A fence with no ``// <filepath>`` comment can't be placed."""
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```typescript\nexport const x: number;\n```\n"
    )
    model = parse_spec_model(markdown)
    import logging
    with caplog.at_level(logging.WARNING, logger="darkfactory.adapters.typescript.stub_compile"):
        stubs = extract_interface_stubs(model)
    assert stubs == []
    assert any("no `// <filepath>` comment" in r.message for r in caplog.records)


def test_extract_stubs_preserves_spec_line_numbers():
    markdown = (
        "# Spec\n\n"
        "## 5. Interface Contract\n\n"
        "\n\n"
        "```typescript\n// apps/api/a.ts\nexport {};\n```\n"
    )
    model = parse_spec_model(markdown)
    stubs = extract_interface_stubs(model)
    # The opening fence should be around line 6.
    assert stubs[0].spec_line_number > 1


# ---------------------------------------------------------------------------
# Scratch placement — path-escape defense
# ---------------------------------------------------------------------------


def test_write_stubs_rejects_path_escape(tmp_path, caplog):
    import logging
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    stubs = [
        InterfaceStub(
            filepath="../../etc/passwd",
            content="export {};\n",
            spec_line_number=1,
            section_path=("Spec", "Interface Contract"),
        ),
        InterfaceStub(
            filepath="apps/api/safe.ts",
            content="export {};\n",
            spec_line_number=2,
            section_path=("Spec", "Interface Contract"),
        ),
    ]
    with caplog.at_level(logging.WARNING, logger="darkfactory.adapters.typescript.stub_compile"):
        placements = _write_stubs_to_scratch(stubs, scratch)
    # Only the safe one should land.
    assert len(placements) == 1
    assert placements[0][0].filepath == "apps/api/safe.ts"
    assert any("escapes scratch dir" in r.message for r in caplog.records)
    # The escape target must not exist.
    assert not (tmp_path / "etc" / "passwd").exists()


def test_write_stubs_creates_nested_directories(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    stubs = [
        InterfaceStub(
            filepath="apps/api/src/db/nested/deep/file.ts",
            content="export const x: number;\n",
            spec_line_number=10,
            section_path=(),
        ),
    ]
    placements = _write_stubs_to_scratch(stubs, scratch)
    assert len(placements) == 1
    assert placements[0][1].read_text() == "export const x: number;\n"


# ---------------------------------------------------------------------------
# tsconfig synthesis
# ---------------------------------------------------------------------------


def test_synthesize_tsconfig_extends_project(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    project_tsconfig = tmp_path / "project" / "tsconfig.json"
    project_tsconfig.parent.mkdir()
    project_tsconfig.write_text('{"compilerOptions":{"strict":true}}')
    stub_path = scratch / "apps" / "api" / "a.ts"
    stub_path.parent.mkdir(parents=True)
    stub_path.write_text("export {};\n")

    synth = _synthesize_tsconfig(scratch, project_tsconfig, [stub_path])
    config = json.loads(synth.read_text())
    assert Path(config["extends"]).resolve() == project_tsconfig.resolve()
    assert config["compilerOptions"]["noEmit"] is True
    # composite must be forced off so tsc doesn't try to build references.
    assert config["compilerOptions"]["composite"] is False
    # include is the relative stub path under the scratch dir.
    assert "apps/api/a.ts" in config["include"]
    # references cleared.
    assert config["references"] == []


# ---------------------------------------------------------------------------
# Diagnostic parsing
# ---------------------------------------------------------------------------


def test_parse_tsc_diagnostic_old_paren_format(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    stub_file = scratch / "apps" / "api" / "db.ts"
    stub_file.parent.mkdir(parents=True)
    stub_file.write_text("export const db: number;\n")

    stub = InterfaceStub(
        filepath="apps/api/db.ts",
        content="export const db: number;\n",
        spec_line_number=141,
        section_path=(),
    )
    placements = [(stub, stub_file)]

    output = (
        "apps/api/db.ts(2,14): error TS2339: "
        "Property 'execute' does not exist on type 'DrizzleTx'.\n"
    )
    findings = _parse_tsc_diagnostics(output, scratch, placements)
    assert len(findings) == 1
    f = findings[0]
    assert f.spec_line_number == 141
    assert f.filepath == "apps/api/db.ts"
    assert f.stub_line == 2
    assert f.tsc_code == "TS2339"
    assert "Property 'execute' does not exist" in f.message


def test_parse_tsc_diagnostic_new_colon_format(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    stub_file = scratch / "a.ts"
    stub_file.write_text("export {};\n")
    stub = InterfaceStub(
        filepath="a.ts", content="export {};\n",
        spec_line_number=5, section_path=(),
    )
    output = "a.ts:3:5 - error TS2305: Module has no exported member 'foo'.\n"
    findings = _parse_tsc_diagnostics(output, scratch, [(stub, stub_file)])
    assert len(findings) == 1
    assert findings[0].tsc_code == "TS2305"
    assert findings[0].stub_line == 3


def test_parse_tsc_diagnostic_skips_non_stub_files(tmp_path):
    """Errors against library .d.ts files aren't the author's problem."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    stub_file = scratch / "a.ts"
    stub_file.write_text("export {};\n")
    stub = InterfaceStub(
        filepath="a.ts", content="export {};\n",
        spec_line_number=1, section_path=(),
    )
    output = (
        "node_modules/drizzle-orm/pg-core.d.ts(10,5): error TS9999: library noise\n"
        "a.ts(2,1): error TS2305: real error\n"
    )
    findings = _parse_tsc_diagnostics(output, scratch, [(stub, stub_file)])
    # Only the stub-side error survives.
    assert len(findings) == 1
    assert findings[0].tsc_code == "TS2305"


def test_parse_tsc_diagnostic_ignores_non_diagnostic_lines(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    stub_file = scratch / "a.ts"
    stub_file.write_text("export {};\n")
    stub = InterfaceStub(
        filepath="a.ts", content="export {};\n",
        spec_line_number=1, section_path=(),
    )
    output = (
        "Found 1 error in 1 file.\n"
        "\n"
        "a.ts(1,1): error TS2307: Cannot find module 'foo'.\n"
    )
    findings = _parse_tsc_diagnostics(output, scratch, [(stub, stub_file)])
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# check_stub_roundtrip — no-op pathways
# ---------------------------------------------------------------------------


_SPEC_WITH_STUB = (
    "# Spec\n\n## 5. Interface Contract\n\n"
    "```typescript\n// apps/api/a.ts\nexport const x: number;\n```\n"
)


def test_no_project_dir_is_noop():
    model = parse_spec_model(_SPEC_WITH_STUB)
    assert check_stub_roundtrip(model, project_dir=None) == []


def test_no_stubs_is_noop(tmp_path):
    model = parse_spec_model("# Spec\n\nNo §5 here.\n")
    # Even with a full project setup, empty stubs returns [] without
    # probing the filesystem.
    assert check_stub_roundtrip(model, project_dir=tmp_path) == []


def test_missing_tsconfig_is_noop(tmp_path, caplog):
    import logging
    (tmp_path / "node_modules").mkdir()
    model = parse_spec_model(_SPEC_WITH_STUB)
    with caplog.at_level(logging.WARNING, logger="darkfactory.adapters.typescript.stub_compile"):
        assert check_stub_roundtrip(model, project_dir=tmp_path) == []
    assert any("no tsconfig.json" in r.message for r in caplog.records)


def test_missing_node_modules_is_noop(tmp_path, caplog):
    import logging
    (tmp_path / "tsconfig.json").write_text("{}")
    model = parse_spec_model(_SPEC_WITH_STUB)
    with caplog.at_level(logging.WARNING, logger="darkfactory.adapters.typescript.stub_compile"):
        assert check_stub_roundtrip(model, project_dir=tmp_path) == []
    assert any("has no node_modules" in r.message for r in caplog.records)


def test_missing_tsc_is_noop(tmp_path, caplog, monkeypatch):
    import logging
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "node_modules").mkdir()
    # Ensure no PATH tsc leaks in.
    monkeypatch.setattr(
        "darkfactory.adapters.typescript.stub_compile.shutil.which",
        lambda _: None,
    )
    model = parse_spec_model(_SPEC_WITH_STUB)
    with caplog.at_level(logging.WARNING, logger="darkfactory.adapters.typescript.stub_compile"):
        assert check_stub_roundtrip(model, project_dir=tmp_path) == []
    assert any("tsc not found" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# check_stub_roundtrip — end-to-end with mocked tsc
# ---------------------------------------------------------------------------


def _prepare_project(tmp_path: Path) -> Path:
    """Minimal project that satisfies all A6 preconditions without real tsc."""
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}')
    (tmp_path / "node_modules").mkdir()
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir()
    tsc = bin_dir / "tsc"
    tsc.write_text("#!/bin/sh\nexit 0\n")
    tsc.chmod(0o755)
    return tsc


def test_clean_compile_produces_zero_findings(tmp_path):
    _prepare_project(tmp_path)
    model = parse_spec_model(_SPEC_WITH_STUB)

    # Mock tsc invocation to produce clean output.
    clean_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    with patch(
        "darkfactory.adapters.typescript.stub_compile._run_tsc",
        return_value=clean_result,
    ):
        findings = check_stub_roundtrip(model, project_dir=tmp_path)
    assert findings == []


def test_tsc_failure_produces_findings_mapped_to_spec(tmp_path):
    _prepare_project(tmp_path)
    model = parse_spec_model(_SPEC_WITH_STUB)

    # The spec stub writes to apps/api/a.ts; tsc will complain about it.
    failure_output = (
        "apps/api/a.ts(1,14): error TS2322: "
        "Type 'string' is not assignable to type 'number'.\n"
    )
    failure_result = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=failure_output, stderr="",
    )
    with patch(
        "darkfactory.adapters.typescript.stub_compile._run_tsc",
        return_value=failure_result,
    ):
        findings = check_stub_roundtrip(model, project_dir=tmp_path)
    assert len(findings) == 1
    assert findings[0].tsc_code == "TS2322"
    assert findings[0].filepath == "apps/api/a.ts"
    # Spec line number must be preserved back to the opening fence.
    assert findings[0].spec_line_number > 0


def test_tsc_timeout_is_noop(tmp_path, caplog):
    import logging
    _prepare_project(tmp_path)
    model = parse_spec_model(_SPEC_WITH_STUB)

    with patch(
        "darkfactory.adapters.typescript.stub_compile._run_tsc",
        side_effect=subprocess.TimeoutExpired(cmd="tsc", timeout=90),
    ):
        with caplog.at_level(
            logging.WARNING, logger="darkfactory.adapters.typescript.stub_compile",
        ):
            findings = check_stub_roundtrip(model, project_dir=tmp_path)
    assert findings == []
    assert any("timed out" in r.message for r in caplog.records)


def test_tsc_os_error_is_noop(tmp_path, caplog):
    import logging
    _prepare_project(tmp_path)
    model = parse_spec_model(_SPEC_WITH_STUB)

    with patch(
        "darkfactory.adapters.typescript.stub_compile._run_tsc",
        side_effect=OSError("broken pipe"),
    ):
        with caplog.at_level(
            logging.WARNING, logger="darkfactory.adapters.typescript.stub_compile",
        ):
            findings = check_stub_roundtrip(model, project_dir=tmp_path)
    assert findings == []
    assert any("tsc invocation failed" in r.message for r in caplog.records)


def test_scratch_dir_is_cleaned_up_on_success(tmp_path):
    _prepare_project(tmp_path)
    model = parse_spec_model(_SPEC_WITH_STUB)
    clean_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )

    seen_scratch: list[Path] = []

    def fake_run(tsc, tsconfig, project_dir, timeout_sec=90):
        seen_scratch.append(tsconfig.parent)
        return clean_result

    with patch("darkfactory.adapters.typescript.stub_compile._run_tsc", side_effect=fake_run):
        check_stub_roundtrip(model, project_dir=tmp_path)

    assert len(seen_scratch) == 1
    # After check returns the temp scratch must be gone.
    assert not seen_scratch[0].exists()


def test_scratch_dir_cleaned_up_even_on_exception(tmp_path):
    _prepare_project(tmp_path)
    model = parse_spec_model(_SPEC_WITH_STUB)

    seen_scratch: list[Path] = []

    def fake_run(tsc, tsconfig, project_dir, timeout_sec=90):
        seen_scratch.append(tsconfig.parent)
        # Simulate a catastrophic unexpected failure to prove the finally
        # path still cleans up.
        raise OSError("boom")

    with patch("darkfactory.adapters.typescript.stub_compile._run_tsc", side_effect=fake_run):
        findings = check_stub_roundtrip(model, project_dir=tmp_path)

    assert findings == []
    assert not seen_scratch[0].exists()
