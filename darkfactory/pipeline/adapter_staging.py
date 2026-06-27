"""Stage per-adapter wire-contract helpers into the per-spec scenarios
directory before the scenario-architect runs.

The test-engineer authors blind scenarios against the spec + architect
manifest; it cannot read implementation source. But scenarios need
wire-level assertions whose truth lives at the library layer (e.g.,
``current_setting(name, true)`` returns ``''`` at the postgres.js wire
surface, not NULL — see the 2026-04-16 database-foundation escalation).

Rather than duplicate those truths in every scenario as hand-rolled
predicates, adapters ship them as executable helpers in their
``contracts_dir()`` tree. The orchestrator copies the tree into
``.dark-factory/scenarios/<spec>/_contracts/<adapter.name>/`` so the
test-engineer imports helpers via a stable relative path
(``./_contracts/<adapter>`` from the scenarios root).

Staging runs *before* ``_step_scenario_generation`` so helpers exist
when the scenario-architect container starts. The scenarios directory
is already on the test-engineer's side of the barrier; no new
bind-mounts are required.

ROADMAP.md Phase 3 B9d adds project-side staging: when a target
project ships its own helpers (project conventions like the column
name ``app.tenant_id`` vs ``app.org_id``, GUC namespaces, etc.) under
``<project>/.dark-factory/contracts/<adapter>/``, those land at
``_contracts/<adapter>/project/`` — path-namespaced so a framework
helper and a project helper with the same filename are physically
distinct files at non-overlapping paths. Path-namespacing is strict:
a framework adapter that ships a child named ``project`` is rejected
at staging time (it would create a structural collision with the
project namespace).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from darkfactory.events.types import emit_contracts_staged

if TYPE_CHECKING:
    from darkfactory.adapters import EcosystemAdapter

log = logging.getLogger(__name__)


def stage_adapter_contracts(
    scenarios_dir: Path,
    adapters: "list[EcosystemAdapter]",
    *,
    spec: str = "",
    run_id: str = "",
    events_file: Path | None = None,
    spec_text: str | None = None,
    project_dir: Path | None = None,
) -> list[str]:
    """Copy each adapter's ``contracts_dir()`` under
    ``scenarios_dir/_contracts/<adapter.name>/`` and (Phase 3 B9d) each
    adapter's ``project_contracts_dir(project_dir)`` under
    ``scenarios_dir/_contracts/<adapter.name>/project/``.

    Returns the list of adapter names that had **framework** contracts
    staged (for caller-side assertions and logging). Project staging
    is a transparent extension — it never appears in the return value
    standalone, because project-only adapters have always been a
    non-event for the pre-existing call sites.

    Idempotent: re-running removes the stale per-adapter dest first,
    then re-copies. Adapters whose ``contracts_dir()`` returns ``None``
    are skipped silently (the ABC default — most adapters have no
    wire-contract layer). Adapters that return a non-existent path
    produce a warning but do not abort the pipeline — the run may still
    succeed if no scenario needs that adapter's helpers.

    When ``spec_text`` is provided, contracts are staged only for
    adapters whose ``spec_triggers(spec_text)`` returns ``True``. This
    closes the substrate-clean-spec coupling case: an adapter (e.g.,
    postgres) that is project-active because the project uses postgres
    in general should NOT have its wire-contract helpers staged for a
    spec that explicitly does not touch the database — the
    ``_check_contract_helper_usage`` validator rule then hard-rejects
    the test-engineer's output because the helpers are semantically
    irrelevant. Phase 5 Stage 1 / 2026-04-25 echo-endpoint smoke.

    When ``spec_text`` is ``None`` (legacy callers), every adapter that
    returns a real ``contracts_dir`` is staged — preserving the
    pre-2026-04-25 behavior so existing call sites and tests don't
    require simultaneous updates.

    When ``project_dir`` is provided, project-side staging runs after
    framework staging. The order matters: framework lands first under
    ``_contracts/<adapter>/`` (flat); project lands second under
    ``_contracts/<adapter>/project/`` (subdir). Same ``spec_triggers``
    gate applies. When ``project_dir`` is ``None`` (legacy callers and
    the unit-test paths that don't care about project staging),
    project-side staging is silently skipped. Project staging requires
    the framework adapter's contracts root to NOT contain a child named
    ``project`` — that child would collide with the namespacing
    invariant; staging refuses and logs an error rather than silently
    overwriting.
    """
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for adapter in adapters:
        # Single gate at the top of the iteration: spec_triggers is
        # adapter-level semantics, so it suppresses BOTH framework and
        # project staging in one decision. Checking it once eliminates
        # the duplicate-gate-with-dead-branch shape Linus flagged on
        # the B9d first-pass review.
        if spec_text is not None and not adapter.spec_triggers(spec_text):
            log.info(
                "adapter %s spec_triggers did not fire for spec %s; "
                "skipping framework + project staging "
                "(substrate-clean spec — helpers would be "
                "semantically irrelevant)",
                adapter.name, spec or "<unknown>",
            )
            continue

        if _stage_framework(
            adapter, scenarios_dir,
            spec=spec, run_id=run_id, events_file=events_file,
        ):
            staged.append(adapter.name)

        if project_dir is not None:
            _stage_project(
                adapter, scenarios_dir, project_dir,
                spec=spec, run_id=run_id, events_file=events_file,
            )

    return staged


def _stage_framework(
    adapter: "EcosystemAdapter",
    scenarios_dir: Path,
    *,
    spec: str,
    run_id: str,
    events_file: Path | None,
) -> bool:
    """Copy adapter's framework contracts (``contracts_dir()``) under
    ``scenarios_dir/_contracts/<adapter>/``. Returns True iff a real
    copy happened (the caller appends the adapter name to its return
    list only on True). Caller is responsible for the ``spec_triggers``
    gate; this function unconditionally stages whatever the adapter
    ships.
    """
    from darkfactory.errors import DarkFactoryError

    src = adapter.contracts_dir()
    if src is None:
        return False
    if not src.is_dir():
        log.warning(
            "adapter %s contracts_dir() = %s, but path is not a "
            "directory; skipping framework stage (project staging "
            "may still proceed if the adapter ships project helpers)",
            adapter.name, src,
        )
        return False

    # Path-namespacing invariant (ROADMAP.md Phase 3 B9d): framework
    # adapters MUST NOT ship a child named ``project`` because that
    # name is reserved for project-side staging at the same path.
    # This is an authoring error, not runtime input — fail loudly at
    # first run rather than waiting for a project that defines its own
    # helpers to expose the collision.
    if (src / "project").exists():
        raise DarkFactoryError(
            f"adapter {adapter.name!r} contracts_dir() at {src} "
            f"contains a child named 'project', which is reserved for "
            f"project-side staging (ROADMAP.md Phase 3 B9d). Rename "
            f"the framework helper or move it to a different "
            f"subdirectory."
        )

    dest = scenarios_dir / "_contracts" / adapter.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)

    file_count = sum(1 for _ in dest.rglob("*") if _.is_file())
    log.info(
        "staged %d framework file(s) from %s → %s",
        file_count, src, dest,
    )
    if events_file is not None:
        emit_contracts_staged(
            events_file,
            spec=spec, run_id=run_id,
            adapter=adapter.name,
            src=str(src), dest=str(dest),
            file_count=file_count,
            staging_kind="framework",
        )
    return True


def _stage_project(
    adapter: "EcosystemAdapter",
    scenarios_dir: Path,
    project_dir: Path,
    *,
    spec: str,
    run_id: str,
    events_file: Path | None,
) -> None:
    """ROADMAP.md Phase 3 B9d: copy
    ``adapter.project_contracts_dir(project_dir)`` under
    ``scenarios_dir/_contracts/<adapter>/project/``.

    Runs as a peer to ``_stage_framework``. Idempotent: removes the
    stale ``project/`` subdirectory before re-copying, leaving framework
    helpers (siblings) intact. Caller is responsible for the
    ``spec_triggers`` gate.
    """
    proj_src = adapter.project_contracts_dir(project_dir)
    if proj_src is None:
        return
    if not proj_src.is_dir():
        log.warning(
            "adapter %s project_contracts_dir() = %s, but path is "
            "not a directory; skipping project stage",
            adapter.name, proj_src,
        )
        return

    proj_dest = scenarios_dir / "_contracts" / adapter.name / "project"
    if proj_dest.exists():
        shutil.rmtree(proj_dest)
    proj_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(proj_src, proj_dest)

    file_count = sum(1 for _ in proj_dest.rglob("*") if _.is_file())
    log.info(
        "staged %d project file(s) from %s → %s",
        file_count, proj_src, proj_dest,
    )
    if events_file is not None:
        emit_contracts_staged(
            events_file,
            spec=spec, run_id=run_id,
            adapter=adapter.name,
            src=str(proj_src), dest=str(proj_dest),
            file_count=file_count,
            staging_kind="project",
        )


def stage_db_reset(
    scenarios_dir: Path,
    adapters: "list[EcosystemAdapter]",
) -> list[str]:
    """Stage each adapter's ``db_reset_setup_dir()`` under
    ``scenarios_dir/_df_setup/<adapter.name>/`` (a SIBLING of ``_contracts/``).

    GATE-INFRASTRUCTURE staging, deliberately separate from
    ``stage_adapter_contracts``:
    - It is gated by the CALLER on a database-active signal (a service injects
      ``DATABASE_URL``), NOT on the architect's substrate manifest. Coupling the
      per-test DB reset to the substrate-manifest gate was the Sprint-28
      mis-gating bug: a plain Postgres CRUD spec emits no substrate manifest, so
      the reset never staged and never ran.
    - It lands OUTSIDE ``_contracts/`` so it does not trip
      ``_check_contract_helper_usage`` (a staged ``.ts`` no test imports) — the
      reset is injected into the test runner's setupFiles by the gate, never
      imported by scenarios.

    Idempotent (rmtree-before-copy), mirroring ``_stage_framework``. Adapters
    whose ``db_reset_setup_dir()`` returns ``None`` (the ABC default) are
    skipped. Returns the list of adapter names that had a reset staged.
    """
    staged: list[str] = []
    for adapter in adapters:
        src = adapter.db_reset_setup_dir()
        if src is None:
            continue
        if not src.is_dir():
            log.warning(
                "adapter %s db_reset_setup_dir() = %s, but path is not a "
                "directory; skipping DB-reset stage", adapter.name, src,
            )
            continue
        dest = scenarios_dir / "_df_setup" / adapter.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        file_count = sum(1 for _ in dest.rglob("*") if _.is_file())
        log.info(
            "staged %d DB-reset file(s) from %s → %s", file_count, src, dest,
        )
        staged.append(adapter.name)
    return staged


def stage_test_env(
    project_dir: Path,
    spec_name: str,
    adapters: "list[EcosystemAdapter]",
) -> str:
    """Materialize the architect's ``required_capabilities`` -> a gate-readable
    test-environment ``.env`` (the Hole-A execution half).

    The bash gate (``scripts/lib/scenarios.sh``) cannot call the Python adapter,
    so this host-side step resolves the abstract capabilities to a concrete vitest
    ``environment`` via the adapter SSOT (``resolve_test_capabilities``) and writes
    it to ``.dark-factory/architecture/<spec>-test-env.env`` as ``KEY=value`` the
    gate greps. Mirrors the ``stage_db_reset`` host-side bridge pattern. Reads ONLY
    the architect manifest; writes one scalar env name (the same closed-vocabulary
    info already in the manifest's ``required_capabilities``). Always writes a
    definitive value (``node`` when no capability changes the env) so the gate has
    an unambiguous answer. Returns the resolved environment.
    """
    manifest_path = (
        project_dir / ".dark-factory" / "architecture" / f"{spec_name}-manifest.yaml"
    )
    environment = "node"
    if manifest_path.is_file():
        try:
            mani = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            caps = (mani.get("test_integration") or {}).get(
                "required_capabilities"
            ) or []
            if isinstance(caps, str):  # manifest allows a bare-string form
                caps = [caps]
            if isinstance(caps, list) and caps:
                caps_s = [str(c) for c in caps]
                # First adapter that maps to a non-default env wins (TS maps dom
                # -> jsdom; others no-op). Closed vocabulary, SSOT in the adapter.
                for adapter in adapters:
                    resolved = adapter.resolve_test_capabilities(caps_s)
                    if resolved.vitest_environment and resolved.vitest_environment != "node":
                        environment = resolved.vitest_environment
                        break
            elif caps:
                # Truthy but neither str nor list (e.g. a dict or scalar int) —
                # a malformed required_capabilities. Don't silently treat it as
                # node; surface it (the run still proceeds, fail-safe to node).
                log.warning(
                    "stage_test_env: required_capabilities for %s is %s, expected "
                    "a list or string; defaulting to node",
                    spec_name, type(caps).__name__,
                )
        except Exception:  # noqa: BLE001 — never let this abort the run; default node
            log.warning(
                "stage_test_env: could not resolve test environment for %s; "
                "defaulting to node", spec_name, exc_info=True,
            )
            environment = "node"
    out = (
        project_dir / ".dark-factory" / "architecture" / f"{spec_name}-test-env.env"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"DF_VITEST_ENVIRONMENT={environment}\n", encoding="utf-8")
    log.info("staged test environment for %s: %s", spec_name, environment)
    return environment


def db_reset_active(config: "Any") -> bool:
    """True when the run provisions a database sidecar that injects
    ``DATABASE_URL`` — the deterministic signal that a per-test DB reset is
    both NEEDED and POSSIBLE (the reset connects via ``DATABASE_URL``).

    Symmetric with the gate's own DB predicate: ``scripts/quality-gate.sh``
    gates the driver preflight on ``[[ -n "$DATABASE_URL" ]]``, and that var
    is set ONLY via a service's ``inject_env`` (``_collect_inject_env`` in
    quality_gate.py). Keying the reset's staging on the same fact means the
    producer (staging) and consumer (gate) can never skew. NOT keyed on
    ``spec_triggers`` (false-fires on out-of-scope prose) or the architect's
    substrate manifest (absent for plain DB CRUD specs — the Sprint-28 bug).
    """
    services = getattr(config, "services", None) or []
    return any("DATABASE_URL" in getattr(svc, "inject_env", {}) for svc in services)
