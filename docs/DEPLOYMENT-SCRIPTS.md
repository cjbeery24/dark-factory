# Deployment Scripts Reference

Dark Factory provides copy-paste reference deployment scripts for common deployment targets. These scripts are templates — customize them for your infrastructure.

## Directory Structure

```
scripts/deploy/
├── k8s-argo/
│   ├── canary-shift.sh      — Argo Rollouts canary traffic shift
│   ├── rollback.sh           — Argo Rollouts abort + rollback
│   └── health-check.sh       — Kubernetes readiness probe
├── docker-compose/
│   ├── canary-shift.sh       — nginx upstream weighting
│   ├── rollback.sh           — stop canary, revert to stable
│   └── docker-compose.deploy.yml — ephemeral environment (app + db + nginx)
└── unleash/
    ├── flag-create.sh        — Unleash API flag creation
    ├── flag-enable.sh        — Unleash API flag enable
    └── flag-disable.sh       — Unleash API flag kill switch
```

## Configuration

Map scripts to Dark Factory config fields in `.dark-factory/config.toml`:

### K8s / Argo Rollouts

```toml
[deploy]
cmd = "scripts/deploy/k8s-argo/health-check.sh"
rollback_cmd = "scripts/deploy/k8s-argo/rollback.sh"

[canary]
enabled = true
shift_cmd = "scripts/deploy/k8s-argo/canary-shift.sh"
rollback_cmd = "scripts/deploy/k8s-argo/rollback.sh"

[[canary.stages]]
percent = 10
soak_seconds = 300

[[canary.stages]]
percent = 50
soak_seconds = 600

[[canary.stages]]
percent = 100
soak_seconds = 0
```

### Docker Compose

```toml
[deploy]
compose_file = "scripts/deploy/docker-compose/docker-compose.deploy.yml"
base_url = "http://localhost:8080"
health_endpoint = "/health"
rollback_cmd = "scripts/deploy/docker-compose/rollback.sh"

[canary]
enabled = true
shift_cmd = "scripts/deploy/docker-compose/canary-shift.sh"
rollback_cmd = "scripts/deploy/docker-compose/rollback.sh"
```

### Unleash Feature Flags

```toml
[flags]
create_cmd = "scripts/deploy/unleash/flag-create.sh"
enable_cmd = "scripts/deploy/unleash/flag-enable.sh"
disable_cmd = "scripts/deploy/unleash/flag-disable.sh"
```

## Environment Variables

All scripts receive these from the pipeline:

| Variable | Description |
|----------|-------------|
| `DF_SPEC_NAME` | Spec being deployed |
| `DF_RUN_ID` | Pipeline run ID |
| `CANARY_PERCENT` | Target traffic percentage (canary scripts only) |

Additional variables are script-specific — see comments in each script header.

## Post-Merge Rollback Monitor

Configure a monitor script that polls health after merge:

```toml
[deploy]
rollback_monitor_cmd = "scripts/deploy/k8s-argo/health-check.sh"
rollback_window_seconds = 3600
rollback_poll_interval = 60
```

The monitor runs `rollback_monitor_cmd` every `rollback_poll_interval` seconds for `rollback_window_seconds` duration. Exit code 0 = healthy, non-zero = breach (triggers L1-L4 rollback cascade).
