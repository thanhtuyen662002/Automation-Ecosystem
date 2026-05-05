from __future__ import annotations


def calculate_retry_delay_seconds(
    attempt: int, base_delay_seconds: int, max_delay_seconds: int
) -> int:
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if base_delay_seconds < 0:
        raise ValueError("base_delay_seconds must be >= 0")
    if max_delay_seconds < 0:
        raise ValueError("max_delay_seconds must be >= 0")
    if base_delay_seconds == 0 or max_delay_seconds == 0:
        return 0
    delay = base_delay_seconds * (2 ** (attempt - 1))
    return min(delay, max_delay_seconds)

