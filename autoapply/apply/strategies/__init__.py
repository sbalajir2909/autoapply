"""ATS-specific form filling strategies."""

from .greenhouse import GreenhouseStrategy
from .lever import LeverStrategy
from .workday import WorkdayStrategy
from .generic import GenericStrategy

STRATEGY_MAP = {
    "greenhouse": GreenhouseStrategy,
    "lever": LeverStrategy,
    "workday": WorkdayStrategy,
}

def get_strategy(ats_platform: str):
    """Return the appropriate strategy class for an ATS platform."""
    return STRATEGY_MAP.get(ats_platform.lower(), GenericStrategy)

__all__ = [
    "GreenhouseStrategy",
    "LeverStrategy",
    "WorkdayStrategy",
    "GenericStrategy",
    "get_strategy",
]
