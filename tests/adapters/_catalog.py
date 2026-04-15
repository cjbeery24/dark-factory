"""Test-harness catalogs of ``EcosystemAdapter``'s required surface.

These lists exist as a tripwire: when an abstract method or property is
added to ``darkfactory.adapters.base.EcosystemAdapter``, the
``test_required_*_catalog_is_complete`` tests fail until both this file
and the per-method positive/negative tests are updated together. That
forces the catalog and tests to stay in sync — preventing the
silent-drift failure mode where the contract grows but the harness
doesn't.

This file is *test-internal*. Production code should not import from
here. The authoritative ABC lives at ``darkfactory.adapters.base``.
"""
from __future__ import annotations


# Methods every ``EcosystemAdapter`` must implement. Add a method here
# only when adding it to the ABC; the catalog-drift test will fail
# until the per-method positive + negative tests also exist.
REQUIRED_METHODS: tuple[str, ...] = (
    "detect",
    "precheck",
    "consistency_rules",
    "architect_addendum",
    "spec_validator_addendum",
    "quality_gate_dispatch",
    "proposal_rehearsal_typecheck",
)


# Properties every ``EcosystemAdapter`` must expose. These are abstract
# properties on the ABC; subclasses typically supply them via dataclass
# field defaults (which shadow the property descriptor at
# class-definition time).
REQUIRED_ATTRS: tuple[str, ...] = ("name", "detection_signals")
