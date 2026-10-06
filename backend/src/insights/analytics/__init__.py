"""Analytics versions and shared thresholds; analytics modules do no DB or HTTP I/O."""

ANALYTICS_VERSION = "1.9.0"
# Feeds snapshot IDs, bootstrap seeds and the derive key; bump it with any threshold change.
THRESHOLDS_VERSION = "1.0.0"

MIN_SAMPLES_P50 = 20
MIN_SAMPLES_LOCATION_P50 = 10
MIN_SAMPLES_WEEKLY_P50 = 5
MIN_RATE_DENOMINATOR = 30
MIN_RATE_EVENTS = 5
CHANGE_MIN_RELATIVE = 0.10
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_CI = 0.90

MIN_LOCATION_PRS = 10
MAX_LOCATIONS_IN_SNAPSHOT = 15
DIRECTORY_LOCATIONS_PER_PR = 3

REVIEW_CONCENTRATION_TOP_K = 2
LARGE_PR_LINES = 500
REVIEW_CAPACITY_MIN_WAIT_SHARE = 0.15


def derive_key(location_dimension: str, directory_depth: int) -> str:
    """Key stamped on derived PR facts; a repo whose key differs is pending a rederive."""
    return f"{ANALYTICS_VERSION}|{THRESHOLDS_VERSION}|{location_dimension}|depth={directory_depth}"
