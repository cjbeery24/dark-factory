"""Project root resolution and path constants."""

from __future__ import annotations

from pathlib import Path

# Relative path fragments under the project root. Always use as: root / CONSTANT
DARK_FACTORY_DIR = ".dark-factory"
SPECS_DIR = f"{DARK_FACTORY_DIR}/specs"
SCENARIOS_DIR = f"{DARK_FACTORY_DIR}/scenarios"
REVIEWS_DIR = f"{DARK_FACTORY_DIR}/reviews"
REPORTS_DIR = f"{DARK_FACTORY_DIR}/reports"
AUDIT_DIR = f"{DARK_FACTORY_DIR}/audit"
STATE_DIR = f"{DARK_FACTORY_DIR}/state"
PLANS_DIR = f"{DARK_FACTORY_DIR}/plans"
EVENTS_FILE = f"{DARK_FACTORY_DIR}/events.jsonl"
METRICS_FILE = f"{DARK_FACTORY_DIR}/metrics.jsonl"
DEBUG_LOG = f"{DARK_FACTORY_DIR}/debug.log"
CONFIG_TOML = f"{DARK_FACTORY_DIR}/config.toml"
CONFIG_SH = f"{DARK_FACTORY_DIR}/config.sh"
SHIPPED_SCENARIOS_DIR = f"{DARK_FACTORY_DIR}/shipped-scenarios"


def resolve_project_root(start: Path | None = None) -> Path:
    """Walk up from start (default cwd) to find the project root.

    The project root is the directory containing .dark-factory/ or .git/.
    Raises FileNotFoundError if neither is found.
    """
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        if (directory / DARK_FACTORY_DIR).is_dir():
            return directory
        if (directory / ".git").exists():
            return directory
    raise FileNotFoundError(
        f"Cannot find project root (no {DARK_FACTORY_DIR}/ or .git/) "
        f"starting from {current}"
    )


def spec_path(root: Path, spec_name: str) -> Path:
    return root / SPECS_DIR / f"{spec_name}.md"


def scenarios_dir(root: Path, spec_name: str) -> Path:
    return root / SCENARIOS_DIR / spec_name


def review_path(root: Path, spec_name: str) -> Path:
    return root / REVIEWS_DIR / f"{spec_name}.md"


def report_path(root: Path, spec_name: str) -> Path:
    return root / REPORTS_DIR / f"{spec_name}.md"


def state_path(root: Path, spec_name: str) -> Path:
    return root / STATE_DIR / f"{spec_name}.state"


def feedback_path(root: Path, spec_name: str, iteration: int) -> Path:
    return root / REVIEWS_DIR / f"{spec_name}-feedback-iter{iteration}.md"


def audit_log_path(root: Path) -> Path:
    return root / AUDIT_DIR / "audit.log"


def events_path(root: Path) -> Path:
    return root / EVENTS_FILE


def metrics_path(root: Path) -> Path:
    return root / METRICS_FILE
