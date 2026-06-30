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
        # ``^api\.anthropic\.com(:\d+)?$`` convention. The optional ``(:\d+)?`` is
        # retained for two reasons: (1) it matches both the bare host tinyproxy
        # presents for any plain-HTTP request it still proxies AND the ``host:port``
        # an https CONNECT carries; (2) it keeps the anthropic-allowlist convention.
        # base_url now requires https:// (claude -p tunnels via CONNECT/TLS), so the
        # CONNECT does carry the port and it COULD be pinned here, but the egress
        # widening is the WHOLE host regardless — accepted because the host is
        # ``host.docker.internal`` = the operator's OWN machine (loopback-equivalent,
        # design §7 trust rationale), materially weaker than allowlisting an external
        # domain. ``port`` is still used for dedup, just not for the filter regex.
        regex_lines.append(f"^{re.escape(host)}(:\\d+)?$")
        if host == HOST_GATEWAY_ALIAS:
            entry = f"{host}:host-gateway"
            if entry not in add_hosts:
                add_hosts.append(entry)

    return result


def proxy_connect_ports_for_profiles(config: PipelineConfig) -> dict[str, list[int]]:
    """Per-net_type CONNECT ports to permit for local backends.

    ``claude -p`` reaches the local endpoint via the HTTP **CONNECT** method — the
    Node proxy agent tunnels through ``HTTP_PROXY`` via CONNECT then does TLS over
    the tunnel (EMPIRICAL, 2026-06-30: agent → ``CONNECT host.docker.internal:3456``
    → tinyproxy "Refused CONNECT method on port 3456"). Because the tunnel is always
    TLS, base_url requires https:// and the shim must terminate TLS (proven on :8443).
    tinyproxy's ``ConnectPort`` default permits 443 only, so a host-bridge backend
    on a non-443 port is blocked unless its port is added. The egress filter already
    admits the host (optional-port regex); this only widens the CONNECT *method* to
    the same host's port. Mirrors ``proxy_allow_for_profiles`` scoping; default-OFF
    => empty lists.

    ACCEPTED TRADE-OFF (documented, like the "whole host" allowlist widening above):
    tinyproxy's ``ConnectPort`` is GLOBAL to the proxy, not per-host — so permitting
    CONNECT on port N lets a client CONNECT to :N on ANY allowlisted host, not just
    the local backend. Reachability is still gated by ``FilterDefaultDeny`` + the host
    allowlist (only ``host.docker.internal`` + the baked hosts), so this is not open
    egress; it is the only granularity tinyproxy offers. Dedup is therefore PORT-only
    per net_type (not ``(host, port)`` like the allowlist) — the directive is global.
    """
    result: dict[str, list[int]] = {nt: [] for nt in _PROXY_NET_TYPES}
    for role_value, pname in config.agent_model_profiles.items():
        if pname == "default":
            continue
        prof = config.model_profiles.get(pname)
        if prof is None or not prof.base_url:
            continue
        port = urlsplit(prof.base_url).port
        if port is None:
            continue
        net_type = _net_type_for_role(role_value)
        if port not in result[net_type]:
            result[net_type].append(port)
    return result
