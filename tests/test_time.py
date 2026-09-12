from datetime import datetime, timezone

import pytest

from lfg.commands import LFGTimeError, parse_session_time


NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def test_relative_time_uses_a_single_absolute_reference():
    timestamp, _ = parse_session_time("in 2 hours", "Asia/Jakarta", now=NOW)
    assert timestamp == int(NOW.timestamp()) + 7200


@pytest.mark.parametrize("value", ["2020-01-01 12:00", "yesterday", "garbage", ""])
def test_invalid_and_past_times_are_rejected(value):
    with pytest.raises(LFGTimeError):
        parse_session_time(value, "Asia/Jakarta", now=NOW)


@pytest.mark.parametrize("value", ["2027-03-14 02:30", "2026-11-01 01:30"])
def test_dst_gap_and_fold_require_clarification(value):
    with pytest.raises(LFGTimeError):
        parse_session_time(value, "America/New_York", now=NOW)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-01T01:30:00-04:00", datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)),
        ("2026-11-01T01:30:00-05:00", datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)),
    ],
)
def test_explicit_offset_resolves_each_dst_fold(value, expected):
    timestamp, _ = parse_session_time(value, "America/New_York", now=NOW)
    assert timestamp == int(expected.timestamp())


def test_explicit_offset_cannot_make_a_dst_gap_valid():
    with pytest.raises(LFGTimeError, match="does not exist"):
        parse_session_time(
            "2027-03-14T02:30:00-05:00", "America/New_York", now=NOW
        )


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
        datetime(2027, 3, 14, 6, 30, tzinfo=timezone.utc),
    ],
)
def test_relative_duration_is_absolute_across_dst_transition(now):
    timestamp, _ = parse_session_time("in 2 hours", "America/New_York", now=now)
    assert timestamp == int(now.timestamp()) + 2 * 60 * 60


def test_absolute_local_time_is_correct():
    timestamp, _ = parse_session_time("2026-09-13 20:00", "Asia/Jakarta", now=NOW)
    assert timestamp == int(datetime(2026, 9, 13, 13, tzinfo=timezone.utc).timestamp())
