# Sprint 10 — forbidden-pattern preflight matches inside comments (breadth blocker)

> Surfaced by Sprint 2 T2.0 (python-fastapi parse-amount-py). The run reached the
> IMPLEMENTER (past validator/architect/qa/TE — Sprint 9 validated) and aborted:
> `forbidden_patterns_persisted`.

## Root cause (trace-confirmed)
`forbidden_patterns.py:_pattern_matches` does a raw substring/regex match on each
added diff line with NO comment/string awareness. The architect declared `round(`
forbidden for FR-3 ("MUST NOT silently round"). The implementer CORRECTLY avoided
`round()` and wrote an explanatory comment:
```
src/app/parse_amount.py:96   # Manual rounding to avoid using round() function
```
The comment's literal `round()` matched the forbidden `round(` → preflight
violation → focused retry → persisted (the implementer can't easily satisfy a
check that flags its own explanation) → iteration aborted.

This is the documented "revisit" case: `forbidden_patterns.py:25-27` deliberately
chose NOT to strip comments ("if you type it in a comment that's deliberate;
revisit only if we see false positives in practice"). T2.0 is that false positive
— the implementer typed it in a comment to say it AVOIDED the construct, the
opposite of deliberate use. Same recurring family as Sprint 8 (denial prose) /
Sprint 9 (non-code contexts): a lexical matcher firing in a non-code context.

## The check's legitimate purpose (must preserve)
Catch the implementer actually USING a forbidden construct (the real
`Math.round(numValue*100)` that shipped against FR-3's "no rounding" — the bug
this preflight exists to stop). A comment/string mentioning it is NOT using it.
Stripping comments has ~zero false-NEGATIVE risk for this purpose (you cannot
USE round() from inside a comment).

## Fix options
- **A — regex line-comment strip (lang-aware by file extension).** Before
  matching, drop the line-comment tail: `#` for py/sh/yaml/toml, `//` for
  js/ts/go/rust/c, best-effort `/* */`. Cheap, one function. FRAGILE: a `#`/`//`
  inside a STRING literal (`"http://…"`, `"a # b"`) would be mis-stripped —
  truncating the line and potentially HIDING a real forbidden use after it
  (a false-NEGATIVE on the ship-gate-adjacent check) or being a no-op.
- **B — language-aware tokenize, match only code tokens.** For python, stdlib
  `tokenize` precisely classifies COMMENT/STRING vs code tokens — robust. But it
  needs the whole file (not isolated diff lines), and there's no stdlib tokenizer
  for JS/TS, so it's per-language with a fallback. Bigger change; the check
  currently operates on diff lines, not files.
- **C — strip comments AND string literals (so a forbidden token in any string,
  e.g. an error message "do not round", also doesn't match).** Strictly more
  than the observed case; more false-negative surface if done by regex.
- **D — accept + instruct the implementer** not to mention forbidden patterns in
  comments. Fragile, blames the implementer for explaining correct behavior; the
  comment is natural authorship. Rejected on its face.

## Questions for Neo
- **Q1 (seam/robustness):** is a regex line-comment strip (A) good enough for the
  realistic case (a comment line or trailing comment explaining avoidance), or
  does the `#`/`//`-inside-a-string fragility force tokenize (B)? The check is a
  PREFLIGHT gate (blocks the implementer); a false-NEGATIVE from over-stripping a
  string is the dangerous direction — weigh it.
- **Q2:** if A, is the string-edge residual acceptable (rare: a forbidden token
  after a `#`/`//` that sits inside a string on an added line), or must we at
  least not strip a marker that's inside quotes (a half-step toward B)?
- **Q3:** should string LITERALS also be exempt (C)? The observed case is a
  comment; is a forbidden token inside a string (error message, regex pattern)
  also a false-positive we should pre-empt, or out of scope until observed?
- **Q4:** the matcher runs per-diff-line without file context. Is line-level
  stripping the right granularity, or does correct comment handling require
  reading the file (block comments, multiline strings span lines)?
- **Q5:** authorization: this makes the preflight reject FEWER (fewer false
  violations); host-side; no barrier/Reviewer→Implementer promotion. Confirm not
  sensitive (mirror Sprint 8/9 direction).
- **Q6:** is the language signal best taken from the file EXTENSION (`current_file`
  is available in the matcher path), or from the project's configured language?

## OUTCOME — shipped, Linus+Codex CLEAN (round 2)

Neo ruled HYBRID (comments-only, by file extension, double-quote-parity). Code gate:
- ROUND 1: Codex BLOCKED an escaped-quote false-negative the parity count missed —
  `const s = "a \" // b"; Math.round(x);` stripped to `const s = "a \" ` and the
  real `Math.round(` was HIDDEN (the escaped `\"` flipped `.count('"')` even at the
  in-string `//`). Linus round-1 CLEAN had tested a non-equivalent input and missed
  it (he acknowledged this in round 2). Empirically confirmed the bug on the staged
  code before fixing.
- FIX: `_unescaped_dquote_count` (walks the prefix skipping `\X` escape pairs, counts
  only unescaped `"`). Degrades safe (over-preserve/over-fire) on every malformed
  input. + Linus's doc FIX-IT (single-quote residual is ALL languages incl. Python,
  needs a tokenizer) + 3 tests (escaped-quote fires, escaped-backslash, single-quote
  residual pinned-deferred).
- ROUND 2: both CLEAN. full unit 3945; 36 forbidden_patterns tests; ruff clean.

DEFERRED (Neo, tracked for a future "Sprint 11"-style declaration-layer fix — the
Sprint-8-shaped structural move): loose bare-identifier patterns (`round(` matches
`surround(`) → warn/validate at the architect's forbidden_patterns DECLARATION
(manifest.py) nudging `re:\bround\(`; single-quoted-string + block-comment lexing
(needs a tokenizer). All softened in production by the downstream blind-scenario /
QA-holdout gate — this preflight is a fast pre-filter, not the sole enforcement.

NEXT: re-run Sprint 2 T2.0 (python-fastapi parse-amount-py) — the implementer should
now clear preflight and the run reach the gate/oracle (ship, or surface the next
breadth blocker).
