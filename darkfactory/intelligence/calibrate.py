"""Cost calibration — measure actual vs estimated token usage.

Runs a small benchmark of agent invocations, parses token counts from
Claude API response metadata, computes a correction factor, and writes
it to config.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run as run_subprocess

log = logging.getLogger(__name__)

# Short prompts for calibration — kept minimal to reduce cost.
_CALIBRATION_PROMPTS = [
    "List the prime numbers less than 20.",
    "Write a Python function that reverses a string.",
    "Explain what a hash table is in two sentences.",
]


def run_calibration(
    config: PipelineConfig,
    project_dir: Path,
) -> float:
    """Run calibration benchmark and compute correction factor.

    Invokes Claude with small prompts, measures actual token usage from
    output metadata, and computes the ratio of actual to estimated tokens.

    Returns the correction factor (> 1.0 means estimates were low).
    """
    estimated_total = 0
    actual_total = 0
    successful = 0

    for i, prompt in enumerate(_CALIBRATION_PROMPTS, 1):
        actual = _measure_invocation(prompt, config)
        if actual is None:
            log.warning("calibration invocation %d failed, skipping", i)
            continue

        est_tokens = config.cost_est_input_tokens_per_turn + config.cost_est_output_tokens_per_turn
        estimated_total += est_tokens
        actual_total += actual
        successful += 1

        print(f"  Agent {i}: estimated {est_tokens:,} tokens, actual {actual:,} tokens")

    if successful == 0:
        log.error("all calibration invocations failed — cannot compute factor")
        return 1.0

    factor = actual_total / estimated_total if estimated_total > 0 else 1.0
    factor = round(factor, 2)

    # Write to config file.
    _write_factor(project_dir, factor)

    return factor


def _measure_invocation(prompt: str, config: PipelineConfig) -> int | None:
    """Run a single Claude invocation and extract token count from output.

    Returns total tokens (input + output) or None on failure.
    """
    result = run_subprocess(
        [
            "claude", "-p", prompt,
            "--output-format", "json",
            "--max-turns", "1",
        ],
        timeout=60,
    )

    if result.exit_code != 0:
        return None

    try:
        data = json.loads(result.stdout)
        # Claude JSON output includes usage metadata.
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total = input_tokens + output_tokens
        return total if total > 0 else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _write_factor(project_dir: Path, factor: float) -> None:
    """Write the correction factor to config.toml."""
    config_path = project_dir / ".dark-factory" / "config.toml"

    if config_path.exists():
        content = config_path.read_text()
        # Check if cost_correction_factor already exists.
        if "cost_correction_factor" in content:
            content = re.sub(
                r'cost_correction_factor\s*=\s*[\d.]+',
                f'cost_correction_factor = {factor}',
                content,
            )
            config_path.write_text(content)
        elif "[cost]" in content:
            content = content.replace(
                "[cost]",
                f"[cost]\ncost_correction_factor = {factor}",
            )
            config_path.write_text(content)
        else:
            with config_path.open("a") as f:
                f.write(f"\n[cost]\ncost_correction_factor = {factor}\n")
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            f"# Dark Factory configuration\n\n[cost]\ncost_correction_factor = {factor}\n"
        )

    print(f"Written to {config_path}: cost_correction_factor = {factor}")
