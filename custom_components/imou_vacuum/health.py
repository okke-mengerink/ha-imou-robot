"""Tolerance for failed polls, so that one hiccup does not hide the robot.

Home Assistant free: it only needs ``const``.
"""

from __future__ import annotations

from collections.abc import Callable
import time

from .const import (
    FAILURES_BEFORE_UNAVAILABLE,
    MAX_STALE_SECONDS,
    RETRY_INTERVAL,
    SCAN_INTERVAL_IDLE,
)


class PollHealth:
    """Count consecutive failed polls.

    A failed poll (API error, timeout, network) and a robot that the cloud calls
    "offline" both count. The last good data may be shown until
    ``FAILURES_BEFORE_UNAVAILABLE`` polls failed in a row, or the last good poll is
    older than ``MAX_STALE_SECONDS``, whichever comes first.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Start without a good poll."""
        self._clock = clock
        self.failures = 0
        self.last_success: float | None = None

    def success(self) -> int:
        """Record a good poll; return how many failed polls came before it."""
        failed = self.failures
        self.failures = 0
        self.last_success = self._clock()
        return failed

    def failure(self) -> bool:
        """Record a failed poll; return True while the last good data may still be shown."""
        self.failures += 1
        if self.last_success is None:
            return False  # there is no good data to show yet
        stale = self._clock() - self.last_success
        return (
            self.failures < FAILURES_BEFORE_UNAVAILABLE and stale < MAX_STALE_SECONDS
        )

    def retry_interval(self, current: float) -> float:
        """Seconds until the next poll after a failure: soon first, then backing off.

        ``current`` is the interval that was in force (30 s while active, so that
        a retry is never later than a normal poll). Once the failures make the
        entities unavailable the interval doubles up to the idle interval, so a
        long outage does not eat the API quota.
        """
        if self.failures < FAILURES_BEFORE_UNAVAILABLE:
            return min(float(RETRY_INTERVAL), current)
        steps = self.failures - FAILURES_BEFORE_UNAVAILABLE + 1
        return min(float(SCAN_INTERVAL_IDLE), RETRY_INTERVAL * 2.0**steps)
