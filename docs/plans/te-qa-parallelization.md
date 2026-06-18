# F Move 2b — Parallelize Test Engineer + QA Holdout scenario generation

## Goal

The per-spec scenario-generation step (`_step_scenario_generation` in
`darkfactory/pipeline/orchestrator.py`) runs the **Test Engineer** (TE — visible
AC-aligned tests → `.dark-factory/scenarios/<spec>/`) and the **QA Holdout** (blind
adversarial tests → `.dark-factory/holdouts/<spec>/`) **serially**. Telemetry puts
them at ~11.5 min + ~10.5 min. They are **independent**:

- TE reads only the spec/manifest; its container masks `.dark-factory/holdouts/`.
- QA Holdout reads only the spec/manifest; its container masks `.dark-factory/scenarios/`.
- They write to disjoint directories.

Running them concurrently saves ~10–11 min on a ~60-min run. This is the **first**
intra-spec concurrency in the pipeline, so all risk is in orchestration mechanics,
not the agents.

This is a **performance optimization, not a correctness fix** — it must be provably
behavior-preserving vs. the serial flow, or it ships behind a default-OFF flag.

## The parallel region (exactly two units)

```
SERIAL prefix (main thread, unchanged):
  step_begin("test_engineer")        # line 3052, in-place config refresh
  regen rmtree, sentinel/te_files_existed detection
  if te_sentinel: return             # sentinel specs never reach fan-out

  ── NEW: step_begin("qa_holdout")   # hoisted out of _invoke_qa_holdout, run
                                     #   SERIALLY here so the two config
                                     #   refreshes never race (risk #2)

PARALLEL region (only when flag on AND TE work remains):
  branch A: _invoke_qa_holdout(...)               # single-shot, self-isolating
  branch B: _invoke_test_engineer_generation(...) # NEW helper = today's TE block
                                                  #   (staging → empty-retry →
                                                  #    SA-validation → coverage floor)

SERIAL downstream (main thread, unchanged):
  TE pre-acceptance, iteration loop, repair — untouched
```

`te_files_existed` (resume after TE success) and `te_sentinel` short-circuit **before**
the fan-out exactly as today; in the `te_files_existed` case QA still runs (serially —
there is no TE work to overlap with), preserving the existing resume contract.

## Implementation shape

1. **Extract** today's TE generation body (mkdir scenarios_dir → coverage floor,
   orchestrator.py ~3120–3614) verbatim into a new private helper
   `_invoke_test_engineer_generation(spec_run, config, barriers, project_dir, *,
   scenarios_dir, events_file, audit_path, dry_run, active_adapters)`. Pure move —
   no logic change. This keeps the orchestration diff small and gives the parallel
   region a single callable for branch B.

2. **Hoist** `step_begin("qa_holdout", ...)` out of `_invoke_qa_holdout` to the caller
   (it is invoked from exactly one site). Both `step_begin` calls then run serially on
   the main thread before any fan-out. This requires moving `_invoke_qa_holdout` from
   `PINNED_STEP_FUNCTIONS` to `RETRY_BODY_HELPERS_EXEMPT_FROM_OFFLIST` in
   `tests/unit/test_step_begin_enforcement.py` — the existing, sanctioned pattern for "a
   step-body helper whose pinned caller (`_step_scenario_generation`) owns the
   `step_begin`" (cf. `_classify_once`/`_step_classify`). The enforcement is **static AST**
   (first-statement check + off-list invoke_agent check), NOT a runtime/thread-sensitive
   wrapper (the test file explicitly declines a `__getattribute__` wrapper), so there is no
   thread-stack coupling at runtime to worry about (resolves Neo B3).

3. **Fan out** with `concurrent.futures.ThreadPoolExecutor(max_workers=1)`:
   ```python
   if parallel:
       with ThreadPoolExecutor(max_workers=1, thread_name_prefix="qa-holdout") as ex:
           qa_future = ex.submit(_invoke_qa_holdout, ...)
           _invoke_test_engineer_generation(...)   # branch B on main thread; may raise
       # with-exit shutdown(wait=True) ALWAYS joins QA → no orphan even if B raised
       qa_future.result()    # reached only if B succeeded → surfaces any QA exception
   else:
       _invoke_qa_holdout(...)
       _invoke_test_engineer_generation(...)
   ```

## How each design risk is addressed

**1. Concurrent host-side append writers — THREE files, not two (Neo B1).**
Three host-side append-only files get concurrent writes from the two branches:
- `events.jsonl` via `emit_event` → `append_jsonl` (`json_helpers.py`)
- `run-telemetry.jsonl` via `_emit_telemetry` (`agents.py:473`, its own `open("a")`)
- the **audit log** via `audit_log` (`audit/logger.py:16`), called on every container
  launch/exec/barrier-check from `containers.py:59,101,138` and `checker.py` — **on both
  branches**. Missed in the first draft; this is the barrier-load-bearing one (a torn
  `BARRIER_VIOLATION`/`CONTAINER:` line corrupts the record the barrier audit consumes).

Each does `open("a")` + one `write()`. O_APPEND makes the *syscall* atomic, but text-mode
`TextIOWrapper` may split a record larger than its buffer (~8 KB) into multiple syscalls —
torn lines under concurrency (the redacted `docker run` audit line is long enough to hit
this). Fix: a **single** module-level `threading.Lock` in `json_helpers.py` plus a shared
`locked_append_text(path, text)` primitive. `append_jsonl`, `_emit_telemetry`, and
`audit_log` all serialize through that one lock instance. Same-process threads only — the
lock is sufficient because the only concurrent writers are host threads (agents write
inside containers to disjoint per-role paths, never to these three files). **Scope
assumption:** one orchestrator process per run; if a future plan-level change parallelizes
specs across *processes*, this lock is insufficient and `fcntl`/bounded-record O_APPEND
would be required (N3). Proven by a unit test that hammers all three writers from many
threads with >8 KB records and asserts every line parses and the count is exact.

**2. `step_begin` returned-config race.** `step_begin` → `refresh_config_from_environment`
mutates the shared `config` **in place** and touches a provenance dict. Running both
branches' `step_begin` concurrently is a shared-mutable-config race. Fix: call **both**
`step_begin` calls serially on the main thread before fan-out (TE at line 3052 already;
QA hoisted to the caller). The parallel branches then only call `invoke_agent`, which is
read-only on config (verified: it reads `config.model_ids`, `config.language`, etc., and
builds barriers via `live_barrier_for(role, config)` without mutating config). No branch
mutates config → no race.

**3. Forked error / timeout handling.** The two failure modes stay distinct and match
today:
- **TE failure** (non-zero exit, MISNAMED, validation exhaustion, coverage floor) raises
  `DarkFactoryError` from branch B on the main thread → propagates exactly as today. The
  `with`-exit still `join`s QA first (no orphan), then the TE exception propagates; we
  never reach `qa_future.result()`, so a concurrent QA error cannot mask the TE failure.
- **QA failure**: `_invoke_qa_holdout` already swallows agent errors (warns, never gates).
  Any *unexpected* exception (filesystem/programmer error) is captured in the future and
  re-raised by `qa_future.result()` **after** TE succeeds — preserving "an unexpected QA
  error aborts the run," which is today's behavior (QA runs first serially today).
  **Behavior divergence, accepted (Neo B2):** today a deterministic QA crash fails fast
  (~0 s, before any TE work). In the parallel path the same crash surfaces only after the
  `with`-exit joins QA *and* TE has finished — so the run burns the TE budget first and TE
  artifacts exist on disk when the step raises. This is acceptable for a perf change that
  is default-OFF and only matters for a *programmer/filesystem* QA error (the normal
  agent-failure path never raises). It cannot be fixed without cancelling the running TE
  thread (not cleanly possible). Listed in the observable-difference set below.
- **Timeout (exit 124)** in either branch is just a non-zero `exit_code` handled by that
  branch's existing logic; it cannot orphan the other branch because the `with` always
  joins.

**4. Ordering — stage THEN fan out.** Adapter contract-helper staging under
`scenarios/_contracts/` happens **inside** branch B (`_invoke_test_engineer_generation`)
before the TE container starts — identical ordering to today. QA does not need contracts
and writes only to `holdouts/`, a disjoint tree, so it is safe to run concurrently with
staging. No shared path is written by both branches.

**5. Resume safety.** `te_sentinel` / `te_files_existed` short-circuits are evaluated in
the serial prefix before fan-out, unchanged. `_invoke_qa_holdout`'s report-file
idempotency check is unchanged. On a `te_files_existed` resume, QA runs serially (no TE
work to overlap), preserving today's "QA still runs on resume" guarantee.

**6. Resource (two 4 GB / 4-CPU containers at once ≈ 8 GB, 8 CPU).** This is not merely a
saturation concern: on a tsc-heavy TS Tier-C spec the 4+4-on-8 CPU contention inflates the
TE's tool-time bucket by an amount comparable to the S41 timeout-salvage margin — see the
**Flip-ON gate** section below, which is the authoritative treatment of this coupling.
Gated behind a new config flag `scenario_parallel_generation` (`[gates]` section).
**Default: OFF** for the first release — this is the first intra-spec concurrency and end-to-end activation cannot
be exercised in unit tests (no Docker), so we ship the airtight mechanism + flag and flip
to ON only after a real pipeline run validates it, matching the project's
roll-forward-by-flag posture (`te_pre_acceptance_check`, `sast_compare_enabled`). When OFF,
the code path is byte-for-byte the serial flow (QA then TE, no thread). `dry_run` also
forces serial.

**Flip-ON gate (tightened post-rebase onto main's TE-timeout work — S0a/S0c/S41/L2).**
Main is actively hardening the Test Engineer against 900 s wall-clock SIGKILL timeouts
(`agent_timeout=900`, `agent_cpus=4`, `agent_memory=4g`). Running the TE concurrently with
the QA Holdout adds CPU/IO contention on an ~8-CPU host. An LLM agent's wall clock splits
into model-time (remote API round-trip — the majority, **immune** to local contention),
tool-time (local `tsc` compiles, file writes — CPU-bound, **exposed** to contention), and
idle-tail. Contention does NOT commonly cause timeouts (model-time dominates), but on a
tsc-heavy TS Tier-C spec the TE's tool-time is the largest discretionary bucket (the
SA-validate + coverage-floor loop re-runs `tsc`), and 4+4 CPU requests on 8 cores means the
TE loses cores during exactly those compile bursts. The inflation there approaches the
contended fraction of tool-time (toward ~2× on the worst spec), which is the **same order
of magnitude as the S41 salvage margin** — S41 was built for a run that walled at 54/55
anchors, one anchor's regen+compile over budget. So TE‖QA is **mildly adversarial to the
marginal TS Tier-C case specifically** (orthogonal everywhere else). This is inert while the
flag is OFF.

Therefore the flip-ON gate is **not** "any green run" — and a naive green run is actively
*wrong* here, because **S41 converts a TE timeout into a green ship** (it salvages the
partial suite and ships it: `_te_timeout_salvaged`, orchestrator.py:3506→3597, event
`scenario_timeout_salvage`). A flag-ON run that walled, got salvaged, and shipped green is
*exactly the regression* the gate must catch — not a pass. **Required flip-ON criteria**
(Neo design-gate ruling, 2026-06-18):

1. **No-salvage clause (load-bearing).** On every flag-ON run, TE `exit_code == 0` and
   `_te_timeout_salvaged == False` / no `scenario_timeout_salvage` event. A salvaged-but-green
   run is a **FAIL**.
2. **Spec & sample.** A **TS Tier-C (tsc-heavy, timeout-marginal)** spec, flag-ON vs. the
   serial baseline, same spec/model, **N ≥ 3** each (beat agent variance). A green Python
   Tier-B run (light tool bucket) is a false-confidence pass — does not count.
3. **Wall headroom.** Flag-ON TE total wall lands **≥ ~120 s under the 900 s timeout**
   (≈ one anchor's regen+compile), and `wall_attribution.time_in_tools_s` is not materially
   above the serial run's.
4. **Output identity.** Coverage, anchor count, and gate decision identical to serial.

Sequence the flip **after** main's TE-timeout/salvage code settles, so it is measured
against a stable baseline. The preferred fix that *removes* the tension (rather than just
measuring it) is **QA CPU-asymmetry** — when parallel, give the TE its full quota and starve
QA (`--cpus=2` / lower cpu-shares), since QA is not the gating, timeout-sensitive role and
has the whole TE wall to finish in; this keeps TE tool-time near-serial while still
overlapping the dominant model-time. That, plus a host-core safety floor (refuse fan-out
below `2×agent_cpus + headroom`), belongs in the **flip-ON PR**, not here. (Reviewers: Linus
ACCEPT, Codex SAFE-TO-MERGE 0-blocking, Neo ADJUST→APPROVE-FLAG-OFF with the no-salvage
clause above as the must-fix.)

**Observable differences vs. serial (Neo B2/B3 — risk #5 corrected).** The parallel path is
behavior-preserving on *outputs* (same files, same dirs, same masks, same gate decision)
but NOT byte-identical on *ordering/timing*: (a) `events.jsonl` / `run-telemetry.jsonl` /
audit-log line **interleave order** differs (each line is intact — the lock guarantees
that — only the relative order of TE vs QA lines changes); (b) a deterministic *unexpected*
QA exception surfaces later (after TE budget) instead of fast (B2); (c) wall-clock drops
~10 min. No change to gate rows, severities, resume-state writes, or which directory any
string lands in. The flag-OFF path is byte-for-byte today's serial flow.

**KeyboardInterrupt / SIGINT during the parallel region (Neo N4, accepted).**
`ThreadPoolExecutor.__exit__` does `shutdown(wait=True)`; worker threads aren't daemon and
can't be interrupted, so Ctrl-C during the parallel region blocks until the QA container
call returns (up to ~10 min) before propagating. Run-level `cleanup_containers` (by
`df-run-id` label) still reaps both containers. Accepted: rare, default-OFF; documented
here rather than worked around.

**7. Barrier integrity.** Two barrier'd containers run at once. Each agent's tmpfs masks
are computed per-role from `barriers.py` and applied to its **own** container at
`docker run` time — there is no shared mount between the TE and QA containers (TE mounts
`scenarios/` rw + `_contracts/` ro and masks `holdouts/`; QA mounts `holdouts/` rw and
masks `scenarios/`). The run's Docker networks are created once at run setup
(`setup_networks`, orchestrator.py:379), not per-agent, so concurrent `invoke_agent`
calls do not race on network creation. Both roles are analysis-network roles
(`_EXECUTION_ROLES == {IMPLEMENTER}`), so they share the analysis network — read-only
co-tenancy, no conflict.

## Authorization determination (`docs/AUTHORIZATION-MODEL.md`)

- **Trigger 1 — audience escalation:** does any string change the directory a
  barrier-restricted container can read? **No.** This change writes no new artifact and
  promotes no report row to gating; no severity changes. The same TE/QA outputs land in the
  same directories with the same mask configuration. The only new state is a thread + a
  write lock, neither of which crosses a barrier. (Note: main's S0a/S0c since this doc's
  first draft added per-invocation stream sidecars under `.dark-factory/failures/`. Those
  are **host-written, per-container, uniquely-named** (role+pid+atomic-`_STREAM_SEQ`) — the
  S0c author explicitly designed the naming to be "safe for future concurrent TE
  containers." `failures/` is already tmpfs-masked from all restricted roles, so running two
  restricted containers concurrently neither shares a sidecar path nor crosses a barrier.
  That artifact belongs to S0a/S0c, not to this change, and crosses no barrier under the
  fan-out.)
- **Trigger 2 — changed-surfaces declaration:** the diff does **not** touch
  `barriers.py`, `scripts/quality-gate.sh` report rows, `scripts/lib/scenario_lint.py`, or
  add any `.dark-factory/` artifact. No `Audience:` declaration is required.

**Determination: neither trigger fires.** This is a pure orchestration/perf change. (Will
be declared explicitly in the commit body regardless.)

## Test plan (real concurrency, not mocked-away)

All tests exercise the **real** threading/lock/error-surfacing code; only the Docker agent
bodies are stubbed (unavoidable without Docker).

1. **JSONL atomicity** (`tests/unit/`): N threads × M appends of >8 KB records through
   `append_jsonl`; assert exact line count and every line is valid JSON (no torn/interleaved
   lines). Repeat for `_emit_telemetry`.
2. **Genuine overlap**: stub `_invoke_qa_holdout` to set `qa_started`, sleep, set `qa_done`;
   stub `_invoke_test_engineer_generation` to record that at its start `qa_started` is set and
   `qa_done` is **not** → proves real overlap, not serialization.
3. **TE raises → QA joined, not orphaned**: TE stub raises `DarkFactoryError`; assert it
   propagates **and** `qa_done` was set (QA was joined by `with`-exit).
4. **QA raises after TE success**: QA stub raises; TE stub succeeds; assert the QA exception
   propagates from the step (via `qa_future.result()`).
5. **TE failure masks-not by QA**: TE stub raises a sentinel error while QA stub also raises a
   different error; assert the **TE** error is what propagates.
6. **Flag OFF → serial**: assert no worker thread is created and order is QA-then-TE.
7. **`step_begin` serial-before-fanout**: assert both step keys are registered before either
   agent body runs.
8. **Resume (`te_files_existed`)**: assert QA runs and the TE body does not, flag on or off.

## Out of scope (must stay serial / unchanged)

`_step_te_pre_acceptance`, the iteration loop, `_step_scenario_repair`, coverage-floor
regen invocations (these live *inside* branch B and run on its single thread, serially as
today). Only the top-level TE-generate vs QA-Holdout-generate pair is parallelized.
