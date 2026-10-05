"""RFC 6901 JSON Pointer lookup used to resolve narrative evidence references."""

from typing import Any


def resolve_pointer(payload: Any, pointer: str) -> Any:
    """Resolve a JSON pointer with ~0/~1 decoding; propagate missing keys or invalid indexes."""
    current = payload
    for part in pointer.lstrip("/").split("/") if pointer else []:
        key = part.replace("~1", "/").replace("~0", "~")
        current = current[int(key)] if isinstance(current, list) else current[key]
    return current
