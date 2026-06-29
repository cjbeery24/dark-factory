# Spec-Overburden Campaign — Kickoff

> **Thesis (Caleb, 2026-06-29):** The spec author is being made to write HOW — internal
> API design, dependency internals, test mechanics, and pipeline scaffolding — when a spec
> should describe only WHAT (observable product behavior). Much of what we've built lately
> (vehicle machinery, the deliverable-scope check, the Controllable-Dependency Seam check)
> *helps the author write the HOW or gates the spec on it* — i.e. treats the symptom. The
> root fix is to move the HOW OFF the author and onto the architect / implementer / a shared
> agent convention / pipeline-derived scaffolding.
>
> Worked example: `2026-03-28-frontend-bootstrap.md` (197 lines). ~70% is not product
> behavior.

---

## Part 1 — The WHAT-only spec (the ~15-line core)

```markdown
# Frontend Application Shell

## Problem
No frontend exists. We need an application shell — screen navigation, authenticated
backend communication, and safe handling of configuration/secrets — so feature teams can
build UI on top of it.

## Behavior

B1 — Navigation.
- Visiting the root shows the dashboard.
- There is a login screen.
- An unrecognized path shows a "not found" screen that offers a way back to the dashboard.
  (Each screen is identifiable to a user, e.g. by a heading.)

B2 — Backend communication.
- The app makes backend requests and receives the response data on success.
- A request the backend REJECTS and a request that FAILS TO REACH the backend are both
  surfaced to the caller as errors — never silently treated as success. A rejection carries
  the backend's error identity (code/message/status); a connectivity failure is
  distinguishable from a rejection.
- The success/error envelope is the backend's contract (see the backend API spec); this
  spec does not redefine it.

B3 — Authentication.
- When a user session is present, backend requests are authenticated as that user.
- When no session is present, requests carry no credentials, and none are fabricated.

B4 — Secret safety.
- The code shipped to the browser contains no server-only secret value.
- The app's backend address is supplied by build-time configuration.

## Out of scope
Visual styling, developer-tooling ergonomics, and the build toolchain itself (trusted).
```

That is the entire product. Four behaviors a person (user or integrating developer) can
observe, plus one referenced external contract.

---

## Part 2 — Where each removed piece's burden moves

| Removed from the spec | Product behavior? | Who must derive it now |
|---|---|---|
| `apiClient.get/post/put/delete<T>`, `ApiError{code,message,statusCode}` class, file path `apps/web/src/lib/api-client.ts` | **No** — internal API design | **Architect** designs the client interface from B2/B3 |
| Error-code *names* (`PARSE_ERROR`, `NETWORK_ERROR`, `UNKNOWN_ERROR`) | The *distinctions* yes (B2 keeps "connectivity failure is distinguishable"); the *names* no | **Architect** names them |
| `{data}` / `{error:{code,message}}` envelope shape | Yes — but it's the **backend's** contract | **Referenced** from the backend spec (a dependency); not authored here |
| `VITE_` prefix, `import.meta.env.VITE_API_URL`, "TS env declaration exposes only VITE_ names" | **No** — Vite's mechanism | **Implementer** applies the stack's config convention to satisfy B4 |
| fetch seam (`globalThis.fetch` at call time, `vi.stubGlobal`, "not snapshotted") | **No** — test mechanics | **Shared agent convention** (implementer + QA-holdout + test-engineer default to the ecosystem's standard mockable idiom) — converges the blind agents WITHOUT the spec |
| "async (reject not throw)" | **No** — impl choice | **Architect/implementer** |
| §9 Scenario Intent table (Layer / ac_id) | **No** — a test plan | **Test-engineer** derives scenarios from B1–B4 (it already generates scenarios; the table pre-empts its job) |
| Tier-1 contract TC-1 (`pnpm build && ! grep …`) | **No** — a verification command | **Pipeline/architect** derives how to verify B4's bundle invariant |
| `disc:differential` / `disc:invariant` / `Vehicle` / `Blocking` / Non-Negotiable list / `subprocess` marker | **No** — pipeline bookkeeping | **Pipeline** infers criticality + verification vehicle |
| Metadata (`spec_type`, `feature-id`, `depends-on`) | **No** — pipeline routing | Plan/pipeline metadata (not author prose) |

---

## The strategic read (why "we may be fixing the wrong thing")

Every row whose "who derives it now" is the **architect**, **implementer**, a **shared
convention**, or the **pipeline** is a place where recent work has been *helping the author
produce the HOW* rather than *removing the HOW from the author*:

- **Vehicle machinery (Phase 0/1, the classifier):** helps the author/pipeline annotate how
  an anchor is verified (`vehicle: subprocess`, Tier-1 contracts). But the author shouldn't
  be choosing verification vehicles at all — that's pipeline-derived.
- **Deliverable-Scope check (shipped):** *correct and keep* — it removes HOW (drops
  dependency-behavior ACs). This one is aligned with the thesis.
- **Controllable-Dependency Seam check (uncommitted):** identifies a real blind-agent
  divergence, but its remedy *taxes the author with test mechanics*. Wrong layer. The
  divergence should be fixed by a shared agent convention; the validator should not gate the
  author into writing `globalThis.fetch`/`vi.stubGlobal`.

So the campaign's job is to invert the default: **the author writes WHAT; the pipeline
derives HOW.** Concretely:
1. **Template/skill:** stop demanding Interface Contracts, Scenario Intents, Tier-1
   contracts, and vehicle/blocking annotations from the author. Demand only behavior.
2. **Architect:** own the interface/design derivation (client API, error taxonomy,
   verification approach) from the WHAT.
3. **Shared agent convention:** implementer + QA-holdout + test-engineer converge on the
   ecosystem's standard test-injection idioms (kills the seam divergence without a spec
   burden).
4. **Pipeline-derived scaffolding:** scenario intents, contracts, criticality, and vehicles
   are generated, not authored.
5. **Re-examine** whether the vehicle machinery and the seam check survive this inversion or
   become redundant.

## Open questions for the campaign
- How thin can the spec get before the pipeline can't act on it? (The architect must absorb
  the slack — is it reliable enough?)
- Cross-spec contracts (the backend envelope): how does a WHAT spec *reference* another
  spec's contract instead of redefining it?
- Does removing author-authored ACs break the coverage floor / blind-holdout model (which
  currently keys off author anchors)?
- What's the minimum the author MUST still provide for the pipeline to be deterministic
  (e.g. the non-negotiable invariants)?
