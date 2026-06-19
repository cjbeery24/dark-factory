"""Orchestrator-side dispatcher for the substrate verification phase.

Spawns ephemeral verifier containers as *peers* to the gate (same
execution network so the verifier can reach the Postgres sidecar) and
aggregates their findings into a single JSON envelope the in-gate
renderer (``scripts/lib/render_substrate_phase.py``) reads.

Why a peer container, not in-gate
---------------------------------
The gate container runs the project's actual build/test toolchain,
which means it executes untrusted user code (transitive npm/pip deps).
Mounting the ``darkfactory`` package into the gate would hand a
compromised dep a read-only blueprint of the orchestrator's writable
state surface — converting "guess at state-file shape to corrupt" into
"targeted tampering with full schematic knowledge." The verifier
container has no project mount and runs only adapter code, so the
same package mount is safe there. See ``build_darkfactory_mount``
docstring and the substrate-verification plan for the full rationale.

Output envelope (written to the findings file the gate reads)::

    {"status": "skip" | "pass" | "fail", "findings": [...]}

Aggregation across multiple active adapters: status is ``"fail"`` if
any adapter failed, else ``"pass"`` if any adapter ran and returned
findings-empty (``[]``), else ``"skip"`` (every adapter returned
``None``). Findings are the union across adapters.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from darkfactory.docker.containers import execute_container
from darkfactory.docker.mounts import build_darkfactory_mount
from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT, RunResult

if TYPE_CHECKING:
    from darkfactory.adapters.base import EcosystemAdapter
    from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)

# Verifier image: dark-factory-python carries psycopg, which the
# Postgres adapter's verify_substrate needs. The verifier doesn't run
# user code, so we always use the python image regardless of the
# project's own language adapter.
_VERIFIER_IMAGE = "dark-factory-python"

# Verifier timeout: substrate verification is a small handful of SQL
# queries against an already-running sidecar. 60s is generous; any
# longer almost certainly means the sidecar is unreachable.
_VERIFIER_TIMEOUT = 60

# stderr/stdout tail size included in synthesised crash findings. Must
# be large enough to carry a full Python import-time traceback
# (ModuleNotFoundError from three-levels-deep imports can run ~2KB);
# 500 bytes was not, and chopped the path in the middle of ``/opt``.
# Keep bounded so an adapter that dumps megabytes to stderr can't blow
# up the findings envelope downstream.
_STDERR_TAIL = 4000


@dataclass
class SubstrateResult:
    """Aggregated substrate verification result across all active adapters."""
    status: str = "skip"  # "skip" | "pass" | "fail"
    findings: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"status": self.status, "findings": self.findings})


def run_substrate_verification(
    adapters: list["EcosystemAdapter"],
    spec_name: str,
    config: "PipelineConfig",
    run_id: str,
    project_dir: Path,
    *,
    sidecar_env: dict[str, str],
    network: str | None,
    dry_run: bool = False,
) -> SubstrateResult:
    """Spawn one verifier container per active adapter and aggregate.

    Writes the aggregated result to
    ``.dark-factory/reports/<spec_name>-substrate-findings.json`` (the
    file the in-gate renderer reads) AND returns it. Caller doesn't
    need to read the file back.

    On dry-run: returns a skip result without spawning containers; the
    gate-side renderer treats a missing findings file as SKIP.
    """
    result = SubstrateResult()

    if dry_run or not adapters:
        _write_findings_file(project_dir, spec_name, result)
        return result

    spec_path = project_dir / ".dark-factory" / "specs" / f"{spec_name}.md"
    manifest_path = (
        project_dir / ".dark-factory" / "architecture"
        / "substrate-manifests" / f"{spec_name}.toml"
    )

    if not spec_path.is_file():
        log.warning(
            "substrate verification: spec file missing at %s; skipping",
            spec_path,
        )
        _write_findings_file(project_dir, spec_name, result)
        return result

    # Architect-authority gate (Sprint 8). The architect read the whole spec and
    # decided whether substrate is needed; the per-adapter verify_substrate falls
    # back to a prose regex (`triggered_rules`) that false-positives on DENIALS
    # ("...no cross-cutting, tenant-isolation, ... invariant"; echo-endpoint's
    # "requires no tenant context") and emits `substrate-manifest-missing`, which
    # ESCALATES functionally-complete substrate-LESS specs. The architect's own
    # YAML manifest is the authoritative signal that it RAN — so:
    #   YAML present + substrate TOML absent  → architect ran AND modeled no
    #     substrate → SKIP (do not let the regex override the architect).
    #   YAML absent                           → architect never produced output →
    #     fall through; verify_substrate's regex branch flags the real missing-
    #     architecture failure (the distinguishability case it exists for).
    # Mirrors the shipped Sprint-6 A3 staging gate (orchestrator.py:2917+), which
    # already made adapter-contract staging manifest-authoritative. Scoped to the
    # GATE only — activation / cache keep the cheap regex (a false trigger there
    # just stages unused tooling, never blocks a ship). The lexical trigger is
    # left untouched (regex negation is intractable; see Sprint 8 design doc).
    yaml_manifest = (
        project_dir / ".dark-factory" / "architecture"
        / f"{spec_name}-manifest.yaml"
    )
    # #20 cross-file invariant: any verifier path that reads LIVE DB state must
    # require a substrate manifest (substrate TOML). The gate's setup pre-pass
    # (quality_gate.py::run_quality_gate, gated on _substrate_manifest_exists)
    # only migrates the sidecar before verification when the TOML is present —
    # so a no-manifest fall-through here MUST NOT probe the DB, or it would
    # re-introduce the verify-before-migration false-negative. Today this holds:
    # postgres verify_substrate only connects to the sidecar on the
    # manifest-present path (adapter.py::verify_substrate). Keep it that way.
    if not manifest_path.is_file() and yaml_manifest.is_file():
        log.info(
            "substrate verification: architect produced a manifest (%s) but no "
            "substrate manifest (%s) — architect modeled no substrate; SKIP "
            "(architect-authority over the prose trigger).",
            yaml_manifest.name, manifest_path.name,
        )
        _write_findings_file(project_dir, spec_name, result)
        return result

    for adapter in adapters:
        per_adapter = _run_one_adapter(
            adapter_name=adapter.name,
            spec_path=spec_path,
            manifest_path=manifest_path,
            sidecar_env=sidecar_env,
            network=network,
            run_id=run_id,
        )
        _merge(result, per_adapter)

    _write_findings_file(project_dir, spec_name, result)
    return result


def _run_one_adapter(
    *,
    adapter_name: str,
    spec_path: Path,
    manifest_path: Path,
    sidecar_env: dict[str, str],
    network: str | None,
    run_id: str,
) -> SubstrateResult:
    """Spawn a single verifier container for one adapter and parse its output.

    Container args:
    - Network: the gate's execution network (so the sidecar hostname resolves)
    - Mounts: spec :ro, manifest :ro (only if the file exists),
      darkfactory pkg :ro
    - Env: PYTHONPATH + sidecar inject_env (DATABASE_URL etc)

    Container exit code is informational — the verifier always exits 0
    on a valid run and emits ``{"status": ..., "findings": [...]}`` on
    stdout. A non-zero exit means the entry script crashed; we
    synthesize a fail result with a verifier-crash finding so the
    failure surfaces in the gate report instead of being lost.
    """
    docker_args = _build_verifier_docker_args(
        spec_path=spec_path,
        manifest_path=manifest_path,
        sidecar_env=sidecar_env,
        network=network,
        run_id=run_id,
    )

    cmd = [
        "-m", "darkfactory.pipeline.substrate_verifier_entry",
        "--adapter", adapter_name,
        "--manifest", "/manifest.toml",
        "--spec", "/spec.md",
    ]

    run_result = execute_container(
        docker_args, _VERIFIER_IMAGE, cmd, timeout=_VERIFIER_TIMEOUT,
    )

    return _parse_verifier_output(adapter_name, run_result)


def _build_verifier_docker_args(
    *,
    spec_path: Path,
    manifest_path: Path,
    sidecar_env: dict[str, str],
    network: str | None,
    run_id: str,
) -> list[str]:
    args: list[str] = [
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=substrate-verifier",
        "--entrypoint", "python3",
        "-e", "PYTHONPATH=/opt/dark-factory",
    ]
    if network:
        args.extend(["--network", network])
    for key, val in sidecar_env.items():
        args.extend(["-e", f"{key}={val}"])

    # Spec is required (caller already verified existence). Manifest is
    # optional — when it's missing the adapter's verify_substrate is
    # responsible for either returning the substrate-manifest-missing
    # finding (rules fired) or None (legitimate skip). We deliberately
    # do not synthesise a fake mount for the missing-manifest case;
    # leaving the path absent inside the container is the natural
    # signal.
    args.extend(["-v", f"{spec_path}:/spec.md:ro"])
    if manifest_path.is_file():
        args.extend(["-v", f"{manifest_path}:/manifest.toml:ro"])
    args.extend(build_darkfactory_mount())
    return args


def _parse_verifier_output(
    adapter_name: str, run_result: RunResult,
) -> SubstrateResult:
    """Parse the verifier's stdout JSON; synthesise fail on crash or timeout.

    Timeout (exit code 124, matching coreutils ``timeout(1)`` and our
    ``subprocess_runner.EXIT_TIMEOUT``) gets its own finding code so
    operators can tell "verifier crashed" from "sidecar unreachable /
    query hung" without reading stderr.

    The entry script redirects stdout to stderr before any darkfactory
    import (see ``substrate_verifier_entry`` module docstring), so the
    only thing on real stdout is the single envelope JSON. We parse the
    whole blob, not the last line — last-line parsing was a workaround
    for adapter logging on stdout that is no longer needed and would
    silently mask any future violation of the redirect discipline.
    """
    if run_result.exit_code == EXIT_TIMEOUT:
        log.warning(
            "substrate verifier (%s) timed out; stderr=%s",
            adapter_name, run_result.stderr[:_STDERR_TAIL],
        )
        return SubstrateResult(
            status="fail",
            findings=[{
                "severity": "error",
                "code": "substrate-verifier-timeout",
                "message": (
                    f"verifier container for adapter '{adapter_name}' "
                    f"exceeded {_VERIFIER_TIMEOUT}s timeout — sidecar "
                    f"likely unreachable or query hung: "
                    f"{(run_result.stderr or run_result.stdout)[:_STDERR_TAIL]}"
                ),
                "path": "",
            }],
        )

    if run_result.exit_code != 0:
        log.warning(
            "substrate verifier (%s) exited %d; stderr=%s",
            adapter_name, run_result.exit_code,
            run_result.stderr[:_STDERR_TAIL],
        )
        return SubstrateResult(
            status="fail",
            findings=[{
                "severity": "error",
                "code": "substrate-verifier-crashed",
                "message": (
                    f"verifier container for adapter '{adapter_name}' "
                    f"exited {run_result.exit_code}: "
                    f"{(run_result.stderr or run_result.stdout)[:_STDERR_TAIL]}"
                ),
                "path": "",
            }],
        )

    try:
        payload = json.loads(run_result.stdout.strip())
    except json.JSONDecodeError as exc:
        log.warning(
            "substrate verifier (%s) output unparseable: %s",
            adapter_name, exc,
        )
        return SubstrateResult(
            status="fail",
            findings=[{
                "severity": "error",
                "code": "substrate-verifier-output-unparseable",
                "message": (
                    f"verifier container for adapter '{adapter_name}' "
                    f"emitted unparseable JSON: {exc}"
                ),
                "path": "",
            }],
        )

    return SubstrateResult(
        status=payload.get("status", "fail"),
        findings=list(payload.get("findings", [])),
    )


def _merge(target: SubstrateResult, source: SubstrateResult) -> None:
    """Merge per-adapter result into the running aggregate.

    Status precedence: fail > pass > skip. Findings union.
    """
    target.findings.extend(source.findings)
    if source.status == "fail":
        target.status = "fail"
    elif source.status == "pass" and target.status == "skip":
        target.status = "pass"


def _write_findings_file(
    project_dir: Path, spec_name: str, result: SubstrateResult,
) -> None:
    """Persist the aggregated envelope to the path the in-gate renderer reads."""
    out = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-substrate-findings.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.to_json(), encoding="utf-8")
