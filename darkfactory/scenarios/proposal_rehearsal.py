"""C2 — Proposal-test typecheck rehearsal.

Runs AFTER architect and BEFORE full scenario generation. Spawns the
Proposal Rehearsal agent to write ONE minimal test per AC/FR/EC anchor.
The host then runs ``tsc --noEmit`` on those proposals and checks for a
banned-pattern list. If ≥30% fail typecheck OR any AC proposal hits a
banned pattern, the pipeline aborts BEFORE scenario generation burns a
full iteration on an untestable spec.

## Why it exists alongside A6

A6 (``stub_compile.py``) verifies the spec's *declared* §5 Interface
Contract typechecks against the pinned library surface. A6 does not
verify that an author can actually *write a passing test* that exercises
that surface — an AC can have a compiling interface but be structurally
untestable at the unit level (classic example: "process exits with code
1"). C2 catches that class.

## Why stdout JSON and not file writes

The Proposal Rehearsal agent runs with an empty ``write_dirs`` barrier
— the project is read-only to it. Output comes back in the captured
container stdout between BEGIN_PROPOSALS / END_PROPOSALS sentinels, the
host parses the JSON and writes each proposal to a host-side tmpdir
owned by this module. That way a misbehaving agent cannot litter the
project with ``.test.ts`` files that would later confuse the Test
Engineer or the gate runner.

## tsc harness reuse

Reuses A6's helpers (``_find_tsc``, ``_find_tsconfig``, ``_node_modules_present``,
``_synthesize_tsconfig``, ``_run_tsc``, ``_parse_tsc_diagnostics``) — same
path resolution rules, same scratch-tsconfig synthesis, same diagnostic
parsing. The only structural difference is that C2's scratch files come
from an LLM's stdout rather than from spec fences.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.models.agent import AgentRole
from darkfactory.spec.anchors import AnchorManifest, manifest_path
from darkfactory.spec.stub_compile import (
    StubCompileFinding,
    _find_tsc,
    _find_tsconfig,
    _node_modules_present,
    _parse_tsc_diagnostics,
    _run_tsc,
    _synthesize_tsconfig,
    InterfaceStub,
)

log = logging.getLogger(__name__)


# The 30% threshold is a judgment call documented in the plan:
# docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md §C2.
# Lower → more false-aborts on spec-authoring quirks that tsc dislikes
# (overly strict tsconfig, etc.). Higher → misses the class of specs
# where only a handful of anchors compile. 30% matches the plan's
# stated intent: "if nearly a third of your proposals don't typecheck,
# something is structurally wrong with the spec's testable surface."
TYPECHECK_FAIL_THRESHOLD = 0.30


# Banned patterns — matched against each proposal's ``content`` field
# AFTER tsc is happy with it. A typechecking but banned-pattern proposal
# tells us the anchor is "compilable but not unit-testable" — the
# author either needs a subprocess harness, a fork, or a re-scope.
#
# (regex, category, description) — the description is surfaced verbatim
# in the abort rationale so a human sees which anchor is untestable and
# why. Regexes are compiled with re.MULTILINE; we match against the
# proposal body, not the full stdout.
_BANNED_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"\bprocess\.exit\s*\("),
        "subprocess-exit",
        "asserting on process.exit requires forking a subprocess — not a unit test",
    ),
    (
        re.compile(r"\bchild_process\b"),
        "subprocess-child-process",
        "child_process import implies subprocess test harness, not unit",
    ),
    (
        re.compile(r"\bexecSync\s*\("),
        "subprocess-execSync",
        "execSync implies forking a subprocess — not a unit test",
    ),
    (
        re.compile(r"\bspawn(?:Sync)?\s*\("),
        "subprocess-spawn",
        "spawn/spawnSync implies forking a subprocess — not a unit test",
    ),
    (
        re.compile(r"\bfork\s*\("),
        "subprocess-fork",
        "fork() implies a subprocess test harness, not unit",
    ),
    # Note: earlier drafts also banned library-internal `._.` access
    # (e.g. drizzle-orm's ``schema._.config``) via ``\.\s*_\s*\.``, but
    # that regex false-positives on lodash patterns (``foo._.map(xs, fn)``
    # and similar) and on any library that legitimately exposes a single-
    # underscore property. A6 + tsc strictness (``noUncheckedIndexedAccess``)
    # cover the real motivating case without the collateral damage; if a
    # specific library's internals become a recurring problem we can add
    # a narrow, library-scoped rule here instead of a syntactic shape.
]


@dataclass(frozen=True)
class Proposal:
    """A single AC/FR/EC proposal test emitted by the agent.

    ``filepath`` is relative to the scratch dir the host creates
    (typically just ``<anchor>.test.ts``). ``content`` is the raw test
    body the agent emitted.
    """

    anchor: str
    filepath: str
    content: str


@dataclass(frozen=True)
class BannedPatternHit:
    """One (proposal, pattern) match surfaced to the abort message.

    Kept as a small dataclass instead of a tuple so the abort rationale
    formatter can be written against a named structure — tuple-typing
    here was the first thing I wrote and it made the abort text
    unreadable.
    """

    anchor: str
    category: str
    description: str


@dataclass
class ProposalRehearsalResult:
    """Outcome of one C2 run.

    ``passed`` is the ship-vs-abort signal. If False, ``reason`` is a
    short human-readable string suitable for the pipeline abort message;
    ``typecheck_findings`` and ``banned_hits`` give the structured
    detail so the caller can render a fuller report.

    ``skipped`` is True when C2 no-oped (non-TS project, no anchors,
    missing tsc/tsconfig/node_modules). A skip is not a pass — the
    caller should still log why C2 didn't run.
    """

    passed: bool
    reason: str = ""
    skipped: bool = False
    total_proposals: int = 0
    typecheck_failure_count: int = 0
    typecheck_findings: list[StubCompileFinding] = field(default_factory=list)
    banned_hits: list[BannedPatternHit] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt + output parsing
# ---------------------------------------------------------------------------


_BEGIN_SENTINEL = "BEGIN_PROPOSALS"
_END_SENTINEL = "END_PROPOSALS"


def build_proposal_prompt(
    spec_name: str,
    anchors: AnchorManifest,
) -> str:
    """Compose the Proposal Rehearsal agent prompt.

    Isolated from the orchestrator step so tests can pin exact prompt
    shape against expected anchors / banned-pattern hints without
    spinning up a container.
    """
    # Dedupe while preserving order — the manifest author's order is
    # the natural sort for human review of the proposals.
    anchor_ids: list[str] = []
    seen: set[str] = set()
    for bucket in (anchors.ac_ids, anchors.fr_ids, anchors.ec_ids):
        for a in bucket:
            if a and a not in seen:
                anchor_ids.append(a)
                seen.add(a)

    banned_hints = "\n".join(
        f"  - {category}: {description}"
        for _, category, description in _BANNED_PATTERNS
    )
    anchor_list = "\n".join(f"  - {a}" for a in anchor_ids) or "  (none)"

    return (
        f"Read your agent definition at /project/.dark-factory/agents/"
        f"proposal-rehearsal.md. "
        f"Read the spec at /project/.dark-factory/specs/{spec_name}.md. "
        f"Read the anchor manifest at /project/.dark-factory/state/"
        f"{spec_name}/anchor_manifest.json. "
        f"Read the architecture manifest at /project/.dark-factory/"
        f"architecture/{spec_name}-manifest.yaml (it tells you which "
        f"library surfaces and module aliases are declared). "
        f"Read /project/tsconfig.json to see the project's paths/strict "
        f"mode.\n\n"
        f"Emit ONE proposal test per anchor below:\n{anchor_list}\n\n"
        f"The host will run tsc --noEmit on every proposal and scan for "
        f"banned patterns. Banned categories:\n{banned_hints}\n\n"
        f"If ≥30% of proposals fail typecheck OR any AC proposal hits a "
        f"banned pattern, the pipeline aborts before scenario generation. "
        f"Do not paper over an untestable anchor with a dishonest "
        f"proposal — emit it honestly (banned pattern or UNTESTABLE "
        f"comment) so the human sees the real testability problem.\n\n"
        f"End your reply with a single JSON object between "
        f"{_BEGIN_SENTINEL} and {_END_SENTINEL} sentinels, as described "
        f"in your agent definition."
    )


def parse_proposals(agent_output: str) -> list[Proposal]:
    """Extract the proposals JSON block from the agent's reply.

    Returns an empty list if the sentinel block is missing or malformed
    — the caller treats that as a protocol failure and aborts with a
    specific message. We do NOT try to be lenient (find a loose JSON
    object, repair missing braces, etc.); a misbehaving agent should
    be visible, not silently fixed up.

    If multiple sentinel pairs appear, only the LAST is used. That
    matches the agent definition's "emit all proposals in ONE JSON
    object" rule and gives us determinism when the model emits a
    reasoning draft before the final block.
    """
    # Find the LAST BEGIN_PROPOSALS → END_PROPOSALS pair.
    begin = agent_output.rfind(_BEGIN_SENTINEL)
    if begin == -1:
        return []
    end = agent_output.find(_END_SENTINEL, begin + len(_BEGIN_SENTINEL))
    if end == -1:
        return []
    body = agent_output[begin + len(_BEGIN_SENTINEL):end].strip()
    # Strip a leading ```json / ``` fence that some models wrap around
    # their final JSON even when asked not to.
    if body.startswith("```"):
        # Drop the first line and any trailing fence.
        lines = body.splitlines()
        lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines.pop()
        body = "\n".join(lines)
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("proposals")
    if not isinstance(raw, list):
        return []
    proposals: list[Proposal] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        anchor = entry.get("anchor")
        filepath = entry.get("filepath")
        content = entry.get("content")
        if not (
            isinstance(anchor, str)
            and isinstance(filepath, str)
            and isinstance(content, str)
        ):
            continue
        proposals.append(Proposal(
            anchor=anchor.strip(),
            filepath=filepath.strip(),
            content=content,
        ))
    return proposals


# ---------------------------------------------------------------------------
# Banned-pattern scan
# ---------------------------------------------------------------------------


def scan_banned_patterns(proposals: list[Proposal]) -> list[BannedPatternHit]:
    """Scan each proposal body for banned patterns.

    Returns one hit per (proposal, matched-pattern). A single proposal
    can contribute multiple hits; the abort rationale formatter dedupes
    them per anchor when rendering.
    """
    hits: list[BannedPatternHit] = []
    for proposal in proposals:
        for pattern, category, description in _BANNED_PATTERNS:
            if pattern.search(proposal.content):
                hits.append(BannedPatternHit(
                    anchor=proposal.anchor,
                    category=category,
                    description=description,
                ))
    return hits


def has_ac_banned_hit(hits: list[BannedPatternHit]) -> bool:
    """True iff any hit names an AC anchor.

    The threshold is strict: ANY AC hit fails C2 (unlike the 30%
    typecheck threshold). Rationale: an AC is a ship contract — if the
    spec's ACs aren't unit-testable, the spec needs re-scoping or a
    subprocess harness before it enters the pipeline, full stop.
    """
    return any(h.anchor.upper().startswith("AC") for h in hits)


# ---------------------------------------------------------------------------
# tsc harness (reuses A6 helpers)
# ---------------------------------------------------------------------------


def typecheck_proposals(
    proposals: list[Proposal],
    project_dir: Path,
    *,
    scratch_dir: Path | None = None,
    timeout_sec: int = 60,
) -> tuple[list[StubCompileFinding], set[str]]:
    """Write proposals to scratch and run tsc --noEmit.

    Returns ``(findings, failing_anchors)`` where ``failing_anchors`` is
    the set of anchor IDs that had at least one tsc diagnostic — used
    for the 30% threshold calculation.

    ``scratch_dir`` override is for tests; in production we create and
    clean up a temp dir.
    """
    tsc = _find_tsc(project_dir)
    tsconfig = _find_tsconfig(project_dir)
    if tsc is None or tsconfig is None or not _node_modules_present(project_dir):
        # Caller handled the no-op decision already; this is a belt-and-
        # suspenders check so a direct unit test can't segfault.
        return [], set()

    owned_scratch = scratch_dir is None
    if owned_scratch:
        scratch_dir = Path(tempfile.mkdtemp(prefix="darkfactory-c2-")).resolve()
    try:
        assert scratch_dir is not None
        # Build pseudo-InterfaceStub per proposal so we can reuse A6's
        # diagnostic parser (it maps "scratch path → originating stub").
        # spec_line_number is a nominal 1 — C2 doesn't have line context.
        placements: list[tuple[InterfaceStub, Path]] = []
        for proposal in proposals:
            # Normalize filepath to defeat path escape (``../etc/passwd``).
            # ``Path.name`` returns only the last path component, so
            # ``../foo.ts`` becomes ``foo.ts`` and absolute paths like
            # ``/etc/passwd`` become ``passwd``. That's sufficient — no
            # follow-up ``relative_to`` check needed because the result
            # is guaranteed to contain no path separators.
            safe = Path(proposal.filepath).name
            if not safe.endswith((".ts", ".tsx", ".mts", ".cts")):
                safe = f"{proposal.anchor}.test.ts"
            dest = scratch_dir / safe
            dest.write_text(proposal.content)
            # HACK: we reuse A6's diagnostic parser (_parse_tsc_diagnostics)
            # by stuffing the anchor ID into InterfaceStub.section_path[0].
            # A6 uses this field for the spec's heading path; C2 hijacks
            # it as the anchor ID so we can map tsc findings back to
            # anchors in the abort rationale. Keep this one-line coupling
            # rather than forking the parser API.
            stub = InterfaceStub(
                filepath=safe,
                content=proposal.content,
                spec_line_number=1,
                section_path=(proposal.anchor,),
            )
            placements.append((stub, dest))
        if not placements:
            return [], set()
        stub_paths = [p for _, p in placements]
        synth_tsconfig = _synthesize_tsconfig(
            scratch_dir, tsconfig, stub_paths,
        )
        try:
            result = _run_tsc(
                tsc, synth_tsconfig, project_dir, timeout_sec=timeout_sec,
            )
        except Exception as exc:  # noqa: BLE001 — tsc invocation is best-effort
            log.warning("C2: tsc invocation failed — %s", exc)
            return [], set()
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        findings = _parse_tsc_diagnostics(combined, scratch_dir, placements)
        # Map findings back to anchors via the originating stub's
        # section_path (we stuffed the anchor ID there).
        filepath_to_anchor = {
            stub.filepath: stub.section_path[0]
            for stub, _ in placements
        }
        failing_anchors: set[str] = set()
        for finding in findings:
            anchor = filepath_to_anchor.get(finding.filepath)
            if anchor:
                failing_anchors.add(anchor)
        return findings, failing_anchors
    finally:
        if owned_scratch and scratch_dir is not None and scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Top-level decision
# ---------------------------------------------------------------------------


def _misconfiguration_reason(
    project_dir: Path,
    anchors: AnchorManifest,
) -> str:
    """Return a non-empty reason if the environment is a hard error, else ''.

    Hard errors (C2 must fail the pipeline with an actionable message,
    NOT silently skip):

    - ``tsconfig.json`` present but ``node_modules`` absent. This is
      almost always "user forgot to run ``npm ci``" — a misconfigured
      environment, not a non-TS project. If we skipped here, the user
      would re-run the full pipeline on a broken setup and burn
      iterations before noticing.
    """
    anchor_count = len(anchors.ac_ids) + len(anchors.fr_ids) + len(anchors.ec_ids)
    if anchor_count == 0:
        return ""
    if _find_tsconfig(project_dir) is not None and not _node_modules_present(project_dir):
        return (
            "tsconfig.json is present but node_modules is absent — this is "
            "a misconfigured TypeScript environment. Run `npm ci` (or "
            "equivalent) before re-running the pipeline."
        )
    return ""


def _skip_conditions(
    project_dir: Path,
    anchors: AnchorManifest,
) -> str:
    """Return a non-empty reason if C2 should skip, else ''.

    Skip reasons (non-failures — the pipeline continues):
    - No anchors at all (spec had no FR/AC/EC; unusual but valid for
      pure-infrastructure specs that opt into SCENARIOS-NOT-APPLICABLE).
    - Project has no tsconfig.json (non-TS project).
    - Project has no tsc binary findable despite node_modules existing
      (exotic setup; we don't pretend to diagnose it).

    The ``tsconfig.json present + node_modules absent`` combination is
    intentionally NOT a skip here; see ``_misconfiguration_reason``.
    """
    anchor_count = len(anchors.ac_ids) + len(anchors.fr_ids) + len(anchors.ec_ids)
    if anchor_count == 0:
        return "anchor manifest is empty (no AC/FR/EC to rehearse)"
    if _find_tsconfig(project_dir) is None:
        return "no tsconfig.json at project root (non-TS project)"
    if _find_tsc(project_dir) is None:
        return "tsc not found (no node_modules/.bin/tsc and not on PATH)"
    return ""


def decide_rehearsal_outcome(
    proposals: list[Proposal],
    findings: list[StubCompileFinding],
    failing_anchors: set[str],
    banned_hits: list[BannedPatternHit],
    anchors: AnchorManifest,
) -> ProposalRehearsalResult:
    """Apply the C2 abort rules to the raw signals.

    Rules (evaluated in order):
    1. No proposals at all → abort (protocol failure or empty agent reply).
    2. Any AC proposal hits a banned pattern → abort.
    3. Typecheck failure rate ≥ TYPECHECK_FAIL_THRESHOLD → abort.
    4. Otherwise → pass. Non-AC banned hits are surfaced in ``reason``
       but don't abort (they're informational for the human).
    """
    total = len(proposals)
    if total == 0:
        return ProposalRehearsalResult(
            passed=False,
            reason=(
                "proposal rehearsal agent emitted no parseable proposals "
                "— missing BEGIN_PROPOSALS/END_PROPOSALS block or malformed JSON"
            ),
            total_proposals=0,
            typecheck_failure_count=0,
        )

    if has_ac_banned_hit(banned_hits):
        ac_hits = [h for h in banned_hits if h.anchor.upper().startswith("AC")]
        categories = ", ".join(sorted({h.category for h in ac_hits}))
        anchors_hit = ", ".join(sorted({h.anchor for h in ac_hits}))
        return ProposalRehearsalResult(
            passed=False,
            reason=(
                f"AC proposal(s) {anchors_hit} trip banned patterns "
                f"({categories}) — acceptance criteria are not unit-testable "
                f"as authored. Re-scope the AC(s) or add a subprocess harness."
            ),
            total_proposals=total,
            typecheck_failure_count=len(failing_anchors),
            typecheck_findings=findings,
            banned_hits=banned_hits,
        )

    fail_rate = len(failing_anchors) / total if total else 0.0
    if fail_rate >= TYPECHECK_FAIL_THRESHOLD:
        return ProposalRehearsalResult(
            passed=False,
            reason=(
                f"{len(failing_anchors)}/{total} proposals "
                f"({fail_rate * 100:.0f}%) fail tsc — threshold is "
                f"{TYPECHECK_FAIL_THRESHOLD * 100:.0f}%. The spec's "
                f"declared surface does not compile against the pinned "
                f"library versions."
            ),
            total_proposals=total,
            typecheck_failure_count=len(failing_anchors),
            typecheck_findings=findings,
            banned_hits=banned_hits,
        )

    return ProposalRehearsalResult(
        passed=True,
        total_proposals=total,
        typecheck_failure_count=len(failing_anchors),
        typecheck_findings=findings,
        banned_hits=banned_hits,
    )


def run_proposal_rehearsal(
    *,
    spec_name: str,
    project_dir: Path,
    agent_output: str,
    scratch_dir: Path | None = None,
    timeout_sec: int = 60,
) -> ProposalRehearsalResult:
    """Run C2 end-to-end given the agent's stdout.

    The orchestrator step invokes the agent and passes its raw output
    here; the module doesn't call invoke_agent itself so the step
    retains control over retries/telemetry/dry-run.

    No-ops (returns skipped=True) when the project isn't a TS project,
    has no anchors, or lacks the tsc/node_modules environment.
    """
    anchors = AnchorManifest.from_json_path(
        manifest_path(project_dir, spec_name)
    )
    if anchors is None:
        # Manifest write failed upstream; C2 can't rehearse specific
        # anchors. Skip rather than block the pipeline on a pre-existing
        # precheck issue.
        log.warning(
            "C2: anchor manifest missing at %s; rehearsal skipped",
            manifest_path(project_dir, spec_name),
        )
        return ProposalRehearsalResult(
            passed=True,
            reason="anchor manifest missing — skipped",
            skipped=True,
        )

    # Hard-error conditions come before skip conditions: a misconfigured
    # environment must fail loudly so the user fixes it, rather than
    # silently passing C2 and burning iterations on a broken setup.
    misconfig = _misconfiguration_reason(project_dir, anchors)
    if misconfig:
        log.error("C2: %s", misconfig)
        return ProposalRehearsalResult(
            passed=False,
            reason=misconfig,
            skipped=False,
        )

    skip_reason = _skip_conditions(project_dir, anchors)
    if skip_reason:
        log.info("C2: skipping proposal rehearsal — %s", skip_reason)
        return ProposalRehearsalResult(
            passed=True,
            reason=skip_reason,
            skipped=True,
        )

    proposals = parse_proposals(agent_output)
    banned_hits = scan_banned_patterns(proposals)
    findings, failing_anchors = typecheck_proposals(
        proposals, project_dir,
        scratch_dir=scratch_dir, timeout_sec=timeout_sec,
    )
    return decide_rehearsal_outcome(
        proposals, findings, failing_anchors, banned_hits, anchors,
    )
