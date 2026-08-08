from datetime import datetime

from orchestration.rd_campaign import hours, segment_model_end, segments


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


def test_segment_model_end_adds_one_overlap_hour_except_at_case_end() -> None:
    case_end = datetime(2020, 1, 2)
    assert segment_model_end(datetime(2020, 1, 1, 12), case_end) == datetime(2020, 1, 1, 13)
    assert segment_model_end(case_end, case_end) == case_end
