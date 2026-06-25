"""Tests for the P2 Validator-Bridge sanitized holdout feedback (NARROW first cut).

The bridge is a STRICT SUPERSET of today's binary feedback: with no manifest, no
failing ids, or no resolvable anchor, it returns EXACTLY the binary fallback. When it
DOES relay, it emits one templated, spec-anchor-only line per failing holdout whose
declared ``spec_anchor`` RESOLVES to a real anchor in the locked spec — with the binary
sentence always appended. There is NO category gate and NO security-domain screen: the
relay decision is solely anchor-resolution + objective-not-instance.

Leak invariants exercised here:
  * The output is always in {binary_fallback, well-formed anchor-only template}.
  * Garbage / fuzzed manifests never produce raw output — only the binary fallback.
"""

from __future__ import annotations

import json
import random
import re
import string

from darkfactory.audit.holdout_bridge import build_holdout_feedback

BINARY = (
    "## Blind Adversarial Validation\n\n"
    "Blind adversarial validation failed. Continue implementing the locked "
    "specification and the visible scenario suite only. No holdout names, "
    "paths, assertions, expected/actual values, spec anchors, categories, "
    "or input shapes are available.\n"
)

SPEC_BODY = """# Spec: widget-service

## 4. Functional Requirements
- **FR-1**: The service returns a 200 for a valid widget request.
- **FR-2**: The service authenticates the caller with a bearer token before
  returning tenant-scoped data. A token with surrounding whitespace MUST be
  rejected with a 401. Cross-tenant reads MUST be rejected.
- **FR-3**: The service rounds the price to two decimal places.

## 5. Edge Cases
- **EC-1**: An empty request body yields a 400 with a descriptive error shape.

## NFR
- **NFR-1**: p99 latency under 200ms.
"""


def _write_spec(tmp_path):
    p = tmp_path / "spec.md"
    p.write_text(SPEC_BODY, encoding="utf-8")
    return p


def _write_manifest(tmp_path, entries):
    p = tmp_path / "holdout-bridge.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


# --- fail-to-binary branches -------------------------------------------------


def test_no_failing_ids_returns_binary(tmp_path):
    spec = _write_spec(tmp_path)
    man = _write_manifest(tmp_path, [{"test_id": "h1", "spec_anchor": "FR-3"}])
    out = build_holdout_feedback(
        failed_ids=[], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out == BINARY


def test_none_failing_ids_returns_binary(tmp_path):
    spec = _write_spec(tmp_path)
    man = _write_manifest(tmp_path, [])
    out = build_holdout_feedback(
        failed_ids=None, manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out == BINARY


def test_manifest_missing_returns_binary(tmp_path):
    spec = _write_spec(tmp_path)
    out = build_holdout_feedback(
        failed_ids=["h1"],
        manifest_path=tmp_path / "nope.json",
        spec_path=spec,
        binary_fallback=BINARY,
    )
    assert out == BINARY


def test_spec_missing_returns_binary(tmp_path):
    man = _write_manifest(tmp_path, [{"test_id": "h1", "spec_anchor": "FR-3"}])
    out = build_holdout_feedback(
        failed_ids=["h1"],
        manifest_path=man,
        spec_path=tmp_path / "nope.md",
        binary_fallback=BINARY,
    )
    assert out == BINARY


def test_unparseable_manifest_returns_binary(tmp_path):
    spec = _write_spec(tmp_path)
    man = tmp_path / "holdout-bridge.json"
    man.write_text("{not json at all]]", encoding="utf-8")
    out = build_holdout_feedback(
        failed_ids=["h1"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out == BINARY


def test_manifest_not_a_list_returns_binary(tmp_path):
    spec = _write_spec(tmp_path)
    man = tmp_path / "holdout-bridge.json"
    man.write_text(json.dumps({"test_id": "h1"}), encoding="utf-8")
    out = build_holdout_feedback(
        failed_ids=["h1"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out == BINARY


def test_duplicate_test_id_returns_binary(tmp_path):
    spec = _write_spec(tmp_path)
    man = _write_manifest(
        tmp_path,
        [
            {"test_id": "h1", "spec_anchor": "FR-3"},
            {"test_id": "h1", "spec_anchor": "FR-1"},
        ],
    )
    out = build_holdout_feedback(
        failed_ids=["h1"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out == BINARY


# --- the relay path ----------------------------------------------------------


def test_resolvable_anchor_relays_plus_binary(tmp_path):
    spec = _write_spec(tmp_path)
    man = _write_manifest(tmp_path, [{"test_id": "h3", "spec_anchor": "FR-3"}])
    out = build_holdout_feedback(
        failed_ids=["h3"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    # Strict superset: relay line for FR-3 + the full binary fallback.
    assert out != BINARY
    assert "FR-3: behavioral deviation" in out
    assert out.endswith(BINARY)
    # No observed value / input instance / test title leaked.
    assert "rounds the price" not in out
    assert "200ms" not in out


def test_auth_domain_anchor_now_relays(tmp_path):
    # THE key behavior change: FR-2 is auth/token-domain (whitespace-token -> 401), an
    # AUTH-FUNCTIONAL requirement. With the security gate removed, a resolvable FR-2
    # anchor RELAYS the anchor-only pointer line — it is no longer forced to binary.
    spec = _write_spec(tmp_path)
    man = _write_manifest(tmp_path, [{"test_id": "h2", "spec_anchor": "FR-2"}])
    out = build_holdout_feedback(
        failed_ids=["h2"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out != BINARY
    assert "FR-2: behavioral deviation" in out
    assert out.endswith(BINARY)
    # Still anchor-only: no spec prose (token/whitespace/401 text) leaks.
    relay_part = out[: -len(BINARY)]
    assert "bearer token" not in relay_part
    assert "401" not in relay_part
    assert "whitespace" not in relay_part


def test_multiple_anchors_dedup_and_sorted(tmp_path):
    spec = _write_spec(tmp_path)
    man = _write_manifest(
        tmp_path,
        [
            {"test_id": "a", "spec_anchor": "FR-3"},
            {"test_id": "b", "spec_anchor": "FR-1"},
            {"test_id": "c", "spec_anchor": "FR-3"},
        ],
    )
    out = build_holdout_feedback(
        failed_ids=["a", "b", "c"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    # FR-1 appears before FR-3 (sorted), each exactly once.
    assert out.count("FR-1: behavioral deviation") == 1
    assert out.count("FR-3: behavioral deviation") == 1
    assert out.index("FR-1") < out.index("FR-3")


def test_unresolvable_anchor_returns_binary(tmp_path):
    spec = _write_spec(tmp_path)
    man = _write_manifest(tmp_path, [{"test_id": "h1", "spec_anchor": "FR-99"}])
    out = build_holdout_feedback(
        failed_ids=["h1"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out == BINARY


def test_anchor_shape_invalid_returns_binary(tmp_path):
    spec = _write_spec(tmp_path)
    man = _write_manifest(tmp_path, [{"test_id": "h1", "spec_anchor": "the price field"}])
    out = build_holdout_feedback(
        failed_ids=["h1"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out == BINARY


def test_anchor_prefix_does_not_spuriously_resolve(tmp_path):
    # FR-2 exists; FR-20 does NOT — a substring match would wrongly resolve FR-20.
    spec = _write_spec(tmp_path)
    man = _write_manifest(tmp_path, [{"test_id": "h1", "spec_anchor": "FR-20"}])
    out = build_holdout_feedback(
        failed_ids=["h1"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out == BINARY


def test_mixed_resolvable_and_unresolvable_relays_only_resolvable(tmp_path):
    spec = _write_spec(tmp_path)
    man = _write_manifest(
        tmp_path,
        [
            {"test_id": "ok", "spec_anchor": "FR-3"},
            {"test_id": "bad", "spec_anchor": "FR-99"},
        ],
    )
    out = build_holdout_feedback(
        failed_ids=["ok", "bad"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert "FR-3: behavioral deviation" in out
    assert "FR-99" not in out
    assert out.endswith(BINARY)


def test_failing_id_with_no_manifest_entry_falls_to_binary(tmp_path):
    spec = _write_spec(tmp_path)
    man = _write_manifest(tmp_path, [{"test_id": "known", "spec_anchor": "FR-3"}])
    # The failing id "ghost" is not in the manifest → no relay line → binary only.
    out = build_holdout_feedback(
        failed_ids=["ghost"], manifest_path=man, spec_path=spec, binary_fallback=BINARY
    )
    assert out == BINARY


# --- fuzz: never raw ---------------------------------------------------------


def _random_value(rng):
    choice = rng.randint(0, 6)
    if choice == 0:
        return "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 40)))
    if choice == 1:
        return rng.randint(-10, 10)
    if choice == 2:
        return None
    if choice == 3:
        return rng.choice(["FR-1", "FR-2", "FR-3", "EC-1", "NFR-1", "FR-99", "AC-7", "bogus"])
    if choice == 4:
        return rng.choice(["arbitrary", "free text", "GARBAGE", "tenant claim auth"])
    if choice == 5:
        return [rng.randint(0, 3) for _ in range(rng.randint(0, 3))]
    return {"nested": "object"}


def test_fuzz_manifest_never_returns_raw(tmp_path):
    rng = random.Random(1337)
    spec = _write_spec(tmp_path)
    template_relay_marker = "behavioral deviation — review the spec's stated behavior"
    for i in range(600):
        # Build a random manifest: sometimes a list of random dicts, sometimes not.
        shape = rng.randint(0, 3)
        if shape == 0:
            entries = []
            for _ in range(rng.randint(0, 5)):
                entry = {}
                for key in rng.sample(
                    ["test_id", "spec_anchor", "junk", "behavioral_objective", "bridge_category"],
                    rng.randint(0, 5),
                ):
                    entry[key] = _random_value(rng)
                entries.append(entry)
            payload = json.dumps(entries)
        elif shape == 1:
            payload = json.dumps(_random_value(rng))  # not a list
        elif shape == 2:
            payload = "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 60)))
        else:
            payload = json.dumps([_random_value(rng) for _ in range(rng.randint(0, 4))])

        man = tmp_path / f"fuzz-{i}.json"
        man.write_text(payload, encoding="utf-8")

        failed_ids = [
            rng.choice(["h1", "h2", "x", "", "known", str(rng.randint(0, 9))])
            for _ in range(rng.randint(0, 4))
        ]
        out = build_holdout_feedback(
            failed_ids=failed_ids, manifest_path=man, spec_path=spec, binary_fallback=BINARY
        )
        # Output is ALWAYS in {binary, well-formed anchor-only template}.
        if out == BINARY:
            continue
        # If it relayed, it must be the strict-superset template: ends with binary,
        # contains only the fixed template marker, and leaks no spec prose / free text.
        assert out.endswith(BINARY), payload
        assert template_relay_marker in out, payload
        relay_part = out[: -len(BINARY)]
        assert "rounds the price" not in relay_part, payload
        assert "bearer token" not in relay_part, payload
        assert "GARBAGE" not in relay_part, payload
        assert "nested" not in relay_part, payload
        # Only valid anchor tokens may appear in relay lines.
        for line in relay_part.splitlines():
            if line.startswith("- "):
                m = re.match(
                    r"^- ((?:FR|EC|AC|NFR|SI)-\d+[A-Za-z0-9.]*): behavioral deviation", line
                )
                assert m, f"bad relay line {line!r} from {payload!r}"
