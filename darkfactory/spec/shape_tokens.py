"""Shared shape-token vocabulary for spec validation.

These tokens, when used in a shape-bearing spec section without an
accompanying literal example (fenced code block or Wire Contracts
reference), indicate that the spec mentions a serialized cross-boundary
structure but never pins its shape. Two LLM agents reading the spec will
each invent a different shape, and the resulting scenarios fight the
implementation forever.

The list is intentionally narrow. Loose words like ``field``, ``format``,
``header``, and ``schema`` are EXCLUDED on purpose — they appear constantly
in legitimate prose ("validate each field", "schema migration") and would
flood false positives. Only high-precision tokens trigger the precheck.
"""

from __future__ import annotations

# NOTE: bare "json" was intentionally dropped — it appears too often in
# legitimate prose ("parse the JSON config", "JSON Schema validation") and
# the more specific tokens below cover the actual divergence cases.
SHAPE_TOKENS: list[str] = [
    "json envelope",
    "envelope",
    "response body",
    "request body",
    "log line",
    "log entry",
    "log format",
    "stdout",
    "stderr",
    "status code mapping",
    "wire format",
]
