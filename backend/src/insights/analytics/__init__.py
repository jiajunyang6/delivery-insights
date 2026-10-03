from insights.analytics.thresholds import THRESHOLDS_VERSION

ANALYTICS_VERSION = "1.4.0"


def derive_key(location_dimension: str, directory_depth: int) -> str:
    return f"{ANALYTICS_VERSION}|{THRESHOLDS_VERSION}|{location_dimension}|depth={directory_depth}"
