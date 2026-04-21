"""Tests for the minimal crontab-style schedule evaluator."""

from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture
def is_due():
    from prax.core.cron_store import is_due as impl
    return impl


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


# ── Star fields ──────────────────────────────────────────────────────────────


def test_all_stars_matches_every_minute(is_due):
    for s in ("2026-04-22 00:00", "2026-04-22 13:37", "2026-12-31 23:59"):
        assert is_due("* * * * *", dt(s))


def test_minute_literal_match(is_due):
    assert is_due("17 * * * *", dt("2026-04-22 13:17"))
    assert not is_due("17 * * * *", dt("2026-04-22 13:18"))


def test_hour_literal_match(is_due):
    assert is_due("0 17 * * *", dt("2026-04-22 17:00"))
    assert not is_due("0 17 * * *", dt("2026-04-22 16:00"))
    assert not is_due("0 17 * * *", dt("2026-04-22 17:01"))


# ── Range / step / list ─────────────────────────────────────────────────────


def test_step_every_5_minutes(is_due):
    assert is_due("*/5 * * * *", dt("2026-04-22 12:00"))
    assert is_due("*/5 * * * *", dt("2026-04-22 12:05"))
    assert not is_due("*/5 * * * *", dt("2026-04-22 12:03"))


def test_range_in_hour(is_due):
    # 9 AM through 5 PM inclusive
    assert is_due("0 9-17 * * *", dt("2026-04-22 09:00"))
    assert is_due("0 9-17 * * *", dt("2026-04-22 17:00"))
    assert not is_due("0 9-17 * * *", dt("2026-04-22 18:00"))


def test_list_of_hours(is_due):
    assert is_due("0 9,12,17 * * *", dt("2026-04-22 09:00"))
    assert is_due("0 9,12,17 * * *", dt("2026-04-22 12:00"))
    assert is_due("0 9,12,17 * * *", dt("2026-04-22 17:00"))
    assert not is_due("0 9,12,17 * * *", dt("2026-04-22 10:00"))


def test_range_with_step(is_due):
    # 0, 10, 20 only
    assert is_due("0-20/10 * * * *", dt("2026-04-22 10:00"))
    assert is_due("0-20/10 * * * *", dt("2026-04-22 10:10"))
    assert is_due("0-20/10 * * * *", dt("2026-04-22 10:20"))
    assert not is_due("0-20/10 * * * *", dt("2026-04-22 10:05"))
    assert not is_due("0-20/10 * * * *", dt("2026-04-22 10:30"))


# ── Day-of-week / day-of-month ──────────────────────────────────────────────


def test_day_of_week_sunday_accepts_0_and_7(is_due):
    # 2026-04-26 is a Sunday
    assert is_due("0 9 * * 0", dt("2026-04-26 09:00"))
    assert is_due("0 9 * * 7", dt("2026-04-26 09:00"))


def test_day_of_week_weekdays(is_due):
    # Mon-Fri at 9 — 2026-04-22 is Wednesday
    assert is_due("0 9 * * 1-5", dt("2026-04-22 09:00"))
    # 2026-04-25 is Saturday
    assert not is_due("0 9 * * 1-5", dt("2026-04-25 09:00"))


def test_day_of_month_match(is_due):
    assert is_due("0 0 1 * *", dt("2026-04-01 00:00"))
    assert not is_due("0 0 1 * *", dt("2026-04-02 00:00"))


def test_month_match(is_due):
    assert is_due("0 0 1 1 *", dt("2026-01-01 00:00"))
    assert not is_due("0 0 1 1 *", dt("2026-02-01 00:00"))


def test_dom_and_dow_are_or_when_both_constrained(is_due):
    """Classic cron: when BOTH dom and dow are restricted, match if EITHER matches.

    dom=15 or dow=Mon — 2026-04-13 is a Monday but not the 15th; 2026-04-15 is Wed.
    Both should fire; 2026-04-14 (Tue, not 15th) should not.
    """
    # 2026-04-13 (Monday)
    assert is_due("0 0 15 * 1", dt("2026-04-13 00:00"))
    # 2026-04-15 (Wed, 15th of month)
    assert is_due("0 0 15 * 1", dt("2026-04-15 00:00"))
    # 2026-04-14 (Tuesday, not 15th)
    assert not is_due("0 0 15 * 1", dt("2026-04-14 00:00"))


def test_dom_star_and_dow_constrained_matches_any_day_of_week(is_due):
    """If dom is * and dow is Mon, match all Mondays."""
    # 2026-04-13 is Monday
    assert is_due("0 0 * * 1", dt("2026-04-13 00:00"))
    # 2026-04-14 Tuesday
    assert not is_due("0 0 * * 1", dt("2026-04-14 00:00"))


# ── Error handling ──────────────────────────────────────────────────────────


def test_rejects_non_five_fields(is_due):
    from prax.core.cron_store import InvalidScheduleError
    with pytest.raises(InvalidScheduleError):
        is_due("* * * *", dt("2026-04-22 00:00"))
    with pytest.raises(InvalidScheduleError):
        is_due("1 2 3 4 5 6", dt("2026-04-22 00:00"))


def test_rejects_out_of_range_minute(is_due):
    from prax.core.cron_store import InvalidScheduleError
    with pytest.raises(InvalidScheduleError):
        is_due("60 * * * *", dt("2026-04-22 00:00"))


def test_rejects_malformed_step(is_due):
    from prax.core.cron_store import InvalidScheduleError
    with pytest.raises(InvalidScheduleError):
        is_due("*/0 * * * *", dt("2026-04-22 00:00"))


def test_rejects_reversed_range(is_due):
    from prax.core.cron_store import InvalidScheduleError
    with pytest.raises(InvalidScheduleError):
        is_due("30-10 * * * *", dt("2026-04-22 00:00"))


# ── Validator helper ────────────────────────────────────────────────────────


def test_validate_schedule_accepts_good_exprs():
    from prax.core.cron_store import validate_schedule
    for expr in ("* * * * *", "0 17 * * *", "*/5 9-17 * * 1-5", "0 0 1,15 * *"):
        validate_schedule(expr)  # raises on invalid, nothing returned


def test_validate_schedule_rejects_bad_exprs():
    from prax.core.cron_store import InvalidScheduleError, validate_schedule
    for expr in ("", "* * *", "60 * * * *", "*/0 * * * *", "a b c d e"):
        with pytest.raises(InvalidScheduleError):
            validate_schedule(expr)
