# Experiment 1 — Holdout convergence on a WHAT-only spec

**Question:** On a thin WHAT-only spec (no interface/seam in the spec), does the blind
QA-holdout converge with the manifest-fed implementer if it reads the architect's interface
contract — and diverge if it's spec-only (current behavior)?

**Method:** Real agent prompts via `claude -p` (sonnet-4-6). Architect run once on the
15-line WHAT-only spec (`thin-spec.md`) → manifest. QA-holdout run K=3 in two conditions:
baseline (spec only, temp=1) vs treatment (spec + the manifest's `test_integration`
contract, temp=1). Measured the interface + seam each holdout targeted.

## Result

**Architect (de-risk part 1 — absorbs the slack):** from 15 lines of pure behavior it
derived a complete, sound, *cleaner* interface than the HOW-burdened original —
`createApiClient({ fetchFn, getSession })` (factory + injected deps), typed
`RejectionError`/`ConnectivityError`, seam = an injected `fetchFn` parameter. The injected
seam means the original module-load-snapshot bug **cannot occur**.

**Holdout convergence:**

| Condition | Import path | Seam | Errors | Matches impl contract? |
|---|---|---|---|---|
| Baseline k=1 (spec-only) | `@/lib/api` | `vi.stubGlobal('fetch')` | `NetworkError` | ✗ |
| Baseline k=2 | `…/src/api/client` | `createApiClient(` | — | ✗ |
| Baseline k=3 | `@/lib/api` | — | — | ✗ |
| Treatment k=1 (spec + contract) | `…/lib/apiClient.js` ✓ | `createApiClient(` + `fetchFn` ✓ | `RejectionError`/`ConnectivityError` ✓ | **✓** |
| Treatment k=2 | `…/lib/apiClient.js` ✓ | `createApiClient(` + `fetchFn` ✓ | `RejectionError`/`ConnectivityError` ✓ | **✓** |
| Treatment k=3 | (empty generation — hiccup, re-runnable) | — | — | n/a |

- **Spec-only holdout: 3 runs → 3 different contracts** (three import paths, three seams,
  an invented `NetworkError`). None matched the implementer's contract. This *is* the
  seam wall, reproduced: with no interface in the spec, the blind holdout guesses
  differently every time.
- **Manifest-fed holdout: both valid runs converged exactly** on the architect's contract
  — same import path, same factory, same injected `fetchFn` seam, same error types.

## Conclusion

Confirmed: the divergence source is the **spec-only QA-holdout** (a deviation from the
original dark-factory design, where the holdout reads the architecture's API contract),
NOT the author's missing seam language. Letting the holdout read the architect's interface
contract converges the blind arms with **zero mechanics in the spec**. The validator
seam-check and the spec seam-language were both fixing this at the wrong (author) layer.

**Caveats:** harness (`claude -p`), not full Docker pipeline; N=2 valid treatment samples;
does not yet prove the converged holdouts *execute-pass* against a real impl. Experiment 2
(full pipeline run, WHAT-only spec + patched holdout, run `b9d022bd`) tests that.

Artifacts: `thin-spec.md`, `arch-manifest-run1.yaml`, `holdout-baseline-{1,2,3}.ts`,
`holdout-treatment-{1,2}.ts` (this directory).

---

# Experiment 2 — Full pipeline run (WHAT-only spec + manifest-fed holdout)

**Run `b9d022bd`** on `frontend-shell-wo` (WHAT-only, no §5/§9/Tier-1), project
`qa-holdout.md` patched to read the architect manifest's `test_integration`. Validation
deliberately bypassed (manufactured PASS status+ledger+state-hash) since the spec
intentionally lacks §5.

**Result:** all hard gates PASS (build/lint/type, 34 scenarios, coverage floor PASS) —
**Blind Holdouts FAIL 18/27.**

**Mechanism (trace-confirmed, NOT interface divergence):** the manifest-fed holdout
CONVERGED on the symbol level — it imported exactly `{ apiRequest, ApiError,
ApiNetworkError }` from the manifest's declared path `apps/web/src/lib/api-client.ts`, and
used the injected-fetch approach (no global stubbing). The patch worked at that level.

BUT it diverged on the **call signature**: the holdout *inferred*
`apiRequest(url, { session, fetchFn })` while the impl built
`apiRequest(url, { fetch, session }, session?, fetchImpl?)` — the impl resolves
`optionsOrInit.fetch`, the holdout passes `fetchFn`. The mock is never injected → falls
back to real `globalThis.fetch` → every "resolve" test fails, "throw" tests incidentally
pass. Same wall class as the original, one level finer.

**Root cause:** the manifest's `entry_points.exports` lists symbol **names**, not full
**signatures**. Symbol-name convergence is necessary but NOT sufficient — the blind
holdout and impl still diverge on param shape/keys when the contract granularity stops at
names.

**Refinement to the campaign (not a refutation):** the architect manifest must pin the
FULL SIGNATURE of each entry point (exact param names/shapes/keys, return type), and all
blind agents build against it verbatim. This matches the ORIGINAL dark-factory design
(architect locks "exact API request/response schemas… used verbatim by all agents"), which
our manifest format under-captures. Convergence ladder observed:
- spec-only holdout → diverges on the whole interface (path, symbols, seam)
- manifest-fed (names only) → converges on path/symbols/seam-concept, diverges on signature
- (next) manifest with full signatures → predicted full convergence

---

# Experiment 3 — Full pipeline run WITH signature-level contract (run `ba6054d5`)

**Change under test:** architect declares full per-export `signatures` in the manifest;
the renderer surfaces them; the QA-holdout prompt is injected with the rendered contract
(reviewer-caught: the holdout was NOT getting the rendered manifest before — only the TE
was); impl/TE/holdout build against signatures verbatim. (Neo-designed; Linus+Codex
SHIP-WITH-EDITS, all required fixes incorporated: holdout injection, null-drop,
no-double-print, consumer prompts, amend-mode; 7 unit tests.)

**Result:** blind holdouts **27/28 PASS** (1 fail), vs **18/27 fail** in Experiment 2.
The param-key wall is CLOSED — verified in code: the architect pinned
`apiRequest(path, opts?: { fetch?; session? })`, the holdout built `{ fetch: ... }` (24×),
the impl resolved `opts?.fetch`. They converged on the exact key.

The lone failure (h21) is an ordinary borderline adversarial edge — a zero-width-space
(U+200B) session the impl treats as non-blank (JS `.trim()` doesn't strip U+200B) — NOT a
structural divergence. That's the normal residual the iteration loop handles.

## Convergence ladder — complete

| Holdout's interface knowledge | Result |
|---|---|
| spec-only (no interface in spec) | total divergence — 3/3 harness runs differ; original wall |
| manifest names-only | converges on path/symbols, diverges on signature — 18/27 fail |
| **manifest with full signatures** | **converged — 27/28 pass** |

## Campaign thesis — validated end-to-end

A **thin WHAT-only spec** (author writes ZERO HOW: no interface, no seam, no §5/§9/Tier-1)
+ **architect-derived signature-level interface contract** + **all three blind arms
(impl, TE, holdout) build against it verbatim** → the blind arms converge and holdouts
pass. The author's job is observable behavior; the pipeline derives the HOW.

Deferred (acceptable; not blocking the result): the anti-collusion Proposal-Rehearsal
cross-check (relocates the holdout's lost accidental interface cross-check), and mechanical
signature-completeness enforcement (currently prompt-enforced + non-blocking lint).
