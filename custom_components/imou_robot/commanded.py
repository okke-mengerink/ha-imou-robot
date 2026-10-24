"""A value commanded from Home Assistant, shown until the cloud confirms it.

Home Assistant free: it only needs ``const``.
"""

from __future__ import annotations

import time

from .const import COMMAND_BOOST_SECONDS


class CommandedValue[T]:
    """Remember the last value commanded for a setting.

    The cloud may lag behind a command, or may not report a setting at all until
    it was changed once. The commanded value is therefore shown while the cloud
    is silent, and while it still reports the old value within
    ``COMMAND_BOOST_SECONDS`` of the command. After that the cloud wins, so a
    rejected command cannot leave a wrong state behind.

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
        self._until = time.monotonic() + COMMAND_BOOST_SECONDS

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
        self._value = None  # confirmed by the cloud, or the command window is over
        return reported
