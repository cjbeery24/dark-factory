# Known-Failing Unit Tests (Local Baseline)

Captured **2026-04-15** on branch `main`.

These 50 tests fail in a local (non-Docker) dev environment. They require
Docker daemon, built images (`dark-factory-{lang}`), or running
compose stacks. They also fail on a clean checkout of `main` without
any local changes — so a new failure in this list is *not* a regression
introduced by your branch.

**Triage in progress:** baseline shrunk from 65 → 50 on 2026-04-15:
- 12 fixed: `test_docker_args` + `test_mounts` cluster (host-path mkdir failures)
- 1 fixed: `test_containers::test_image_present_no_error` (mock returned empty stdout)
- 1 fixed: `test_sa_validation::test_fail_over_limit` (substring vs exact match)
- 1 fixed: `test_plan_executor::test_dry_run` (plan table format + spec_type frontmatter)

Remaining 50 are categorized below.

**Usage:** when running `pytest tests/unit` locally, diff failures
against this list. Anything here = environmental. Anything new =
something your change broke.

## How to verify this baseline is still accurate

```bash
git stash && \
python3 -m pytest tests/unit -q --timeout=30 \
  --deselect tests/unit/test_agents.py::TestCheckLanguageImageFromAgents::test_check_language_image_called_before_safety_checks \
  2>&1 | grep '^FAILED' | sort > /tmp/current-failures.txt && \
diff /tmp/current-failures.txt docs/dev/known-failing-tests-raw.txt ; \
git stash pop
```

If the diff is empty, the baseline is accurate. If there are additions
to the baseline (tests now failing that previously passed), either
the environment changed or main regressed — investigate before
assuming your branch is clean.

## Categories

### Docker daemon / image required (~18 tests)
- `test_containers.py::TestCheckDockerImage::*`
- `test_environment.py::TestProvision::*` and `TestTeardown::*`
- `test_health_checks.py::*`
- `test_monitor.py::*`

### Enterprise tier (post-ship deployment) (~25 tests)
Enterprise features are off by default; test harness still runs them.
- `test_deploy_integration.py::*`
- `test_post_ship_pipeline.py::*`
- `test_progressive.py::*`
- `test_rollback.py::*`
- `test_feature_flags.py::*`
- `test_metrics_gate.py::*`
- `test_migration_check.py::*`

### Other (~1 test)
- `test_agents.py::TestCheckLanguageImageFromAgents::test_check_language_image_called_before_safety_checks` — filesystem write_dir missing (preflight setup issue)

## Full list

See `docs/dev/known-failing-tests-raw.txt` for the exact `FAILED tests/...` lines that pytest emits.
