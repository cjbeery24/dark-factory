"""Security anti-patterns organized by language (FR-3).

Patterns are static and compiled at import time. Each pattern is a tuple of
(name, severity, compiled_regex). Severity is "HIGH", "MEDIUM", or "LOW".
"""

from __future__ import annotations

import re

# Type alias for a single pattern entry.
PatternEntry = tuple[str, str, re.Pattern[str]]

# --- Python patterns ---

_PYTHON_PATTERNS: list[PatternEntry] = [
    # SQL injection: f-string or concatenation in execute()
    (
        "sql_injection",
        "HIGH",
        re.compile(
            r"(?:cursor|db|conn|session)\.execute\(\s*f[\"']"
            r"|\.execute\([^)]*\+\s*",
        ),
    ),
    # Command injection: os.system or shell=True with non-literal
    (
        "command_injection",
        "HIGH",
        re.compile(
            r"os\.system\("
            r"|subprocess\.(?:call|run)\([^)]*shell\s*=\s*True",
        ),
    ),
    # Path traversal: open/Path with user input concatenation
    (
        "path_traversal",
        "HIGH",
        re.compile(
            r"\.open\([^)]*(?:request|user|input)"
            r"|Path\([^)]*(?:request|user|input)",
        ),
    ),
    # Hardcoded secrets
    (
        "hardcoded_secret",
        "MEDIUM",
        re.compile(
            r"(?:password|secret|api_key|token|private_key)\s*=\s*[\"'][^\"']{8,}[\"']",
            re.IGNORECASE,
        ),
    ),
    # Insecure deserialization
    (
        "insecure_deserialization",
        "HIGH",
        re.compile(
            r"pickle\.loads?\("
            r"|yaml\.load\((?![^)]*SafeLoader)"
            r"|(?<!\w)eval\("
            r"|(?<!\w)exec\(",
        ),
    ),
]

# --- Node/TypeScript patterns ---

_NODE_PATTERNS: list[PatternEntry] = [
    # SQL injection: template literal in query
    (
        "sql_injection",
        "HIGH",
        re.compile(
            r"query\(\s*`[^`]*\$\{"
            r"|query\([^)]*\+\s*",
        ),
    ),
    # XSS
    (
        "xss",
        "HIGH",
        re.compile(
            r"innerHTML\s*="
            r"|dangerouslySetInnerHTML"
            r"|document\.write\(",
        ),
    ),
    # Command injection
    (
        "command_injection",
        "HIGH",
        re.compile(
            r"child_process\.exec\(\s*`"
            r"|child_process\.exec\([^)]*\+\s*",
        ),
    ),
    # Hardcoded secrets (same as Python)
    (
        "hardcoded_secret",
        "MEDIUM",
        re.compile(
            r"(?:password|secret|api_key|token|private_key)\s*=\s*[\"'][^\"']{8,}[\"']",
            re.IGNORECASE,
        ),
    ),
    # Prototype pollution
    (
        "prototype_pollution",
        "HIGH",
        re.compile(
            r"Object\.assign\(\s*(?!{)[^,]+,",
        ),
    ),
]

# --- Go patterns ---

_GO_PATTERNS: list[PatternEntry] = [
    # SQL injection
    (
        "sql_injection",
        "HIGH",
        re.compile(
            r"db\.(?:Query|Exec)\(\s*fmt\.Sprintf\("
            r"|db\.(?:Query|Exec)\([^)]*\+\s*",
        ),
    ),
    # Command injection
    (
        "command_injection",
        "HIGH",
        re.compile(
            r"exec\.Command\(",
        ),
    ),
    # Hardcoded secrets
    (
        "hardcoded_secret",
        "MEDIUM",
        re.compile(
            r"(?:password|secret|api_key|token|private_key)\s*=\s*[\"'][^\"']{8,}[\"']",
            re.IGNORECASE,
        ),
    ),
]

# --- Rust patterns ---

_RUST_PATTERNS: list[PatternEntry] = [
    # SQL injection
    (
        "sql_injection",
        "HIGH",
        re.compile(
            r"query\(\s*format!\(",
        ),
    ),
    # Hardcoded secrets
    (
        "hardcoded_secret",
        "MEDIUM",
        re.compile(
            r"(?:password|secret|api_key|token|private_key)\s*=\s*[\"'][^\"']{8,}[\"']",
            re.IGNORECASE,
        ),
    ),
]

# Language → patterns mapping.  "node" covers both Node.js and TypeScript.
SECURITY_PATTERNS: dict[str, list[PatternEntry]] = {
    "python": _PYTHON_PATTERNS,
    "node": _NODE_PATTERNS,
    "go": _GO_PATTERNS,
    "rust": _RUST_PATTERNS,
}
