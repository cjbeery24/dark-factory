"""Phase 6 I2: per-scenario structural fingerprint extraction.

Produces a deterministic structural digest of a scenario file — imports,
assertion count, test names — so V-dimension runs can detect scenario
drift without being confused by comment/prose churn.

Intentionally prose-blind:
- Assertion messages, inline comments, and docstrings do not affect the
  fingerprint.
- ``import_set`` / ``symbol_set`` are sourced from the active
  ``EcosystemAdapter.parse_test_imports`` (``ImportInfo`` list) so the
  TS adapter's existing ES/CJS parser is reused rather than duplicated.
- Assertion and test-name scanners are regex-based, language-agnostic,
  and best-effort — they recognize common ``expect(``, ``assert``,
  ``it(``, ``test(``, ``def test_`` shapes across JS/TS, Python, Go,
  and Rust. A scenario file that uses an idiomatic testing DSL will
  produce stable counts; drift appears only when test structure
  actually changes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable


# Assertion tokens. Order does not matter — each pattern contributes its
# own count to the total. Kept narrow so a test file isn't inflated by
# framework boilerplate (``beforeEach`` etc.) that isn't really an
# assertion. Matches are counted once per occurrence regardless of line.
_ASSERTION_PATTERNS = [
    re.compile(r"\bexpect\s*\("),             # jest/vitest: expect(x)
    re.compile(r"\bassert\s*\("),             # generic: assert(expr)
    re.compile(r"\bassert\s+\w"),             # pytest: assert x
    re.compile(r"\bassert!\s*\("),            # rust: assert!(...)
    re.compile(r"\bassert_eq!\s*\("),         # rust: assert_eq!(...)
    re.compile(r"\bassert_ne!\s*\("),         # rust: assert_ne!(...)
    re.compile(r"\bt\.(?:Error|Fatal|Errorf|Fatalf)\b"),  # go
    re.compile(r"\b(?:should|must)\.\w+\("),  # chai/should-style
]


# Test-name extractors — emit the first capture group.
_TEST_NAME_PATTERNS = [
    # jest/vitest/mocha — it('name', ...) / test('name', ...)
    re.compile(r"""\b(?:it|test)\s*\(\s*['"]([^'"]+)['"]"""),
    # jest/vitest — describe('name', ...) folded in so a file that only
    # uses describe blocks still emits structural names.
    re.compile(r"""\bdescribe\s*\(\s*['"]([^'"]+)['"]"""),
    # pytest — def test_name(...)
    re.compile(r"""\bdef\s+(test_\w+)\s*\("""),
    # go — func TestName(t *testing.T) / func BenchmarkName(...)
    re.compile(r"""\bfunc\s+(Test\w+|Benchmark\w+)\s*\("""),
    # rust — #[test]\nfn name(...)
    re.compile(r"""#\[test\]\s*\n\s*(?:pub\s+)?fn\s+(\w+)"""),
]


def compute_scenario_fingerprint(
    file_path: Path,
    adapters: Iterable[Any],
) -> dict[str, Any]:
    """Return a structural fingerprint for one scenario file.

    Fields:
        ``scenario_path`` — absolute path as string.
        ``import_set`` — sorted list of unique import specifiers.
        ``symbol_set`` — sorted list of ``specifier::symbol`` pairs.
        ``assertion_count`` — total matches across ``_ASSERTION_PATTERNS``.
        ``test_names`` — sorted list of unique test names.

    ``adapters`` is the active-adapter set; ``parse_test_imports`` is
    called on each in order until one returns a non-empty list (first
    adapter that claims the file wins). Adapters that return ``[]`` are
    treated as "didn't parse this file" — the orchestrator's typical
    path since only the language-matching adapter will extract imports.
    A markdown scenario (``*.scenarios.md``) produces empty import/symbol
    sets; its fingerprint still carries ``assertion_count=0`` and
    ``test_names=[]`` — the fact that it was emitted is the signal.
    """
    scenario_path = str(file_path)
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {
            "scenario_path": scenario_path,
            "import_set": [],
            "symbol_set": [],
            "assertion_count": 0,
            "test_names": [],
        }

    imports: list = []
    for adapter in adapters:
        parser = getattr(adapter, "parse_test_imports", None)
        if parser is None:
            continue
        try:
            found = parser(file_path)
        except Exception:
            found = []
        if found:
            imports = list(found)
            break

    import_specifiers: set[str] = set()
    symbol_pairs: set[str] = set()
    for imp in imports:
        specifier = getattr(imp, "specifier", "")
        if not specifier:
            continue
        import_specifiers.add(specifier)
        for sym in getattr(imp, "symbols", []) or []:
            symbol_pairs.add(f"{specifier}::{sym}")

    assertion_count = 0
    for pat in _ASSERTION_PATTERNS:
        assertion_count += len(pat.findall(content))

    test_names: set[str] = set()
    for pat in _TEST_NAME_PATTERNS:
        for m in pat.finditer(content):
            name = m.group(1).strip()
            if name:
                test_names.add(name)

    return {
        "scenario_path": scenario_path,
        "import_set": sorted(import_specifiers),
        "symbol_set": sorted(symbol_pairs),
        "assertion_count": assertion_count,
        "test_names": sorted(test_names),
    }


def iter_scenario_files(scenarios_dir: Path) -> list[Path]:
    """Return the set of scenario files under ``scenarios_dir``.

    Uses the same ``_TEST_PATTERNS`` as ``detection.has_scenario_files``
    so the fingerprint scan and the "does this dir have scenarios?" gate
    agree on what counts. ``_contracts/`` is excluded — those are
    adapter-owned wire helpers, not scenario files authored by the Test
    Engineer. Results are deduplicated and sorted by string path for
    stable event ordering.
    """
    from darkfactory.scenarios.detection import _TEST_PATTERNS

    if not scenarios_dir.is_dir():
        return []

    contracts_dir = scenarios_dir / "_contracts"
    seen: set[Path] = set()
    for pattern in _TEST_PATTERNS:
        for p in scenarios_dir.glob(pattern):
            if not p.is_file():
                continue
            try:
                p.relative_to(contracts_dir)
                continue  # inside _contracts/ — skip
            except ValueError:
                pass
            seen.add(p)
    return sorted(seen, key=str)
