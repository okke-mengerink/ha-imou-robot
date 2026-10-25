"""A value commanded from Home Assistant, shown until the cloud confirms it.

Home Assistant free: it only needs ``const``.
"""

from __future__ import annotations

import time

from .const import COMMAND_GRACE_SECONDS


class CommandedValue[T]:
    """Remember the last value commanded for a setting.

    A commanded value is shown while the cloud has not reported that setting at
    all (some robots never report auto-empty or resume-after-charging), and for at
    most ``COMMAND_GRACE_SECONDS`` after the command while the cloud still reports
    the old value (it can lag by a few seconds). After that a reported value
    always wins, so a command that the robot ignores is visible instead of being
    masked.

    One instance per setting lives on the coordinator, so every entity that
    shows the same setting (for example the fan speed of the vacuum and the
    suction select) sees the same value.
    """

    def __init__(self) -> None:
        """Initialize without a commanded value."""
        self._value: T | None = None
        self._until = 0.0

    def set(self, value: T) -> None:
        """Remember a value that was just commanded."""
        self._value = value
        self._until = time.monotonic() + COMMAND_GRACE_SECONDS

    def resolve(self, reported: T | None) -> T | None:
        """Return what to show, given the value reported by the cloud."""
        if reported is None:
            return self._value
        if (
            self._value is not None
            and reported != self._value
            and time.monotonic() < self._until
        ):
            return self._value
        self._value = None  # confirmed by the cloud, or the grace period is over
        return reported
