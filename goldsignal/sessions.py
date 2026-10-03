"""Handelszeiten, Sessions, Rollover- und Wochenend-Risiko."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
LDN = ZoneInfo("Europe/London")

MAIN_SESSIONS = ("overlap", "london", "newyork")


def session_of(t: datetime) -> str:
    """overlap = London & New York gleichzeitig offen; asia = alles andere (inkl. Nacht)."""
    ldn = t.astimezone(LDN)
    ny = t.astimezone(NY)
    in_ldn = ldn.weekday() < 5 and time(8, 0) <= ldn.time() < time(17, 0)
    in_ny = ny.weekday() < 5 and time(8, 0) <= ny.time() < time(17, 0)
    if in_ldn and in_ny:
        return "overlap"
    if in_ldn:
        return "london"
    if in_ny:
        return "newyork"
    return "asia"


SESSION_LABEL = {"overlap": "London/NY-Überschneidung", "london": "London", "newyork": "New York", "asia": "Asien/Nacht"}


def is_market_open(t: datetime) -> bool:
    """Gold bei OANDA: So 18:00 – Fr 17:00 New York, täglich Pause 17:00–18:00 NY (≈ 23–24 Uhr MEZ)."""
    ny = t.astimezone(NY)
    wd, tm = ny.weekday(), ny.time()
    if wd == 5:
        return False
    if wd == 4 and tm >= time(17, 0):
        return False
    if wd == 6 and tm < time(18, 0):
        return False
    if time(17, 0) <= tm < time(18, 0):
        return False
    return True


def next_rollover(t: datetime) -> datetime:
    ny = t.astimezone(NY)
    ro = ny.replace(hour=17, minute=0, second=0, microsecond=0)
    if ro <= ny:
        ro += timedelta(days=1)
    return ro.astimezone(timezone.utc)


def week_close(t: datetime) -> datetime:
    ny = t.astimezone(NY)
    days = (4 - ny.weekday()) % 7
    fc = (ny + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0)
    if fc <= ny:
        fc += timedelta(days=7)
    return fc.astimezone(timezone.utc)


def risk_flags(entry_time: datetime, expires_at: datetime) -> list[str]:
    flags = []
    if next_rollover(entry_time) <= expires_at:
        flags.append("Rollover (~23 Uhr MEZ) im Haltezeitraum: Spread weitet sich, Finanzierungskosten")
    if week_close(entry_time) <= expires_at + timedelta(hours=2):
        flags.append("Nahe Wochenschluss: Gap-Risiko übers Wochenende")
    return flags


def in_quiet_hours(t: datetime, quiet: str, tz: str) -> bool:
    if not quiet or "-" not in quiet:
        return False
    a, b = (x.strip() for x in quiet.split("-", 1))
    start = time.fromisoformat(a)
    end = time.fromisoformat(b)
    lt = t.astimezone(ZoneInfo(tz)).time()
    if start <= end:
        return start <= lt < end
    return lt >= start or lt < end
