"""Docker network and proxy lifecycle management.

Replaces setup_networks, cleanup_networks, setup_proxies, teardown_proxies from docker.sh.
"""

from __future__ import annotations

import logging

from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)

# Network suffixes matching bash: analysis, execution, external, evaluation.
_NETWORK_SUFFIXES = ["analysis", "execution", "external", "evaluation"]


def network_name(run_id: str, suffix: str) -> str:
    """Build a namespaced network name: df-{run_id}-{suffix}."""
    return f"df-{run_id}-{suffix}"


def proxy_name(run_id: str, suffix: str) -> str:
    """Build a namespaced proxy container name."""
    return f"df-{run_id}-proxy-{suffix}"


def setup_networks(
    run_id: str,
    *,
    egress_filtering: bool = True,
    dry_run: bool = False,
) -> list[str]:
    """Create Docker networks for the pipeline run.

    When egress_filtering is enabled, creates 4 internal networks
    plus one external network. Otherwise creates a single shared network.

    Returns list of created network names.
    """
    created: list[str] = []

    if not egress_filtering:
        name = f"df-{run_id}"
        run(
            ["docker", "network", "create", name],
            dry_run=dry_run,
        )
        created.append(name)
        return created

    for suffix in _NETWORK_SUFFIXES:
        name = network_name(run_id, suffix)
        cmd = ["docker", "network", "create"]
        # All networks are internal except 'external'.
        if suffix != "external":
            cmd.append("--internal")
        cmd.append(name)
        run(cmd, dry_run=dry_run)
        created.append(name)
        log.debug("created network: %s", name)

    return created


def cleanup_networks(run_id: str, *, dry_run: bool = False) -> None:
    """Remove all Docker networks for a run."""
    for suffix in _NETWORK_SUFFIXES:
        name = network_name(run_id, suffix)
        run(["docker", "network", "rm", name], dry_run=dry_run)

    # Also try the non-egress single network.
    run(["docker", "network", "rm", f"df-{run_id}"], dry_run=dry_run)


def setup_proxies(
    run_id: str,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Start tinyproxy containers for egress-filtered networks.

    Returns list of proxy container IDs/names.
    """
    proxies: list[str] = []

    for net_type in ["analysis", "execution"]:
        net = network_name(run_id, net_type)
        ext_net = network_name(run_id, "external")
        name = proxy_name(run_id, net_type)

        result = run(
            [
                "docker", "run", "-d",
                "--name", name,
                "--network", ext_net,
                "dark-factory-proxy:latest",
            ],
            dry_run=dry_run,
        )
        if result.exit_code == 0 and not dry_run:
            # Connect proxy to the internal network too.
            run(
                ["docker", "network", "connect", net, name],
                dry_run=dry_run,
            )
            proxies.append(name)
            log.debug("started proxy: %s on %s + %s", name, ext_net, net)

    return proxies


def teardown_proxies(run_id: str, *, dry_run: bool = False) -> None:
    """Stop and remove proxy containers."""
    for net_type in ["analysis", "execution"]:
        name = proxy_name(run_id, net_type)
        run(["docker", "stop", name], timeout=10, dry_run=dry_run)
        run(["docker", "rm", "-f", name], dry_run=dry_run)
