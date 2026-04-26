"""Time helpers.

`iso_now` returns a tz-aware UTC ISO 8601 string suitable for storing in TEXT
columns. Tests override `now_provider` to freeze time.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

NowProvider = Callable[[], datetime]


def _default_now() -> datetime:
    return datetime.now(tz=UTC)


_now_provider: NowProvider = _default_now


def set_now_provider(provider: NowProvider) -> None:
    """Test hook. Production code never calls this."""
    global _now_provider  # noqa: PLW0603
    _now_provider = provider


def reset_now_provider() -> None:
    set_now_provider(_default_now)


def now() -> datetime:
    return _now_provider()


def iso_now() -> str:
    return now().isoformat()


def parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string. Naive inputs are assumed to be UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
