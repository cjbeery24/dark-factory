<!--
Python + FastAPI addendum. Kept intentionally minimal — this adapter
is a Sprint 6 stub proving the extension point works. Fill in when a
real Python FastAPI spec runs end-to-end.

Governance cap: 80 non-blank lines. Enforced by the contract harness
(`test_architect_addendum_within_governance_line_cap`).
-->

## Python + FastAPI addendum

- **Packaging:** prefer `pyproject.toml` as the dependency manifest.
  `requirements.txt` is accepted; `setup.py`-only projects are legal
  but less common. The architect MUST NOT invent a parallel
  dependency file.
- **Dependency installation:** emit an install command that matches
  the project's lockfile. `uv sync` when `uv.lock` is present;
  `pip install -r requirements.txt` for `requirements.txt`;
  `pip install -e .` for `pyproject.toml` without a lockfile. Do not
  mix managers in one run.
- **Test runner:** `pytest` is the default. Scope test commands to
  the package under test (`pytest apps/api/tests`) — do not invoke
  root-level `pytest` on a monorepo layout without explicit consent.
- **Typecheck:** `mypy` or `pyright` if the project declares one in
  its config; otherwise omit `commands.typecheck`. Do not force a
  typechecker the project does not use.
- **Module imports:** Python resolves imports from `sys.path`. When
  tests live under a separate tree (`.dark-factory/scenarios/`),
  ensure the package under test is installed (`pip install -e .`) or
  the scenario run sets `PYTHONPATH`. FastAPI apps typically import
  their app object from a module path (e.g. `from app.main import app`)
  — entry-point specifiers in `test_integration` use dotted module
  paths, not relative file paths.
- **CWD:** if the FastAPI app lives at `apps/api/`, the pyproject and
  pytest.ini live there. Emit `cd apps/api &&` prefixes on
  `commands.*` where relative paths matter.

### Consistency-rule reminders

This adapter ships no baseline consistency rules. Project-specific
rules (migration discipline for SQLAlchemy/Alembic, banned commands)
live in `.dark-factory/project-hints.toml` under
`[[policy.consistency_rules]]` — not here.

### Stub status

The precheck, consistency_rules, and proposal_rehearsal_typecheck
hooks intentionally return empty / None. A real Python FastAPI
precheck (import validation against declared deps, Pydantic model
stub round-trip, etc.) is follow-up work. This stub's job is to
prove the registry extension point: a Python project detects as
`python-fastapi` (not as `typescript-node`) and the pipeline uses
this file's guidance in the architect prompt instead of the
TypeScript one.
