"""Analytics package versioning; analytics modules are pure computation with no DB or HTTP I/O."""

from insights.analytics.thresholds import THRESHOLDS_VERSION

ANALYTICS_VERSION = "1.7.0"


def derive_key(location_dimension: str, directory_depth: int) -> str:
    """Key stamped on derived PR facts; a repo whose key differs is pending a rederive."""
    return f"{ANALYTICS_VERSION}|{THRESHOLDS_VERSION}|{location_dimension}|depth={directory_depth}"
