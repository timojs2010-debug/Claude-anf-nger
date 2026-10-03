"""Wirtschaftskalender: Fed-Sitzungen (FOMC), US-CPI, Arbeitsmarktbericht (NFP).

Quellen (in dieser Reihenfolge zusammengeführt):
1. Fest hinterlegte FOMC-Entscheidungstermine (14:00 New Yorker Zeit).
2. Regel für den Arbeitsmarktbericht: erster Freitag im Monat, 08:30 New York
   (Näherung – das Arbeitsministerium verschiebt ihn gelegentlich).
3. Live-Wochenkalender von faireconomy.media (ForexFactory-Export), nur USD + hohe Wichtigkeit.
   Diese Termine werden in der Datenbank gespeichert, damit spätere Backtests sie kennen.
4. Eigene Termine aus data/events.csv (Spalten: time_utc,title[,impact]).
"""
from __future__ import annotations

import csv
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")

# FOMC-Zinsentscheide (Tag der Bekanntgabe). Bei Bedarf in data/events.csv ergänzen.
FOMC_DATES = [
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
IMPORTANT_KEYWORDS = ("CPI", "Non-Farm", "Nonfarm", "FOMC", "Federal Funds", "Fed Chair", "PCE", "GDP",
                      "Unemployment Rate", "Retail Sales", "PPI", "ISM")


def _ny(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=NY).astimezone(timezone.utc)


def builtin_events(start: datetime, end: datetime) -> list[dict]:
    events = []
    for ds in FOMC_DATES:
        d = date.fromisoformat(ds)
        t = _ny(d, 14, 0)
        if start <= t <= end:
            events.append({"time": t, "title": "FOMC Zinsentscheid", "impact": "High", "currency": "USD", "source": "builtin"})
    # NFP: erster Freitag im Monat
    m = date(start.year, start.month, 1)
    while m <= end.date():
        d = m
        while d.weekday() != 4:
            d += timedelta(days=1)
        t = _ny(d, 8, 30)
        if start <= t <= end:
            events.append({"time": t, "title": "US-Arbeitsmarktbericht (NFP, Regel)", "impact": "High",
                           "currency": "USD", "source": "rule"})
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return events


def csv_events(path: str, start: datetime, end: datetime) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with p.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                t = datetime.fromisoformat(row["time_utc"].strip().replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if start <= t <= end:
                out.append({"time": t.astimezone(timezone.utc), "title": row.get("title", "Termin").strip(),
                            "impact": row.get("impact", "High"), "currency": "USD", "source": "csv"})
    return out


def fetch_live_week(timeout: float = 15.0) -> list[dict]:
    resp = requests.get(FF_URL, timeout=timeout, headers={"User-Agent": "goldsignal/0.1"})
    resp.raise_for_status()
    out = []
    for e in resp.json():
        if e.get("country") != "USD" or e.get("impact") != "High":
            continue
        try:
            t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({"time": t, "title": e.get("title", "USD-Termin"), "impact": "High", "currency": "USD", "source": "forexfactory"})
    return out


def merge(events: list[dict]) -> list[dict]:
    """Doppelte Termine (gleiche Stunde, ähnlicher Typ) zusammenfassen."""
    events = sorted(events, key=lambda e: e["time"])
    out: list[dict] = []
    for e in events:
        if out and abs((e["time"] - out[-1]["time"]).total_seconds()) < 1800:
            if out[-1]["source"] in ("rule", "builtin") and e["source"] not in ("rule", "builtin"):
                out[-1] = e  # genauere Quelle bevorzugen
            continue
        out.append(e)
    return out


def is_blocked(t: datetime, events: list[dict], hours: float) -> dict | None:
    """Liefert den Termin, wenn t innerhalb ±hours um einen wichtigen Termin liegt."""
    for e in events:
        if abs((e["time"] - t).total_seconds()) <= hours * 3600:
            return e
    return None


def upcoming(t: datetime, events: list[dict], hours: float = 24) -> list[dict]:
    return [e for e in events if t <= e["time"] <= t + timedelta(hours=hours)]
