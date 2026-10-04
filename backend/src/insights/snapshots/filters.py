"""Row filters and cursor syntax for paginated PR rows."""

import re
from dataclasses import asdict, dataclass

CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


@dataclass(frozen=True, slots=True)
class PrFilters:
    status: str = "merged"
    at_risk: bool = False
    state: str | None = None
    location: str | None = None

    def canonical_dict(self) -> dict[str, object]:
        """Serialize all filter fields for stable cursor identity checks."""
        return asdict(self)
