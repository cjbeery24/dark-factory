"""Unit tests for C2 — proposal-test typecheck rehearsal.

Covers the deterministic, host-side parts of the pipeline: prompt
shape, sentinel parsing, banned-pattern scan, the abort-rule decision
table, and the skip conditions. Does NOT spin up a container and does
NOT shell out to tsc (that belongs to integration tests).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from darkfactory.scenarios.proposal_rehearsal import (
    BannedPatternHit,
    Proposal,
    ProposalRehearsalResult,
    TYPECHECK_FAIL_THRESHOLD,
    build_proposal_prompt,
    decide_rehearsal_outcome,
    has_ac_banned_hit,
    parse_proposals,
    run_proposal_rehearsal,
    scan_banned_patterns,
)
from darkfactory.spec.anchors import AnchorManifest, manifest_path


# ---------------------------------------------------------------------------
# Prompt shape
# ---------------------------------------------------------------------------


class TestBuildProposalPrompt:
    def test_includes_anchors_in_declared_order(self):
        anchors = AnchorManifest(
            fr_ids=["FR-2"],
            ac_ids=["AC-1"],
            ec_ids=["EC-3"],
        )
        prompt = build_proposal_prompt("spec-1", anchors)
        # AC first, then FR, then EC (the iteration order inside the
        # dedupe loop).
        ac_idx = prompt.index("AC-1")
        fr_idx = prompt.index("FR-2")
        ec_idx = prompt.index("EC-3")
        assert ac_idx < fr_idx < ec_idx

    def test_dedupes_anchors_across_buckets(self):
        anchors = AnchorManifest(
            fr_ids=["AC-1", "FR-2"],  # AC-1 appearing here too shouldn't dupe
            ac_ids=["AC-1"],
            ec_ids=[],
        )
        prompt = build_proposal_prompt("spec-1", anchors)
        assert prompt.count("AC-1") == 1

    def test_embeds_banned_pattern_hints(self):
        anchors = AnchorManifest(ac_ids=["AC-1"])
        prompt = build_proposal_prompt("spec-1", anchors)
        # A few of the banned-category labels should appear verbatim.
        assert "subprocess-exit" in prompt
        assert "subprocess-child-process" in prompt
        assert "subprocess-execSync" in prompt

    def test_references_sentinel_block(self):
        prompt = build_proposal_prompt("spec-1", AnchorManifest(ac_ids=["AC-1"]))
        assert "BEGIN_PROPOSALS" in prompt
        assert "END_PROPOSALS" in prompt

    def test_empty_manifest_renders_none_marker(self):
        prompt = build_proposal_prompt("spec-1", AnchorManifest())
        assert "(none)" in prompt


# ---------------------------------------------------------------------------
# parse_proposals
# ---------------------------------------------------------------------------


def _wrap_sentinels(body: str) -> str:
    return f"some preamble\nBEGIN_PROPOSALS\n{body}\nEND_PROPOSALS\nsome trailing\n"


class TestParseProposals:
    def test_parses_well_formed_json(self):
        body = json.dumps({
            "proposals": [
                {"anchor": "AC-1", "filepath": "AC-1.test.ts", "content": "x"},
                {"anchor": "FR-1", "filepath": "FR-1.test.ts", "content": "y"},
            ]
        })
        out = parse_proposals(_wrap_sentinels(body))
        assert len(out) == 2
        assert out[0].anchor == "AC-1"
        assert out[1].content == "y"

    def test_missing_begin_sentinel_returns_empty(self):
        body = '{"proposals": [{"anchor": "A", "filepath": "A.test.ts", "content": "z"}]}'
        out = parse_proposals("no sentinels here at all")
        assert out == []

    def test_missing_end_sentinel_returns_empty(self):
        out = parse_proposals("BEGIN_PROPOSALS\n{}\n(no end)")
        assert out == []

    def test_malformed_json_returns_empty(self):
        out = parse_proposals(_wrap_sentinels("{not valid json"))
        assert out == []

    def test_top_level_list_rejected(self):
        # Per the contract, agent must emit a dict with a "proposals" key.
        out = parse_proposals(_wrap_sentinels('[{"anchor":"A"}]'))
        assert out == []

    def test_missing_proposals_key_returns_empty(self):
        out = parse_proposals(_wrap_sentinels('{"notes": []}'))
        assert out == []

    def test_skips_entries_with_missing_fields(self):
        body = json.dumps({
            "proposals": [
                {"anchor": "AC-1", "filepath": "AC-1.test.ts", "content": "x"},
                {"anchor": "AC-2"},  # missing filepath/content — dropped
                {"filepath": "AC-3.test.ts", "content": "z"},  # missing anchor
            ]
        })
        out = parse_proposals(_wrap_sentinels(body))
        assert [p.anchor for p in out] == ["AC-1"]

    def test_strips_code_fence_wrapper(self):
        # Model wraps the JSON in a ```json fence even when told not to.
        body = "```json\n" + json.dumps({
            "proposals": [
                {"anchor": "AC-1", "filepath": "AC-1.test.ts", "content": "x"},
            ]
        }) + "\n```"
        out = parse_proposals(_wrap_sentinels(body))
        assert len(out) == 1
        assert out[0].anchor == "AC-1"

    def test_uses_last_sentinel_pair_when_multiple(self):
        # Agent emits a draft block, then a final block. Only final counts.
        draft = json.dumps({"proposals": [
            {"anchor": "AC-0", "filepath": "AC-0.test.ts", "content": "draft"}
        ]})
        final = json.dumps({"proposals": [
            {"anchor": "AC-1", "filepath": "AC-1.test.ts", "content": "final"}
        ]})
        stdout = (
            f"draft:\nBEGIN_PROPOSALS\n{draft}\nEND_PROPOSALS\n"
            f"final:\nBEGIN_PROPOSALS\n{final}\nEND_PROPOSALS\n"
        )
        out = parse_proposals(stdout)
        assert len(out) == 1
        assert out[0].anchor == "AC-1"

    def test_trims_whitespace_in_anchor_and_filepath(self):
        body = json.dumps({
            "proposals": [
                {"anchor": "  AC-1  ", "filepath": "  AC-1.test.ts  ", "content": "x"}
            ]
        })
        out = parse_proposals(_wrap_sentinels(body))
        assert out[0].anchor == "AC-1"
        assert out[0].filepath == "AC-1.test.ts"


# ---------------------------------------------------------------------------
# Banned-pattern scan
# ---------------------------------------------------------------------------


class TestScanBannedPatterns:
    def test_detects_process_exit(self):
        p = Proposal(anchor="AC-1", filepath="AC-1.test.ts",
                     content="expect(() => process.exit(1)).toThrow();")
        hits = scan_banned_patterns([p])
        categories = [h.category for h in hits]
        assert "subprocess-exit" in categories

    def test_detects_child_process(self):
        p = Proposal(anchor="AC-2", filepath="AC-2.test.ts",
                     content='import cp from "child_process";')
        hits = scan_banned_patterns([p])
        assert any(h.category == "subprocess-child-process" for h in hits)

    def test_detects_exec_sync(self):
        p = Proposal(anchor="AC-3", filepath="AC-3.test.ts",
                     content='execSync("node x");')
        hits = scan_banned_patterns([p])
        assert any(h.category == "subprocess-execSync" for h in hits)

    def test_detects_spawn(self):
        for call in ("spawn(", "spawnSync("):
            p = Proposal(anchor="AC-4", filepath="AC-4.test.ts",
                         content=f'{call}"node", ["x"]);')
            hits = scan_banned_patterns([p])
            assert any(h.category == "subprocess-spawn" for h in hits), (
                f"pattern {call!r} missed"
            )

    def test_library_internal_underscore_not_banned(self):
        """Linus-review #1: the `._.` regex false-positived on idiomatic
        TS (lodash, libraries with legitimate single-underscore properties).
        Dropped from _BANNED_PATTERNS; A6 + tsc strictness cover the real
        motivating case. This test pins the removal."""
        p = Proposal(anchor="AC-5", filepath="AC-5.test.ts",
                     content="expect(schema._.config).toEqual({});")
        hits = scan_banned_patterns([p])
        assert not any(h.category == "library-internal-underscore" for h in hits)

    def test_clean_proposal_no_hits(self):
        p = Proposal(anchor="AC-6", filepath="AC-6.test.ts",
                     content='expect(add(2, 3)).toBe(5);')
        hits = scan_banned_patterns([p])
        assert hits == []

    def test_multi_proposal_hits_separate(self):
        proposals = [
            Proposal("AC-1", "AC-1.test.ts", "process.exit(1);"),
            Proposal("FR-1", "FR-1.test.ts", "execSync('x');"),
            Proposal("EC-1", "EC-1.test.ts", "const x = 1;"),
        ]
        hits = scan_banned_patterns(proposals)
        anchors_hit = {h.anchor for h in hits}
        assert anchors_hit == {"AC-1", "FR-1"}


class TestHasAcBannedHit:
    def test_ac_prefix_triggers(self):
        assert has_ac_banned_hit([
            BannedPatternHit(anchor="AC-1", category="x", description="y"),
        ])

    def test_lowercase_ac_triggers_too(self):
        # Spec authors sometimes use "ac-1" — the check uppercases before
        # matching so cosmetic variants don't slip past.
        assert has_ac_banned_hit([
            BannedPatternHit(anchor="ac-1", category="x", description="y"),
        ])

    def test_fr_and_ec_do_not_trigger(self):
        assert not has_ac_banned_hit([
            BannedPatternHit(anchor="FR-1", category="x", description="y"),
            BannedPatternHit(anchor="EC-3", category="x", description="y"),
        ])

    def test_empty_returns_false(self):
        assert not has_ac_banned_hit([])


# ---------------------------------------------------------------------------
# Abort decision table
# ---------------------------------------------------------------------------


def _mk(*, anchor: str, content: str = "x") -> Proposal:
    return Proposal(anchor=anchor, filepath=f"{anchor}.test.ts", content=content)


class TestDecideRehearsalOutcome:
    def test_no_proposals_aborts(self):
        anchors = AnchorManifest(ac_ids=["AC-1"])
        out = decide_rehearsal_outcome([], [], set(), [], anchors)
        assert out.passed is False
        assert "no parseable proposals" in out.reason
        assert out.total_proposals == 0

    def test_ac_banned_hit_aborts_regardless_of_typecheck(self):
        anchors = AnchorManifest(ac_ids=["AC-1"], fr_ids=["FR-1"])
        proposals = [_mk(anchor="AC-1"), _mk(anchor="FR-1")]
        banned = [BannedPatternHit(
            anchor="AC-1", category="subprocess-exit",
            description="asserting on process.exit...",
        )]
        out = decide_rehearsal_outcome(
            proposals, findings=[], failing_anchors=set(),
            banned_hits=banned, anchors=anchors,
        )
        assert out.passed is False
        assert "AC-1" in out.reason
        assert "subprocess-exit" in out.reason

    def test_typecheck_threshold_aborts(self):
        # 2/4 = 50% fail → over the 30% threshold → abort.
        anchors = AnchorManifest(ac_ids=["AC-1", "AC-2", "AC-3", "AC-4"])
        proposals = [
            _mk(anchor="AC-1"), _mk(anchor="AC-2"),
            _mk(anchor="AC-3"), _mk(anchor="AC-4"),
        ]
        failing = {"AC-1", "AC-2"}
        out = decide_rehearsal_outcome(
            proposals, findings=[], failing_anchors=failing,
            banned_hits=[], anchors=anchors,
        )
        assert out.passed is False
        assert "fail tsc" in out.reason
        assert out.typecheck_failure_count == 2

    def test_typecheck_under_threshold_passes(self):
        # 1/4 = 25% < 30% → pass.
        anchors = AnchorManifest(ac_ids=["AC-1", "AC-2", "AC-3", "AC-4"])
        proposals = [
            _mk(anchor="AC-1"), _mk(anchor="AC-2"),
            _mk(anchor="AC-3"), _mk(anchor="AC-4"),
        ]
        out = decide_rehearsal_outcome(
            proposals, findings=[], failing_anchors={"AC-4"},
            banned_hits=[], anchors=anchors,
        )
        assert out.passed is True
        assert out.typecheck_failure_count == 1

    def test_threshold_is_inclusive(self):
        # 30% exactly → aborts (the constant documents ≥).
        assert TYPECHECK_FAIL_THRESHOLD == 0.30
        # 3/10 = 30% → at threshold → abort.
        proposals = [_mk(anchor=f"AC-{i}") for i in range(10)]
        failing = {"AC-0", "AC-1", "AC-2"}
        anchors = AnchorManifest(ac_ids=[p.anchor for p in proposals])
        out = decide_rehearsal_outcome(
            proposals, findings=[], failing_anchors=failing,
            banned_hits=[], anchors=anchors,
        )
        assert out.passed is False

    def test_non_ac_banned_hit_does_not_abort(self):
        # FR-level banned hit: informational only, surfaced but not blocking.
        anchors = AnchorManifest(fr_ids=["FR-1"])
        proposals = [_mk(anchor="FR-1", content="process.exit(1);")]
        banned = [BannedPatternHit(
            anchor="FR-1", category="subprocess-exit", description="x",
        )]
        out = decide_rehearsal_outcome(
            proposals, findings=[], failing_anchors=set(),
            banned_hits=banned, anchors=anchors,
        )
        assert out.passed is True
        assert out.banned_hits == banned

    def test_ac_banned_takes_priority_over_typecheck(self):
        # Even with typecheck under threshold, AC-banned still aborts.
        anchors = AnchorManifest(ac_ids=["AC-1", "AC-2"])
        proposals = [_mk(anchor="AC-1"), _mk(anchor="AC-2")]
        banned = [BannedPatternHit(
            anchor="AC-1", category="subprocess-exit", description="x",
        )]
        out = decide_rehearsal_outcome(
            proposals, findings=[], failing_anchors=set(),
            banned_hits=banned, anchors=anchors,
        )
        assert out.passed is False
        assert "AC-1" in out.reason


# ---------------------------------------------------------------------------
# run_proposal_rehearsal — skip conditions
# ---------------------------------------------------------------------------


def _write_manifest(project_dir: Path, spec_name: str, anchors: AnchorManifest) -> None:
    anchors.to_json_path(manifest_path(project_dir, spec_name))


class TestRunProposalRehearsalSkip:
    def test_missing_manifest_skips(self, tmp_path: Path):
        out = run_proposal_rehearsal(
            spec_name="spec-x", project_dir=tmp_path, agent_output="",
        )
        assert out.skipped is True
        assert "manifest missing" in out.reason

    def test_empty_manifest_skips(self, tmp_path: Path):
        _write_manifest(tmp_path, "spec-x", AnchorManifest())
        out = run_proposal_rehearsal(
            spec_name="spec-x", project_dir=tmp_path, agent_output="",
        )
        assert out.skipped is True
        assert "anchor manifest is empty" in out.reason

    def test_no_tsconfig_skips(self, tmp_path: Path):
        _write_manifest(tmp_path, "spec-x", AnchorManifest(ac_ids=["AC-1"]))
        # No tsconfig.json, no node_modules — a non-TS project.
        out = run_proposal_rehearsal(
            spec_name="spec-x", project_dir=tmp_path, agent_output="",
        )
        assert out.skipped is True
        assert "tsconfig" in out.reason

    def test_tsconfig_present_but_no_node_modules_is_hard_error(self, tmp_path: Path):
        """Misconfigured TS environment — must fail loudly (not skip) so
        the user runs `npm ci` rather than burning a full pipeline
        iteration on a broken setup. Linus-review item #3."""
        _write_manifest(tmp_path, "spec-x", AnchorManifest(ac_ids=["AC-1"]))
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{}}')
        # node_modules intentionally absent.
        out = run_proposal_rehearsal(
            spec_name="spec-x", project_dir=tmp_path, agent_output="",
        )
        assert out.skipped is False
        assert out.passed is False
        assert "node_modules" in out.reason
        assert "npm ci" in out.reason


# ---------------------------------------------------------------------------
# run_proposal_rehearsal — full decide path (tsc mocked out)
# ---------------------------------------------------------------------------


class TestRunProposalRehearsalDecide:
    def test_ac_banned_path_aborts_without_tsc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """End-to-end: agent emits process.exit proposal on an AC; module
        aborts before tsc is needed. Proves the banned-pattern scan runs
        on the parsed proposals even when the tsc environment is set up."""
        _write_manifest(tmp_path, "spec-x", AnchorManifest(ac_ids=["AC-1"]))
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{}}')
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / ".bin").mkdir()
        (tmp_path / "node_modules" / ".bin" / "tsc").write_text("#!/bin/sh\n")
        (tmp_path / "node_modules" / ".bin" / "tsc").chmod(0o755)

        # Short-circuit typecheck so we don't actually shell out to tsc —
        # the AC-banned abort rule should fire before the result matters,
        # but we patch for deterministic behaviour regardless.
        monkeypatch.setattr(
            "darkfactory.scenarios.proposal_rehearsal.typecheck_proposals",
            lambda *a, **k: ([], set()),
        )

        body = json.dumps({
            "proposals": [{
                "anchor": "AC-1",
                "filepath": "AC-1.test.ts",
                "content": "it('exits', () => { process.exit(1); });",
            }]
        })
        stdout = f"BEGIN_PROPOSALS\n{body}\nEND_PROPOSALS"

        out = run_proposal_rehearsal(
            spec_name="spec-x", project_dir=tmp_path, agent_output=stdout,
        )
        assert out.skipped is False
        assert out.passed is False
        assert "AC-1" in out.reason

    def test_clean_proposals_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        _write_manifest(tmp_path, "spec-x", AnchorManifest(ac_ids=["AC-1"]))
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{}}')
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / ".bin").mkdir()
        (tmp_path / "node_modules" / ".bin" / "tsc").write_text("#!/bin/sh\n")
        (tmp_path / "node_modules" / ".bin" / "tsc").chmod(0o755)
        monkeypatch.setattr(
            "darkfactory.scenarios.proposal_rehearsal.typecheck_proposals",
            lambda *a, **k: ([], set()),
        )

        body = json.dumps({
            "proposals": [{
                "anchor": "AC-1",
                "filepath": "AC-1.test.ts",
                "content": "it('adds', () => { expect(1+1).toBe(2); });",
            }]
        })
        stdout = f"BEGIN_PROPOSALS\n{body}\nEND_PROPOSALS"

        out = run_proposal_rehearsal(
            spec_name="spec-x", project_dir=tmp_path, agent_output=stdout,
        )
        assert out.skipped is False
        assert out.passed is True
        assert out.total_proposals == 1
