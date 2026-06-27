"""SSOT capability->config mapping (Hole-A deterministic materialization).

resolve_test_capabilities is the ONLY place abstract test capabilities map to
concrete test config. dom -> jsdom + DOM lib; everything else -> node + no lib
(no DOM over-provision on backend specs). The base adapter default is a no-op.
"""

from __future__ import annotations

import unittest

from darkfactory.adapters.base import ResolvedTestEnv
from darkfactory.adapters.typescript.adapter import TypescriptNodeAdapter


class TestResolveTestCapabilities(unittest.TestCase):
    def setUp(self) -> None:
        self.a = TypescriptNodeAdapter()

    def test_dom_maps_to_jsdom_and_dom_lib(self) -> None:
        r = self.a.resolve_test_capabilities(["dom"])
        self.assertEqual(r.vitest_environment, "jsdom")
        self.assertEqual(r.tsconfig_lib, ["DOM", "DOM.Iterable"])

    def test_dom_case_insensitive(self) -> None:
        self.assertEqual(
            self.a.resolve_test_capabilities(["DOM"]).vitest_environment, "jsdom")

    def test_dom_mixed_with_others_still_jsdom(self) -> None:
        r = self.a.resolve_test_capabilities(["network", "dom"])
        self.assertEqual(r.vitest_environment, "jsdom")

    def test_database_does_not_over_provision_dom(self) -> None:
        r = self.a.resolve_test_capabilities(["database"])
        self.assertEqual(r.vitest_environment, "node")
        self.assertEqual(r.tsconfig_lib, [])

    def test_empty_is_node_no_lib(self) -> None:
        r = self.a.resolve_test_capabilities([])
        self.assertEqual(r.vitest_environment, "node")
        self.assertEqual(r.tsconfig_lib, [])

    def test_non_overriding_adapter_inherits_noop_default(self) -> None:
        # A real concrete adapter that does NOT override (Postgres) must inherit
        # the safe base default (node, no lib) even when asked for dom — exercising
        # the actual inheritance path, not a hand-rolled stub.
        from darkfactory.adapters.postgres.adapter import PostgresAdapter
        r = PostgresAdapter().resolve_test_capabilities(["dom"])
        self.assertEqual(r, ResolvedTestEnv())


if __name__ == "__main__":
    unittest.main()
