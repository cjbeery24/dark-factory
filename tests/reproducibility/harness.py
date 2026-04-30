"""Reproducibility harness for the contradiction-typology surfaces.

Phase 3.5 Session 2 ships this harness to measure the catch rate of the
two surfaces that emit typed T1-T5 contradictions:

* Densifier ``/df-densify-spec`` Step 4.5 (``.claude/skills/df-densify-spec/SKILL.md``)
* Validator ``spec-validator`` Consistency Check (``agents/spec-validator.md``)

Per Neo's design pass, this harness measures **Consistency-Check-only /
Step-4.5-only behavior** in isolation — not the full validator or the
full densifier pass. The catch-rate number it produces does not directly
transfer to the catch rate the production pipeline would observe under
the full attention budget; it isolates the typology procedure itself.
That gap is intentional and documented in the catch-rate report.

The harness is invoked one of two ways:

* Pytest: ``pytest tests/reproducibility/ -m llm`` — opt-in only, never on
  every CI push. Each fixture × surface is one parametrized test that
  runs N invocations (default 5) and asserts the SHIP criterion.
* CLI sweep: ``python -m tests.reproducibility.run_sweep`` — runs the
  full 12 × 2 × 5 = 120-invocation suite and writes the catch-rate
  report Markdown. The CLI is what produces the artefact captured in
  ``docs/pipeline-run-reports/``; pytest is the per-fixture verifier.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures" / "contradiction_typology"
TYPOLOGY_FILE = REPO_ROOT / "darkfactory" / "contracts" / "contradiction_typology.md"
VALIDATOR_AGENT_FILE = REPO_ROOT / "agents" / "spec-validator.md"
DENSIFIER_SKILL_FILE = REPO_ROOT / ".claude" / "skills" / "df-densify-spec" / "SKILL.md"
POSTGRES_VALIDATOR_ADDENDUM = (
    REPO_ROOT / "darkfactory" / "adapters" / "postgres" / "spec_validator_addendum.md"
)

# Verbatim from ``darkfactory/docker/barriers.py:189-197`` (validator_prompt).
# Inlined here rather than imported because the harness reconstruction is
# itself the artefact being audited — Neo's correction #1: the harness
# prompt must be pinned and diff-able against production. A hidden import
# would let the production string drift while the harness silently
# tracked it; keeping a verbatim copy plus a unit-test diff against the
# import path catches drift the next reviewer sees.
VALIDATOR_BARRIER_PROMPT = (
    "INFORMATION BARRIER: You MUST NOT access source code directories "
    "or .git/. These directories do not exist in your filesystem. "
    "Evaluate the spec based solely on its text and the deterministic "
    "precheck findings at .dark-factory/precheck/<spec>.json. Do not "
    "attempt to read, search, or reference any implementation source "
    "code — the Precision Analysis is adversarial by design and "
    "peeking at real code defeats it."
)

# Production default at darkfactory/models/config.py:318.
DEFAULT_MODEL = "claude-sonnet-4-20250514"

VALID_TYPOLOGY_SLOTS = {"T1", "T2", "T3", "T4", "T5"}


# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------


@dataclass
class Fixture:
    """Loaded fixture: the .md spec stub plus its .expected.json oracle."""

    fixture_id: str
    type_category: str
    expected_catch: bool
    expected_type: str | None
    expected_type_name: str | None
    permitted_slots: list[str]
    expected_anchor_pattern: dict | None
    expected_paired_anchors: list[dict]
    expected_discriminator_keywords: list[str]
    active_adapters: list[str]
    rationale: str
    discriminator_stability_required: bool = False
    md_path: Path = None  # type: ignore[assignment]
    md_content: str = ""

    @property
    def short_id(self) -> str:
        return self.fixture_id


def load_fixtures(root: Path = FIXTURES_ROOT) -> list[Fixture]:
    """Load every ``<name>.expected.json`` + ``<name>.md`` pair under ``root``.

    Returns fixtures sorted by ``fixture_id`` for deterministic ordering.
    """
    out: list[Fixture] = []
    for json_path in sorted(root.rglob("*.expected.json")):
        stem = json_path.name.removesuffix(".expected.json")
        md_path = json_path.parent / f"{stem}.md"
        if not md_path.exists():
            raise FileNotFoundError(
                f"fixture {json_path.name} has no sibling .md at {md_path}"
            )
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        meta["md_path"] = md_path
        meta["md_content"] = md_path.read_text(encoding="utf-8")
        out.append(Fixture(**meta))
    return sorted(out, key=lambda f: f.fixture_id)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _extract_step_45_section() -> str:
    """Return the SKILL.md text from ``### Step 4.5:`` to ``### Step 5:``.

    The section includes the procedure, the JSON output schema, and the
    SSOT typology block — everything Step 4.5 needs to apply the
    contradiction typology to the AC table.
    """
    text = DENSIFIER_SKILL_FILE.read_text(encoding="utf-8")
    start = text.index("### Step 4.5:")
    end = text.index("### Step 5:")
    return text[start:end].rstrip() + "\n"


def _extract_consistency_check_section() -> str:
    """Return the validator agent text covering the Consistency Check.

    Slices ``agents/spec-validator.md`` from the
    ``### Consistency Check (Cross-Section Contradiction Procedure)``
    heading through the end of the inlined SSOT block (right before
    ``## Precision Analysis``). The section contains Pass 1 / Pass 2 /
    Pass 3 + the typology + the worked example.

    Used as the system-append for the validator surface — the goal is
    to measure the typology procedure in isolation (Neo correction #6
    sub-bullet four, "fixtures are not specs"). Including the full
    34K-char agent.md drags in eight other procedures (Completeness
    Checklist, Precision Analysis, Vehicle Selection, etc.) and the
    model's attention competes with the Consistency Check on small
    fixtures. The trimmed slice is ~10K chars and keeps all the
    behavioral rules the Consistency Check needs.

    The reconstruction-parity unit test asserts the SSOT block inside
    this slice byte-matches the canonical typology file.
    """
    text = VALIDATOR_AGENT_FILE.read_text(encoding="utf-8")
    start = text.index(
        "### Consistency Check (Cross-Section Contradiction Procedure)"
    )
    end = text.index("## Precision Analysis")
    return text[start:end].rstrip() + "\n"


def build_validator_prompt(fixture: Fixture) -> tuple[str, str]:
    """Construct (system_append, stdin) for the validator surface.

    The harness prompt:

    * Picks Neo's option (i): inlines the agent definition into the
      system-append. The validator is not in ``_AGENT_DEF_INJECT_ROLES``
      in production (the agent reads its own .md via the Read tool);
      that read-tool turn is unavailable in subprocess invocation, so we
      embed the agent text directly. The reconstruction-parity unit
      test confirms this matches a captured production prompt
      character-for-character on the agent-text portion.
    * Concatenates the postgres adapter addendum when the fixture
      declares ``"postgres"`` in active_adapters (matches
      ``_compose_adapter_addenda`` at orchestrator.py:1047-1053).
    * Asks for Consistency-Check-only output (option (ii) per Neo) —
      explicitly skips the full validator pipeline so we measure the
      typology procedure in isolation.
    """
    # System-prompt assembly mirrors production at
    # darkfactory/docker/args.py:201-211: agent definition first
    # (highest-salience position), barrier prompt last. Adapter
    # addendum is concatenated after the agent def, matching
    # ``_compose_adapter_addenda`` at orchestrator.py:1047-1053.
    #
    # Session 2 iteration history:
    #
    # * Round 1: full agent.md as system-append. 4 cases failed.
    # * Iter-1: kept full agent.md, strengthened the stdin directive
    #   to MANDATORY STEPS / Pass 1 / Pass 2 / Pass 3 imperative
    #   form. T2 recovered (3/5 → 4/5) but T3 + T4-borderline
    #   remained at 3/5.
    # * Iter-2: trimmed agent.md to just the Consistency Check
    #   section (~10K vs 34K chars) hoping to remove
    #   competing-procedure interference. T4-borderline jumped to
    #   5/5 but T2 regressed (4/5 → 3/5) and T3 regressed (3/5 →
    #   2/5). Net negative; no universal config dominates.
    #
    # Reverted to full agent.md (production-faithful per Neo
    # correction #1). Final state: 22/24 PASS. The 2 remaining
    # failures (T3-journal-migrate validator, T4-borderline-entity
    # validator) are escalated as Session 1.5 territory — the
    # underlying agent's Pass 2 procedure needs revision for
    # lifecycle-ordering and entry-point-exclusivity types.
    system_parts: list[str] = []
    system_parts.append(VALIDATOR_AGENT_FILE.read_text(encoding="utf-8"))
    if "postgres" in fixture.active_adapters:
        system_parts.append(POSTGRES_VALIDATOR_ADDENDUM.read_text(encoding="utf-8"))
    system_parts.append(VALIDATOR_BARRIER_PROMPT)
    system = "\n\n".join(system_parts)

    stdin = (
        "Execute the Cross-Section Contradiction Procedure described in "
        "your agent definition. This is the ONLY task — do NOT run the full "
        "validator (skip the Completeness Checklist, Precision Analysis, "
        "Scope Check, Vehicle Selection, and Foot-gun checks).\n\n"
        "MANDATORY STEPS (perform every step; no early exit):\n\n"
        "  Pass 1 — for every FR in the spec, extract every load-bearing "
        "claim from Processing / Side effects / Output, plus the THEN "
        "clause of every AC tagged to the FR, plus every EC referencing "
        "it. Reduce each clause to a (subject, predicate, polarity, "
        "object, anchor) tuple per the typology. Number claims as "
        "FR-N.cM. Compound clauses joined by AND/OR/COMMA split into "
        "separate tuples. The fr_ledgers block MUST contain at least one "
        "claim per FR.\n\n"
        "  Pass 2 — for every pair (claim_a, claim_b) in the ledger "
        "(intra-FR AND cross-FR), apply the refinement filter, then "
        "attempt to fit the pair to T1-T5. Silence on a pair that fits a "
        "typology slot is a procedure failure. Open-vocabulary 'seems "
        "off' is banned. If you cannot name a slot, do not flag.\n\n"
        "  Pass 3 — emit the consistency ledger.\n\n"
        "OUTPUT FORMAT — emit exactly these two artefacts and nothing else:\n\n"
        "  1. A single fenced ```yaml block containing the ledger with "
        "keys `spec`, `fr_ledgers` (non-empty for any spec with FRs), "
        "and `contradictions` (the typed T1-T5 list; explicit empty "
        "list `[]` if you found none — do not omit the key). Each "
        "contradictions[*] entry MUST carry: claim_a, claim_b, type, "
        "type_name, discriminator.\n\n"
        "  2. Immediately after the closing ``` a single line: "
        "`consistency=PASS` (when contradictions is empty) OR "
        "`consistency=FAIL` (when contradictions is non-empty).\n\n"
        "No prose, no markdown headers, no other YAML blocks. If you do "
        "not find any contradiction, you still emit the ledger with the "
        "extracted fr_ledgers and `contradictions: []` — skipping the "
        "ledger emission is itself a procedure failure.\n\n"
        f"--- SPEC: {fixture.fixture_id} ---\n{fixture.md_content}\n"
    )
    return system, stdin


def build_densifier_prompt(fixture: Fixture) -> tuple[str, str]:
    """Construct (system_append, stdin) for the densifier surface.

    Per Neo's correction #5: the densifier in production runs Step 4.5
    on AC output it just emitted in Step 3 — not on a static spec. We
    frame the prompt as "Steps 1-4 are complete; here's the Step 3 AC
    table; now run Step 4.5" so the model's calibration prior matches
    production (SKILL.md line 314 names liberal-recall calibration as a
    consequence of just-emitted ACs).
    """
    step_45 = _extract_step_45_section()
    system = step_45
    stdin = (
        "You are running /df-densify-spec on the spec below. Steps 1-4 of the "
        "skill have completed. Step 3 emitted the AC table that appears in "
        "section 8 of the spec (the densified ACs you would have just "
        "produced). The user has not yet seen Step 4's summary.\n\n"
        "Now perform Step 4.5 verbatim per the procedure in your system "
        "prompt. For each AC the densifier emitted in Step 3, walk against "
        "the parent FR's prose (Processing / Side effects / Output) and "
        "every sibling AC, reduce each load-bearing clause to a tuple, apply "
        "the refinement filter, and fit surviving pairs to T1-T5.\n\n"
        "Calibration: liberal recall. The user is at the keyboard and can "
        "dismiss false positives with one keystroke; surface anything that "
        "fits a typology slot. The hard-stop trigger is "
        "len(contradictions) > 0; a clean pass emits "
        "contradictions: [].\n\n"
        "Emit ONLY the Step 4.5 JSON block (the schema in the procedure, "
        "with step='4.5'). No prose, no markdown headers, no other output. "
        "Wrap in a fenced ```json code block.\n\n"
        f"--- SPEC: {fixture.fixture_id} ---\n{fixture.md_content}\n"
    )
    return system, stdin


# ---------------------------------------------------------------------------
# Claude invocation
# ---------------------------------------------------------------------------


@dataclass
class InvocationResult:
    raw: str
    stderr: str
    exit_code: int
    duration_s: float
    truncated: bool = False


def claude_available() -> bool:
    return shutil.which("claude") is not None


def _decode_partial(x: object) -> str:
    """Coerce a ``subprocess.TimeoutExpired.std{out,err}`` to ``str``.

    CPython documents these attributes as ``bytes | None`` regardless of
    ``text=True`` on the parent ``run`` call, but the underlying
    behavior has historically been "whatever was buffered" (bytes
    today; could shift to ``str`` in a future release per the
    ``text=True`` intent). This helper handles all three cases so the
    timeout path doesn't crash when CPython's behavior shifts.
    """
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode("utf-8", "replace")
    return str(x)


def invoke_claude(
    system_append: str,
    stdin_input: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature_zero: bool = False,
    timeout_seconds: int = 240,
) -> InvocationResult:
    """Subprocess ``claude -p`` with the given system-append and stdin.

    The output format is ``json`` (matches dark-factory's production at
    ``docker/args.py:189``). The harness extracts the ``result`` text
    from the JSON wrapper before passing to the schema parsers below.
    """
    cmd: list[str] = [
        "claude",
        "-p",
        "--dangerously-skip-permissions",
        "--output-format=json",
        f"--model={model}",
    ]
    if system_append:
        cmd.extend(["--append-system-prompt", system_append])
    if temperature_zero:
        cmd.extend(["--settings", '{"temperature": 0}'])

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        duration = time.monotonic() - start
        return InvocationResult(
            raw=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            duration_s=duration,
        )
    except subprocess.TimeoutExpired as e:
        duration = time.monotonic() - start
        return InvocationResult(
            raw=_decode_partial(e.stdout),
            stderr=_decode_partial(e.stderr)
            + f"\n[harness] timeout after {timeout_seconds}s",
            exit_code=-1,
            duration_s=duration,
            truncated=True,
        )


def extract_result_text(claude_output: str) -> str:
    """Pull the ``result`` field out of Claude's --output-format=json envelope.

    The CLI returns a JSON object like ``{"result": "...", ...}``; the
    real model output is in ``result``. Falls back to the raw output if
    the envelope can't be parsed (older Claude Code versions, or when
    the CLI errors out and writes plain text).
    """
    text = claude_output.strip()
    if not text:
        return ""
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
        return envelope["result"]
    return text


# ---------------------------------------------------------------------------
# Output parsers (per-surface)
# ---------------------------------------------------------------------------


_YAML_FENCE_RE = re.compile(r"```(?:yaml|yml)\s*\n(.*?)\n```", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def parse_validator_output(result_text: str) -> list[dict[str, Any]]:
    """Extract typed contradictions + their resolved anchors from the validator output.

    Walks every ```yaml fence in order, returns the first one that parses
    as a dict containing a ``contradictions`` key. Each contradiction is
    augmented with ``_anchor_a`` and ``_anchor_b`` resolved from the
    ledger's ``fr_ledgers[*].claims[*].id → anchor`` map.
    """
    for fence in _YAML_FENCE_RE.findall(result_text):
        try:
            data = yaml.safe_load(fence)
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        if "contradictions" not in data:
            continue
        claim_anchors: dict[str, str] = {}
        for fr in data.get("fr_ledgers") or []:
            if not isinstance(fr, dict):
                continue
            for claim in fr.get("claims") or []:
                if not isinstance(claim, dict):
                    continue
                cid = claim.get("id")
                if cid is not None:
                    claim_anchors[str(cid)] = str(claim.get("anchor") or "")
        contradictions: list[dict[str, Any]] = []
        for entry in data.get("contradictions") or []:
            if not isinstance(entry, dict):
                continue
            entry = dict(entry)
            entry["_anchor_a"] = claim_anchors.get(str(entry.get("claim_a", "")), "")
            entry["_anchor_b"] = claim_anchors.get(str(entry.get("claim_b", "")), "")
            contradictions.append(entry)
        return contradictions
    return []


def parse_densifier_output(result_text: str) -> list[dict[str, Any]]:
    """Extract typed contradictions from the densifier Step 4.5 JSON block."""
    for fence in _JSON_FENCE_RE.findall(result_text):
        try:
            data = json.loads(fence)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("step", "")) != "4.5":
            continue
        contradictions = data.get("contradictions") or []
        if not isinstance(contradictions, list):
            return []
        out: list[dict[str, Any]] = []
        for entry in contradictions:
            if isinstance(entry, dict):
                out.append(entry)
        return out
    return []


# ---------------------------------------------------------------------------
# Anchor normalization
# ---------------------------------------------------------------------------


_FR_FIELD_LINE_RE = re.compile(
    r"\b(?P<fr>FR-\d+)(?:\.(?P<field>[A-Za-z_-]+))?(?:@line(?P<line>\d+))?"
)
_AC_RE = re.compile(r"\b(?P<id>AC-\d+)\b")
_EC_RE = re.compile(r"\b(?P<id>EC-\d+)\b")


def _normalize_field_name(field: str | None) -> str | None:
    """Treat ``-`` and ``_`` as equivalent and lower-case for matching.

    Specs in the wild use both ``side-effects`` (prose-style) and
    ``side_effects`` (the SSOT canonical form). The fixture's
    ``expected_anchor_pattern.fields`` declares the canonical form;
    the model emits whichever the SSOT example pinned plus any local
    variations. Normalize both sides before comparison so a hyphen
    in either does not silently flip a real catch into a miss.
    """
    if field is None:
        return None
    return field.replace("-", "_").lower()


def normalize_anchor(raw: str) -> dict[str, Any]:
    """Parse a free-form anchor string into a structured tuple.

    Handles every shape the densifier and validator emit:

    * ``FR-3.processing@line62`` — full form
    * ``FR-3.processing`` — fieldless line missing
    * ``FR-3@line62`` — field missing
    * ``AC-24`` — bare AC id
    * ``EC-7`` — bare EC id
    * ``FR-3.c1`` — claim id (validator's claim_a/claim_b form)

    Returns a dict with whichever keys could be extracted: ``fr_id``,
    ``field``, ``line`` (int), ``ac_id``, ``ec_id``, plus ``raw`` always.
    """
    out: dict[str, Any] = {"raw": raw or ""}
    if not raw:
        return out
    fr_m = _FR_FIELD_LINE_RE.search(raw)
    if fr_m:
        out["fr_id"] = fr_m.group("fr")
        if fr_m.group("field"):
            out["field"] = fr_m.group("field")
        if fr_m.group("line"):
            out["line"] = int(fr_m.group("line"))
    ac_m = _AC_RE.search(raw)
    if ac_m:
        out["ac_id"] = ac_m.group("id")
    ec_m = _EC_RE.search(raw)
    if ec_m:
        out["ec_id"] = ec_m.group("id")
    return out


# ---------------------------------------------------------------------------
# Per-run evaluator
# ---------------------------------------------------------------------------


@dataclass
class RunEvaluation:
    caught: bool
    slot: str | None
    type_name: str | None
    discriminator: str
    anchors: list[dict[str, Any]] = field(default_factory=list)
    fp: bool = False  # false positive on a refinement counter-example
    line_precision: bool | None = None  # True/False when a line was emitted
    notes: str = ""


def _anchor_matches_pattern(anchor: dict[str, Any], pattern: dict) -> bool:
    """True when the structured anchor lands inside the fixture's prose block.

    Match is at the **fr_id + field level**. Line precision is reported
    separately by ``_anchor_line_precision`` because the LLMs in
    practice (a) frequently emit lineless anchors like
    ``FR-3.processing`` and (b) sometimes echo the SSOT worked
    example's literal ``@line62`` regardless of the fixture's actual
    line numbers. Treating line-precision as a hard match criterion
    rejects real catches; treating it as an advisory metric preserves
    the catch signal AND surfaces the precision regression.

    Field comparison normalizes ``-`` and ``_`` as equivalent (specs
    use both forms; without normalization a model emitting
    ``side-effects`` is graded as a miss against a fixture that
    declared ``side_effects``).

    The fixture's `acceptable_lines` and `allow_fieldless` keys are
    consumed by the precision check, not the match check.
    """
    if pattern.get("fr_id") and anchor.get("fr_id") != pattern["fr_id"]:
        return False
    fields = pattern.get("fields") or []
    field_value = _normalize_field_name(anchor.get("field"))
    if fields:
        if field_value is None:
            if not pattern.get("allow_fieldless", False):
                return False
        else:
            normalized_pattern_fields = {
                _normalize_field_name(f) for f in fields
            }
            if field_value not in normalized_pattern_fields:
                return False
    return True


def _anchor_line_precision(anchor: dict[str, Any], pattern: dict) -> bool | None:
    """Return True when the anchor's line lands in ``acceptable_lines``,
    False when a line is emitted but out of range, ``None`` when no
    line was emitted (so precision is unmeasured for this anchor).
    """
    line_value = anchor.get("line")
    if line_value is None:
        return None
    acceptable_lines = pattern.get("acceptable_lines") or []
    if not acceptable_lines:
        return None
    return line_value in acceptable_lines


def _extract_anchors(entry: dict, surface: str) -> list[dict[str, Any]]:
    """Return all candidate anchor strings normalized to structured tuples."""
    raw_anchors: list[str] = []
    if surface == "validator":
        raw_anchors.append(str(entry.get("_anchor_a", "")))
        raw_anchors.append(str(entry.get("_anchor_b", "")))
    else:  # densifier
        if entry.get("conflicting_anchor"):
            raw_anchors.append(str(entry["conflicting_anchor"]))
        # The densifier's ``ac_id`` and ``parent_fr`` are also anchors
        if entry.get("ac_id"):
            raw_anchors.append(str(entry["ac_id"]))
        if entry.get("parent_fr"):
            raw_anchors.append(str(entry["parent_fr"]))
    return [normalize_anchor(a) for a in raw_anchors if a]


def _fixture_matches_entry(entry: dict, fixture: Fixture, surface: str) -> bool:
    """Whether this contradiction entry is the one the fixture is testing for.

    Match criteria (ALL must hold):
    * Slot lands in ``permitted_slots`` (or expected_type, whichever is set)
    * At least one extracted anchor matches the fixture's anchor pattern
    """
    slot = str(entry.get("type", "")).upper()
    permitted = set(fixture.permitted_slots) if fixture.permitted_slots else (
        {fixture.expected_type} if fixture.expected_type else set()
    )
    if permitted and slot not in permitted:
        return False
    pattern = fixture.expected_anchor_pattern
    if pattern is None:
        return False
    anchors = _extract_anchors(entry, surface)
    return any(_anchor_matches_pattern(a, pattern) for a in anchors)


def evaluate_run(
    fixture: Fixture,
    contradictions: list[dict],
    surface: str,
) -> RunEvaluation:
    """Score one invocation against one fixture's expectation."""
    if fixture.expected_catch:
        for entry in contradictions:
            if _fixture_matches_entry(entry, fixture, surface):
                anchors = _extract_anchors(entry, surface)
                # Line-precision is True when at least one anchor's
                # emitted line lands in acceptable_lines, False when
                # every emitted line is out-of-range, None when no
                # anchor carried a line.
                pattern = fixture.expected_anchor_pattern or {}
                precisions = [
                    _anchor_line_precision(a, pattern) for a in anchors
                ]
                if all(p is None for p in precisions):
                    line_precision = None
                else:
                    line_precision = any(p is True for p in precisions)
                return RunEvaluation(
                    caught=True,
                    slot=str(entry.get("type", "")).upper(),
                    type_name=str(entry.get("type_name", "")) or None,
                    discriminator=str(entry.get("discriminator", "")),
                    anchors=anchors,
                    fp=False,
                    line_precision=line_precision,
                )
        return RunEvaluation(
            caught=False,
            slot=None,
            type_name=None,
            discriminator="",
            anchors=[],
            fp=False,
            notes=(
                f"no contradiction matched fixture pattern "
                f"(emitted {len(contradictions)} contradiction(s) total)"
            ),
        )
    # Refinement counter-example: ANY emitted contradiction is a false positive.
    if contradictions:
        first = contradictions[0]
        return RunEvaluation(
            caught=True,
            slot=str(first.get("type", "")).upper() or None,
            type_name=str(first.get("type_name", "")) or None,
            discriminator=str(first.get("discriminator", "")),
            anchors=_extract_anchors(first, surface),
            fp=True,
            notes=(
                f"refinement fixture flagged a contradiction "
                f"({len(contradictions)} total emitted) — false positive"
            ),
        )
    return RunEvaluation(
        caught=False,
        slot=None,
        type_name=None,
        discriminator="",
        anchors=[],
        fp=False,
    )


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


@dataclass
class FixtureAggregate:
    fixture_id: str
    surface: str
    runs: int
    catches: int
    false_positives: int
    slots_caught: list[str]
    slot_stable: bool | None  # None for refinement fixtures
    discriminator_stable: bool | None  # None when not required
    line_precision_caught: int  # caught runs with line in acceptable range
    line_precision_unmeasured: int  # caught runs with no emitted line
    passed: bool
    failure_reason: str | None
    per_run: list[RunEvaluation] = field(default_factory=list)


def _ship_threshold(runs: int) -> int:
    """≥4/5 generalizes to ≥(0.8 * runs), rounded up."""
    if runs == 5:
        return 4
    return -(-runs * 4 // 5)


def aggregate_fixture(
    fixture: Fixture,
    surface: str,
    per_run: list[RunEvaluation],
) -> FixtureAggregate:
    runs = len(per_run)
    catches = sum(1 for r in per_run if r.caught and not r.fp)
    fps = sum(1 for r in per_run if r.fp)
    slots = [r.slot for r in per_run if r.slot and not r.fp]

    if fixture.expected_catch:
        permitted = set(fixture.permitted_slots) if fixture.permitted_slots else (
            {fixture.expected_type} if fixture.expected_type else set()
        )
        observed_slots = set(slots)
        # Slot stability: every caught run lands inside permitted_slots.
        # When permitted_slots is a singleton (the default), this collapses
        # to "all 5 runs same slot." When permitted_slots has multiple
        # entries (borderline fixtures), this allows alternation but only
        # within the declared set.
        slot_stable = bool(catches) and observed_slots.issubset(permitted)

        # Line-precision side metric (advisory; does NOT gate ship).
        line_precision_caught = sum(
            1 for r in per_run if r.caught and not r.fp and r.line_precision is True
        )
        line_precision_unmeasured = sum(
            1 for r in per_run if r.caught and not r.fp and r.line_precision is None
        )

        discriminator_stable: bool | None = None
        if fixture.discriminator_stability_required:
            discriminators = [
                r.discriminator.lower()
                for r in per_run
                if r.caught and not r.fp and r.discriminator
            ]
            if discriminators:
                # Discriminator stable iff every run's discriminator contains
                # at least one of the fixture's expected_discriminator_keywords.
                kw = [k.lower() for k in fixture.expected_discriminator_keywords]
                discriminator_stable = all(
                    any(k in d for k in kw) for d in discriminators
                ) if kw else (len(set(discriminators)) <= 2)
            else:
                discriminator_stable = False

        threshold = _ship_threshold(runs)
        if catches < threshold:
            passed = False
            reason = (
                f"catches {catches}/{runs} below ship threshold {threshold}/{runs}"
            )
        elif not slot_stable:
            passed = False
            reason = (
                f"slot instability — observed {sorted(observed_slots)}, "
                f"permitted {sorted(permitted)}"
            )
        elif discriminator_stable is False:
            passed = False
            reason = "discriminator instability across caught runs"
        else:
            passed = True
            reason = None

        return FixtureAggregate(
            fixture_id=fixture.fixture_id,
            surface=surface,
            runs=runs,
            catches=catches,
            false_positives=fps,
            slots_caught=slots,
            slot_stable=slot_stable,
            discriminator_stable=discriminator_stable,
            line_precision_caught=line_precision_caught,
            line_precision_unmeasured=line_precision_unmeasured,
            passed=passed,
            failure_reason=reason,
            per_run=per_run,
        )

    # Refinement fixture: ≤1/5 false positives = PASS.
    fp_cap = max(1, runs // 5)  # 1 for runs=5; scales mildly for larger runs
    passed = fps <= fp_cap
    reason = None if passed else (
        f"{fps}/{runs} false positives exceed cap {fp_cap}/{runs}"
    )
    return FixtureAggregate(
        fixture_id=fixture.fixture_id,
        surface=surface,
        runs=runs,
        catches=0,
        false_positives=fps,
        slots_caught=[],
        slot_stable=None,
        discriminator_stable=None,
        line_precision_caught=0,
        line_precision_unmeasured=0,
        passed=passed,
        failure_reason=reason,
        per_run=per_run,
    )


# ---------------------------------------------------------------------------
# Single-fixture sweep helper (used by both pytest tests and the CLI sweep)
# ---------------------------------------------------------------------------


def run_fixture_sweep(
    fixture: Fixture,
    surface: str,
    *,
    runs: int = 5,
    model: str = DEFAULT_MODEL,
    timeout_seconds: int = 300,
) -> FixtureAggregate:
    """Invoke the surface ``runs`` times, return the aggregate."""
    if surface not in {"validator", "densifier"}:
        raise ValueError(f"unknown surface: {surface!r}")

    if surface == "validator":
        system, stdin = build_validator_prompt(fixture)
        temperature_zero = True  # Phase 3 lockdown
        parser = parse_validator_output
    else:
        system, stdin = build_densifier_prompt(fixture)
        temperature_zero = False  # Densifier skill has no temperature pin
        parser = parse_densifier_output

    per_run: list[RunEvaluation] = []
    for _ in range(runs):
        result = invoke_claude(
            system,
            stdin,
            model=model,
            temperature_zero=temperature_zero,
            timeout_seconds=timeout_seconds,
        )
        if result.exit_code != 0:
            per_run.append(
                RunEvaluation(
                    caught=False,
                    slot=None,
                    type_name=None,
                    discriminator="",
                    anchors=[],
                    fp=False,
                    notes=(
                        f"claude exit {result.exit_code} after "
                        f"{result.duration_s:.1f}s"
                    ),
                )
            )
            continue
        result_text = extract_result_text(result.raw)
        contradictions = parser(result_text)
        per_run.append(evaluate_run(fixture, contradictions, surface))

    return aggregate_fixture(fixture, surface, per_run)
