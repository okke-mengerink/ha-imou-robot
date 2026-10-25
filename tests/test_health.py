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


def test_the_rules_are_three_polls_fifteen_minutes_sixty_seconds() -> None:
    assert const.FAILURES_BEFORE_UNAVAILABLE == 3
    assert const.MAX_STALE_SECONDS == 15 * 60
    assert const.RETRY_INTERVAL == 60


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


def test_a_last_success_older_than_15_minutes_is_unavailable_at_once() -> None:
    health, clock = make()
    health.success()
    clock.now += 15 * 60 - 1
    assert health.failure() is True  # just under 15 minutes
    health.success()
    clock.now += 15 * 60
    assert health.failure() is False  # 15 minutes: too old, even for the first failure
    assert health.failures == 1


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


def test_a_long_outage_backs_off_to_the_idle_interval() -> None:
    health, _ = make()
    health.success()
    seen = []
    for _ in range(6):
        health.failure()
        seen.append(health.retry_interval(60))
    assert seen == [60, 60, 120, 240, 300, 300]


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
