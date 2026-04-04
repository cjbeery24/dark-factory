# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date:** 2026-04-02 23:48 UTC  
**Spec:** 2026-03-28-api-bootstrap  
**Target repo:** ~/sites/budget-brain-v2  
**Dark Factory version:** HEAD (main)  
**Run ID:** afd23848a75740bb  
**Total wall time:** ~46 minutes (23:51:19 to 00:37:08 UTC)  
**Outcome:** ESCALATED after 2 iterations (both NO-SHIP)

## Pre-Run State

- Previous run existed: YES — ended `NO-SHIP` after 3 iterations (status: interrupted)
- Previous state: `smoke_result=failed, pass_rate=0, server_error=true`
- Modified tracked files: `apps/api/package.json`, `apps/api/src/index.ts`, `apps/api/tsconfig.tsbuildinfo`, `pnpm-lock.yaml`
- Untracked files: `.dark-factory/architecture/`, `.dark-factory/reviews/`, `apps/api/src/common/`, `apps/api/src/server.ts`
- Reset: all tracked files restored, untracked cleaned, runtime artifacts removed

## Setup Steps

### 1. Agent/Template Sync
`dark-factory integrate budget-brain-v2 --language node --force` — completed successfully.

### 2. Docker Image Rebuild
`dark-factory build node` — all 5 images rebuilt (all layers cached). No issues.

### 3. Repository Reset
`git checkout -- . && git clean -fd` + removed `.dark-factory/{architecture,reviews,reports,scenarios,shipped-scenarios,audit,state,failures,events.jsonl,metrics.jsonl}`. Clean state confirmed.

---

## Pipeline Run

### Phase Progression

| Phase | Agent | Iter | Attempt | Wall (s) | Exit | Notes |
|-------|-------|------|---------|----------|------|-------|
| spec-validator | spec-validator | - | 1 | 107.7 | 0 | |
| architect | architect | - | 1 | 119.3 | 0 | |
| scenario-architect | scenario-architect | - | 1 | 269.4 | 0 | Two invocations (269s + 202s) |
| scenario-architect | scenario-architect | - | 2 | 201.8 | 0 | |
| implementer | implementer | 1 | 1 | 323.3 | 0 | Three invocations (workspace check retries?) |
| implementer | implementer | 1 | 2 | 123.9 | 0 | |
| implementer | implementer | 1 | 3 | 126.1 | 0 | |
| smoke check | (pipeline) | 1 | - | - | - | FAILED, server_error=true |
| quality gate | (pipeline) | 1 | - | - | 1 | Build PASS, Lint PASS, TypeCheck PASS, Server FAIL |
| reviewer | reviewer | 1 | 1 | 104.2 | 0 | |
| oracle | oracle | 1 | 1+2 | 48.6+50.9 | 0 | NO-SHIP |
| implementer | implementer | 2 | 1 | 150.7 | 0 | Two invocations |
| implementer | implementer | 2 | 2 | 117.3 | 0 | |
| smoke check | (pipeline) | 2 | - | - | - | FAILED, server_error=true |
| quality gate | (pipeline) | 2 | - | 1 | 1 | Build PASS, Lint PASS, TypeCheck PASS, Server FAIL |
| reviewer | reviewer | 2 | 1 | 257.1 | 0 | |
| oracle | oracle | 2 | 1+2 | 141.9+56.3 | 0 | NO-SHIP |
| escalation | escalation | 3 | 1 | 59.0 | 0 | SPEC_ESCALATED |

### State File (final)

```
spec=2026-03-28-api-bootstrap
run_id=afd23848a75740bb
spec_hash=3daed8fbb63262825e58555d4f3e7158e99d2306af1c1d57b5a9a9485ad36781
smoke_result=failed
pass_rate=0
gate_exit_code=1
server_error=true
last_decision=NO-SHIP
phase=oracle_complete
status=interrupted
iteration=3
completed_step=2
```

### Quality Gate Report (final, iter 2)

```
| Gate | Status |
|------|--------|
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Self-Tests | SKIP |
| Mutation Testing | SKIP |
| Server Startup | FAIL |

Result: FAIL
```

### Container Logs (server)

Both iterations produced identical logs:
```
Scope: all 4 workspace projects
Lockfile is up to date, resolution step is skipped
Packages: +224/+226
Done in ~540ms using pnpm v9.15.9

> @budget-brain/api@0.0.0 start /project/apps/api
> node dist/index.js

{"level":30,"time":...,"msg":"Server listening at http://127.0.0.1:3000"}
{"level":30,"time":...,"msg":"Server listening at http://172.25.0.3:3000"}
{"level":30,"time":...,"port":3000,"env":"development","msg":"Server started"}
```

Server starts and logs successfully, but health check fails:
```
health poll #1 at 1.7s: exit 1 wget: can't connect to remote host: Connection refused
...
health poll #70 at 74.7s: exit 1 wget: can't connect to remote host: Connection refused
```

### Events Summary

- **Anomaly detected:** `spec_turns` — turns=1000 > max=800
- **Trajectory classified (iter 2):** PLATEAU — "Fundamentally different approach needed"
- **Estimated cost:** iter 1 = $126, iter 2 = $84 (total ~$210)
- **Total agent invocations:** 16 (many agents invoked 2-3 times each)

### Implementation Quality

The implementer produced correct, well-structured code:
- `apps/api/src/server.ts` — Fastify 5 server with CORS, rate limiting, structured logging
- `apps/api/src/common/config.ts` — Zod-validated environment variables
- `apps/api/src/common/health.ts` — Health check registry
- `apps/api/src/common/error-handler.ts` — Global error handler plugin
- `apps/api/src/common/errors.ts` — AppError class

Build, lint, and type check all pass. The server code correctly uses `host: '0.0.0.0'`.

---

## Root Cause Analysis: IPv6 Localhost Resolution Bug

### The Problem

The health check uses `docker exec <container> wget -q -O /dev/null http://localhost:3000/health`. In Alpine Linux (node:22-alpine, Alpine 3.23.3), BusyBox `wget` resolves `localhost` to `::1` (IPv6 loopback) before trying `127.0.0.1` (IPv4 loopback).

The server binds to `0.0.0.0:3000` (IPv4 all-interfaces only). Nothing listens on `[::1]:3000`, so `wget` gets "Connection refused."

### Proof

Reproduced with a minimal test:
```
$ docker run --rm -d --name test node:22-alpine sh -c \
    "node -e \"require('http').createServer((req,res)=>{res.writeHead(200);res.end('ok')}).listen(3000,'0.0.0.0')\""

$ docker exec test wget -q -O - http://localhost:3000/
wget: can't connect to remote host: Connection refused    # <-- FAILS (IPv6 ::1)

$ docker exec test wget -q -O - http://127.0.0.1:3000/
ok                                                         # <-- WORKS (IPv4)
```

Alpine's `/etc/hosts`:
```
127.0.0.1    localhost
::1          localhost ip6-localhost ip6-loopback
```

BusyBox wget prefers IPv6 when `::1 localhost` is present, even though the IPv4 entry comes first.

### Where It Happens

`darkfactory/pipeline/quality_gate.py:803`:
```python
url = f"http://localhost:{port}{health_path}"
```

This constructs the wget URL with `localhost`, which resolves to `::1` in the Alpine container.

### Impact

This bug **blocks every spec that requires server health checks**. It is not an implementation issue — the server works perfectly on IPv4. The pipeline's health check mechanism is fundamentally broken on Alpine-based Docker images.

### Fix

Replace `localhost` with `127.0.0.1` in the health check URL:
```python
url = f"http://127.0.0.1:{port}{health_path}"
```

This should be a one-line fix in `_wait_for_server()`.

---

## Other Observations

### 1. Excessive Agent Retries
Multiple agents were invoked 2-3 times within a single iteration step (e.g., implementer invoked 3 times in iter 1, scenario-architect invoked twice). This is likely the workspace-check retry mechanism. Each retry costs ~$21 (200 turns estimated). Total of ~16 agent invocations for a 2-iteration run is expensive.

### 2. Port Mismatch Between Manifest and Config
- Architect manifest: `test_base_url: "http://localhost:3001"` (matches spec default PORT=3001)
- Config.toml: `base_url = "http://localhost:3000"` (different)
- `patch_config_from_runtime()` doesn't override because config.toml already has a value
- The pipeline injects `PORT=3000` into the server container (from config.toml's base_url)
- The server then binds to 3000, not its default of 3001

This isn't a bug per se (the port is consistent between the injected PORT env and the health check target), but it shows the manifest's `test_base_url` is effectively ignored when config.toml has a value.

### 3. Oracle Feedback Quality
The oracle's feedback correctly identified that server logs show successful startup but health checks fail. However, it classified the issue as "ENV_ISSUE" and directed the implementer to fix deployment/environment issues — but the implementer has no control over the Docker health check mechanism. The feedback was a dead end because the root cause is in the pipeline, not the implementation.

### 4. Trajectory Classification
The pipeline correctly classified the trajectory as "PLATEAU" after iter 2, since pass_rate remained at 0% across iterations. The "fundamentally different approach needed" strategy was appropriate but misdirected — no implementation approach can fix a pipeline-level health check bug.

---

## Conclusions

### Blocking Issue
**The pipeline has a critical bug**: BusyBox `wget` in Alpine resolves `localhost` to IPv6 `::1`, but servers bind to IPv4 `0.0.0.0`. The health check will fail for ANY server spec using the current Alpine-based Docker images. This is the same root cause as all previous failed runs of this spec.

### Recommended Fix
Change `quality_gate.py:803` from `http://localhost:{port}` to `http://127.0.0.1:{port}`.

### Cost
Estimated ~$210 for this run (2 iterations, 16 agent invocations). All spending was wasted due to the pipeline bug.

### Regression Status
This is a **pre-existing regression** — the same failure pattern was observed in the previous run (state file from prior session showed identical `smoke_result=failed, server_error=true, pass_rate=0`).
