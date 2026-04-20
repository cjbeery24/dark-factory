"""Phase 6 V2/V3/V4 anchor-file staging.

Seeds a target project's ``.dark-factory/`` tree with artifacts from a
prior run so V-dimension runs can isolate one stage of variance at a
time:

- ``--anchor-manifest``  → V2: pin the architect manifest, let TE and
  implementer run freely.
- ``--anchor-scenarios`` → V3: pin architect manifest AND TE scenarios,
  let the implementer run freely.
- ``--anchor-environment`` → V4: pin the environment contract, let
  everything else run freely.

Operates by copying files into the exact on-disk locations the
orchestrator would otherwise generate/cache. The pipeline's existing
cache-hit and "already exist" skip paths do the actual anchoring — we
just seed the filesystem ahead of them. Each anchor emits a log line
via ``progress`` so a V-run is visibly distinguishable from a free
run in the terminal and the events log.

Raises ``FileNotFoundError`` early if a source path does not exist so a
miswired anchor flag fails before any pipeline state is mutated.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnchorPaths:
    """Immutable record of the three optional anchor source paths.

    Any field may be ``None`` — the common V-dimension shape sets one
    or two fields at a time. ``apply()`` is a no-op if none are set,
    so callers that always construct an AnchorPaths regardless of
    whether flags were passed do not pay a correctness penalty.
    """

    manifest: Path | None = None
    scenarios: Path | None = None
    environment: Path | None = None

    def any_set(self) -> bool:
        return any((self.manifest, self.scenarios, self.environment))


def apply_anchors(
    anchors: AnchorPaths,
    project_dir: Path,
    spec_name: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Copy anchor artifacts into the project's ``.dark-factory/`` tree.

    Idempotent: re-running on an already-anchored project simply
    overwrites the prior copies with the same sources, so repeated
    ``dark-factory run --anchor-* ...`` invocations remain well-defined.

    Call order: run this BEFORE ``run_pipeline`` so the orchestrator's
    first look at the project tree sees the anchored files. Applying
    mid-run would race against the step that the anchor is supposed to
    replace.
    """
    if not anchors.any_set():
        return

    def _log(msg: str) -> None:
        log.info(msg)
        if progress is not None:
            progress(msg)

    for label, src in (
        ("manifest", anchors.manifest),
        ("scenarios", anchors.scenarios),
        ("environment", anchors.environment),
    ):
        if src is not None and not src.exists():
            raise FileNotFoundError(
                f"--anchor-{label} source not found: {src}"
            )

    if anchors.manifest is not None:
        dest_dir = project_dir / ".dark-factory" / "architecture"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{spec_name}-manifest.yaml"
        shutil.copy2(anchors.manifest, dest)
        _log(f"[V-anchor] manifest: {anchors.manifest} -> {dest}")

        # Auto-seed substrate manifests from the anchor's sibling dir.
        # Architects produced in prior runs emit per-substrate manifest
        # YAMLs into <archived>/architecture/substrate-manifests/; the
        # downstream substrate cache keys off those files. Anchoring
        # the architect manifest without also staging substrates causes
        # substrate_manifest_missing on every adapter and invalidates
        # the anchor even though the manifest hash matches. Seed them
        # here so V-dimension runs reproduce the source run's substrate
        # state without a manual `cp` workaround.
        substrate_src = anchors.manifest.parent / "substrate-manifests"
        substrate_dest = dest_dir / "substrate-manifests"
        if substrate_src.is_dir():
            substrate_dest.mkdir(parents=True, exist_ok=True)
            copied = 0
            for entry in substrate_src.iterdir():
                if entry.is_file():
                    shutil.copy2(entry, substrate_dest / entry.name)
                    copied += 1
            _log(
                f"[V-anchor] substrate-manifests: {substrate_src} -> "
                f"{substrate_dest} ({copied} file(s))"
            )
        else:
            _log(
                f"[V-anchor] WARNING: no substrate-manifests sibling at "
                f"{substrate_src}; anchor may invalidate on substrate cache "
                f"miss. Pipeline will proceed."
            )

    if anchors.scenarios is not None:
        dest_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
        # Scenario anchoring overwrites the target dir — a partial
        # pre-existing set would cause the pipeline's "already exist"
        # skip check to fire against a mixed state.
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        if anchors.scenarios.is_dir():
            shutil.copytree(anchors.scenarios, dest_dir)
        else:
            raise ValueError(
                f"--anchor-scenarios must be a directory, got: {anchors.scenarios}"
            )
        _log(f"[V-anchor] scenarios: {anchors.scenarios} -> {dest_dir}")

    if anchors.environment is not None:
        dest_dir = project_dir / ".dark-factory" / "architecture"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "environment.toml"
        shutil.copy2(anchors.environment, dest)
        _log(f"[V-anchor] environment: {anchors.environment} -> {dest}")
