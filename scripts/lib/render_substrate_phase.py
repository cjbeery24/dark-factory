#!/usr/bin/env python3
"""Render the Substrate Verification phase row into the gate report.

Stdlib-only — runs inside the gate container (which has the host
``scripts/`` directory mounted at ``/opt/dark-factory/scripts``) and
therefore must not depend on the ``darkfactory`` package (which is
deliberately *not* mounted into the gate; see
``build_darkfactory_mount`` for the security rationale).

The orchestrator-side dispatcher (``darkfactory.pipeline.substrate_phase``)
runs in a peer verifier container before the gate launches and writes
the aggregated findings envelope to
``.dark-factory/reports/<spec>-substrate-findings.json``. This script
reads that file and renders one row per the gate report's existing
schema, plus a details block when the phase failed.

Exit code: ``0`` for skip / pass, ``1`` for fail. The bash caller
maps that into ``GATE_FAILED=true`` for the existing soft-fail path.

Usage::

    render_substrate_phase.py REPORT_FILE SUMMARY_FILE FINDINGS_FILE
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_GATE_NAME = "Substrate Verification"


def render(report_file: Path, summary_file: Path, findings_file: Path) -> int:
    """Append phase rows to report+summary; return 0 (skip/pass) or 1 (fail).

    Absent findings file is treated as FAIL, not SKIP. The orchestrator
    dispatcher (``_run_substrate_phase`` in ``quality_gate.py``) is
    responsible for *always* writing an envelope — including a synthetic
    fail envelope when the dispatcher itself crashes. A missing file
    therefore means a bug upstream, not a legitimate skip; SKIP requires
    an explicit ``{"status": "skip"}`` envelope.
    """
    if not findings_file.is_file():
        msg = (
            f"findings file missing at {findings_file} — orchestrator "
            f"dispatcher should have written an envelope (skip/pass/fail)"
        )
        _append(report_file, f"| {_GATE_NAME} | FAIL | {msg} |")
        _append(summary_file, f"| {_GATE_NAME} | FAIL |")
        print(f"    FAIL ({_GATE_NAME}: {msg})")
        return 1

    try:
        payload = json.loads(findings_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _append(report_file, f"| {_GATE_NAME} | FAIL | findings file unreadable: {exc} |")
        _append(summary_file, f"| {_GATE_NAME} | FAIL |")
        print(f"    FAIL ({_GATE_NAME}: findings file unreadable: {exc})")
        return 1

    status = payload.get("status", "skip")
    findings = payload.get("findings", [])

    if status == "skip":
        _append(report_file, f"| {_GATE_NAME} | SKIP | No substrate manifest required |")
        _append(summary_file, f"| {_GATE_NAME} | SKIP |")
        print(f"    SKIP ({_GATE_NAME}: no manifest required)")
        return 0

    if status == "pass":
        _append(report_file, f"| {_GATE_NAME} | PASS | All checks satisfied |")
        _append(summary_file, f"| {_GATE_NAME} | PASS |")
        print(f"    PASS ({_GATE_NAME})")
        return 0

    # status == "fail"
    count = len(findings)
    _append(
        report_file,
        f"| {_GATE_NAME} | FAIL | {count} finding{'s' if count != 1 else ''} |",
    )
    _append(summary_file, f"| {_GATE_NAME} | FAIL |")

    # Details block — same shape as ``run_gate``'s failure detail, so
    # downstream consumers (reviewer, oracle) parse it the same way.
    detail_lines = ["", f"### {_GATE_NAME} Failure Details", "```"]
    for f in findings:
        code = f.get("code", "?")
        msg = f.get("message", "")
        detail_lines.append(f"- {code}: {msg}")
    detail_lines.append("```")
    detail_lines.append("")
    _append(report_file, "\n".join(detail_lines))

    print(f"    FAIL ({_GATE_NAME}: {count} finding{'s' if count != 1 else ''})")
    return 1


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: render_substrate_phase.py REPORT_FILE SUMMARY_FILE FINDINGS_FILE",
            file=sys.stderr,
        )
        return 2
    return render(Path(argv[1]), Path(argv[2]), Path(argv[3]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
