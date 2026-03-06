"""Exception hierarchy with defined catch points."""


class DarkFactoryError(Exception):
    """Base exception for all Dark Factory errors."""


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
