"""Exception hierarchy with defined catch points."""


class DarkFactoryError(Exception):
    """Base exception for all Dark Factory errors.

    ``run_id`` is an optional structured hint carried from pipeline
    emit-then-raise sites. When set, the CLI top-level catch looks up the
    matching REMEDIATION_TARGET in ``.dark-factory/events.jsonl`` and
    appends a "To fix: ..." next-command hint after the error message
    (C3). Sites that don't have a run_id (CLI-level ConfigError, helper
    validation) leave it empty and the hint is skipped.
    """

    def __init__(self, *args: object, run_id: str = "") -> None:
        super().__init__(*args)
        self.run_id = run_id


class ConfigError(DarkFactoryError):
    """Invalid or missing configuration."""


class BarrierViolation(DarkFactoryError):
    """Information barrier was violated."""


class AgentTimeout(DarkFactoryError):
    """Agent execution exceeded time limit."""


class BudgetExceeded(DarkFactoryError):
    """Cost budget has been exceeded."""


class GateFailed(DarkFactoryError):
    """Quality gate check failed."""


class HardAbort(DarkFactoryError):
    """Immediate pipeline abort (hard gate failure, anomaly, etc.)."""


class HumanHoldRequired(DarkFactoryError):
    """Pipeline paused — human confirmation required before ship (autonomy level 1)."""


class TransientFailure(DarkFactoryError):
    """Infrastructure failure (OOM, network timeout, Docker daemon) — retryable."""
