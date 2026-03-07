"""Spec sanitization — detect prompt injection patterns before pipeline entry."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SanitizeResult:
    clean: bool
    findings: list[Finding]

    @property
    def max_severity(self) -> Severity | None:
        if not self.findings:
            return None
        order = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}
        return max(self.findings, key=lambda f: order[f.severity]).severity


@dataclass
class Finding:
    pattern_name: str
    severity: Severity
    matched_text: str
    line_number: int


# Patterns: (name, severity, regex)
_PATTERNS: list[tuple[str, Severity, re.Pattern[str]]] = [
    (
        "system_prompt_override",
        Severity.HIGH,
        re.compile(
            r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above)\s+"
            r"(?:instructions|prompts|rules)",
            re.IGNORECASE,
        ),
    ),
    (
        "role_injection",
        Severity.HIGH,
        re.compile(
            r"you\s+are\s+now\s+(?:a|an|the)\s+\w+\s+(?:who|that|which)\s+(?:ignores|bypasses|disregards|overrides)|"
            r"act\s+as\s+(?:a|an|the)\s+\w+\s+(?:who|that|which)\s+(?:ignores|bypasses|disregards|overrides)",
            re.IGNORECASE,
        ),
    ),
    (
        "tool_calling_injection",
        Severity.HIGH,
        re.compile(
            r"</?(?:tool_use|function_call|tool_result|antml:invoke|invoke)\b",
            re.IGNORECASE,
        ),
    ),
    # Limitation: matches "do not read .dark-factory/scenarios" (negation context).
    # Regex cannot reason about intent; false positives are acceptable at HIGH severity.
    (
        "barrier_circumvention",
        Severity.HIGH,
        re.compile(
            r"(?:read|cat|access|open|view)\s+.*\.dark-factory/scenarios|"
            r"(?:read|cat|access|open|view)\s+.*\.dark-factory/reports|"
            r"git\s+(?:log|show|diff)\s+.*scenarios",
            re.IGNORECASE,
        ),
    ),
    (
        "encoded_payload",
        Severity.MEDIUM,
        re.compile(
            r"(?:base64|atob|btoa)\s*\(|"
            r"\\x[0-9a-fA-F]{2}(?:\\x[0-9a-fA-F]{2}){3,}|"
            r"\\u[0-9a-fA-F]{4}(?:\\u[0-9a-fA-F]{4}){3,}",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_delimiter",
        Severity.MEDIUM,
        re.compile(
            r"={5,}\s*(?:SYSTEM|HUMAN|ASSISTANT|USER)\s*={5,}|"
            r"<\|(?:im_start|im_end|endoftext|system|user|assistant)\|>",
            re.IGNORECASE,
        ),
    ),
    (
        "dangerously_skip_permissions",
        Severity.HIGH,
        re.compile(
            r"--dangerously-skip-permissions",
            re.IGNORECASE,
        ),
    ),
]


def sanitize_spec(text: str) -> SanitizeResult:
    """Scan spec text for prompt injection patterns.

    Returns SanitizeResult with clean=True if no HIGH severity findings.
    """
    findings: list[Finding] = []
    lines = text.split("\n")

    for line_num, line in enumerate(lines, start=1):
        for name, severity, pattern in _PATTERNS:
            for match in pattern.finditer(line):
                findings.append(Finding(
                    pattern_name=name,
                    severity=severity,
                    matched_text=match.group()[:100],
                    line_number=line_num,
                ))

    has_high = any(f.severity == Severity.HIGH for f in findings)
    return SanitizeResult(clean=not has_high, findings=findings)
