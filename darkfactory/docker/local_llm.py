"""Egress-proxy allowlisting for local-LLM backends (Phase 4).

When agents are routed to a local model server on the host bridge
(``host.docker.internal``), the model host must be added to the tinyproxy
allowlist so an ``--internal`` agent container can reach it THROUGH the existing
proxy — the same path used for ``api.anthropic.com``. Agents never touch the host
directly: with ``HTTP_PROXY`` set, the agent sends an absolute-URI request to the
proxy and the PROXY resolves + connects (so only the proxy needs ``--add-host``).

This module computes, per egress ``net_type``, the allowlist regex lines and
``--add-host`` args the proxy needs. Default-OFF: returns empty lists for every
net_type when no role is mapped to a custom-backend profile.

Security note: ``base_url`` is already validated at ``ModelProfile`` construction
(http scheme, non-loopback host, no whitespace/control chars), so the host is safe
to anchor into a filter-file regex here.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig

# Docker host-gateway alias — the host bridge. Reaching it requires --add-host on
# the proxy (automatic on Docker Desktop, required on Linux Docker Engine).
HOST_GATEWAY_ALIAS = "host.docker.internal"

# Net_type each role's container is assigned to. Mirrors
# pipeline.agents._EXECUTION_ROLES (IMPLEMENTER -> execution, every other role ->
# analysis). Kept as a local constant to preserve docker<-pipeline layering (this
# low-level module must not import from pipeline/); a consistency test in
# tests/unit/test_local_llm_proxy.py asserts it stays in sync with the canonical set.
_EXECUTION_ROLE_VALUES = frozenset({AgentRole.IMPLEMENTER.value})

# The egress net_types that have a tinyproxy (matches networks.setup_proxies).
_PROXY_NET_TYPES = ("analysis", "execution")


def _net_type_for_role(role_value: str) -> str:
    return "execution" if role_value in _EXECUTION_ROLE_VALUES else "analysis"


def proxy_allow_for_profiles(
    config: PipelineConfig,
) -> dict[str, tuple[list[str], list[str]]]:
    """Per-net_type ``(regex_lines, add_hosts)`` to widen the egress proxy.

    Scoped per net_type (Phase-4 SHOULD #7): only the net_types whose mapped roles
    actually use a custom-backend profile are widened — so an analysis-only local
    role does NOT open the host on the ``execution`` network (where the
    implementer's built code and blind scenarios run). Only profiles actually
    referenced by ``agent_model_profiles`` widen egress; a defined-but-unused
    profile is inert.

    Returns ``{net_type: ([], [])}`` for every net_type when nothing is routed to a
    custom backend (default-OFF).
    """
    result: dict[str, tuple[list[str], list[str]]] = {
        nt: ([], []) for nt in _PROXY_NET_TYPES
    }
    seen: dict[str, set[tuple[str, int | None]]] = {nt: set() for nt in _PROXY_NET_TYPES}

    for role_value, pname in config.agent_model_profiles.items():
        if pname == "default":
            continue
        prof = config.model_profiles.get(pname)
        if prof is None or not prof.base_url:
            continue
        net_type = _net_type_for_role(role_value)
        parsed = urlsplit(prof.base_url)
        host = parsed.hostname or ""
        port = parsed.port
        if (host, port) in seen[net_type]:
            continue
        seen[net_type].add((host, port))

        # `result[net_type]` is a tuple holding two list objects; unpacking binds
        # these names to those SAME lists, so the .append() calls below mutate the
        # dict's lists in place. Do NOT reassign regex_lines/add_hosts (a tuple
        # can't be reassigned-into → a fresh list would silently lose the mutation).
        regex_lines, add_hosts = result[net_type]
        # Anchored + escaped, optional-port — matches the existing
        # ``^api\.anthropic\.com(:\d+)?$`` convention. EMPIRICAL FINDING (Phase-4
        # manual Docker check, 2026-06-27): tinyproxy filters on the BARE host for
        # plain-HTTP proxying (it strips the port before matching), so a strict
        # ``^host:PORT$`` is DENIED — the agent never reaches the model — while
        # ``(:\d+)?`` matches the bare host tinyproxy actually presents (verified:
        # strict→403, optional-port→200 to host.docker.internal:11434). The port
        # therefore CANNOT be pinned at this layer for an http:// backend (an HTTPS
        # CONNECT WOULD carry the port, but base_url validation forbids https here).
        # So the egress widening is the WHOLE host — accepted because the host is
        # ``host.docker.internal`` = the operator's OWN machine (loopback-equivalent,
        # design §7 trust rationale), materially weaker than allowlisting an external
        # domain. ``port`` is still used for dedup, just not for the filter regex.
        regex_lines.append(f"^{re.escape(host)}(:\\d+)?$")
        if host == HOST_GATEWAY_ALIAS:
            entry = f"{host}:host-gateway"
            if entry not in add_hosts:
                add_hosts.append(entry)

    return result
