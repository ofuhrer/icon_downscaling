from datetime import datetime

from orchestration.rd_campaign import hours, segments


def test_hours_bracket_off_hour_segment() -> None:
    assert list(hours(
        datetime(2020, 2, 10, 1, 30),
        datetime(2020, 2, 10, 2, 0),
    )) == [
        datetime(2020, 2, 10, 1, 0),
        datetime(2020, 2, 10, 2, 0),
    ]


def test_fractional_segment_length() -> None:
    assert list(segments(
        datetime(2020, 2, 10, 0, 0),
        datetime(2020, 2, 10, 2, 0),
        1.5,
    )) == [
        (datetime(2020, 2, 10, 0, 0), datetime(2020, 2, 10, 1, 30)),
        (datetime(2020, 2, 10, 1, 30), datetime(2020, 2, 10, 2, 0)),
    ]
