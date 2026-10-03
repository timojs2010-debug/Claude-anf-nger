from datetime import datetime, timezone

from goldsignal.sessions import in_quiet_hours, is_market_open, risk_flags, session_of
from goldsignal.sources import calendar as cal
from goldsignal.sources import news


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_sessions():
    assert session_of(utc(2025, 3, 18, 14, 0)) == "overlap"   # 14 UTC = 14 London (GMT), 10 NY (EDT)
    assert session_of(utc(2025, 3, 18, 9, 0)) == "london"
    assert session_of(utc(2025, 3, 18, 19, 0)) == "newyork"
    assert session_of(utc(2025, 3, 18, 2, 0)) == "asia"


def test_market_hours():
    assert not is_market_open(utc(2025, 3, 15, 12))          # Samstag
    assert is_market_open(utc(2025, 3, 18, 12))
    assert not is_market_open(utc(2025, 3, 18, 21, 30))      # tägliche Pause 17-18 NY (EDT)


def test_quiet_hours_over_midnight():
    assert in_quiet_hours(utc(2025, 3, 18, 23, 30), "23:00-07:00", "UTC")
    assert in_quiet_hours(utc(2025, 3, 18, 5, 0), "23:00-07:00", "UTC")
    assert not in_quiet_hours(utc(2025, 3, 18, 12, 0), "23:00-07:00", "UTC")


def test_rollover_and_weekend_flags():
    f = risk_flags(utc(2025, 3, 18, 19, 0), utc(2025, 3, 18, 23, 0))
    assert any("Rollover" in x for x in f)
    f2 = risk_flags(utc(2025, 3, 21, 18, 0), utc(2025, 3, 21, 22, 0))
    assert any("Wochenschluss" in x for x in f2)
    assert risk_flags(utc(2025, 3, 18, 8, 0), utc(2025, 3, 18, 12, 0)) == []


def test_nfp_rule_and_block():
    ev = cal.builtin_events(utc(2025, 3, 1), utc(2025, 3, 31))
    nfp = [e for e in ev if "NFP" in e["title"]]
    assert nfp and nfp[0]["time"] == utc(2025, 3, 7, 13, 30)
    fomc = [e for e in ev if "FOMC" in e["title"]]
    assert fomc and fomc[0]["time"] == utc(2025, 3, 19, 18, 0)
    assert cal.is_blocked(utc(2025, 3, 7, 12, 30), ev, 1.5)
    assert cal.is_blocked(utc(2025, 3, 7, 11, 0), ev, 1.5) is None


def test_news_scoring():
    assert news.score_headline("Gold rises to record high as dollar weakens") > 0
    assert news.score_headline("Gold falls as Fed signals rate hikes") < 0
    assert news.label(0.3) == "bullisch" and news.label(-0.3) == "bärisch" and news.label(None) == "unbekannt"
