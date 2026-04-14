# Release cadence

Dark Factory ships **minor versions** (`X.Y` → `X.Y+1`) on a **monthly-or-slower** cadence. Patch versions (`X.Y.Z` → `X.Y.Z+1`) ship on demand for bug fixes that don't change a public surface.

## What "minor" means here

| Bump | Triggers | Examples |
|------|----------|----------|
| **Patch** (`X.Y.Z` → `X.Y.Z+1`) | Bug fixes, doc fixes, internal refactors with no public-surface change | Tighten an error message; fix a quality-gate command bug; rewrite a test fixture |
| **Minor** (`X.Y` → `X.Y+1`) | Net-new features, new public exports, deprecations, *removal of items deprecated ≥ 2 minor versions ago* | Add a new adapter; add a new config key; remove a deprecated re-export that's been warning since `X.Y-2` |
| **Major** (`X` → `X+1`) | Breaking changes to anything not previously deprecated, large architectural shifts | Change the `EcosystemAdapter` ABC in a non-backward-compatible way without a prior deprecation cycle |

## Why this matters for the deprecation lifecycle

`docs/ADAPTER-GOVERNANCE.md` defines a **two-minor-version** deprecation window for re-exports and renamed symbols (introduce `DeprecationWarning` in `X.Y`; eligible for removal in `X.Y+2`). That window is anchored to *this* cadence — so "two minor versions" means at minimum two months of warning, in practice longer.

If the cadence slips, the lifecycle slips with it. Removing a deprecated symbol is gated on **time elapsed in minor-version units**, not wall-clock months — so a slow release month doesn't shorten user runway.

## Operational notes

- Releases are tagged on `main`. Pre-release (`X.Y.Z-rc.N`) tags are used for risky cuts.
- The plan owner cuts releases. There is no calendar-driven release train; we cut when the work is ready.
- Every tagged release — patch or minor — gets its own `CHANGELOG.md` entry. Trivial entries are short, not absent. Reconstructing a regression 14 months later is hard enough without missing changelog rows.
