"""Plain-Python tests: the commanded-value memory shared by entities.

Run: python3 -I tests/test_commanded.py
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pure import load, run_all  # noqa: E402

commanded_mod = load("commanded")


def make() -> object:
    return commanded_mod.CommandedValue()


def test_nothing_commanded_shows_the_reported_value() -> None:
    value = make()
    assert value.resolve(5) == 5
    assert value.resolve(None) is None


def test_silent_cloud_keeps_the_commanded_value() -> None:
    value = make()
    value.set(7)
    assert value.resolve(None) == 7
    assert value.resolve(None) == 7  # reading does not consume it


def test_stale_report_inside_the_window_shows_the_commanded_value() -> None:
    value = make()
    value.set(7)
    assert value.resolve(5) == 7


def test_after_the_window_the_cloud_wins() -> None:
    value = make()
    value.set(7)
    value._until = 0.0  # the command window is over (a rejected command)
    assert value.resolve(5) == 5
    assert value.resolve(None) is None  # and the commanded value is forgotten


def test_confirmation_clears_the_memory() -> None:
    value = make()
    value.set(7)
    assert value.resolve(7) == 7
    assert value.resolve(None) is None
    assert value.resolve(3) == 3  # a later change from the app is shown right away


def test_falsy_values_work() -> None:
    value = make()
    value.set(0)
    assert value.resolve(None) == 0
    assert value.resolve(3) == 0
    flag = make()
    flag.set(False)
    assert flag.resolve(None) is False
    assert flag.resolve(True) is False


class FakeClock:
    """Stands in for the ``time`` module inside commanded.py."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def with_clock(test: object) -> None:
    clock = FakeClock()
    real_time = commanded_mod.time
    commanded_mod.time = clock
    try:
        test(clock)
    finally:
        commanded_mod.time = real_time


def test_grace_period_is_30_seconds() -> None:
    const = load("const")
    assert const.COMMAND_GRACE_SECONDS == 30

    def check(clock: FakeClock) -> None:
        value = make()
        value.set(1)
        assert abs(value._until - (clock.now + const.COMMAND_GRACE_SECONDS)) < 0.001

    with_clock(check)


def test_reported_value_overrides_a_stale_commanded_one_after_the_grace_period() -> None:
    def check(clock: FakeClock) -> None:
        value = make()
        value.set(1)  # commanded 1; the robot ignores it and keeps reporting 2
        clock.now += 29
        assert value.resolve(2) == 1  # inside the grace period the cloud may still lag
        clock.now += 2  # 31 s after the command
        assert value.resolve(2) == 2  # the stale commanded value no longer masks the cloud
        assert value.resolve(2) == 2
        assert value.resolve(None) is None  # and it is forgotten

    with_clock(check)


def test_settings_the_cloud_never_reports_keep_the_commanded_value() -> None:
    def check(clock: FakeClock) -> None:
        value = make()
        value.set(True)  # e.g. auto-empty: the robot never reports it
        clock.now += 10_000
        assert value.resolve(None) is True

    with_clock(check)


if __name__ == "__main__":
    raise SystemExit(run_all(globals()))
