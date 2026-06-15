# Vendored Semgrep security rules (maintained-external SAST)

These YAML rule definitions are **vendored verbatim** from the official
[`semgrep/semgrep-rules`](https://github.com/semgrep/semgrep-rules) registry,
pinned at the commit recorded in `RULESET_VERSION`. They are the *maintained,
externally-curated* ruleset that the SAST-swap (`docs/curation-audit.md`,
00-RESET-PLAN.md "Generalization thesis & curation policy") introduces to
measure — and eventually subsume — dark-factory's bespoke in-house security
regexes (`darkfactory/review/patterns.py`).

**These are NOT bespoke curation.** They are maintained upstream by the Semgrep
community and amortized across every Semgrep user — exactly the "orchestrate the
ecosystem's own tools" path the curation policy prefers over hand-encoded rules.
The curation-budget guard (`tests/unit/test_curation_budget.py`) therefore does
**not** count these toward the bespoke-curation budget. Adding rules here = good
(more maintained coverage); the budgeted axis is the *shrinking* count of bespoke
regexes in `patterns.py`.

## What is vendored

| Dir | Upstream source (at `RULESET_VERSION`) | Rules |
|-----|----------------------------------------|-------|
| `python/` | `python/lang/security/**/*.yaml` | 91 |
| `node/`   | `javascript/lang/security/**/*.yaml` + `typescript/lang/security/**/*.yaml` | 31 |

Only `*.yaml` rule **definitions** are vendored. The upstream `*.py` / `*.js` /
`*.ts` rule-test *target* files (example-vulnerable code paired with each rule for
Semgrep's own `--test`) are deliberately excluded: we never run `semgrep --test`
here, and they would (a) bloat the tree and (b) plant example-vuln code inside the
package. `.test.yaml` files are likewise excluded.

`go/` and `rust/` are intentionally absent: those stacks have no active
dark-factory coverage today, so vendoring unvalidated rules would be dead weight.
`run_semgrep()` returns `ran=False` for a language with no vendored ruleset
(honest "did not scan", never a false-clean) — symmetric with the regex scanner's
`scanned=False` for an unsupported language. They ride with the 3rd-stack breadth
work.

## License

The vendored rules are licensed under the upstream `LICENSE` (LGPL-2.1),
preserved verbatim in this directory. Each rule file retains its upstream
`metadata` (including `license`, `source`, and `cwe`).

## How to re-sync (bump the pin)

```bash
git clone --depth 1 https://github.com/semgrep/semgrep-rules /tmp/semgrep-rules
cd /tmp/semgrep-rules && git rev-parse HEAD            # → new RULESET_VERSION
# re-copy *.yaml (excluding *.test.yaml) from the three security dirs above,
# update RULESET_VERSION, re-run tests/unit/test_sast.py.
```

A bump is a deliberate, reviewed change (a maintained-ruleset version move), not
a silent edit — record the new SHA in `RULESET_VERSION` and in the bump PR.

## Category mapping

`darkfactory/review/sast.py` joins each finding to one of dark-factory's seven
regex categories (sql_injection, command_injection, path_traversal,
hardcoded_secret, insecure_deserialization, xss, prototype_pollution) via the
finding's **CWE** metadata — a standard, upstream-maintained taxonomy (CWE-89 →
sql_injection, CWE-78 → command_injection, …). Findings whose CWE does not map to
one of the seven are recorded as `uncategorized` (counted in the compare artifact,
never matched as agreement). The CWE→category table is the *only* bespoke join we
own here, and it is a standard-taxonomy mapping, not stack-gotcha knowledge.
