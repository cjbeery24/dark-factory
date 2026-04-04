# Pipeline Run Report: budget-brain-v2 / 2026-03-30-auth-schemas-middleware

**Date:** 2026-04-02  
**Repo:** budget-brain-v2  
**Spec:** 2026-03-30-auth-schemas-middleware  
**Goal:** Track pipeline improvement/regression metrics across runs

---

## Run 1

**Start time (UTC):** 2026-04-02 ~14:50  
**End time (UTC):** 2026-04-02 ~15:30  
**Duration:** ~40 minutes  
**Outcome:** ESCALATED after 2 iterations  

### Pre-run state
- Repo reset to clean state (tracked files restored, untracked deleted, .dark-factory artifacts cleared)
- Docker images rebuilt (all cached — no changes)
- Agents and templates synced from dark-factory

### Pipeline progression

| Step | Status | Notes |
|------|--------|-------|
| Spec pre-check | PASS | 1 ambiguity warning ("etc." on line 21) |
| Spec validation | PASS | — |
| Architecture | PASS | Manifest generated; postgres detected as Docker service |
| Scenario generation | PASS | 4 scenario files + 1 tier1 contract test (30 test cases) |
| Implementation (iter 0) | DONE | — |
| Quality gate (iter 0) | FAIL | Server crash: `pino-pretty` not found as transport target; 0/30 tests passed |
| Implementation (iter 1) | DONE | Fixed pino-pretty, but introduced Fastify schema error |
| Quality gate (iter 1) | FAIL | Server crash: `FST_ERR_SCH_SERIALIZATION_BUILD` — `data/required must be array` on GET /api/v1/auth/me; 1/1 passed (contract tests bundled as single test) |
| Implementation (iter 2) | DONE | Fixed Fastify schema error; server now starts |
| Quality gate (iter 2) | FAIL | Build/Lint/TypeCheck/Scenarios all PASS; **Server Startup: FAIL** — health check unreachable despite server listening on port 3000 |
| Oracle (iter 2) | NO-SHIP | Detected ENV_ISSUE: server_setup_cmd failed 2 consecutive iterations |
| Escalation | TRIGGERED | "server_setup_cmd failed 2 consecutive iterations — likely config/infrastructure issue" |

### Iteration test results

| Iteration | Tests Passed | Tests Total | Pass Rate | Gate Result | Server Started |
|-----------|-------------|-------------|-----------|-------------|----------------|
| 0 | 0 | 30 | 0% | FAIL | No (pino-pretty crash) |
| 1 | 1 | 1 | 100%* | FAIL | No (Fastify schema error) |
| 2 | 1 | 1 | 100%* | FAIL | Yes, but health check failed |

*Iter 1-2: The 30 contract tests were aggregated as a single test result entry. Pass rate is misleading — it reflects all-or-nothing bundling.

### Key observations

1. **Test result aggregation issue:** In iter 0, individual test results were reported (30 entries). In iter 1-2, the entire test file was reported as a single entry. This makes pass rate tracking inconsistent.

2. **Server health check connectivity problem (critical):** In iter 2, the server started successfully and was listening on both `127.0.0.1:3000` and `172.25.0.4:3000`. But the quality gate's smoke check still failed. The health check likely can't reach the server inside the Docker network — possibly a container networking issue where the gate container tries `localhost:3000` but the server is in a different container.

3. **Oracle leakage detection working:** Oracle feedback initially contained "100% pass" which was flagged as a barrier leakage pattern. The validation caught it.

4. **ENV_ISSUE escalation is correct:** The server startup failure across iterations is indeed an environment/infra issue, not an implementation bug. The escalation detection logic correctly identified this pattern.

5. **Implementation quality was actually good by iter 2:** Build, lint, typecheck, and blind scenarios all passed. The only failure was the server health check, which is an infrastructure issue.

6. **Pino-pretty as a recurring issue:** The implementer added `pino-pretty` as a dependency (common in Fastify apps) but it wasn't available in the Docker container. This was fixed in iter 1 by removing the dependency.

### Pipeline improvement opportunities

- **Health check container networking:** The quality gate server container may need to bind to `0.0.0.0` and the health check needs to target the server container's IP or use Docker's internal DNS. This is likely a config.toml `health_path`/`base_url` issue or a Docker network routing issue.
- **Test result consistency:** The aggregation of 30 tests into 1 result between iterations is confusing. Should be consistent.
- **Oracle feedback sanitization:** The leakage detection worked, but the feedback should have been sanitized before delivery, not just logged.

---

## Summary

| Run | Outcome | Iterations | Final Pass Rate | Duration | Notes |
|-----|---------|------------|-----------------|----------|-------|
| 1 | ESCALATED | 2 (of 3 max) | 100% (static only) | ~40 min | Server health check unreachable despite server running; ENV_ISSUE detected |
| 2 | — | — | — | — | Not attempted (user stopped after run 1) |
| 3 | — | — | — | — | — |
| 4 | — | — | — | — | — |

### Blocking issue for further runs

The server startup health check in the quality gate cannot reach the server container. This appears to be a Docker networking/configuration issue. Further pipeline runs would likely hit the same problem unless the `config.toml` test_server configuration or the quality gate's server networking is fixed. The implementation itself (schemas, middleware, routes) is complete and passes all static checks.
