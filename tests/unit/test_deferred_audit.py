"""Tests for the cross-spec forward-reference audit (deferred_audit)."""

from pathlib import Path

from darkfactory.spec.deferred_audit import audit_deferred_obligations


def _spec(dir_: Path, name: str, body: str) -> None:
    (dir_ / f"{name}.md").write_text(body, encoding="utf-8")


def _substrate(verifiable: str) -> str:
    return f"""\
## Substrate Primitives

```toml
[[tenant_isolation]]
tenant_table = "tenants"
{verifiable}
```
"""


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".dark-factory" / "specs").mkdir(parents=True)
    return tmp_path


def test_pending_when_never_made_verifiable_true(tmp_path):
    p = _project(tmp_path)
    _spec(p / ".dark-factory" / "specs", "f1c", _substrate("verifiable = false"))
    obs = audit_deferred_obligations(p)
    assert len(obs) == 1
    o = obs[0]
    assert o.primitive_type == "tenant_isolation"
    assert o.pending is True
    assert o.deferred_in == ["f1c"] and o.resolved_in == []


def test_resolved_when_later_spec_makes_it_verifiable_true(tmp_path):
    p = _project(tmp_path)
    sd = p / ".dark-factory" / "specs"
    _spec(sd, "f1c", _substrate("verifiable = false"))
    _spec(sd, "f2", _substrate("verifiable = true"))
    obs = audit_deferred_obligations(p)
    assert len(obs) == 1
    o = obs[0]
    assert o.pending is False
    assert o.deferred_in == ["f1c"] and o.resolved_in == ["f2"]


def test_forward_ref_anchors_attached(tmp_path):
    p = _project(tmp_path)
    body = _substrate("verifiable = false") + """
## 8. Acceptance Criteria

| ID | Criterion | Blocking |
|----|-----------|:-:|
| AC-14 | only tenant A's rows | @untestable: forward-reference — deferred |
"""
    _spec(p / ".dark-factory" / "specs", "f1c", body)
    obs = audit_deferred_obligations(p)
    assert obs[0].forward_ref_anchors == {"f1c": ["AC-14"]}


def test_no_substrate_no_obligations(tmp_path):
    p = _project(tmp_path)
    _spec(p / ".dark-factory" / "specs", "plain", "## 8. Acceptance Criteria\n| AC-1 | x | yes |\n")
    assert audit_deferred_obligations(p) == []


def test_missing_specs_dir(tmp_path):
    assert audit_deferred_obligations(tmp_path) == []
