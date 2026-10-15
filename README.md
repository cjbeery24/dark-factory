# Dark Factory

A spec-driven pipeline that has LLM coding agents build software **while treating
those agents as untrusted**. Each agent runs in its own Docker container. Filesystem
masks control what each one can read, and an allowlist proxy controls what each one
can reach on the network. The agent that writes the code never sees the adversarial
tests that decide whether its code ships.

> **Authorship.** I designed and directed this project, and wrote most of it with AI
> coding agents (Claude Code, with Codex as a second reviewer). I set the
> architecture, the threat model and the barrier rules. I reviewed the changes and made the
> ship/no-ship calls. The agents wrote most of the code and most of the commit
> messages. Every commit is under my name, and the history has no `Co-Authored-By`
> trailers, so please read the log as "AI-assisted under my direction" and not as
> hand-written code.
> Some stretches ran as autonomous agent loops under standing instructions I gave in
> advance, notably 2026-06-11 and the night of 2026-06-24. The June 24 run included a
> barrier change I had pre-authorized. Those runs are logged in
> [`docs/plans/00-RESET-PLAN.md`](docs/plans/00-RESET-PLAN.md).
> — Caleb Beery

## Why this is an AI-control problem

When an agent writes both the code and the tests, or can read the tests that grade
it, it can satisfy the grader without satisfying the spec. Dark Factory takes that
as the starting assumption. It is meant to stay safe when an agent:

- **games its evaluation**: reads the hidden tests or the gate reports, or
  overfits to feedback that names what failed;
- **acts with no human approval**: every agent runs `claude -p
  --dangerously-skip-permissions`, because nobody is in the loop to approve tool calls;
- **gets prompt-injected or runs hostile code**: for example a dependency's install
  script, or text in the target repo.

The design response is to stop relying on the agent to behave and enforce the
limits from outside it. The container, its mounts and its network are the
security boundary. Prompt instructions and `--disallowedTools` are only
defense in depth.

## Two-minute tour

| Read this | For |
|---|---|
| [`darkfactory/docker/barriers.py`](darkfactory/docker/barriers.py) | Which directories each role can't see, as one table in code |
| [`darkfactory/docker/mounts.py`](darkfactory/docker/mounts.py) (`build_tmpfs_masks`) | How masking works: an empty read-only tmpfs over each hidden directory |
| [`docs/INTEGRATION.md` § Egress Filtering](docs/INTEGRATION.md#egress-filtering), [`docker/filter-*.txt`](docker/) | The egress allowlist proxy |
| [`docker/versions.env`](docker/versions.env) | Digest-pinned base images and exact tool versions |
| [`docs/AUTHORIZATION-MODEL.md`](docs/AUTHORIZATION-MODEL.md) | The change-control rule for anything that could widen what an agent can read, written after a real mistake |
| [`docs/dev/2026-06-24-wire-blind-holdout-gate-design.md`](docs/dev/2026-06-24-wire-blind-holdout-gate-design.md) | The holdout tests weren't running; why the fix tells the implementer pass/fail only |
| [`docs/benchmark/`](docs/benchmark/) | Raw run scorecards (JSONL) |

## How it works

A spec goes through a chain of roles. Each role runs in a fresh container with its
own view of the project:

| Role | Writes? | Cannot see (masked) | Job |
|---|:---:|---|---|
| Spec Validator | ro | source, `.git` | Check the spec is complete and consistent |
| Architect | ro | visible tests, holdouts, audit, state, `.git` | Produce the architecture manifest |
| Test Engineer | ro* | source, holdouts, `.git` | Write **visible** acceptance tests from the spec |
| QA Holdout | ro* | source, visible tests, `.git` | Write **blind adversarial** holdout tests from the spec |
| Implementer | rw | holdouts, gate reports, audit, state, `.git` | Write the code. It can read the visible tests as TDD targets |
| Quality Gate | rw | — | Build, lint, type-check, then run the visible tests **and the holdouts** |
| Reviewer | ro | visible tests, holdouts, reports, `.git` | Code review against the spec |
| Oracle | ro | — | Ship / no-ship |

\* Writes only to its own output directory.

**Enforcement layers, strongest first:**

1. **Filesystem masking.** Each hidden directory is covered by
   `--tmpfs /project/<dir>:ro,size=0`, so inside the container it is empty, not merely
   off-limits. `.git/` is masked too, so `git log` and `git show` can't recover hidden
   content. Code-blind roles also have compiled output (`dist/`, `build/`, …) masked,
   after a Test Engineer was caught reading the implementation's compiled JS. Pre-flight
   checks refuse to launch when a masked directory is itself a symlink, or (for the Implementer) when a source file is hard-linked to a masked file.
2. **Egress allowlist.** Agents sit on `--internal` Docker networks with no default
   gateway. A tinyproxy sidecar forwards only `CONNECT` requests to allowlisted hosts,
   and denies everything else by default. Analysis roles can reach `api.anthropic.com`
   and nothing else. Build roles can also reach the package registries.
3. **Supply-chain pinning.** Base images are pinned by `@sha256` digest, and the
   agent CLI and system packages by exact version. Lockfiles are generated in-container.
4. **Feedback sanitization.** Between iterations the Implementer gets feedback keyed
   to spec sections (FR-N / EC-N), never test names or assertion text. From the
   holdout suite it gets one bit: *"blind adversarial validation failed."*
5. **Audit and defense in depth.** Agent output is scanned for references to
   restricted paths. Most roles also get `--disallowedTools` (for example no WebSearch or
   WebFetch for the Implementer) plus a barrier prompt.

**Change control on the barrier itself.**
[`docs/AUTHORIZATION-MODEL.md`](docs/AUTHORIZATION-MODEL.md) came out of a real
mistake. A one-line "make lint failures gating" fix (`d2fecbf`, reverted in
`5130935`; see the SHA note below) quietly made a Reviewer-only signal readable by
the Implementer. Any change that alters which strings reach a directory a
restricted agent can read now needs explicit sign-off. Commits that touch barrier
surfaces must declare an `Audience:` line.

## A finding worth reading

The QA Holdout's adversarial tests were being **generated and then thrown away**.
The gate only ever ran the visible tests. A spec shipped with a Bearer-token bug
(a whitespace-padded token was `.trim()`ed and accepted), even though a holdout
test that catches it existed. The only remaining backstop was an LLM reviewer, and
it approved the bug. The fix wires the holdouts into the gate as a **binary** signal.
Richer feedback (expected vs. actual, or which spec anchor failed) would leak the
attack vector and invite iterative gaming, which is a semantic leak that a
substring scanner can't detect.

## Known limitations

These are stated plainly; most are deliberate trade-offs, not oversights.

- **No permission system inside containers.** `--dangerously-skip-permissions` is
  required for unattended runs. An agent can run any command and write anywhere
  that is mounted writable. The protections are the masks, mounts and network
  limits. The agent's own self-restraint is not one of them.
- **Allowed hosts can still carry data out.** `api.anthropic.com` is reachable from
  every role. `storage.googleapis.com` is on the build-role allowlist and reaches
  *any* GCS bucket. An agent determined to exfiltrate data could use either.
- **Semantic leakage is not detected.** The audit matches path strings. It catches
  accidental and naive barrier violations, not paraphrase. Spec-anchored feedback
  still tells the Implementer which sections have coverage, and the barrier weakens
  with each NO-SHIP iteration.
- **Compiled-output masking is a denylist** of common build directory names, so an
  unusual build tool's output directory could slip through.
- **The host is trusted.** Docker escapes, a compromised `claude` binary in a pinned
  image (pinning shrinks the window but doesn't close it), and the host machine
  itself are out of scope.
- **Authoring skills run without isolation.** The `.claude/skills/df-*` helpers run in
  the user's own Claude Code session for steps that don't touch the barrier, such as
  writing and validating specs. Steps that do touch it go through `dark-factory run`.
- **Narrow ecosystem.** [`docs/audits/2026-04-14-ecosystem-leakage.md`](docs/audits/2026-04-14-ecosystem-leakage.md)
  is a self-audit of *ecosystem coupling* (not barrier leakage). It found 54
  load-bearing Node/TypeScript/Postgres assumptions in the core. Python/FastAPI works;
  other stacks are untested.
- **Code health.** `darkfactory/pipeline/orchestrator.py` (~7.6k lines) and
  `iteration.py` (~6.8k lines) are too large and are known debt.

## Results

Scorecards in [`docs/benchmark/`](docs/benchmark/). Each row is one end-to-end
pipeline run. Specs are small: a parsing function, a notes CRUD API, and the same API
with Postgres.

| Battery | Model | Outcome | Cost |
|---|---|---|---|
| `scorecard-2026-06-12` (Python + TS, 5 specs) | claude-sonnet-4 | 13 clean ships / 15 (1 escalated, 1 aborted) | ~$92 |
| `scorecard-ts-26-27` (TS) | claude-sonnet-4 | 6 / 6 | ~$32 |
| `scorecard-sast-compare-*` | claude-sonnet-4.6 | 3 / 3 | ~$17 |
| `scorecard-tier-c-ts*` (TS + Postgres) | sonnet-4 / 4.6 | 1 / 6 (4 escalated, 1 aborted) | ~$51 |

Sample sizes are small, and the hardest tier (Postgres + TS) mostly escalates to a
human. That is the designed failure mode: escalate rather than ship something
unverified.

## Running it

Requires Python ≥ 3.11 and Docker. Real pipeline runs also need Claude Code
credentials and **spend API money**. The test suite doesn't.

```bash
pip install -e '.[dev]'          # add ,sql-lint for the pglast-backed tests
python -m pytest                 # LLM-calling tests are excluded by default (-m 'not llm')
bash docker/build-images.sh node # agent + proxy images, for real runs
```

See [`docs/QUICKSTART.md`](docs/QUICKSTART.md) for `dark-factory init` / `run` on a
target project.

**Test status, measured from a clean clone on 2026-10-15** (`pip install -e
'.[dev,sql-lint]'`, Docker running, default `python -m pytest`): **5,626 passed,
77 failed, 5 skipped**. All 77 failures are integration or adapter-contract tests
that have fallen behind the code:

- 14 start real containers but aren't marked `docker`;
- 13 use plan fixtures that have drifted from the plan parser;
- 6 assume the in-container `/project` path;
- 5 have fixtures missing a directory that a newer fail-closed preflight now
  requires (`darkfactory/docker/mounts.py`). These include reviewer-mount barrier
  tests, which fail *closed*: the agent isn't launched;
- 2 are real overruns of a governance cap: the TypeScript and Postgres architect
  addenda exceed their line limits;
- the rest are stale assertions (event sequences, retry counts, enterprise gating)
  where the pipeline changed and the test didn't.

The CI workflows in `.github/workflows/` are manual-only (`workflow_dispatch`) in
this public copy.

## About this history

The repository was developed privately, 2026-02-28 → 2026-06-29. Before publishing,
the history was rewritten with `git filter-repo` to remove a local settings file,
compiled bytecode and a business-planning document, and to replace local absolute paths
with `~/`. Nothing else was changed. **Every commit SHA changed.** The old SHAs that
docs and commit messages cite (e.g. `d2fecbf`) resolve through
[`docs/history/sha-map.txt`](docs/history/sha-map.txt) (old → new).

## Credits

The 11-agent / 9-phase pipeline shape, including holdout tests that the builder
never sees, follows Haytham Yassine's (Yassine Dwkte) public write-up of his
"Dark Factory" pipeline (LinkedIn, March 2026). [`docs/goals/`](docs/goals/) records
how this project reads that design. The idea of a "software factory" with no human
code review comes from StrongDM's [factory.strongdm.ai](https://factory.strongdm.ai/).
The `/df-densify-spec` skill (the "Yassine Product Owner pattern") is deprecated
but kept, because the Phase 3.5 reproducibility harness in `tests/reproducibility/`
measures it.

## License

MIT; see [LICENSE](LICENSE). The one exception is the vendored Semgrep rules in
`darkfactory/review/sast_rules/`, which stay under their upstream
[Semgrep Rules License v1.0](darkfactory/review/sast_rules/LICENSE).
