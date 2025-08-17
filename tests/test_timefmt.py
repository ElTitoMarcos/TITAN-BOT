from utils.timefmt import fmt_ts


def test_fmt_ts_seconds():
    # 2024-01-15 12:34:56 UTC -> 2024-01-15 13:34:56 Europe/Madrid
    assert fmt_ts(1705322096) == "2024-01-15 13:34:56"


def test_fmt_ts_milliseconds():
    assert fmt_ts(1705322096000) == "2024-01-15 13:34:56"


def test_fmt_ts_iso():
    assert fmt_ts("2024-07-15T15:20:30Z") == "2024-07-15 17:20:30"
