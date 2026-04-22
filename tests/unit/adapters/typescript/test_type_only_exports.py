"""Tests for the TypeScript type-only export classifier (Task 42).

Covers:

* ``classify_exports_text`` — per-declaration classification (unambiguous
  type-only forms, unambiguous runtime forms, brace-export forms with the
  ``type`` modifier at both positions, re-exports, default).
* ``find_type_only_exports`` — manifest-level scan that pairs entry_point
  declared exports against on-disk classification and returns the
  offending set.

The worked example at the bottom of the file (``DrizzleTx``) reproduces
the exact failure mode that motivated this task, so a future reader can
see the bug and the fix side-by-side.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from darkfactory.adapters.typescript.type_only_exports import (
    ExportClassification,
    TypeOnlyFinding,
    classify_exports,
    classify_exports_text,
    find_type_only_exports,
)


# ---------------------------------------------------------------------------
# classify_exports_text — type-only declarations
# ---------------------------------------------------------------------------


def test_export_type_alias_is_type_only() -> None:
    result = classify_exports_text("export type Foo = string;")
    assert result.type_only == {"Foo"}
    assert result.runtime == set()


def test_export_generic_type_alias_is_type_only() -> None:
    result = classify_exports_text(
        "export type Foo<T> = Parameters<T>[0];",
    )
    assert result.classify("Foo") == "type_only"


def test_export_interface_is_type_only() -> None:
    result = classify_exports_text("export interface Foo { x: number; }")
    assert result.type_only == {"Foo"}


def test_generic_interface_is_type_only() -> None:
    result = classify_exports_text(
        "export interface Foo<T extends object> { x: T; }",
    )
    assert result.classify("Foo") == "type_only"


# ---------------------------------------------------------------------------
# classify_exports_text — runtime declarations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source,name", [
    ("export const foo = 1;", "foo"),
    ("export let foo = 1;", "foo"),
    ("export var foo = 1;", "foo"),
    ("export function foo() {}", "foo"),
    ("export async function foo() {}", "foo"),
    ("export function* foo() {}", "foo"),
    ("export class Foo {}", "Foo"),
    ("export abstract class Foo {}", "Foo"),
    ("export enum Foo { A }", "Foo"),
])
def test_runtime_forms_are_runtime(source: str, name: str) -> None:
    result = classify_exports_text(source)
    assert result.runtime == {name}
    assert result.type_only == set()


def test_default_expression_is_runtime() -> None:
    result = classify_exports_text("export default { foo: 1 };")
    assert result.classify("default") == "runtime"


def test_default_function_is_runtime() -> None:
    result = classify_exports_text("export default function foo() {}")
    assert result.classify("foo") == "runtime"


# ---------------------------------------------------------------------------
# Brace clauses — the ``type`` modifier in both positions
# ---------------------------------------------------------------------------


def test_outer_type_modifier_makes_everything_type_only() -> None:
    """``export type { A, B as C }`` — every item is type-only."""
    result = classify_exports_text(
        "type Inner = string;\n"
        "type Other = number;\n"
        "export type { Inner, Other as Renamed };"
    )
    assert result.type_only == {"Inner", "Renamed"}
    assert result.runtime == set()


def test_inner_type_modifier_is_type_only() -> None:
    """``export { type A }`` — only the type-marked name is type-only."""
    result = classify_exports_text(
        "type A = string;\n"
        "const b = 1;\n"
        "export { type A, b };"
    )
    assert result.type_only == {"A"}
    assert result.runtime == {"b"}


def test_brace_export_resolves_against_local_type_decl() -> None:
    """``export { X }`` where X is declared as ``type X = …`` is type-only.

    This is the subtle case: the architect may have listed ``X`` without
    a ``type`` modifier because TypeScript permits it in a non-isolated-
    modules configuration. We still need to reject it because the
    emitted JS has no runtime binding.
    """
    result = classify_exports_text(
        "type X = { a: number };\n"
        "export { X };"
    )
    assert result.classify("X") == "type_only"


def test_brace_export_resolves_against_local_runtime_decl() -> None:
    """``export { X }`` where X is declared as ``const X = …`` is runtime."""
    result = classify_exports_text(
        "const X = 42;\n"
        "export { X };"
    )
    assert result.classify("X") == "runtime"


def test_local_name_with_both_type_and_value_bindings_resolves_to_runtime() -> None:
    """TypeScript permits a type and value with the same name (declaration
    merging); a plain ``export { X }`` in that case refers to the value.
    """
    result = classify_exports_text(
        "type X = string;\n"
        "const X = 'x';\n"
        "export { X };"
    )
    assert result.classify("X") == "runtime"


# ---------------------------------------------------------------------------
# Re-exports — conservative fallback
# ---------------------------------------------------------------------------


def test_re_export_without_type_modifier_is_runtime() -> None:
    """``export { A } from './x'`` without type modifier → runtime.

    We can't resolve the target without reading ``./x``; the gate's
    Import Contract check will verify the name actually exists at
    runtime. Our job is only to catch the unambiguous type-only case.
    """
    result = classify_exports_text("export { A } from './x';")
    assert result.classify("A") == "runtime"


def test_re_export_with_outer_type_is_type_only() -> None:
    result = classify_exports_text("export type { A } from './x';")
    assert result.classify("A") == "type_only"


def test_re_export_with_inner_type_is_type_only() -> None:
    result = classify_exports_text("export { type A, B } from './x';")
    assert result.classify("A") == "type_only"
    assert result.classify("B") == "runtime"


# ---------------------------------------------------------------------------
# Aliasing — ``as`` renames the exported symbol
# ---------------------------------------------------------------------------


def test_as_alias_classifies_exported_name() -> None:
    result = classify_exports_text(
        "type Internal = string;\n"
        "export { type Internal as Public };"
    )
    assert result.classify("Public") == "type_only"
    # The original internal name is not exported and not queried via the
    # public API; classify() returns "not_found" for it.
    assert result.classify("Internal") == "not_found"


# ---------------------------------------------------------------------------
# Comments must not confuse the detector
# ---------------------------------------------------------------------------


def test_ignores_export_type_inside_block_comment() -> None:
    source = (
        "/* example: export type Foo = string; */\n"
        "export const bar = 1;"
    )
    result = classify_exports_text(source)
    assert result.runtime == {"bar"}
    assert result.type_only == set()


def test_ignores_export_type_inside_line_comment() -> None:
    source = (
        "// export type Foo = string; (example)\n"
        "export const bar = 1;"
    )
    result = classify_exports_text(source)
    assert result.type_only == set()


# ---------------------------------------------------------------------------
# classify — name-lookup API
# ---------------------------------------------------------------------------


def test_classify_missing_name_returns_not_found() -> None:
    result = classify_exports_text("export const foo = 1;")
    assert result.classify("bar") == "not_found"


def test_empty_source_returns_empty_classification() -> None:
    result = classify_exports_text("")
    assert result.runtime == set()
    assert result.type_only == set()


# ---------------------------------------------------------------------------
# classify_exports (on-disk) — I/O edge cases
# ---------------------------------------------------------------------------


def test_classify_missing_file_returns_empty(tmp_path: Path) -> None:
    result = classify_exports(tmp_path / "nonexistent.ts")
    assert result.runtime == set()
    assert result.type_only == set()


def test_classify_reads_utf8_file(tmp_path: Path) -> None:
    p = tmp_path / "m.ts"
    p.write_text(
        "export type Foo = string;\nexport const bar = 1;\n",
        encoding="utf-8",
    )
    result = classify_exports(p)
    assert result.type_only == {"Foo"}
    assert result.runtime == {"bar"}


# ---------------------------------------------------------------------------
# find_type_only_exports — manifest-level scan
# ---------------------------------------------------------------------------


class _Ep:
    """Minimal EntryPoint stand-in — avoids importing the manifest dataclass
    so this test module stays focused on the classifier itself."""

    def __init__(self, path: str, exports: list[str]) -> None:
        self.path = path
        self.exports = exports


def test_find_type_only_flags_type_alias(tmp_path: Path) -> None:
    src = tmp_path / "db.ts"
    src.write_text(
        "import type { Foo } from 'x';\n"
        "export const db = 1;\n"
        "export type DrizzleTx = Parameters<Parameters<typeof db>[0]>[0];\n",
        encoding="utf-8",
    )
    eps = [_Ep("db.ts", ["db", "DrizzleTx"])]
    findings = find_type_only_exports(eps, tmp_path)
    assert findings == [TypeOnlyFinding(
        entry_point_idx=0,
        path="db.ts",
        type_only_names=("DrizzleTx",),
    )]


def test_find_type_only_skips_non_ts_file(tmp_path: Path) -> None:
    """A ``.js`` path is not our concern — the architect may have picked
    a compiled artifact whose sources we cannot inspect. Skip silently.
    """
    src = tmp_path / "db.js"
    src.write_text("module.exports = { db: 1 };", encoding="utf-8")
    eps = [_Ep("db.js", ["db"])]
    assert find_type_only_exports(eps, tmp_path) == []


def test_find_type_only_skips_missing_file(tmp_path: Path) -> None:
    """Before the implementer runs, the file may not exist. The Import
    Contract gate catches that downstream; this scan must not flag it
    as a type-only violation.
    """
    eps = [_Ep("apps/api/src/db.ts", ["db"])]
    assert find_type_only_exports(eps, tmp_path) == []


def test_find_type_only_skips_empty_exports(tmp_path: Path) -> None:
    """An entry point with no declared exports has nothing to classify."""
    src = tmp_path / "db.ts"
    src.write_text("export type Foo = string;", encoding="utf-8")
    eps = [_Ep("db.ts", [])]
    assert find_type_only_exports(eps, tmp_path) == []


def test_find_type_only_collects_multiple_names(tmp_path: Path) -> None:
    """One finding per entry_point, collecting every offending name so the
    architect retry prompt can list them all at once."""
    src = tmp_path / "db.ts"
    src.write_text(
        "export interface Foo { a: number }\n"
        "export type Bar = string;\n"
        "export const baz = 1;\n",
        encoding="utf-8",
    )
    eps = [_Ep("db.ts", ["Foo", "Bar", "baz"])]
    findings = find_type_only_exports(eps, tmp_path)
    assert len(findings) == 1
    assert findings[0].type_only_names == ("Foo", "Bar")


def test_find_type_only_across_multiple_entry_points(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text(
        "export type A = string;\nexport const aa = 1;",
        encoding="utf-8",
    )
    (tmp_path / "b.ts").write_text("export const bb = 2;", encoding="utf-8")
    (tmp_path / "c.ts").write_text(
        "export interface C { x: number }",
        encoding="utf-8",
    )
    eps = [
        _Ep("a.ts", ["A", "aa"]),
        _Ep("b.ts", ["bb"]),  # all-runtime — no finding
        _Ep("c.ts", ["C"]),
    ]
    findings = find_type_only_exports(eps, tmp_path)
    # Entry points 0 and 2 have findings; 1 does not.
    assert [f.entry_point_idx for f in findings] == [0, 2]
    assert findings[0].type_only_names == ("A",)
    assert findings[1].type_only_names == ("C",)


# ---------------------------------------------------------------------------
# Worked example: the iter-3 escalation reproduction.
# ---------------------------------------------------------------------------


def test_drizzle_tx_regression_reproduction(tmp_path: Path) -> None:
    """Reproduces the exact failure from 2026-04-21 database-foundation iter-3.

    Prior to Task 42 this manifest would pass validation, iterate for
    ~25 minutes, then fail the Import Contract gate with "DrizzleTx not
    in resolved module keys" — structurally unfixable from the
    implementer's writable scope because the declaration itself is
    correct TypeScript.
    """
    src_dir = tmp_path / "apps" / "api" / "src" / "db"
    src_dir.mkdir(parents=True)
    (src_dir / "index.ts").write_text(
        "import { drizzle } from 'drizzle-orm/postgres-js';\n"
        "import postgres from 'postgres';\n"
        "export const connection = postgres(process.env.DATABASE_URL!);\n"
        "export const db = drizzle(connection);\n"
        "export const withTenantDb = async (id: string, fn: Function) => fn(db);\n"
        "export type DrizzleTx = "
        "Parameters<Parameters<typeof db.transaction>[0]>[0];\n",
        encoding="utf-8",
    )
    eps = [_Ep(
        "apps/api/src/db/index.ts",
        ["db", "connection", "withTenantDb", "DrizzleTx"],
    )]
    findings = find_type_only_exports(eps, tmp_path)
    assert findings == [TypeOnlyFinding(
        entry_point_idx=0,
        path="apps/api/src/db/index.ts",
        type_only_names=("DrizzleTx",),
    )]


def test_classification_dataclass_is_mutable() -> None:
    """``ExportClassification.runtime``/``type_only`` are sets we mutate
    during the multi-phase scan; guard against a future change that
    freezes the dataclass from breaking the classifier silently."""
    c = ExportClassification(path="m.ts")
    c.runtime.add("foo")
    c.type_only.add("Bar")
    assert c.classify("foo") == "runtime"
    assert c.classify("Bar") == "type_only"


# ---------------------------------------------------------------------------
# Regression: Linus review BLOCKER #1 — ``const enum`` alternation ordering
# ---------------------------------------------------------------------------
#
# Before the fix, ``export const enum Foo`` matched the ``const\s+`` branch
# first, capturing ``enum`` as the NAME. The name ``Foo`` was then classified
# as ``not_found``. ``const enum`` emits a runtime object when
# ``preserveConstEnums`` is true (common in lib builds), so treating it as
# runtime (not type-only) is the safe default.


def test_const_enum_captures_name_not_enum_keyword() -> None:
    result = classify_exports_text("export const enum Foo { A = 1, B = 2 }")
    assert result.classify("Foo") == "runtime"
    assert result.classify("enum") == "not_found"


def test_plain_enum_classified_as_runtime() -> None:
    result = classify_exports_text("export enum Color { Red, Green }")
    assert result.classify("Color") == "runtime"


def test_local_const_enum_resolves_via_brace_export() -> None:
    """Local ``const enum X`` + ``export { X }``: X must resolve as runtime,
    not ``not_found`` (which would happen if the local-runtime regex also
    captured ``enum`` instead of the name)."""
    result = classify_exports_text(
        "const enum K { A = 1 }\nexport { K };"
    )
    assert result.classify("K") == "runtime"


# ---------------------------------------------------------------------------
# Regression: Linus review BLOCKER #2 — bare re-export bypass
# ---------------------------------------------------------------------------
#
# An architect can split ``export type DrizzleTx = …`` into a sibling file
# and re-export it bare (``export { DrizzleTx } from './types'``). Before
# the fix, re-exports without a ``type`` modifier were unconditionally
# classified as runtime, re-opening the exact bug Task 42 exists to close.
# One-hop resolution via ``classify_exports`` now follows the ``from`` clause.


def test_bare_reexport_of_type_caught_via_one_hop(tmp_path: Path) -> None:
    types_file = tmp_path / "types.ts"
    types_file.write_text(
        "export type DrizzleTx = { readonly _brand: unique symbol };\n",
        encoding="utf-8",
    )
    index_file = tmp_path / "index.ts"
    index_file.write_text(
        "export { DrizzleTx } from './types';\n",
        encoding="utf-8",
    )
    result = classify_exports(index_file)
    assert result.classify("DrizzleTx") == "type_only", \
        "bare re-export of a type must resolve via the ./types hop"


def test_bare_reexport_of_type_with_js_specifier(tmp_path: Path) -> None:
    """TS-with-NodeNext writes ``from './types.js'`` but the source is
    ``types.ts``. The resolver must strip ``.js`` and retry as ``.ts``."""
    (tmp_path / "types.ts").write_text(
        "export type Tx = unknown;\n", encoding="utf-8",
    )
    index_file = tmp_path / "index.ts"
    index_file.write_text(
        "export { Tx } from './types.js';\n",
        encoding="utf-8",
    )
    assert classify_exports(index_file).classify("Tx") == "type_only"


def test_bare_reexport_of_runtime_classified_as_runtime(tmp_path: Path) -> None:
    (tmp_path / "lib.ts").write_text(
        "export const answer = 42;\n", encoding="utf-8",
    )
    index_file = tmp_path / "index.ts"
    index_file.write_text(
        "export { answer } from './lib';\n",
        encoding="utf-8",
    )
    assert classify_exports(index_file).classify("answer") == "runtime"


def test_reexport_of_type_through_barrel_index(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "index.ts").write_text(
        "export type Handle = string;\n", encoding="utf-8",
    )
    index_file = tmp_path / "index.ts"
    index_file.write_text(
        "export { Handle } from './sub';\n", encoding="utf-8",
    )
    assert classify_exports(index_file).classify("Handle") == "type_only"


def test_reexport_to_bare_package_falls_back_to_runtime(tmp_path: Path) -> None:
    """External packages (``'react'``) cannot be resolved without reading
    node_modules. Fallback to runtime preserves the prior policy."""
    index_file = tmp_path / "index.ts"
    index_file.write_text(
        "export { Component } from 'react';\n", encoding="utf-8",
    )
    assert classify_exports(index_file).classify("Component") == "runtime"


def test_reexport_hop_catches_two_level_barrel(tmp_path: Path) -> None:
    """Canonical monorepo barrel: ``a.ts → ./b → ./c`` where the type is
    declared in ``c.ts``. Two hops must catch it — anything shallower
    silently leaks the DrizzleTx bug class back in via the common
    ``apps/api/src/db/index.ts → ./types → ../shared/types`` shape."""
    (tmp_path / "c.ts").write_text(
        "export type Deep = unknown;\n", encoding="utf-8",
    )
    (tmp_path / "b.ts").write_text(
        "export { Deep } from './c';\n", encoding="utf-8",
    )
    a_file = tmp_path / "a.ts"
    a_file.write_text(
        "export { Deep } from './b';\n", encoding="utf-8",
    )
    assert classify_exports(a_file).classify("Deep") == "type_only"


def test_reexport_hop_bounded_at_max_depth(tmp_path: Path) -> None:
    """A chain deeper than ``_REEXPORT_MAX_HOPS`` falls back to runtime —
    the bound is a policy choice, not a structural limit. If this test
    needs to be updated because the limit was raised, that's intentional
    evidence — raise the constant and update this test together."""
    # 4-file chain: a → b → c → d, type declared in d.ts.
    (tmp_path / "d.ts").write_text(
        "export type Deepest = unknown;\n", encoding="utf-8",
    )
    (tmp_path / "c.ts").write_text(
        "export { Deepest } from './d';\n", encoding="utf-8",
    )
    (tmp_path / "b.ts").write_text(
        "export { Deepest } from './c';\n", encoding="utf-8",
    )
    a_file = tmp_path / "a.ts"
    a_file.write_text(
        "export { Deepest } from './b';\n", encoding="utf-8",
    )
    assert classify_exports(a_file).classify("Deepest") == "runtime"


def test_reexport_cycle_terminates(tmp_path: Path) -> None:
    """``a → b → a`` must terminate — the depth counter bounds the
    recursion even on a cycle, no visited-set required."""
    a = tmp_path / "a.ts"
    b = tmp_path / "b.ts"
    a.write_text("export { X } from './b';\n", encoding="utf-8")
    b.write_text("export { X } from './a';\n", encoding="utf-8")
    # Neither file has a real declaration; falls back to runtime.
    assert classify_exports(a).classify("X") == "runtime"


# ---------------------------------------------------------------------------
# Resolver containment — host-FS read protection
# ---------------------------------------------------------------------------


def test_resolver_rejects_path_outside_project_root(tmp_path: Path) -> None:
    """A re-export that escapes ``project_root`` via ``..`` traversal
    must not read the external file. Without containment, the architect
    could trigger arbitrary host-FS reads at orchestrator level."""
    project = tmp_path / "proj"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (outside / "secret.ts").write_text(
        "export type Leaked = string;\n", encoding="utf-8",
    )
    entry = project / "index.ts"
    entry.write_text(
        "export { Leaked } from '../outside/secret';\n",
        encoding="utf-8",
    )
    # With project_root, the hop is rejected (out-of-tree) and the
    # exported name falls back to runtime via the classifier's default.
    result = classify_exports(entry, project_root=project)
    assert result.classify("Leaked") == "runtime"


def test_resolver_allows_path_inside_project_root(tmp_path: Path) -> None:
    """Sanity: containment doesn't break legitimate ``../`` traversal
    within the project. ``apps/api/src → ../../shared/types`` is the
    exact monorepo shape we need to support."""
    (tmp_path / "apps" / "api" / "src").mkdir(parents=True)
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "types.ts").write_text(
        "export type Handle = string;\n", encoding="utf-8",
    )
    entry = tmp_path / "apps" / "api" / "src" / "index.ts"
    entry.write_text(
        "export { Handle } from '../../../shared/types';\n",
        encoding="utf-8",
    )
    result = classify_exports(entry, project_root=tmp_path)
    assert result.classify("Handle") == "type_only"


def test_resolver_no_project_root_does_not_contain(tmp_path: Path) -> None:
    """When ``project_root`` is ``None``, no containment is enforced —
    preserves the classify-single-file path for callers who don't have
    a project context. Test-only convenience."""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "t.ts").write_text(
        "export type T = number;\n", encoding="utf-8",
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    entry = proj / "e.ts"
    entry.write_text(
        "export { T } from '../outside/t';\n", encoding="utf-8",
    )
    # With no project_root, the hop succeeds and T is classified type_only.
    assert classify_exports(entry).classify("T") == "type_only"


def test_reexport_type_modifier_still_wins_without_resolver(tmp_path: Path) -> None:
    """Explicit ``export type { X } from './y'`` is type-only even when
    no resolver is available — the modifier alone is authoritative."""
    result = classify_exports_text(
        "export type { Foo } from './nonexistent';"
    )
    assert result.classify("Foo") == "type_only"


# ---------------------------------------------------------------------------
# Regression: Linus review BLOCKER #3 — ``export declare`` + ``.d.ts``
# ---------------------------------------------------------------------------
#
# ``export declare`` is ambient: TypeScript emits no runtime code for it,
# regardless of the inner keyword. Every declared name is type-only. A
# ``.d.ts`` file has no runtime module at all.


def test_export_declare_const_is_type_only() -> None:
    result = classify_exports_text("export declare const Secret: string;")
    assert result.classify("Secret") == "type_only"


def test_export_declare_function_is_type_only() -> None:
    result = classify_exports_text(
        "export declare function handler(req: Req): Res;",
    )
    assert result.classify("handler") == "type_only"


def test_export_declare_class_is_type_only() -> None:
    result = classify_exports_text("export declare class Widget { x: number; }")
    assert result.classify("Widget") == "type_only"


def test_export_declare_namespace_is_type_only() -> None:
    result = classify_exports_text(
        "export declare namespace Foo { interface A {} }",
    )
    assert result.classify("Foo") == "type_only"


def test_export_declare_const_enum_is_type_only() -> None:
    result = classify_exports_text(
        "export declare const enum Mode { A, B }",
    )
    assert result.classify("Mode") == "type_only"


def test_dts_file_classifies_all_names_as_type_only(tmp_path: Path) -> None:
    """A ``.d.ts`` file has no runtime JS module. Even names that look
    like ``export const``/``export function`` are ambient — the file is
    type-only by construction."""
    dts = tmp_path / "api.d.ts"
    dts.write_text(
        "export const version: string;\n"
        "export function handler(req: Req): Res;\n"
        "export type Handle = string;\n",
        encoding="utf-8",
    )
    result = classify_exports(dts)
    assert result.classify("version") == "type_only"
    assert result.classify("handler") == "type_only"
    assert result.classify("Handle") == "type_only"
    assert result.runtime == set()


def test_dts_file_honors_dmts_and_dcts_suffixes(tmp_path: Path) -> None:
    dmts = tmp_path / "api.d.mts"
    dmts.write_text(
        "export const esmThing: number;\n", encoding="utf-8",
    )
    assert classify_exports(dmts).classify("esmThing") == "type_only"


def test_export_declare_then_runtime_prefers_runtime() -> None:
    """``export declare const X`` followed by ``export const X`` is a
    redeclaration that tsc would reject (TS2300 duplicate identifier),
    but if a classifier sees both, runtime is the safer fallback — a
    false positive (rejecting a spec with an erroneous const+const)
    forces the architect to fix the spec anyway."""
    result = classify_exports_text(
        "export declare const X: number;\n"
        "export const X = 42;\n"
    )
    assert result.classify("X") == "runtime"


# ---------------------------------------------------------------------------
# find_type_only_exports — DrizzleTx-via-sibling-file integration
# ---------------------------------------------------------------------------


class _Ep2:
    def __init__(self, path: str, exports: list[str]):
        self.path = path
        self.exports = exports


def test_find_type_only_catches_drizzle_tx_via_sibling(tmp_path: Path) -> None:
    """End-to-end: the architect writes DrizzleTx in a sibling file and
    re-exports it from index.ts without the ``type`` modifier. The manifest
    scan must still flag it."""
    src = tmp_path / "apps" / "api" / "src" / "db"
    src.mkdir(parents=True)
    (src / "types.ts").write_text(
        "export type DrizzleTx = "
        "Parameters<Parameters<typeof db.transaction>[0]>[0];\n",
        encoding="utf-8",
    )
    (src / "index.ts").write_text(
        "export const db = 1 as any;\n"
        "export { DrizzleTx } from './types';\n",
        encoding="utf-8",
    )
    eps = [_Ep2(
        "apps/api/src/db/index.ts",
        ["db", "DrizzleTx"],
    )]
    findings = find_type_only_exports(eps, tmp_path)
    assert findings == [TypeOnlyFinding(
        entry_point_idx=0,
        path="apps/api/src/db/index.ts",
        type_only_names=("DrizzleTx",),
    )]


def test_find_type_only_logs_when_source_missing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Skip-logging breadcrumb (Linus review #6): a missing entry_point
    file must leave an INFO-level trace so a misconfigured ``project_dir``
    silently skipping every check is greppable in the orchestrator logs."""
    import logging
    eps = [_Ep2("apps/api/src/never-written.ts", ["foo"])]
    with caplog.at_level(logging.INFO, logger="darkfactory.adapters.typescript.type_only_exports"):
        findings = find_type_only_exports(eps, tmp_path)
    assert findings == []
    assert any(
        "never-written.ts" in r.message and "not found" in r.message
        for r in caplog.records
    ), "expected a skip breadcrumb in INFO logs"
