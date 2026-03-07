"""Scale validation — parallel scenario runner with throughput metrics."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.models.config import PipelineConfig
from darkfactory.scenarios.runner import run_scenarios
from darkfactory.state.registry import list_specs, scenario_dir

log = logging.getLogger(__name__)


@dataclass
class ScaleResult:
    """Result of a scale validation run."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    wall_seconds: float = 0.0
    latencies: list[float] = field(default_factory=list)

    @property
    def scenarios_per_hour(self) -> float:
        if self.wall_seconds <= 0 or self.total <= 0:
            return 0.0
        return (self.total / self.wall_seconds) * 3600

    @property
    def p50(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[len(s) // 2]

    @property
    def p95(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        idx = int(len(s) * 0.95)
        return s[min(idx, len(s) - 1)]

    @property
    def p99(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        idx = int(len(s) * 0.99)
        return s[min(idx, len(s) - 1)]


def run_scale_validation(
    config: PipelineConfig,
    project_dir: Path,
    *,
    concurrency: int = 0,
    events_file: Path | None = None,
    run_id: str = "",
) -> ScaleResult:
    """Run all registered scenarios in parallel for throughput measurement.

    Args:
        concurrency: Max parallel workers (0 = use config.scale_concurrency)
    """
    workers = concurrency or config.scale_concurrency
    specs = list_specs(project_dir)

    if not specs:
        log.warning("no registered specs for scale validation")
        return ScaleResult()

    result = ScaleResult(total=len(specs))
    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for spec_name in specs:
            sdir = scenario_dir(project_dir, spec_name)
            if not sdir:
                continue
            future = pool.submit(_run_one, spec_name, config, project_dir)
            futures[future] = spec_name

        for future in as_completed(futures):
            spec_name = futures[future]
            try:
                ok, latency = future.result()
                result.latencies.append(latency)
                if ok:
                    result.passed += 1
                else:
                    result.failed += 1
            except Exception as e:
                log.warning("scale: %s error: %s", spec_name, e)
                result.failed += 1

    result.wall_seconds = time.monotonic() - start

    log.info("scale validation: %d/%d passed, %.1f scenarios/hr, p50=%.1fs p95=%.1fs",
             result.passed, result.total, result.scenarios_per_hour,
             result.p50, result.p95)

    return result


def _run_one(
    spec_name: str,
    config: PipelineConfig,
    project_dir: Path,
) -> tuple[bool, float]:
    """Run scenarios for a single spec. Returns (passed, latency_seconds)."""
    start = time.monotonic()
    r = run_scenarios(spec_name, config, project_dir)
    latency = time.monotonic() - start
    return r.exit_code == 0, latency
