#!/usr/bin/env python3
"""Import Contract gate — verify architect manifest entry_points resolve at runtime.

Runs inside the gate container (stdlib-only, no PyYAML — same constraint
as ``render_substrate_phase.py``). Reads the architect manifest for the
current spec; for each ``test_integration.entry_points`` entry,
synthesizes a scratch test file **at scenarios-dir depth** that imports
from the declared ``specifier`` and asserts that every declared named
export is present on the resolved module; then runs the project's
configured vitest (honoring ``vitest_config`` and workspace layout) and
emits a gate report row.

Why this exists
---------------
The Implementer is instructed (``agents/implementer.md``) to verify
runtime resolution with a scratch import, but that verification is
manual — a forgotten check leaks into ``SCENARIO_CRASH`` failures that
burn an iteration with no useful diagnostic. Promoting the check into
the quality gate turns silent crashes into hard, actionable failures
with a pointer to the manifest specifier that cannot be resolved.

Placement
---------
Runs between the Substrate Verification phase (so generated types like
drizzle-kit output are already in place) and the Self-Tests phase (so
the clearer, single-import-per-file diagnostic surfaces before a full
test suite masks it).

Exit code: ``0`` for skip / pass, ``1`` for fail. The bash caller maps
that into ``GATE_FAILED=true`` for the existing soft-fail path.

Usage::

    render_import_contract_phase.py REPORT_FILE SUMMARY_FILE SPEC_NAME TOOLCHAIN
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

_GATE_NAME = "Import Contract"
# Phase classifier — Import Contract tests that the Implementer's code can
# be imported by test runners (pipeline infrastructure), not that the code
# itself is correct. Prefix the detail column so operators can distinguish
# a pipeline-infra failure from an impl failure at a glance.
_PHASE_CLASS = "[pipeline]"


# ---------------------------------------------------------------------------
# Manifest parser
# ---------------------------------------------------------------------------
#
# py3-yaml is pinned into dark-factory-base (docker/versions.env); see
# Dockerfile.base for the apk package. safe_load is used exclusively —
# never yaml.load — since the manifest can be authored by any agent and
# must not trigger tag/constructor resolution.


@dataclass
class EntryPoint:
    path: str
    specifier: str
    exports: list[str]
    note: str = ""


@dataclass
class TestIntegration:
    module_system: str
    import_extension: str
    vitest_config: str
    tsconfig: str
    entry_points: list[EntryPoint]


def parse_test_integration(manifest_text: str) -> TestIntegration | None:
    """Extract ``test_integration`` from a manifest YAML document.

    Returns ``None`` when the block is absent. Callers treat that as
    SKIP (non-Node specs legitimately omit this block).
    """
    try:
        doc = yaml.safe_load(manifest_text) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    ti = doc.get("test_integration")
    if ti is None:
        return None
    if not isinstance(ti, dict):
        return None

    entry_points: list[EntryPoint] = []
    for raw in ti.get("entry_points") or []:
        if not isinstance(raw, dict):
            continue
        exports_raw = raw.get("exports") or []
        if not isinstance(exports_raw, list):
            exports_raw = []
        entry_points.append(EntryPoint(
            path=str(raw.get("path") or ""),
            specifier=str(raw.get("specifier") or ""),
            exports=[str(e) for e in exports_raw],
            note=str(raw.get("note") or ""),
        ))

    return TestIntegration(
        module_system=str(ti.get("module_system") or ""),
        import_extension=str(ti.get("import_extension") or ""),
        vitest_config=str(ti.get("vitest_config") or ""),
        tsconfig=str(ti.get("tsconfig") or ""),
        entry_points=entry_points,
    )


# ---------------------------------------------------------------------------
# Scratch file synthesis
# ---------------------------------------------------------------------------


def _scratch_dir_for(spec_name: str) -> Path:
    """Dedicated scratch dir that sits at the same depth as scenarios.

    Two levels below the project root: ``.dark-factory/resolution-check/
    <spec>/``. Same depth as ``.dark-factory/scenarios/<spec>/`` so a
    manifest specifier like ``../../../apps/api/src/server.js`` resolves
    identically from either location. A dedicated namespace (not the
    scenarios dir) keeps scratch files off the Scenario Architect's
    surface and out of blind-scenario counts.

    The depth here is load-bearing: manifest authors write specifiers
    assuming scenarios-dir depth (three ``../`` segments to reach the
    project root). If this dir sits any deeper, every specifier in the
    Import Contract gate would resolve to ``.dark-factory/...`` instead
    of the project root, producing spurious FAILs.
    """
    return Path(".dark-factory") / "resolution-check" / spec_name


# Source-file extensions that Node ESM (NodeNext) will not resolve at
# runtime. If the manifest declares ``import_extension: .js`` (the
# standard NodeNext convention — tsc emits .js, and imports MUST spell
# the emitted extension), a specifier ending in .ts silently breaks
# runtime resolution. We normalize those forms to the declared
# ``import_extension`` before synthesis. This handles agents that copy
# the TS source path into ``specifier`` verbatim.
_REWRITABLE_EXTENSIONS: frozenset[str] = frozenset(
    {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"},
)


def _normalize_specifier_extension(specifier: str, import_extension: str) -> str:
    """Rewrite the specifier's trailing extension to ``import_extension``.

    The manifest contract is that every ``entry_points[].specifier`` is
    importable at runtime as-is. Node ESM configured with ``NodeNext`` +
    ``.js`` cannot resolve ``.ts``; tsc emits ``.js`` and runtime imports
    must use the emitted extension. Agents sometimes copy the source
    ``.ts`` path into ``specifier``; normalizing here turns that drift
    into a working import instead of a false-FAIL.

    If ``import_extension`` is empty (e.g. a non-Node toolchain) the
    specifier is returned unchanged. If the specifier has no extension
    at all, it is returned unchanged as well — adding ``.js`` to a
    bare-specifier package import (``@scope/pkg``) would break it.
    """
    if not import_extension:
        return specifier
    # Find the extension boundary (last '.' after the last path separator).
    last_sep = max(specifier.rfind("/"), specifier.rfind("\\"))
    dot_idx = specifier.rfind(".", last_sep + 1)
    if dot_idx < 0:
        # No extension — bare specifier or path without extension; leave alone.
        return specifier
    ext = specifier[dot_idx:]
    if ext in _REWRITABLE_EXTENSIONS and ext != import_extension:
        return specifier[:dot_idx] + import_extension
    return specifier


def _synth_scratch_file(idx: int, entry: EntryPoint, ti: TestIntegration) -> str:
    """Render a single-entry scratch test file.

    The scratch file uses a **top-level namespace import** to force the
    specifier to be evaluated at module load time — esbuild/vitest may
    tree-shake an unused named import, and that tree-shake would turn a
    real resolution failure into a silent PASS. A namespace binding
    (``import * as _mod``) has a runtime value and cannot be elided.

    The single ``test(...)`` block then asserts every declared export
    is a key on the resolved module object. Missing exports fail the
    test with a clear message.

    The specifier's trailing extension is normalized to ``ti.import_extension``
    at synthesis time via ``_normalize_specifier_extension``, so a manifest
    that declares ``import_extension: .js`` but writes ``specifier: ../x.ts``
    still produces a working scratch import.
    """
    specifier = _normalize_specifier_extension(entry.specifier, ti.import_extension)
    exports_js = ", ".join(json.dumps(e) for e in entry.exports)
    label = f"contract: {specifier}"
    # Use a stable filename pattern so failure reports can point back.
    return (
        f"// Auto-generated by render_import_contract_phase.py — do not edit.\n"
        f"// Verifies manifest test_integration.entry_points[{idx}]:\n"
        f"//   specifier: {specifier}\n"
        f"//   exports:   {', '.join(entry.exports)}\n"
        f"//   note:      {entry.note}\n"
        f"\n"
        f"import * as _mod from {json.dumps(specifier)};\n"
        f"import {{ test, expect }} from 'vitest';\n"
        f"\n"
        f"test({json.dumps(label)}, () => {{\n"
        f"  const _keys = Object.keys(_mod);\n"
        f"  for (const name of [{exports_js}]) {{\n"
        f"    if (!_keys.includes(name)) {{\n"
        f"      throw new Error(\n"
        f"        `Manifest declares named export \\`${{name}}\\` on specifier "
        f"{specifier} (entry_points[{idx}]), but the resolved module has "
        f"only: ${{_keys.join(', ') || '<none>'}}`,\n"
        f"      );\n"
        f"    }}\n"
        f"  }}\n"
        f"}});\n"
    )


def _synth_wrapper_config(
    scratch_dir: Path, vitest_config: str, workspace_dir: str | None,
) -> tuple[Path, str, str]:
    """Render a dedicated vitest wrapper config that targets the scratch dir.

    The wrapper extends the project's declared ``vitest_config`` via
    ``mergeConfig`` and overrides ``test.include`` to cover only the
    scratch dir. This avoids the false-PASS trap where the project's
    include pattern excludes the scratch files and vitest silently runs
    zero tests. Positional file paths alone are not enough — vitest
    treats them as filters WITHIN the include list in some versions, so
    we must also override the include itself.

    Returns ``(wrapper_path, --config flag value)`` — the flag value is
    relative to the CWD the vitest command will run from (workspace dir
    if present, otherwise project root).
    """
    # Path from scratch wrapper location back to project root.
    if workspace_dir:
        # Place the wrapper inside the workspace dir so its bare-specifier
        # imports (vitest/config) resolve from the workspace's
        # node_modules — see scenarios.sh _df_resolve_vitest_config for
        # the same rationale used by the scenarios wrapper.
        wrapper_path = Path(workspace_dir) / ".df-vitest-resolution-check.config.ts"
        # Import the user's vitest config by path relative to the workspace.
        base_rel = os.path.relpath(vitest_config, workspace_dir)
        # Depth from workspace dir back to project root.
        depth = len([p for p in Path(workspace_dir).parts if p])
        up = "".join(["../"] * depth)
        include_glob = (
            f"{up}.dark-factory/resolution-check/*/**/*.test.ts"
        )
        flag_value = ".df-vitest-resolution-check.config.ts"
    else:
        wrapper_path = scratch_dir / ".df-vitest-resolution-check.config.ts"
        # scratch_dir is .dark-factory/resolution-check/<spec>/
        # — 3 levels below the project root.
        depth = 3
        up = "".join(["../"] * depth)
        base_rel = f"{up}{vitest_config}"
        include_glob = "**/*.test.ts"
        flag_value = str(wrapper_path)

    # Node ESM treats imports without a leading ./, ../, or / as bare
    # specifiers (package imports), which fail with ERR_MODULE_NOT_FOUND
    # when the target is a relative file. relpath(vitest_config,
    # workspace_dir) returns a bare filename when they share a directory.
    if not base_rel.startswith(("./", "../", "/")):
        base_rel = "./" + base_rel

    # json.dumps produces a valid JSON string literal which is also a
    # valid TS string literal — defends against paths containing quotes.
    # Vitest projects/workspace mode IGNORES top-level ``test.include`` in favor
    # of each project's own ``include``, and filters positional file args THROUGH
    # those includes — so the resolution-check scratch files (which no real
    # project includes) get dropped (``No test files found`` -> false FAIL on
    # every vitest-projects repo). When the base config defines ``test.projects``,
    # replace them with a SOLE synthetic project that includes the scratch glob
    # AND inherits the ROOT ``resolve``/``plugins``/``esbuild`` so specifiers
    # resolve identically to the real scenario run (faithfulness — projects do
    # NOT inherit root ``resolve``: vitest configs that need an alias re-declare
    # it per-project, so a minimal ``{test:{...}}`` project would lose it and
    # false-FAIL on the module load). Sibling ``setupFiles`` (DB connections) are
    # intentionally NOT inherited — a resolution-only check needs none, and
    # omitting them keeps it fast and side-effect-free. LIMIT: a specifier
    # resolvable only via a project-LEVEL resolve override absent at root is not
    # captured; root resolve/plugins/esbuild is the correct general case and a
    # miss FAILs loudly via the returncode path. Only the ``projects`` array form
    # (vitest 3.2+) is handled; flat configs keep the top-level include override.
    glob_lit = json.dumps(include_glob)
    body = (
        f"import config from {json.dumps(base_rel)};\n"
        f"import {{ defineConfig, mergeConfig }} from 'vitest/config';\n"
        f"const merged = mergeConfig(config, defineConfig({{ test: {{}} }}));\n"
        f"merged.test = merged.test || {{}};\n"
        f"if (Array.isArray(merged.test.projects) && merged.test.projects.length > 0) {{\n"
        f"  // Projects mode: top-level include is inert; use a sole synthetic\n"
        f"  // project that inherits root resolve/plugins/esbuild (faithfulness).\n"
        f"  merged.test.projects = [{{\n"
        f"    resolve: merged.resolve,\n"
        f"    plugins: merged.plugins,\n"
        f"    esbuild: merged.esbuild,\n"
        f"    test: {{ name: '__df_resolution_check', include: [{glob_lit}] }},\n"
        f"  }}];\n"
        f"}} else {{\n"
        f"  merged.test.include = [{glob_lit}];\n"
        f"}}\n"
        f"export default merged;\n"
    )
    return wrapper_path, flag_value, body


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _detect_workspace(vitest_config: str) -> str | None:
    """Return workspace dir when vitest_config lives in a subdirectory.

    Mirrors the detection in ``scripts/lib/scenarios.sh:
    _df_resolve_vitest_config``. Keeping the two in lockstep is
    important — drift here produces false-PASS in the resolution
    check that the scenarios run would not share.
    """
    config_dir = os.path.dirname(vitest_config)
    if config_dir and config_dir != "." and os.path.isdir(config_dir):
        return config_dir
    return None


def _resolve_runner(workspace_dir: str | None) -> list[str] | None:
    """Return the vitest runner command prefix, or None if none available.

    Workspace mode uses ``pnpm exec`` to pick up workspace-scoped
    node_modules (same rationale as scenarios.sh). Non-workspace mode
    tries ``./node_modules/.bin/vitest`` first, then ``npx vitest``.
    """
    if workspace_dir:
        return ["pnpm", "exec", "vitest"]
    local = Path("node_modules/.bin/vitest")
    if local.exists():
        return [str(local)]
    if shutil.which("npx"):
        return ["npx", "vitest"]
    return None


def _append_row(report_file: Path, summary_file: Path, row: str, summary_row: str) -> None:
    with report_file.open("a", encoding="utf-8") as f:
        f.write(row + "\n")
    with summary_file.open("a", encoding="utf-8") as f:
        f.write(summary_row + "\n")


def _emit_skip(
    report_file: Path, summary_file: Path, reason: str,
) -> int:
    _append_row(
        report_file,
        summary_file,
        f"| {_GATE_NAME} | SKIP | {_PHASE_CLASS} {reason} |",
        f"| {_GATE_NAME} | SKIP |",
    )
    print(f"  {_GATE_NAME}: SKIP ({reason})")
    return 0


def _emit_pass(
    report_file: Path, summary_file: Path, count: int,
) -> int:
    _append_row(
        report_file,
        summary_file,
        f"| {_GATE_NAME} | PASS | {_PHASE_CLASS} {count} entry point(s) resolve at runtime |",
        f"| {_GATE_NAME} | PASS |",
    )
    print(f"  {_GATE_NAME}: PASS ({count} entry point(s))")
    return 0


def _emit_fail(
    report_file: Path,
    summary_file: Path,
    reason: str,
    diagnostic: str,
) -> int:
    _append_row(
        report_file,
        summary_file,
        f"| {_GATE_NAME} | FAIL | {_PHASE_CLASS} {reason} |",
        f"| {_GATE_NAME} | FAIL |",
    )
    with report_file.open("a", encoding="utf-8") as f:
        f.write("\n<details><summary>Import Contract diagnostic</summary>\n\n")
        f.write("```\n")
        f.write(diagnostic.rstrip() + "\n")
        f.write("```\n\n</details>\n\n")
    print(f"  {_GATE_NAME}: FAIL ({reason})")
    return 1


def render(
    report_file: Path,
    summary_file: Path,
    spec_name: str,
    toolchain: str,
) -> int:
    manifest_path = (
        Path(".dark-factory") / "architecture" / f"{spec_name}-manifest.yaml"
    )
    # Non-Node toolchains may legitimately have no test_integration; skip.
    if toolchain != "node":
        return _emit_skip(
            report_file, summary_file,
            f"toolchain={toolchain} — test_integration is Node/TS-specific",
        )
    if not manifest_path.exists():
        return _emit_skip(
            report_file, summary_file, "no architect manifest on disk",
        )

    manifest_text = manifest_path.read_text(encoding="utf-8")
    ti = parse_test_integration(manifest_text)
    if ti is None or not ti.entry_points:
        return _emit_skip(
            report_file, summary_file,
            "manifest has no test_integration.entry_points",
        )
    if not ti.vitest_config:
        return _emit_skip(
            report_file, summary_file,
            "manifest test_integration.vitest_config is empty",
        )
    if not Path(ti.vitest_config).exists():
        return _emit_fail(
            report_file, summary_file,
            f"declared vitest_config does not exist: {ti.vitest_config}",
            f"Manifest test_integration.vitest_config = {ti.vitest_config}\n"
            f"This file must exist on disk before the gate runs.",
        )

    scratch_dir = _scratch_dir_for(spec_name)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # Synthesize one scratch file per entry point.
    scratch_files: list[Path] = []
    for idx, ep in enumerate(ti.entry_points):
        if not ep.specifier:
            # No specifier — nothing to resolve. Fail loudly.
            return _emit_fail(
                report_file, summary_file,
                f"entry_points[{idx}] missing specifier",
                f"entry_points[{idx}] = specifier={ep.specifier!r}, "
                f"exports={ep.exports!r}",
            )
        # An empty ``exports`` list is NOT malformed: an intentionally-empty
        # barrel (``export {{}};`` — a fresh ``packages/shared`` index) has no
        # named exports yet, but the entry point must still RESOLVE at runtime.
        # ``_synth_scratch_file`` degenerates correctly for this case — the
        # namespace import still forces resolution (with extension
        # normalization), and the per-export assertion loop iterates zero
        # times — so the scratch test passes iff the module imports cleanly
        # (resolution-only). A broken specifier still FAILs via the vitest
        # returncode path below.
        path = scratch_dir / f"contract-{idx:02d}.test.ts"
        path.write_text(_synth_scratch_file(idx, ep, ti), encoding="utf-8")
        scratch_files.append(path)

    workspace_dir = _detect_workspace(ti.vitest_config)
    wrapper_path, flag_value, wrapper_body = _synth_wrapper_config(
        scratch_dir, ti.vitest_config, workspace_dir,
    )
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    wrapper_path.write_text(wrapper_body, encoding="utf-8")

    try:
        runner = _resolve_runner(workspace_dir)
        if runner is None:
            return _emit_fail(
                report_file, summary_file,
                "no vitest runner available",
                "Neither ./node_modules/.bin/vitest nor npx is available. "
                "Install vitest as a devDependency.",
            )
        cmd = runner + ["run", "--config", flag_value]
        # Explicit positional paths for each scratch file. In workspace
        # mode these must be relative to the workspace dir.
        for f in scratch_files:
            if workspace_dir:
                cmd.append(os.path.relpath(f, workspace_dir))
            else:
                cmd.append(str(f))

        cwd = workspace_dir if workspace_dir else "."
        print(f"  Running Import Contract check ({len(scratch_files)} entry point(s))...")
        try:
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired as e:
            return _emit_fail(
                report_file, summary_file,
                "vitest timed out after 120s",
                f"cmd: {' '.join(cmd)}\ncwd: {cwd}\n"
                f"stdout:\n{(e.stdout or '').strip()}\n"
                f"stderr:\n{(e.stderr or '').strip()}",
            )

        output = (result.stdout or "") + "\n" + (result.stderr or "")
        # Defensive: vitest exits 0 with "No test files found" when the
        # positional paths are filtered out by the (real) include list —
        # that would be a silent false PASS. Our dedicated wrapper
        # overrides include, but keep this check as belt-and-suspenders.
        if "No test files found" in output:
            return _emit_fail(
                report_file, summary_file,
                "vitest found no test files — wrapper config did not apply",
                f"cmd: {' '.join(cmd)}\ncwd: {cwd}\n\noutput:\n{output.strip()}",
            )
        if result.returncode != 0:
            return _emit_fail(
                report_file, summary_file,
                f"{len(ti.entry_points)} entry point(s) — one or more failed to resolve",
                f"cmd: {' '.join(cmd)}\ncwd: {cwd}\n\n"
                f"exit: {result.returncode}\n\n"
                f"{output.strip()}",
            )
        return _emit_pass(report_file, summary_file, len(ti.entry_points))
    finally:
        # Always clean up. The bash EXIT trap is the backstop; doing it
        # here keeps the filesystem clean on the normal path and avoids
        # leaking scratch files into iteration N+1.
        for f in scratch_files:
            try:
                f.unlink()
            except OSError:
                pass
        try:
            wrapper_path.unlink()
        except OSError:
            pass
        try:
            scratch_dir.rmdir()
        except OSError:
            pass
        try:
            scratch_dir.parent.rmdir()  # .dark-factory/resolution-check
        except OSError:
            pass


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            "Usage: render_import_contract_phase.py "
            "REPORT_FILE SUMMARY_FILE SPEC_NAME TOOLCHAIN",
            file=sys.stderr,
        )
        return 2
    return render(
        report_file=Path(argv[1]),
        summary_file=Path(argv[2]),
        spec_name=argv[3],
        toolchain=argv[4],
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
