"""Phase 2.5 — plan-state schema.

Tracks specs that have shipped, persisted as an append-only JSONL log at
``.dark-factory/plan/shipped.jsonl``. Each ship event records the env
contract hash at ship time, the delta filename (if any), and a pointer to
the archived scenario set, so Phase 5's regression suite and Phase 6's
plan runner have a stable record of "what was decided when".

Append-only and line-oriented on purpose: every entry is a single line of
JSON, recovery is trivial (read line by line, drop malformed tail), and
two writers can't corrupt earlier rows.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

SpecType = Literal["bootstrap", "feature", "bugfix", "refactor"]

# Must stay in sync with ALLOWED_SPEC_TYPES in darkfactory/config/profiles.py.
# Every spec_type the pipeline accepts must be representable in shipped.jsonl,
# otherwise Phase 5.1 post-ship archival silently drops refactor specs.
_VALID_SPEC_TYPES: frozenset[str] = frozenset(
    ("bootstrap", "feature", "bugfix", "refactor")
)

_SHIPPED_RELPATH = Path(".dark-factory") / "plan" / "shipped.jsonl"


@dataclass(frozen=True)
class ShippedSpec:
    """One row in the shipped-specs log.

    ``env_hash_at_ship`` is the ``hash_view`` of the materialized
    ``EnvironmentContract`` at the moment the Oracle said SHIP — it lets
    Phase 5 detect environment drift between ship time and a later
    regression run, and lets Phase 6 reason about which contract a spec
    was validated against.

    ``delta_filename`` is None for specs that didn't amend the env
    contract (most feature specs). ``scenario_archive_path`` is repo-
    relative.
    """

    spec_id: str
    spec_type: SpecType
    spec_origin: str
    shipped_at: datetime
    env_hash_at_ship: str
    delta_filename: str | None
    scenario_archive_path: str

    def __post_init__(self) -> None:
        if not self.spec_id:
            raise ValueError("ShippedSpec.spec_id must be non-empty")
        if self.spec_type not in _VALID_SPEC_TYPES:
            raise ValueError(
                f"ShippedSpec.spec_type must be one of {sorted(_VALID_SPEC_TYPES)}, "
                f"got {self.spec_type!r}"
            )
        if not self.spec_origin:
            raise ValueError("ShippedSpec.spec_origin must be non-empty")
        if self.shipped_at.tzinfo is None:
            raise ValueError("ShippedSpec.shipped_at must be timezone-aware")
        if not self.env_hash_at_ship:
            raise ValueError("ShippedSpec.env_hash_at_ship must be non-empty")
        if not self.scenario_archive_path:
            raise ValueError("ShippedSpec.scenario_archive_path must be non-empty")

    def to_jsonl(self) -> str:
        d = asdict(self)
        # datetime → ISO-8601 with explicit Z for UTC for human readability.
        ts = self.shipped_at.astimezone(timezone.utc)
        d["shipped_at"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        return json.dumps(d, sort_keys=True, ensure_ascii=True)

    @classmethod
    def from_dict(cls, raw: dict) -> "ShippedSpec":
        ts_raw = raw.get("shipped_at")
        if not isinstance(ts_raw, str):
            raise ValueError("shipped_at must be an ISO-8601 string")
        # Accept trailing Z by replacing with +00:00 for fromisoformat.
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        return cls(
            spec_id=raw["spec_id"],
            spec_type=raw["spec_type"],
            spec_origin=raw["spec_origin"],
            shipped_at=ts,
            env_hash_at_ship=raw["env_hash_at_ship"],
            delta_filename=raw.get("delta_filename"),
            scenario_archive_path=raw["scenario_archive_path"],
        )


def shipped_log_path(project_dir: Path) -> Path:
    return project_dir / _SHIPPED_RELPATH


def append_shipped(project_dir: Path, row: ShippedSpec) -> None:
    """Append one row to ``.dark-factory/plan/shipped.jsonl``.

    Durability: fsyncs the file (and the parent directory on first
    create) before returning. The whole point of this log is "what was
    decided when" — losing the tail to a crash would let Phase 6 re-run
    a spec that already shipped.

    Concurrency: writes the entire ``line + "\\n"`` in a single
    ``write()`` so POSIX ``O_APPEND`` semantics make the append atomic
    relative to other appenders. The pipeline is single-writer in
    practice; this is defense in depth.
    """
    path = shipped_log_path(project_dir)
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = row.to_jsonl() + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    if not parent_existed:
        # Persist the dirent for the new file/directory.
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def iter_shipped(project_dir: Path) -> Iterator[ShippedSpec]:
    """Yield each ShippedSpec row in order. Skips blank lines.

    Strict: any malformed line raises ``ValueError`` with the line
    number. For tail-tolerant recovery (torn final write after a crash),
    use ``load_shipped(strict=False)``.
    """
    path = shipped_log_path(project_dir)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"shipped.jsonl line {lineno}: invalid JSON: {exc}"
                ) from exc
            try:
                yield ShippedSpec.from_dict(raw)
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"shipped.jsonl line {lineno}: {exc}"
                ) from exc


def load_shipped(project_dir: Path, *, strict: bool = True) -> list[ShippedSpec]:
    """Read every shipped row.

    With ``strict=False``, a malformed *final* line is dropped (recovery
    from a torn write at EOF). Mid-file corruption still raises — that's
    a real bug, not crash recovery.
    """
    path = shipped_log_path(project_dir)
    if not path.exists():
        return []
    if strict:
        return list(iter_shipped(project_dir))

    rows: list[ShippedSpec] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    # Find the last non-blank line index — that's the only one we tolerate
    # being torn.
    last_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip():
            last_idx = i
            break
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        try:
            rows.append(ShippedSpec.from_dict(json.loads(s)))
        except (json.JSONDecodeError, KeyError, ValueError):
            if i == last_idx:
                break  # tolerate torn final line
            raise ValueError(f"shipped.jsonl line {i + 1}: corrupted")
    return rows
