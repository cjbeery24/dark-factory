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
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

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
) -> list[str]:
    """Copy each adapter's ``contracts_dir()`` under
    ``scenarios_dir/_contracts/<adapter.name>/``.

    Returns the list of adapter names that had contracts staged (for
    caller-side assertions and logging).

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
    """
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for adapter in adapters:
        src = adapter.contracts_dir()
        if src is None:
            continue
        if not src.is_dir():
            log.warning(
                "adapter %s contracts_dir() = %s, but path is not a "
                "directory; skipping stage (scenarios will not have "
                "helpers for this adapter)",
                adapter.name, src,
            )
            continue

        if spec_text is not None and not adapter.spec_triggers(spec_text):
            log.info(
                "adapter %s has contracts_dir but spec_triggers did "
                "not fire for spec %s; skipping stage (substrate-clean "
                "spec — helpers would be semantically irrelevant)",
                adapter.name, spec or "<unknown>",
            )
            continue

        dest = scenarios_dir / "_contracts" / adapter.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

        file_count = sum(1 for _ in dest.rglob("*") if _.is_file())
        log.info(
            "staged %d file(s) from %s → %s", file_count, src, dest,
        )
        if events_file is not None:
            emit_contracts_staged(
                events_file,
                spec=spec, run_id=run_id,
                adapter=adapter.name,
                src=str(src), dest=str(dest),
                file_count=file_count,
            )
        staged.append(adapter.name)
    return staged
