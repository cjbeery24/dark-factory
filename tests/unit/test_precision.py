"""Tests for the precision gap detector in spec/precision.py.

The precision gap detector finds API surface contract ambiguities —
factory functions without return types, throws without sync/async
clarification, plugin registration without import semantics, etc.
All findings are advisory (never block pipeline entry).
"""

import unittest

from darkfactory.spec.precision import check_precision_gaps
from darkfactory.spec.spec_model import parse_spec_model


def _spec(sections: str) -> str:
    """Wrap section content in a minimal spec structure."""
    return f"# Test Spec\n\n{sections}"


def _fr_section(body: str) -> str:
    return f"## Functional Requirements\n\n{body}\n"


def _ic_section(body: str) -> str:
    return f"## Interface Contract\n\n{body}\n"


def _ec_section(body: str) -> str:
    return f"## Edge Cases\n\n{body}\n"


class TestFactoryReturnType(unittest.TestCase):
    """Pattern 1: Factory/constructor without return type constraint."""

    def test_factory_without_return_type_warns(self) -> None:
        text = _spec(_fr_section(
            "FR-1: The `createServer()` function returns a configured server."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        factory = [f for f in findings if f.pattern_id == "FACTORY_NO_RETURN_TYPE"]
        self.assertEqual(len(factory), 1)
        self.assertIn("createServer", factory[0].matched_text)

    def test_factory_with_backtick_return_type_passes(self) -> None:
        text = _spec(_fr_section(
            "FR-1: The `createServer()` function returns a `FastifyInstance`."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        factory = [f for f in findings if f.pattern_id == "FACTORY_NO_RETURN_TYPE"]
        self.assertEqual(len(factory), 0)

    def test_build_function_detected(self) -> None:
        text = _spec(_fr_section(
            "FR-1: Call `buildConfig()` to assemble runtime configuration."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        factory = [f for f in findings if f.pattern_id == "FACTORY_NO_RETURN_TYPE"]
        self.assertEqual(len(factory), 1)
        self.assertIn("buildConfig", factory[0].matched_text)

    def test_initializeApp_detected(self) -> None:
        text = _spec(_fr_section(
            "FR-1: `initializeApp()` sets up the application."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        factory = [f for f in findings if f.pattern_id == "FACTORY_NO_RETURN_TYPE"]
        self.assertEqual(len(factory), 1)

    def test_non_factory_function_ignored(self) -> None:
        """Regular function names like 'validate()' are not flagged."""
        text = _spec(_fr_section(
            "FR-1: The `validateInput()` function checks the request body."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        factory = [f for f in findings if f.pattern_id == "FACTORY_NO_RETURN_TYPE"]
        self.assertEqual(len(factory), 0)

    def test_code_block_satisfies(self) -> None:
        """A TypeScript code block in the same section suppresses the finding."""
        text = _spec(
            "## Functional Requirements\n\n"
            "FR-1: `createServer()` produces a server.\n\n"
            "```typescript\n"
            "function createServer(): FastifyInstance { ... }\n"
            "```\n"
        )
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        factory = [f for f in findings if f.pattern_id == "FACTORY_NO_RETURN_TYPE"]
        self.assertEqual(len(factory), 0)

    def test_problem_statement_section_ignored(self) -> None:
        """Non-precision-bearing sections are not scanned."""
        text = _spec(
            "## Problem Statement\n\n"
            "We need to createServer() for the API.\n"
        )
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        factory = [f for f in findings if f.pattern_id == "FACTORY_NO_RETURN_TYPE"]
        self.assertEqual(len(factory), 0)


class TestThrowsSyncAsync(unittest.TestCase):
    """Pattern 2: Error propagation without sync/async semantics."""

    def test_throws_without_qualifier_warns(self) -> None:
        text = _spec(_fr_section(
            "FR-1: `createServer()` throws `ConfigError` when env vars are invalid."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        throws = [f for f in findings if f.pattern_id == "THROWS_NO_SYNC_ASYNC"]
        self.assertEqual(len(throws), 1)

    def test_throws_with_synchronous_qualifier_passes(self) -> None:
        text = _spec(_fr_section(
            "FR-1: `createServer()` is a synchronous function that throws "
            "`ConfigError` when env vars are invalid."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        throws = [f for f in findings if f.pattern_id == "THROWS_NO_SYNC_ASYNC"]
        self.assertEqual(len(throws), 0)

    def test_raises_with_async_qualifier_passes(self) -> None:
        text = _spec(_fr_section(
            "FR-1: The async `connectDb()` function raises `DbError` on failure. "
            "The caller must await the Promise."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        throws = [f for f in findings if f.pattern_id == "THROWS_NO_SYNC_ASYNC"]
        self.assertEqual(len(throws), 0)

    def test_python_raises_detected(self) -> None:
        text = _spec(_fr_section(
            "FR-1: `load_config()` raises `ValueError` if the config file is malformed."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        throws = [f for f in findings if f.pattern_id == "THROWS_NO_SYNC_ASYNC"]
        self.assertEqual(len(throws), 1)

    def test_qualifier_elsewhere_in_section_satisfies(self) -> None:
        """The sync/async qualifier can be in a different paragraph of the same section."""
        text = _spec(
            "## Functional Requirements\n\n"
            "FR-1: `createServer()` throws `ConfigError` on bad env.\n\n"
            "`createServer()` is a synchronous factory function.\n"
        )
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        throws = [f for f in findings if f.pattern_id == "THROWS_NO_SYNC_ASYNC"]
        self.assertEqual(len(throws), 0)


class TestPluginRegistration(unittest.TestCase):
    """Pattern 3: Plugin/middleware registration without import semantics."""

    def test_register_without_import_style_warns(self) -> None:
        text = _spec(_fr_section(
            "FR-1: Register rate limiting and CORS middleware."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        plugin = [f for f in findings if f.pattern_id == "PLUGIN_NO_IMPORT_SEMANTICS"]
        self.assertEqual(len(plugin), 1)

    def test_register_with_import_semantics_passes(self) -> None:
        text = _spec(_fr_section(
            "FR-1: Register rate limiting via static import at startup, "
            "before listen."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        plugin = [f for f in findings if f.pattern_id == "PLUGIN_NO_IMPORT_SEMANTICS"]
        self.assertEqual(len(plugin), 0)

    def test_code_block_showing_registration_satisfies(self) -> None:
        text = _spec(
            "## Functional Requirements\n\n"
            "FR-1: Register CORS middleware.\n\n"
            "```typescript\n"
            "server.register(cors, { origin: '*' });\n"
            "```\n"
        )
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        plugin = [f for f in findings if f.pattern_id == "PLUGIN_NO_IMPORT_SEMANTICS"]
        self.assertEqual(len(plugin), 0)


class TestConfigPrecedence(unittest.TestCase):
    """Pattern 4: Config sources without precedence."""

    def test_two_sources_without_precedence_warns(self) -> None:
        text = _spec(_fr_section(
            "FR-1: Read configuration from environment variables "
            "and a config file. Use default values for unset keys."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        config = [f for f in findings if f.pattern_id == "CONFIG_NO_PRECEDENCE"]
        self.assertEqual(len(config), 1)

    def test_two_sources_with_precedence_passes(self) -> None:
        text = _spec(_fr_section(
            "FR-1: Read PORT from environment variable. "
            "Falls back to default value 3000 if not set."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        config = [f for f in findings if f.pattern_id == "CONFIG_NO_PRECEDENCE"]
        self.assertEqual(len(config), 0)

    def test_single_source_no_warning(self) -> None:
        text = _spec(_fr_section(
            "FR-1: Read PORT from the environment variable PORT."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        config = [f for f in findings if f.pattern_id == "CONFIG_NO_PRECEDENCE"]
        self.assertEqual(len(config), 0)


class TestLifecycleOrdering(unittest.TestCase):
    """Pattern 5: Lifecycle hooks without ordering."""

    def test_graceful_shutdown_without_steps_warns(self) -> None:
        text = _spec(_ec_section(
            "EC-1: The server performs graceful shutdown when receiving SIGTERM."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        lifecycle = [f for f in findings if f.pattern_id == "LIFECYCLE_NO_ORDER"]
        self.assertEqual(len(lifecycle), 1)

    def test_shutdown_with_numbered_steps_passes(self) -> None:
        text = _spec(
            "## Edge Cases\n\n"
            "EC-1: Graceful shutdown sequence:\n\n"
            "1. Stop accepting new connections\n"
            "2. Drain in-flight requests\n"
            "3. Close database pool\n"
            "4. Exit with code 0\n"
        )
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        lifecycle = [f for f in findings if f.pattern_id == "LIFECYCLE_NO_ORDER"]
        self.assertEqual(len(lifecycle), 0)


class TestConcurrencyMechanism(unittest.TestCase):
    """Pattern 6: Concurrency without mechanism."""

    def test_concurrent_without_mechanism_warns(self) -> None:
        text = _spec(_fr_section(
            "FR-1: The server must handle concurrent requests."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        concurrency = [f for f in findings if f.pattern_id == "CONCURRENCY_NO_MECHANISM"]
        self.assertEqual(len(concurrency), 1)

    def test_concurrent_with_event_loop_passes(self) -> None:
        text = _spec(_fr_section(
            "FR-1: The server handles concurrent requests via the Node.js "
            "event loop (single-threaded, non-blocking I/O)."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        concurrency = [f for f in findings if f.pattern_id == "CONCURRENCY_NO_MECHANISM"]
        self.assertEqual(len(concurrency), 0)

    def test_thread_safe_without_mechanism_warns(self) -> None:
        text = _spec(_fr_section(
            "FR-1: The cache must be thread-safe."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        concurrency = [f for f in findings if f.pattern_id == "CONCURRENCY_NO_MECHANISM"]
        self.assertEqual(len(concurrency), 1)


class TestFalsePositiveResilience(unittest.TestCase):
    """Cross-cutting tests for false-positive avoidance."""

    def test_catalog_does_not_trigger_concurrency(self) -> None:
        """Domain words should not trigger concurrency detection."""
        text = _spec(_fr_section(
            "FR-1: Display the product catalog with pricing."
        ))
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        self.assertEqual(len(findings), 0)

    def test_middleware_in_dependencies_section_ignored(self) -> None:
        """Dependencies section is not precision-bearing."""
        text = _spec(
            "## Dependencies & Out of Scope\n\n"
            "This spec depends on the middleware layer from F-1.\n"
        )
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        self.assertEqual(len(findings), 0)

    def test_multiple_patterns_in_one_spec(self) -> None:
        """Multiple precision gaps in the same spec each produce findings."""
        text = _spec(
            _fr_section(
                "FR-1: `createServer()` throws `ConfigError` on bad env.\n\n"
                "FR-2: Register CORS middleware.\n\n"
                "FR-3: The server must handle concurrent requests."
            )
        )
        model = parse_spec_model(text)
        findings = check_precision_gaps(model)
        pattern_ids = {f.pattern_id for f in findings}
        # Should detect at least factory, throws, plugin, and concurrency.
        self.assertIn("FACTORY_NO_RETURN_TYPE", pattern_ids)
        self.assertIn("THROWS_NO_SYNC_ASYNC", pattern_ids)
        self.assertIn("PLUGIN_NO_IMPORT_SEMANTICS", pattern_ids)
        self.assertIn("CONCURRENCY_NO_MECHANISM", pattern_ids)


if __name__ == "__main__":
    unittest.main()
