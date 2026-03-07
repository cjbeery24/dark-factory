"""Digital twin data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TwinSpec:
    """Configuration for one digital twin service."""
    name: str
    upstream: str  # Real service URL to proxy/record
    port: int = 0  # Local port for the replay server (0 = auto-assign)
    record_file: str = ""  # JSONL file for recorded interactions


@dataclass
class TwinConfig:
    """Top-level twin configuration."""
    twins: list[TwinSpec] = field(default_factory=list)
    record_dir: str = ".dark-factory/twins"


@dataclass
class RecordedInteraction:
    """A single recorded HTTP request/response pair."""
    method: str
    path: str
    body_hash: str
    status_code: int
    response_body: str
    request_headers: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
