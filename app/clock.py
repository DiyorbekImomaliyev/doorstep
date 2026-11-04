"""Injectable clock so retries, caps and token expiry are testable."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC wall-clock time."""
        ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin; never goes backwards."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()
