"""Maintained-SAST (Semgrep) security scan — ADVISORY / COMPARE mode.

This module runs a vendored, externally-maintained Semgrep ruleset
(`sast_rules/`) over the implementation source and reconciles its findings
against the bespoke in-house regex scanner (`darkfactory/review/patterns.py`,
consumed by `prepass.analyze_security`). It is the measurement half of the
SAST-swap (docs/curation-audit.md; 00-RESET-PLAN.md "Generalization thesis &
curation policy").

**It does NOT gate.** In compare mode the regexes remain the sole security
authority: this module's output never feeds `security_high_count`, never sets
`GATE_FAILED`, never alters any score. It writes a separate compare artifact
(`{spec}-sast-compare-iter{N}.jsonl`) consumed offline + by the scorecard to
decide, empirically, which bespoke regexes Semgrep can subsume (the demote bar
lives in docs/curation-audit.md). The eventual flip to Semgrep-authoritative is
a separate, authorization-gated change.

Design invariants (Neo design pass, Sprint 30):
- `run_semgrep` NEVER raises (graceful no-op, like the pglast SQL-lint). A
  missing/old/crashing/timed-out Semgrep yields `ran=False` — which is NOT
  "clean"; it means "did not scan". Compare-mode cannot misread that as clean
  because Semgrep doesn't feed the gate here.
- Owned-surface scoping reuses `prepass._partition_by_surface` /
  `_normalize_rel_path` by import (one normalization implementation — a silent
  path mismatch is the dangerous false-clean direction).
- Offline & hermetic: `--config` points at the local vendored dir (never a
  `p/...` pack or URL), `--metrics=off`, no version check → zero network fetch.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.review.prepass import (
    SecurityResult,
    _normalize_rel_path,
    _partition_by_surface,
)

log = logging.getLogger(__name__)

# Vendored-ruleset root (sibling of this module). See sast_rules/README.md.
_RULES_DIR = Path(__file__).resolve().parent / "sast_rules"

# Language → vendored ruleset subdir. "node" covers JS + TS (mirrors
# patterns.SECURITY_PATTERNS). Languages absent here → no scan (ran=False),
# honest "did not scan" rather than a false-clean.
_LANG_SUBDIR = {"python": "python", "node": "node"}

# Semgrep version pin (mirrors pyproject [sast] extra: semgrep>=1.166,<2).
# Outside this range → ran=False (don't run an unpinned engine; non-reproducible).
_SEMGREP_MIN = (1, 166)
_SEMGREP_MAX_MAJOR = 2

# Wall-clock cap on the whole Semgrep subprocess (outer guard against a hang;
# the inner `--timeout` is per-rule). Advisory scan — keep it modest.
_SUBPROCESS_TIMEOUT_S = 180
_PER_RULE_TIMEOUT_S = 30

# Semgrep severity → dark-factory severity (mirrors patterns.py vocabulary).
_SEVERITY_MAP = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}

# CWE number → one of patterns.py's seven categories. Standard taxonomy join,
# not stack-gotcha curation (see sast_rules/README.md). Unmapped → "uncategorized".
_CWE_CATEGORY = {
    89: "sql_injection",
    77: "command_injection",
    78: "command_injection",
    22: "path_traversal",
    23: "path_traversal",
    36: "path_traversal",
    259: "hardcoded_secret",
    321: "hardcoded_secret",
    798: "hardcoded_secret",
    94: "insecure_deserialization",   # code injection (eval/exec family)
    95: "insecure_deserialization",   # eval injection
    502: "insecure_deserialization",  # deserialization of untrusted data
    79: "xss",
    1321: "prototype_pollution",
}

_CWE_NUM_RE = re.compile(r"CWE-(\d+)")
# Line-proximity window: a regex and a Semgrep rule flagging the SAME (file,
# category) within +/- this many lines are treated as agreement (cosmetic
# offset, not a real disagreement).
_LINE_WINDOW = 2

_SEMGREP_VERSION: "str | None | bool" = False  # False = not yet probed


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SastFinding:
    file: str            # project-relative, normalized identically to SecurityFinding.file
    line_number: int
    category: str        # one of the 7 regex categories, or "uncategorized"
    severity: str        # "HIGH" | "MEDIUM" | "LOW"
    rule_id: str         # vendored rule id (provenance)
    matched_text: str    # <= 100 chars


@dataclass
class SastResult:
    # False == Semgrep absent / wrong version / crashed / timed out / no vendored
    # ruleset for this language. NOT "clean" — "did not scan". (Cannot be misread
    # as clean in compare mode: Semgrep does not feed the gate here.)
    ran: bool
    findings: list[SastFinding] = field(default_factory=list)            # owned-surface (gating-equivalent)
    advisory_findings: list[SastFinding] = field(default_factory=list)   # out-of-scope
    error: str = ""
    semgrep_version: str = ""
    ruleset_version: str = ""
    language: str = ""


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def _probe_semgrep() -> "str | None":
    """Return the Semgrep version string if a PINNED-range Semgrep is callable,
    else None. Cached per process (mirrors sql_lint._probe_pglast)."""
    global _SEMGREP_VERSION
    if _SEMGREP_VERSION is not False:
        return _SEMGREP_VERSION  # type: ignore[return-value]
    _SEMGREP_VERSION = None
    try:
        proc = subprocess.run(
            ["semgrep", "--version"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip().splitlines()
    ver = raw[0].strip() if raw else ""
    m = re.match(r"(\d+)\.(\d+)", ver)
    if not m:
        return None
    major, minor = int(m.group(1)), int(m.group(2))
    if major >= _SEMGREP_MAX_MAJOR or (major, minor) < _SEMGREP_MIN:
        log.warning(
            "SAST: semgrep %s outside pinned range >=%d.%d,<%d — skipping scan",
            ver, _SEMGREP_MIN[0], _SEMGREP_MIN[1], _SEMGREP_MAX_MAJOR,
        )
        return None
    _SEMGREP_VERSION = ver
    return ver


def _ruleset_version() -> str:
    try:
        return (_RULES_DIR / "RULESET_VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def _category_for(cwes: "list[str] | None") -> str:
    for cwe in cwes or []:
        m = _CWE_NUM_RE.search(cwe)
        if m and int(m.group(1)) in _CWE_CATEGORY:
            return _CWE_CATEGORY[int(m.group(1))]
    return "uncategorized"


def _source_line(abs_path: Path, line_no: int, _cache: dict) -> str:
    """Best-effort matched-source line for triage. Semgrep's ``extra.lines`` can
    be a placeholder ("requires login") for some OSS rules, so read the real line
    from disk (the file was just scanned). Returns "" on any read error."""
    if line_no <= 0:
        return ""
    key = str(abs_path)
    if key not in _cache:
        try:
            _cache[key] = abs_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            _cache[key] = []
    lines = _cache[key]
    return lines[line_no - 1].strip()[:100] if 0 < line_no <= len(lines) else ""


def _parse_results(payload: dict, project_dir: Path) -> list[SastFinding]:
    findings: list[SastFinding] = []
    line_cache: dict[str, list[str]] = {}
    for r in payload.get("results", []):
        try:
            abs_path = Path(r["path"])
            try:
                rel = str(abs_path.relative_to(project_dir))
            except ValueError:
                rel = r["path"]
            extra = r.get("extra", {}) or {}
            meta = extra.get("metadata", {}) or {}
            sev = _SEVERITY_MAP.get(str(extra.get("severity", "")).upper(), "LOW")
            rule_id = str(r.get("check_id", "")).split(".")[-1]
            line_no = int(r.get("start", {}).get("line", 0))
            matched = _source_line(abs_path, line_no, line_cache)
            if not matched:
                matched = str(extra.get("lines", ""))[:100]
            findings.append(SastFinding(
                file=rel,
                line_number=line_no,
                category=_category_for(meta.get("cwe")),
                severity=sev,
                rule_id=rule_id,
                matched_text=matched,
            ))
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("SAST: skipping malformed semgrep result: %s", exc)
    return findings


def run_semgrep(
    project_dir: Path,
    source_dirs: list[str],
    language: str,
    owned_surface: "set[str] | None" = None,
) -> SastResult:
    """Run the vendored Semgrep ruleset over source_dirs. NEVER raises.

    Returns ``ran=False`` (NOT clean) when Semgrep is unavailable/out-of-pin/
    crashes/times out, or no vendored ruleset exists for ``language``. Findings
    are split into gating (``findings``) and out-of-scope (``advisory_findings``)
    by ``owned_surface`` using the SAME partition as ``analyze_security``.
    """
    rsv = _ruleset_version()
    subdir = _LANG_SUBDIR.get(language)
    if subdir is None:
        return SastResult(ran=False, error=f"no vendored ruleset for language {language!r}",
                          ruleset_version=rsv, language=language)
    rules_path = _RULES_DIR / subdir
    if not rules_path.is_dir():
        return SastResult(ran=False, error=f"vendored ruleset dir missing: {rules_path}",
                          ruleset_version=rsv, language=language)

    version = _probe_semgrep()
    if version is None:
        return SastResult(ran=False, error="semgrep not installed or outside pinned range",
                          ruleset_version=rsv, language=language)

    targets = [str(project_dir / sd) for sd in source_dirs if (project_dir / sd).is_dir()]
    if not targets:
        # No source dirs present — a real clean scan (symmetric with regex EC-3).
        return SastResult(ran=True, semgrep_version=version, ruleset_version=rsv, language=language)

    argv = [
        "semgrep", "scan",
        "--config", str(rules_path),
        "--metrics=off",
        "--disable-version-check",
        "--no-git-ignore",           # parity with the regex scanner (scans all source_dirs files)
        "--quiet", "--json",
        "--timeout", str(_PER_RULE_TIMEOUT_S),
        *targets,
    ]
    env = dict(os.environ)
    env["SEMGREP_SEND_METRICS"] = "off"
    env["SEMGREP_ENABLE_VERSION_CHECK"] = "0"

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_SUBPROCESS_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired:
        log.warning("SAST: semgrep timed out after %ds — advisory scan skipped", _SUBPROCESS_TIMEOUT_S)
        return SastResult(ran=False, error="timeout", semgrep_version=version,
                          ruleset_version=rsv, language=language)
    except (OSError, subprocess.SubprocessError) as exc:
        return SastResult(ran=False, error=f"subprocess error: {exc}", semgrep_version=version,
                          ruleset_version=rsv, language=language)

    # Semgrep exit codes: 0 = ran (no/with findings), 1 = ran + blocking findings
    # (only with --error, which we don't pass — so success is rc 0 in practice, but
    # accept {0,1} for robustness). >=2 = engine/config/usage error (e.g. 2 invalid,
    # 7 no rules/targets) — NOT a valid scan even if it emitted JSON.
    try:
        payload = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return SastResult(ran=False, error=f"unparseable output (rc={proc.returncode}): {tail[0][:120]}",
                          semgrep_version=version, ruleset_version=rsv, language=language)
    # [Codex P1] A non-zero ENGINE error with parseable JSON must NOT read as a
    # clean scan — reject it as ran=False, never silent-clean.
    if proc.returncode not in (0, 1):
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return SastResult(ran=False, error=f"semgrep engine error (rc={proc.returncode}): {tail[0][:120]}",
                          semgrep_version=version, ruleset_version=rsv, language=language)
    # [Codex P2] Valid JSON of an unexpected shape (top-level array, results:null)
    # must not crash _parse_results — run_semgrep must never raise.
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return SastResult(ran=False, error="unexpected semgrep JSON shape",
                          semgrep_version=version, ruleset_version=rsv, language=language)

    try:
        all_findings = _parse_results(payload, project_dir)
    except Exception as exc:  # defensive backstop — never raise
        return SastResult(ran=False, error=f"result parse error: {exc}",
                          semgrep_version=version, ruleset_version=rsv, language=language)
    gating, advisory = _partition_by_surface(all_findings, lambda f: f.file, owned_surface)
    return SastResult(
        ran=True, findings=gating, advisory_findings=advisory,
        semgrep_version=version, ruleset_version=rsv, language=language,
    )


# ---------------------------------------------------------------------------
# Reconcile (regex vs Semgrep) + compare artifact
# ---------------------------------------------------------------------------


def _reconcile(regex_findings: list, sast_findings: list[SastFinding], scope: str) -> list[dict]:
    """Match regex vs Semgrep findings on (normalized file, category) within a
    +/-_LINE_WINDOW line window. Returns one row per reconciled site:
    verdict in {"agree","regex_only","semgrep_only"}.

    ``regex_findings`` are ``prepass.SecurityFinding`` (``.pattern`` == category).
    """
    rows: list[dict] = []
    sast_remaining = list(sast_findings)

    for rf in regex_findings:
        rcat = rf.pattern
        rfile = _normalize_rel_path(rf.file)
        match = None
        for sf in sast_remaining:
            if (_normalize_rel_path(sf.file) == rfile and sf.category == rcat
                    and abs(sf.line_number - rf.line_number) <= _LINE_WINDOW):
                match = sf
                break
        if match is not None:
            sast_remaining.remove(match)
            rows.append({
                "scope": scope, "verdict": "agree", "file": rfile,
                "category": rcat, "line": rf.line_number,
                "line_delta": match.line_number - rf.line_number,
                "regex": {"pattern": rf.pattern, "severity": rf.severity,
                          "matched": rf.matched_text},
                "semgrep": {"rule_id": match.rule_id, "severity": match.severity,
                            "matched": match.matched_text},
            })
        else:
            rows.append({
                "scope": scope, "verdict": "regex_only", "file": rfile,
                "category": rcat, "line": rf.line_number,
                "regex": {"pattern": rf.pattern, "severity": rf.severity,
                          "matched": rf.matched_text},
                "semgrep": None,
            })

    for sf in sast_remaining:
        rows.append({
            "scope": scope, "verdict": "semgrep_only",
            "file": _normalize_rel_path(sf.file),
            "category": sf.category, "line": sf.line_number,
            "regex": None,
            "semgrep": {"rule_id": sf.rule_id, "severity": sf.severity,
                        "matched": sf.matched_text},
        })
    return rows


def _summarize(rows: list[dict], sast: SastResult) -> dict:
    gating = [r for r in rows if r["scope"] == "gating"]
    return {
        "kind": "sast_compare_summary",
        "sast_ran": 1 if sast.ran else 0,
        "sast_error": sast.error,
        "semgrep_version": sast.semgrep_version,
        "ruleset_version": sast.ruleset_version,
        "language": sast.language,
        # Counts on the GATING surface (the surface that actually ship-gates —
        # the demote bar in docs/curation-audit.md is computed on these).
        "agree": sum(1 for r in gating if r["verdict"] == "agree"),
        "regex_only": sum(1 for r in gating if r["verdict"] == "regex_only"),
        "semgrep_only": sum(1 for r in gating if r["verdict"] == "semgrep_only"),
        # The single most important number: regex HIGH findings Semgrep does NOT
        # corroborate — candidate false positives that can NO-SHIP a clean run.
        "regex_only_high": sum(
            1 for r in gating
            if r["verdict"] == "regex_only" and r.get("regex", {}).get("severity") == "HIGH"
        ),
    }


def build_compare(regex_security: SecurityResult, sast: SastResult) -> tuple[dict, list[dict]]:
    """Pure reconciliation (no I/O) — returns (summary, rows). Testable in isolation."""
    rows = _reconcile(regex_security.findings, sast.findings, "gating")
    rows += _reconcile(
        getattr(regex_security, "advisory_findings", []) or [],
        sast.advisory_findings, "advisory",
    )
    return _summarize(rows, sast), rows


def write_sast_compare(
    project_dir: Path,
    spec_name: str,
    iteration: int,
    regex_security: SecurityResult,
    sast: SastResult,
) -> dict:
    """Write the per-iteration compare artifact (JSONL: summary line + one row
    per reconciled finding-site) to the reports dir. Returns the summary dict.

    Reports dir is unconditionally masked from the Implementer/Reviewer at the
    filesystem level (barriers.py); this artifact is NEVER inlined into any agent
    prompt — purely offline + scorecard consumption. ADVISORY: nothing here gates.
    """
    summary, rows = build_compare(regex_security, sast)
    reports_dir = project_dir / ".dark-factory" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{spec_name}-sast-compare-iter{iteration}.jsonl"
    lines = [json.dumps(summary)] + [json.dumps(r) for r in rows]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(
        "SAST compare (advisory): ran=%s agree=%d regex_only=%d semgrep_only=%d "
        "regex_only_high=%d → %s",
        summary["sast_ran"], summary["agree"], summary["regex_only"],
        summary["semgrep_only"], summary["regex_only_high"], out.name,
    )
    return summary
