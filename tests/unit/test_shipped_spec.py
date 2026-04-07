"""Phase 2.5 — ShippedSpec dataclass + shipped.jsonl append-only log."""

from datetime import datetime, timezone

import pytest

from darkfactory.plan.state import (
    ShippedSpec,
    append_shipped,
    iter_shipped,
    load_shipped,
    shipped_log_path,
)


def _row(**overrides) -> ShippedSpec:
    base = dict(
        spec_id="2026-04-06-foo",
        spec_type="feature",
        spec_origin="user",
        shipped_at=datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc),
        env_hash_at_ship="abc123",
        delta_filename=None,
        scenario_archive_path=".dark-factory/scenarios/archive/foo.toml",
    )
    base.update(overrides)
    return ShippedSpec(**base)


def test_validates_spec_type():
    with pytest.raises(ValueError, match="spec_type"):
        _row(spec_type="rando")


def test_requires_timezone_aware_shipped_at():
    with pytest.raises(ValueError, match="timezone-aware"):
        _row(shipped_at=datetime(2026, 4, 6, 12, 0, 0))


def test_rejects_empty_required_fields():
    with pytest.raises(ValueError):
        _row(spec_id="")
    with pytest.raises(ValueError):
        _row(env_hash_at_ship="")
    with pytest.raises(ValueError):
        _row(scenario_archive_path="")


def test_round_trip_jsonl():
    row = _row(delta_filename="0001-foo.toml")
    import json
    back = ShippedSpec.from_dict(json.loads(row.to_jsonl()))
    assert back == row


def test_to_jsonl_uses_z_suffix():
    assert '"shipped_at": "2026-04-06T12:00:00Z"' in _row().to_jsonl()


def test_append_then_iter(tmp_path):
    a = _row(spec_id="2026-04-06-a")
    b = _row(spec_id="2026-04-06-b", delta_filename="0002-b.toml")
    append_shipped(tmp_path, a)
    append_shipped(tmp_path, b)
    assert load_shipped(tmp_path) == [a, b]


def test_iter_missing_log_yields_nothing(tmp_path):
    assert list(iter_shipped(tmp_path)) == []


def test_blank_lines_skipped(tmp_path):
    append_shipped(tmp_path, _row())
    path = shipped_log_path(tmp_path)
    path.write_text(path.read_text() + "\n\n")
    assert len(load_shipped(tmp_path)) == 1


def test_malformed_line_raises_with_line_number(tmp_path):
    append_shipped(tmp_path, _row())
    with shipped_log_path(tmp_path).open("a") as f:
        f.write("{not json\n")
    with pytest.raises(ValueError, match="line 2"):
        load_shipped(tmp_path)


def test_loose_mode_tolerates_torn_final_line(tmp_path):
    a = _row(spec_id="2026-04-06-a")
    append_shipped(tmp_path, a)
    with shipped_log_path(tmp_path).open("a") as f:
        f.write("{partial")  # torn final write, no newline
    # Strict raises, loose recovers everything before the tear.
    with pytest.raises(ValueError):
        load_shipped(tmp_path)
    assert load_shipped(tmp_path, strict=False) == [a]


def test_loose_mode_still_raises_on_mid_file_corruption(tmp_path):
    append_shipped(tmp_path, _row(spec_id="a"))
    with shipped_log_path(tmp_path).open("a") as f:
        f.write("{garbage\n")
    append_shipped(tmp_path, _row(spec_id="b"))
    with pytest.raises(ValueError, match="line 2"):
        load_shipped(tmp_path, strict=False)


def test_log_path_under_dark_factory(tmp_path):
    assert shipped_log_path(tmp_path) == tmp_path / ".dark-factory" / "plan" / "shipped.jsonl"
