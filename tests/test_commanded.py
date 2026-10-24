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


def test_window_is_the_command_boost() -> None:
    const = load("const")
    value = make()
    value.set(1)
    left = value._until - commanded_mod.time.monotonic()
    assert abs(left - const.COMMAND_BOOST_SECONDS) < 2


if __name__ == "__main__":
    raise SystemExit(run_all(globals()))
