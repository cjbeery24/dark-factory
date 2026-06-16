"""Sprint A.0 (2026-06-16): --skip-validation reuse decision.

`_should_reuse_validation` is the host-side, fail-closed guard that decides
whether the slow LLM spec-validator agent may be skipped. Reuse requires BOTH
a host-stamped spec-hash match AND a still-PASSing .status artifact; any miss
falls through to a full validation. The hash is host-computed, never read from
the agent-authored .status file.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from darkfactory.pipeline.orchestrator import _should_reuse_validation
from darkfactory.state.store import write_state


def _write_status(reviews_dir: Path, spec: str, verdict: str = "PASS",
                  score: int = 100, divergence: str = "LOW",
                  blockers: int = 0, with_report: bool = True) -> None:
    """Stage a prior validation's artifacts. By default writes BOTH the
    structured ``.status`` and the ``.md`` report, since reuse now requires
    both. Pass ``with_report=False`` to stage a status-only state."""
    reviews_dir.mkdir(parents=True, exist_ok=True)
    (reviews_dir / f"{spec}-validation.status").write_text(
        f"verdict={verdict}\nscore={score}\ndivergence={divergence}\n"
        f"blockers={blockers}\nconsistency=PASS\n",
        encoding="utf-8",
    )
    if with_report:
        _write_report(reviews_dir, spec, verdict=verdict)


def _write_report(reviews_dir: Path, spec: str, verdict: str = "PASS") -> None:
    reviews_dir.mkdir(parents=True, exist_ok=True)
    (reviews_dir / f"{spec}-validation.md").write_text(
        f"# Validation Report\n\nverdict: {verdict}\n\n"
        "```yaml\nunresolved_contracts: []\n```\n",
        encoding="utf-8",
    )


def test_reuse_when_hash_matches_and_status_pass(tmp_path):
    spec = "demo"
    state_file = tmp_path / f"{spec}.state"
    reviews_dir = tmp_path / "reviews"
    write_state(state_file, "validated_spec_hash", "abc123")
    _write_status(reviews_dir, spec)
    assert _should_reuse_validation(
        True, state_file, reviews_dir, spec, "abc123"
    ) is True


def test_no_reuse_when_flag_off(tmp_path):
    spec = "demo"
    state_file = tmp_path / f"{spec}.state"
    reviews_dir = tmp_path / "reviews"
    write_state(state_file, "validated_spec_hash", "abc123")
    _write_status(reviews_dir, spec)
    # Same conditions, but skip_validation=False → never reuse.
    assert _should_reuse_validation(
        False, state_file, reviews_dir, spec, "abc123"
    ) is False


def test_no_reuse_when_hash_differs(tmp_path):
    """Spec bytes changed since validation → fail closed → full validation."""
    spec = "demo"
    state_file = tmp_path / f"{spec}.state"
    reviews_dir = tmp_path / "reviews"
    write_state(state_file, "validated_spec_hash", "abc123")
    _write_status(reviews_dir, spec)
    assert _should_reuse_validation(
        True, state_file, reviews_dir, spec, "DIFFERENT"
    ) is False


def test_no_reuse_when_no_stamped_hash(tmp_path):
    """No prior validated_spec_hash (e.g. never validated, or --fresh
    dropped artifacts) → fail closed."""
    spec = "demo"
    state_file = tmp_path / f"{spec}.state"
    reviews_dir = tmp_path / "reviews"
    _write_status(reviews_dir, spec)  # status present, but no stamped hash
    assert _should_reuse_validation(
        True, state_file, reviews_dir, spec, "abc123"
    ) is False


def test_no_reuse_when_status_missing(tmp_path):
    """Hash matches but NO artifacts at all → fail closed."""
    spec = "demo"
    state_file = tmp_path / f"{spec}.state"
    reviews_dir = tmp_path / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    write_state(state_file, "validated_spec_hash", "abc123")
    # No status or report file written.
    assert _should_reuse_validation(
        True, state_file, reviews_dir, spec, "abc123"
    ) is False


def test_no_reuse_when_only_markdown_report_no_status(tmp_path):
    """Codex catch: a matching hash + a PASS .md report but NO structured
    .status must NOT reuse. validate_spec_result falls back to grepping the
    markdown for 'verdict: pass' — reuse must rest on the structured .status,
    so the explicit .status presence check blocks the looser md-fallback."""
    spec = "demo"
    state_file = tmp_path / f"{spec}.state"
    reviews_dir = tmp_path / "reviews"
    write_state(state_file, "validated_spec_hash", "abc123")
    _write_report(reviews_dir, spec, verdict="PASS")  # .md only, no .status
    assert _should_reuse_validation(
        True, state_file, reviews_dir, spec, "abc123"
    ) is False


def test_no_reuse_when_status_present_but_no_report(tmp_path):
    """Codex catch: a PASS .status but NO .md report must NOT reuse — without
    the report, the deterministic post-validation gates in
    _step_spec_validation early-return (skipping free-variables + consistency).
    Requiring the report keeps those gates reachable on the reuse path."""
    spec = "demo"
    state_file = tmp_path / f"{spec}.state"
    reviews_dir = tmp_path / "reviews"
    write_state(state_file, "validated_spec_hash", "abc123")
    _write_status(reviews_dir, spec, with_report=False)  # .status only
    assert _should_reuse_validation(
        True, state_file, reviews_dir, spec, "abc123"
    ) is False


def test_no_reuse_when_status_not_pass(tmp_path):
    """Hash matches but the recorded verdict is not PASS → fail closed."""
    spec = "demo"
    state_file = tmp_path / f"{spec}.state"
    reviews_dir = tmp_path / "reviews"
    write_state(state_file, "validated_spec_hash", "abc123")
    _write_status(reviews_dir, spec, verdict="REVISE", score=60)
    assert _should_reuse_validation(
        True, state_file, reviews_dir, spec, "abc123"
    ) is False


def test_reverted_spec_with_stale_failing_status_no_reuse(tmp_path):
    """Spec reverted to a previously-validated hash, but the on-disk status
    reflects a later FAILED validation → status gate fails closed."""
    spec = "demo"
    state_file = tmp_path / f"{spec}.state"
    reviews_dir = tmp_path / "reviews"
    write_state(state_file, "validated_spec_hash", "H1")
    _write_status(reviews_dir, spec, verdict="REVISE", score=40)
    assert _should_reuse_validation(
        True, state_file, reviews_dir, spec, "H1"
    ) is False


_FR3_CONTRADICTION_LEDGER = """\
spec: demo
fr_ledgers:
  - fr_id: FR-3
    claims:
      - id: FR-3.c1
        subject: "baseline migration"
        predicate: creates
        polarity: negative
        object: "tables"
        anchor: "FR-3.processing@line62"
        raw: "creates no tables"
      - id: FR-3.c3
        subject: "baseline migration"
        predicate: creates
        polarity: positive
        object: "__drizzle_migrations table"
        anchor: "FR-3.side_effects@line70"
        raw: "the __drizzle_migrations table is created"
contradictions:
  - claim_a: FR-3.c1
    claim_b: FR-3.c3
    type: T1
    type_name: entity_creation_polarity_mismatch
    discriminator: "__drizzle_migrations table existence"
"""


def test_reuse_path_still_runs_deterministic_gates_so_stamp_unreachable(tmp_path):
    """The load-bearing stamp-ordering invariant (Linus review): the
    ``validated_spec_hash`` stamp is written at the call site ONLY AFTER
    ``_step_spec_validation`` returns. So a deterministic post-validation gate
    that fails must RAISE — making the stamp unreachable — EVEN on the reuse
    path (where the LLM agent is skipped). This test stages a PASS .status
    (so the cached-result check passes) alongside a consistency-ledger that
    encodes a real T1 contradiction (consistency=FAIL), invokes
    ``_step_spec_validation(reuse_validation=True)``, and asserts it raises
    WITHOUT ever calling the LLM agent. That proves skip-validation means
    'skip the slow LLM agent', NOT 'skip the gates' — and that a poisoned
    spec can never reach the stamp."""
    from darkfactory.docker.barriers import configure_barriers
    from darkfactory.errors import DarkFactoryError
    from darkfactory.models.config import PipelineConfig
    from darkfactory.models.pipeline import SpecRun
    from darkfactory.pipeline.orchestrator import _step_spec_validation

    spec = "demo"
    df = tmp_path / ".dark-factory"
    (df / "specs").mkdir(parents=True)
    (df / "specs" / f"{spec}.md").write_text("# Spec\n", encoding="utf-8")
    reviews = df / "reviews"
    reviews.mkdir(parents=True)
    # A minimal valid report so the unresolved_contracts parse is clean and we
    # reach the consistency gate.
    (reviews / f"{spec}-validation.md").write_text(
        "# Report\n\n```yaml\nunresolved_contracts: []\n```\n", encoding="utf-8",
    )
    # PASS verdict (blockers=0) but consistency=FAIL with a real ledger.
    (reviews / f"{spec}-validation.status").write_text(
        "verdict=PASS\nscore=100\ndivergence=LOW\nblockers=0\nconsistency=FAIL\n",
        encoding="utf-8",
    )
    (reviews / f"{spec}-consistency-ledger.yaml").write_text(
        _FR3_CONTRADICTION_LEDGER, encoding="utf-8",
    )

    spec_run = SpecRun(spec_name=spec)
    config = PipelineConfig(test_cmd="pytest")
    barriers = configure_barriers(config.source_dirs)

    invoke_mock = MagicMock()
    with patch("darkfactory.pipeline.orchestrator.invoke_agent", invoke_mock):
        with pytest.raises(DarkFactoryError) as exc:
            _step_spec_validation(
                spec_run, config, barriers, tmp_path, dry_run=False,
                active_adapters=[], reuse_validation=True,
            )
    # The consistency gate fired even though we reused.
    assert "consistency=FAIL" in str(exc.value)
    # And the LLM validator agent was NOT invoked on the reuse path.
    invoke_mock.assert_not_called()
