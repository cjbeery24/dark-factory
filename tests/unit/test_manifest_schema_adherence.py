"""Tests for the architect-manifest schema-adherence lint (D2).

The lint catches an architect that drifted off the canonical manifest schema
(e.g. emitted ``files:`` instead of ``file_map:``, or an invented ``scenarios:``
block) BEFORE the silently-tolerant parser turns it into an empty ``file_map``
that starves the Test Engineer. See manifest.check_manifest_schema_adherence.
"""

import unittest

from darkfactory.spec.manifest import (
    _CANONICAL_TOP_LEVEL_KEYS,
    _FRAMEWORK_STAMPED_KEYS,
    check_manifest_schema_adherence,
    parse_manifest,
)

_CANONICAL_YAML = """\
version: 1
spec: my-spec
spec_hash: abc123
language: node
file_map:
  - {path: src/a.ts, action: create, purpose: "does a thing"}
data_models: []
api_contracts: []
dependencies: []
environment: []
runtime: {}
test_integration: {entry_points: []}
forbidden_patterns: {}
"""

# The bb/TypeScript drift shape the lint exists to catch.
_DRIFT_YAML = """\
spec_id: my-spec
feature_id: F-1
spec_type: bootstrap
files:
  - {path: src/a.ts, action: create, description: "does a thing"}
scenarios:
  - {id: S-1, intent: "..."}
layout: {source_dirs: [src]}
server: {port: 3000}
test_integration: {entry_points: []}
"""


class TestSchemaAdherenceLint(unittest.TestCase):
    def test_canonical_manifest_is_clean(self):
        self.assertEqual(check_manifest_schema_adherence(_CANONICAL_YAML), [])

    def test_drift_keys_each_get_a_correction(self):
        errs = check_manifest_schema_adherence(_DRIFT_YAML)
        joined = "\n".join(errs)
        # Every drift key is flagged...
        for k in ("spec_id", "feature_id", "spec_type", "files", "scenarios",
                  "layout", "server"):
            self.assertIn(f"`{k}`", joined, f"{k} not flagged")
        # ...with actionable corrective text for the load-bearing ones.
        self.assertIn("rename `files:` to `file_map:`", joined)
        self.assertIn("remove the top-level `scenarios:`", joined)
        self.assertIn("under `runtime:`", joined)  # layout/server

    def test_unknown_key_gets_generic_message_with_canonical_list(self):
        errs = check_manifest_schema_adherence("file_map: []\nbananas: 3\n")
        self.assertEqual(len(errs), 1)
        self.assertIn("`bananas`", errs[0])
        self.assertIn("not part of the manifest schema", errs[0])
        self.assertIn("file_map", errs[0])  # the canonical list is shown

    def test_framework_stamped_keys_tolerated(self):
        # Correction A guard: even though the lint runs pre-stamp, it must never
        # false-fail if handed a STAMPED manifest (active_adapters/stamp_integrity).
        stamped = _CANONICAL_YAML + "active_adapters: [typescript]\nstamp_integrity: deadbeef\n"
        self.assertEqual(check_manifest_schema_adherence(stamped), [])

    def test_malformed_yaml_and_non_mapping_are_not_double_reported(self):
        # parse_manifest owns these errors; the lint stays silent.
        self.assertEqual(check_manifest_schema_adherence("{not: valid: yaml: ["), [])
        self.assertEqual(check_manifest_schema_adherence("- just\n- a\n- list\n"), [])
        self.assertEqual(check_manifest_schema_adherence(""), [])

    def test_allowlist_matches_parser_data_get_keys(self):
        # The REAL desync guard (Linus): introspect the keys parse_manifest
        # actually reads off the raw `data` dict and assert set-equality with the
        # lint's allowlist. Catches a new `data.get("new_field")` added to the
        # parser without updating _CANONICAL_TOP_LEVEL_KEYS (which would otherwise
        # make the lint reject a now-legitimate key) — and vice versa.
        import inspect
        import re
        from darkfactory.spec import manifest as _m
        src = inspect.getsource(_m.parse_manifest)
        parser_keys = set(re.findall(r'data\.get\(\s*"([^"]+)"', src))
        self.assertEqual(
            parser_keys, set(_CANONICAL_TOP_LEVEL_KEYS),
            "schema-lint allowlist has drifted from the top-level keys "
            "parse_manifest reads — update _CANONICAL_TOP_LEVEL_KEYS",
        )
        self.assertEqual(_FRAMEWORK_STAMPED_KEYS, frozenset({"active_adapters", "stamp_integrity"}))

    def test_canonical_keys_are_all_parser_recognized(self):
        # The allowlist must not drift from what parse_manifest actually reads:
        # a manifest using exactly the canonical keys parses with no errors, and
        # the lint agrees it is clean. (Guards against the lint allowing a key
        # the parser ignores, or vice versa.)
        result = parse_manifest(_CANONICAL_YAML)
        self.assertEqual(result.errors, [], result.errors)
        self.assertIsNotNone(result.manifest)
        self.assertEqual(check_manifest_schema_adherence(_CANONICAL_YAML), [])

    def test_stamped_manifest_still_parses_without_error(self):
        # Correction A: parse_manifest is re-invoked on STAMPED manifests at many
        # downstream callsites; it must keep ignoring the framework keys (the lint
        # is NOT wired into the parser, so this stays true).
        stamped = _CANONICAL_YAML + "active_adapters: [typescript]\nstamp_integrity: deadbeef\n"
        result = parse_manifest(stamped)
        self.assertEqual(result.errors, [], result.errors)
        self.assertIsNotNone(result.manifest)


if __name__ == "__main__":
    unittest.main()
