"""Plain-Python tests: tolerance for failed polls.

Run: python3 -I tests/test_health.py
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pure import load, run_all  # noqa: E402

const = load("const")
health_mod = load("health")


class Clock:
    """A clock that only moves when the test says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make() -> tuple[object, Clock]:
    clock = Clock()
    return health_mod.PollHealth(clock), clock


def test_the_rules_are_three_polls_thirty_minutes_sixty_seconds() -> None:
    assert const.FAILURES_BEFORE_UNAVAILABLE == 3
    assert const.MAX_STALE_SECONDS == 30 * 60
    assert const.RETRY_INTERVAL == 60
    # the stale limit must leave room for a failure after a resting interval and the
    # retries after it, or one failed poll of a resting robot hides it at once
    assert const.MAX_STALE_SECONDS > const.SCAN_INTERVAL_RESTING + 2 * const.RETRY_INTERVAL


def test_a_single_failed_poll_keeps_the_last_data() -> None:
    health, _ = make()
    health.success()
    assert health.failure() is True
    assert health.failures == 1


def test_three_failed_polls_in_a_row_make_the_entities_unavailable() -> None:
    health, clock = make()
    health.success()
    results = []
    for _ in range(5):
        clock.now += 60
        results.append(health.failure())
    assert results == [True, True, False, False, False]


def test_a_good_poll_resets_the_count() -> None:
    health, clock = make()
    health.success()
    assert health.failure() is True
    assert health.failure() is True
    assert health.success() == 2  # two failed polls came before it
    assert health.failures == 0
    assert health.failure() is True and health.failure() is True  # counting starts again


def test_a_last_success_older_than_30_minutes_is_unavailable_at_once() -> None:
    health, clock = make()
    health.success()
    clock.now += 30 * 60 - 1
    assert health.failure() is True  # just under 30 minutes
    health.success()
    clock.now += 30 * 60
    assert health.failure() is False  # 30 minutes: too old, even for the first failure
    assert health.failures == 1


def test_a_resting_robot_survives_one_failed_poll() -> None:
    health, clock = make()
    health.success()
    clock.now += const.SCAN_INTERVAL_RESTING  # the next poll of a resting robot fails
    assert health.failure() is True
    clock.now += health.retry_interval(const.SCAN_INTERVAL_RESTING)
    assert health.failure() is True  # and its retry: still the last data
    clock.now += health.retry_interval(const.SCAN_INTERVAL_RESTING)
    assert health.failure() is False  # the third failure in a row


def test_without_a_good_poll_there_is_nothing_to_keep() -> None:
    health, _ = make()
    assert health.failure() is False
    assert health.failure() is False


def test_retry_comes_after_60_seconds_not_300() -> None:
    health, _ = make()
    health.success()
    health.failure()
    assert health.retry_interval(300) == 60  # idle robot
    assert health.retry_interval(30) == 30  # active robot: never later than a normal poll
    assert health.retry_interval(60) == 60
    health.failure()
    assert health.retry_interval(60) == 60


def test_a_long_outage_backs_off_to_the_resting_interval() -> None:
    health, _ = make()
    health.success()
    seen = []
    for _ in range(7):
        health.failure()
        seen.append(health.retry_interval(60))
    assert seen == [60, 60, 120, 240, 480, 900, 900]


def test_success_reports_how_many_polls_failed_before_it() -> None:
    health, _ = make()
    assert health.success() == 0
    health.failure()
    health.failure()
    health.failure()
    assert health.success() == 3
    assert health.success() == 0


if __name__ == "__main__":
    raise SystemExit(run_all(globals()))
