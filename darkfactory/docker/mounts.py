"""Mount, volume, and tmpfs argument builders.

Pure functions that generate Docker CLI arguments for bind mounts,
tmpfs overlays, read-write overrides, and cache volumes.

Extracted from the monolithic docker.sh for testability.
"""

from __future__ import annotations

import logging
from pathlib import Path

from darkfactory.errors import BarrierViolation

log = logging.getLogger(__name__)


def build_project_mount(project_dir: str, *, rw: bool = False) -> list[str]:
    """Build the primary project bind mount argument.

    Args:
        project_dir: Host path to the project.
        rw: If True, mount read-write. Otherwise read-only.

    Returns:
        Docker CLI args like ["-v", "/host/path:/project:ro"].
    """
    mode = "rw" if rw else "ro"
    return ["-v", f"{project_dir}:/project:{mode}"]


def build_tmpfs_masks(project_dir: str, masked_dirs: list[str]) -> list[str]:
    """Build tmpfs overlay arguments to mask restricted directories.

    Each masked directory gets an empty read-only tmpfs mounted on top,
    making the files invisible inside the container.

    Creates missing host-side directories before building args — Docker
    requires the mountpoint to exist in the bind-mounted filesystem, which
    fails for source_dirs that haven't been created yet (e.g. on a fresh
    project before implementation runs).

    Returns:
        Docker CLI args like ["--tmpfs", "/project/.dark-factory/scenarios:ro,size=0"].
    """
    args: list[str] = []
    for d in masked_dirs:
        host_path = Path(project_dir) / d
        if not host_path.exists():
            host_path.mkdir(parents=True, exist_ok=True)
            log.debug("created missing mountpoint for tmpfs mask: %s", host_path)
        container_path = f"/project/{d}"
        args.extend(["--tmpfs", f"{container_path}:ro,size=0"])
    return args



def build_rw_overlays(project_dir: str, write_dirs: list[str]) -> list[str]:
    """Build read-write bind mount overlays for specific subdirectories.

    When the project is mounted :ro, these overlays grant :rw access
    to specific subdirectories by mounting them separately.

    Returns:
        Docker CLI args like ["-v", "/host/path/.dark-factory/reviews:/project/.dark-factory/reviews:rw"].
    """
    args: list[str] = []
    for d in write_dirs:
        host_path = f"{project_dir}/{d}"
        container_path = f"/project/{d}"
        args.extend(["-v", f"{host_path}:{container_path}:rw"])
    return args


def build_cache_volumes(role: str) -> list[str]:
    """Build cache volume mounts (npm, pip, etc.).

    Returns:
        Docker CLI args for named volumes.
    """
    volumes = {
        "node": ["-v", f"df-npm-cache-{role}:/home/agent/.npm"],
        # pnpm: no named volume. Named volumes are created by the Docker daemon
        # (owned by root) and cannot be written by the container user (1000:1000).
        # Instead, npm_config_store_dir=/tmp/pnpm-store is set in build_base_args
        # so pnpm uses the world-writable /tmp tmpfs directly.
        "python": ["-v", f"df-pip-cache-{role}:/home/agent/.cache/pip"],
        "go": ["-v", f"df-go-cache-{role}:/home/agent/go"],
        "rust": ["-v", f"df-cargo-cache-{role}:/home/agent/.cargo"],
    }
    # Return all cache volumes — Docker handles non-existent paths gracefully.
    args: list[str] = []
    for lang_args in volumes.values():
        args.extend(lang_args)
    return args


def check_symlink_safety(
    project_dir: Path,
    masked_dirs: list[str],
    visible_dirs: list[str] | None = None,
) -> None:
    """Check for symlinks that cross barrier boundaries.

    Checks two directions:
    1. The masked directory itself being a symlink.
    2. Symlinks in visible directories that resolve into masked directories.

    Raises BarrierViolation if a cross-barrier symlink is found.
    """
    resolved_masked: list[Path] = []
    for masked in masked_dirs:
        masked_path = project_dir / masked
        # Check if the masked path itself is a symlink.
        if masked_path.is_symlink():
            target = masked_path.resolve()
            raise BarrierViolation(
                f"symlink {masked_path} → {target} crosses barrier boundary"
            )
        if masked_path.is_dir():
            resolved_masked.append(masked_path.resolve())

    # Check reverse direction: symlinks in visible dirs pointing into masked dirs.
    if not visible_dirs or not resolved_masked:
        return
    for vis_dir in visible_dirs:
        vis_path = project_dir / vis_dir
        if not vis_path.is_dir():
            continue
        for f in vis_path.rglob("*"):
            if f.is_symlink():
                try:
                    target = f.resolve()
                except OSError:
                    continue
                for mp in resolved_masked:
                    if target == mp or mp in target.parents:
                        raise BarrierViolation(
                            f"symlink {f} → {target} points into masked directory"
                        )


def check_hardlink_safety(project_dir: Path, source_dirs: list[str], masked_dirs: list[str]) -> None:
    """Check for hardlinks that could leak masked content into visible directories.

    Compares inodes of files in source dirs against files in masked dirs.
    Raises BarrierViolation if a hardlink is detected.
    """
    masked_inodes: set[int] = set()
    for masked in masked_dirs:
        masked_path = project_dir / masked
        if not masked_path.is_dir():
            continue
        for f in masked_path.rglob("*"):
            if f.is_file():
                masked_inodes.add(f.stat().st_ino)

    if not masked_inodes:
        return

    for src_dir in source_dirs:
        src_path = project_dir / src_dir
        if not src_path.is_dir():
            continue
        for f in src_path.rglob("*"):
            if f.is_file() and f.stat().st_ino in masked_inodes:
                raise BarrierViolation(
                    f"hardlink detected: {f} shares inode with masked directory"
                )
