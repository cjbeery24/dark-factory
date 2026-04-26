"""Project-side contracts management — Phase 4 home for ``init`` and
``verify`` subcommands of ``dark-factory contracts``.

* ``init`` (v0) copies framework starter templates into a project's
  ``<project>/.dark-factory/contracts/<adapter>/``.
* ``verify`` (v1, future) runs project-side helpers against canonical
  foot-gun fixtures.
* ``B5`` (future) ships the shared regex library — keyed names, dialect
  tags, fix hints — that ``scenario_lint``, ``analyze_trial.KNOWN_BUGS``,
  and ``/df-densify-spec`` all import from.

The retire_after auto-eval workflow (Phase 3 B9b) is **not** under this
package — it lives next to the postgres adapter at
``darkfactory.adapters.postgres.retire_after_*`` because it is an
adapter-shipping concern (one ``retire_after.toml`` per adapter, with
adapter-specific fixtures). The ``contracts/`` package houses
project-facing tooling that is uniform across adapters.
"""
