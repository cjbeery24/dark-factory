"""Phase 4 — egress-proxy allowlisting for local-LLM backends.

Covers: proxy_allow_for_profiles (port-pinned/escaped regex, per-net_type scoping,
default-OFF), ModelProfile host-bridge validation (loopback/https/control-char),
setup_proxies arg-building (default byte-identical; extra-allow injection), the
role->net_type consistency with the canonical _EXECUTION_ROLES, and the proxy
entrypoint's blank-line guard (MUST #1) via a real shell run.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from darkfactory.docker.local_llm import (
    HOST_GATEWAY_ALIAS,
    _EXECUTION_ROLE_VALUES,
    _net_type_for_role,
    proxy_allow_for_profiles,
)
from darkfactory.docker.networks import setup_proxies
from darkfactory.errors import ConfigError
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import ModelProfile, PipelineConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENTRYPOINT = _REPO_ROOT / "docker" / "proxy-entrypoint.sh"


def _local(host_port: str = "host.docker.internal:11434", **kw) -> ModelProfile:
    return ModelProfile(
        base_url=f"http://{host_port}",
        auth_token_env="DF_LOCAL_TOKEN",
        model="qwen3-coder",
        max_output_tokens=8192,
        family="local",
        **kw,
    )


class TestBaseUrlValidation(unittest.TestCase):
    def test_loopback_rejected(self) -> None:
        # Exact forms + variant spellings (Codex P2): 127.0.0.0/8 range, the 127.x
        # inet_aton shorthand, expanded IPv6 ::1, and unspecified 0.0.0.0.
        for h in (
            "localhost", "LOCALHOST", "127.0.0.1", "127.0.1.1", "127.1",
            "0.0.0.0", "[::1]", "[0:0:0:0:0:0:0:1]",
        ):
            with self.assertRaises(ConfigError, msg=h):
                _local(f"{h}:11434")

    def test_non_loopback_host_ok(self) -> None:
        # A real host IP (not loopback) is allowed.
        p = _local("10.0.0.5:11434")
        self.assertEqual(p.base_url, "http://10.0.0.5:11434")

    def test_https_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            ModelProfile(base_url="https://host.docker.internal:11434", max_output_tokens=8192)

    def test_non_http_scheme_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            ModelProfile(base_url="ftp://host.docker.internal:11434", max_output_tokens=8192)
        with self.assertRaises(ConfigError):
            ModelProfile(base_url="host.docker.internal:11434", max_output_tokens=8192)

    def test_control_char_injection_rejected(self) -> None:
        # A newline in base_url could inject an arbitrary allowlist line (MUST #2).
        with self.assertRaises(ConfigError):
            ModelProfile(base_url="http://host\nevil.com:11434", max_output_tokens=8192)
        with self.assertRaises(ConfigError):
            ModelProfile(base_url="http://host .com:11434", max_output_tokens=8192)

    def test_no_host_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            ModelProfile(base_url="http://", max_output_tokens=8192)

    def test_valid_host_bridge_ok(self) -> None:
        p = _local()
        self.assertEqual(p.base_url, "http://host.docker.internal:11434")


class TestProxyAllowForProfiles(unittest.TestCase):
    def test_default_off_all_empty(self) -> None:
        res = proxy_allow_for_profiles(PipelineConfig())
        self.assertEqual(res, {"analysis": ([], []), "execution": ([], [])})

    def test_unused_profile_does_not_widen(self) -> None:
        # Defined but not referenced by agent_model_profiles → inert.
        cfg = PipelineConfig(model_profiles={"local": _local()})
        res = proxy_allow_for_profiles(cfg)
        self.assertEqual(res["analysis"], ([], []))
        self.assertEqual(res["execution"], ([], []))

    def test_analysis_role_widens_only_analysis(self) -> None:
        cfg = PipelineConfig(
            model_profiles={"local": _local()},
            agent_model_profiles={AgentRole.ESCALATION.value: "local"},
        )
        res = proxy_allow_for_profiles(cfg)
        self.assertEqual(
            res["analysis"],
            (["^host\\.docker\\.internal(:\\d+)?$"], ["host.docker.internal:host-gateway"]),
        )
        # SHOULD #7: execution network (built code / blind scenarios) NOT widened.
        self.assertEqual(res["execution"], ([], []))

    def test_implementer_widens_execution(self) -> None:
        cfg = PipelineConfig(
            model_profiles={"local": _local()},
            agent_model_profiles={AgentRole.IMPLEMENTER.value: "local"},
        )
        res = proxy_allow_for_profiles(cfg)
        self.assertEqual(res["execution"][0], ["^host\\.docker\\.internal(:\\d+)?$"])
        self.assertEqual(res["analysis"], ([], []))

    def test_host_scoped_optional_port_regex(self) -> None:
        # The regex is host-scoped with an optional port (matches the existing
        # ^api\.anthropic\.com(:\d+)?$ convention). EMPIRICAL: tinyproxy filters on
        # the bare host for plain HTTP, so a strict ^host:PORT$ would be DENIED and
        # the port cannot be pinned at this layer — verified by the Phase-4 manual
        # Docker check. The host (host.docker.internal = operator's own machine) is
        # what's allowlisted; the specific port is not enforceable here.
        cfg = PipelineConfig(
            model_profiles={"p": _local("host.docker.internal:3456")},
            agent_model_profiles={AgentRole.ESCALATION.value: "p"},
        )
        lines = proxy_allow_for_profiles(cfg)["analysis"][0]
        self.assertEqual(lines, ["^host\\.docker\\.internal(:\\d+)?$"])
        # Anchored to the exact host (not a substring/wildcard host).
        self.assertTrue(lines[0].startswith("^") and lines[0].endswith("$"))
        self.assertIn("host\\.docker\\.internal", lines[0])

    def test_dedup_same_host_port(self) -> None:
        cfg = PipelineConfig(
            model_profiles={"a": _local(), "b": _local()},
            agent_model_profiles={
                AgentRole.ESCALATION.value: "a",
                AgentRole.DEFECT_CLASSIFIER.value: "b",
            },
        )
        res = proxy_allow_for_profiles(cfg)
        self.assertEqual(len(res["analysis"][0]), 1)
        self.assertEqual(len(res["analysis"][1]), 1)


class TestRoleNetTypeConsistency(unittest.TestCase):
    def test_matches_canonical_execution_roles(self) -> None:
        # _EXECUTION_ROLE_VALUES must stay in sync with pipeline.agents._EXECUTION_ROLES.
        from darkfactory.pipeline.agents import _EXECUTION_ROLES
        self.assertEqual(_EXECUTION_ROLE_VALUES, {r.value for r in _EXECUTION_ROLES})

    def test_net_type_mapping(self) -> None:
        self.assertEqual(_net_type_for_role(AgentRole.IMPLEMENTER.value), "execution")
        self.assertEqual(_net_type_for_role(AgentRole.ESCALATION.value), "analysis")
        self.assertEqual(_net_type_for_role(AgentRole.REVIEWER.value), "analysis")


class TestSetupProxiesArgs(unittest.TestCase):
    def _proxy_cmds(self, **kw) -> list[list[str]]:
        # dry_run=True so run() records but doesn't execute; capture via monkeypatch.
        import darkfactory.docker.networks as nets
        captured: list[list[str]] = []
        orig = nets.run

        class _R:
            exit_code = 0
            stdout = ""

        def _fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            return _R()

        nets.run = _fake_run
        try:
            setup_proxies("run1", **kw)
        finally:
            nets.run = orig
        return [c for c in captured if c[:3] == ["docker", "run", "-d"]]

    def test_default_off_byte_identical(self) -> None:
        cmds = self._proxy_cmds()
        for c in cmds:
            self.assertNotIn("DF_EXTRA_ALLOW_REGEX", " ".join(c))
            self.assertNotIn("--add-host", c)

    def test_extra_allow_injected_only_for_named_net(self) -> None:
        extra = {
            "analysis": (["^host\\.docker\\.internal(:\\d+)?$"], ["host.docker.internal:host-gateway"]),
            "execution": ([], []),
        }
        cmds = self._proxy_cmds(extra_allow=extra)
        analysis = next(c for c in cmds if "df-run1-proxy-analysis" in c)
        execution = next(c for c in cmds if "df-run1-proxy-execution" in c)
        joined_a = " ".join(analysis)
        self.assertIn("DF_EXTRA_ALLOW_REGEX=^host\\.docker\\.internal(:\\d+)?$", joined_a)
        self.assertIn("--add-host", analysis)
        self.assertIn("host.docker.internal:host-gateway", analysis)
        # execution proxy got no widening
        self.assertNotIn("DF_EXTRA_ALLOW_REGEX", " ".join(execution))
        self.assertNotIn("--add-host", execution)


@unittest.skipUnless(os.name == "posix", "shell entrypoint test is POSIX-only")
class TestProxyEntrypointShell(unittest.TestCase):
    """Real shell run of the proxy entrypoint's allowlist-append (MUST #1 + the
    Linus BLOCKING fixes: idempotent-on-restart, loud-fail-closed)."""

    _BASE = "^api\\.anthropic\\.com(:\\d+)?$\n"

    def _make_dir(self, d: Path) -> Path:
        (d / "filter-analysis.txt").write_text(self._BASE)
        (d / "filter-execution.txt").write_text(self._BASE)
        bindir = d / "bin"
        bindir.mkdir()
        fake = bindir / "tinyproxy"  # fake so the entrypoint's exec succeeds
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return bindir

    def _run_in(self, d: Path, bindir: Path, env_extra: dict[str, str],
                check: bool = True) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
            "DF_FILTER_DIR": str(d),
            "TINYPROXY_CONF": str(d / "filter-analysis.txt"),
            **env_extra,
        }
        return subprocess.run(
            ["sh", str(_ENTRYPOINT)], env=env, check=check, timeout=20,
            capture_output=True, text=True,
        )

    def _run_entrypoint(self, env_extra: dict[str, str], runs: int = 1) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as dstr:
            d = Path(dstr)
            bindir = self._make_dir(d)
            for _ in range(runs):
                self._run_in(d, bindir, env_extra)
            return (
                (d / "filter-analysis.txt").read_text(),
                (d / "filter-execution.txt").read_text(),
            )

    def test_no_env_no_append(self) -> None:
        # MUST #1: with DF_EXTRA_ALLOW_REGEX unset, filter content is byte-identical
        # (NO blank line — a blank line in a deny-by-default filter could match-all).
        a, e = self._run_entrypoint({})
        self.assertEqual(a, self._BASE)
        self.assertEqual(e, self._BASE)

    def test_empty_env_no_append(self) -> None:
        a, e = self._run_entrypoint({"DF_EXTRA_ALLOW_REGEX": ""})
        self.assertNotIn("\n\n", a)
        self.assertEqual(a.count("\n"), 1)

    def test_appends_when_set(self) -> None:
        line = "^host\\.docker\\.internal(:\\d+)?$"
        a, e = self._run_entrypoint({"DF_EXTRA_ALLOW_REGEX": line})
        # Appended verbatim (backslashes preserved by printf '%s') to BOTH files.
        self.assertIn(line + "\n", a)
        self.assertIn(line + "\n", e)
        self.assertTrue(a.startswith("^api\\.anthropic\\.com"))

    def test_idempotent_across_restarts(self) -> None:
        # BLOCKING-1: the ENTRYPOINT runs on every restart and the filter file
        # persists; appending must NOT duplicate. Three runs => exactly one copy.
        line = "^host\\.docker\\.internal(:\\d+)?$"
        a, _ = self._run_entrypoint({"DF_EXTRA_ALLOW_REGEX": line}, runs=3)
        self.assertEqual(a.count(line), 1)
        self.assertEqual(a, self._BASE + line + "\n")

    def test_blank_lines_in_multiline_regex_skipped(self) -> None:
        # A multi-line value with an embedded blank line: both real lines append,
        # the blank is skipped (no match-all empty pattern).
        val = "^a\\.b:1$\n\n^c\\.d:2$"
        a, _ = self._run_entrypoint({"DF_EXTRA_ALLOW_REGEX": val})
        self.assertIn("^a\\.b:1$\n", a)
        self.assertIn("^c\\.d:2$\n", a)
        self.assertNotIn("\n\n", a)  # no blank line introduced

    def test_loud_fatal_on_unwritable_filter(self) -> None:
        # BLOCKING-2: a failed append must abort LOUDLY (FATAL to stderr) rather than
        # silently start tinyproxy without the allowlist or with a mystery error.
        with tempfile.TemporaryDirectory() as dstr:
            d = Path(dstr)
            bindir = self._make_dir(d)
            (d / "filter-analysis.txt").chmod(0o444)  # read-only → append fails
            try:
                proc = self._run_in(
                    d, bindir, {"DF_EXTRA_ALLOW_REGEX": "^h\\.d\\.i:1$"}, check=False,
                )
            finally:
                (d / "filter-analysis.txt").chmod(0o644)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("FATAL", proc.stderr)


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    unittest.main()
