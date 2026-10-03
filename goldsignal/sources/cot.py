"""CFTC Commitments of Traders (wöchentlich): Positionierung der Hedgefonds ("Managed Money") bei Gold.

Quelle: CFTC Public Reporting (Socrata), Datensatz "Disaggregated Futures Only" (72hh-3qpy),
Gold COMEX = Kontraktcode 088691. Der Bericht bezieht sich auf Dienstag und erscheint am Freitag –
im Backtest wird er daher erst ab Freitagabend verwendet (kein Blick in die Zukunft).
"""
from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
import requests

log = logging.getLogger(__name__)

URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
GOLD_CODE = "088691"


def fetch_cot(weeks: int = 260, timeout: float = 20.0) -> pd.DataFrame:
    params = {
        "cftc_contract_market_code": GOLD_CODE,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(weeks),
        "$select": "report_date_as_yyyy_mm_dd,m_money_positions_long_all,m_money_positions_short_all,open_interest_all",
    }
    resp = requests.get(URL, params=params, timeout=timeout)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise RuntimeError("COT: keine Daten")
    df = pd.DataFrame(rows)
    df["report_date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"], utc=True)
    for c in ("m_money_positions_long_all", "m_money_positions_short_all", "open_interest_all"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return prepare(df.rename(columns={
        "m_money_positions_long_all": "mm_long",
        "m_money_positions_short_all": "mm_short",
        "open_interest_all": "oi",
    })[["report_date", "mm_long", "mm_short", "oi"]])


def prepare(df: pd.DataFrame, lookback: int = 52) -> pd.DataFrame:
    df = df.sort_values("report_date").reset_index(drop=True)
    df["net"] = df["mm_long"] - df["mm_short"]
    lo = df["net"].rolling(lookback, min_periods=12).min()
    hi = df["net"].rolling(lookback, min_periods=12).max()
    df["cot_index"] = 100 * (df["net"] - lo) / (hi - lo).replace(0, pd.NA)
    df["net_change"] = df["net"].diff()
    # Veröffentlichung: Freitag 15:30 New York ≈ Dienstag + 3 Tage, 21:00 UTC (konservativ)
    df["available_at"] = df["report_date"] + timedelta(days=3, hours=21)
    return df


def cot_vote(cot_index: float | None) -> int:
    """Kontra-Indikator an Extremen: sehr viele Hedgefonds long -> Risiko für Long-Trades."""
    if cot_index is None or pd.isna(cot_index):
        return 0
    if cot_index >= 85:
        return -1
    if cot_index <= 15:
        return 1
    return 0


def describe(row: dict | None) -> str:
    if not row or row.get("cot_index") is None or pd.isna(row.get("cot_index")):
        return "COT: keine Daten"
    idx = float(row["cot_index"])
    lage = "überfüllt long (Kontra: bärisch)" if idx >= 85 else "stark short (Kontra: bullisch)" if idx <= 15 else "neutral"
    return f"COT: Hedgefonds netto {int(row['net']):+,} Kontrakte, Index {idx:.0f}/100 – {lage}".replace(",", ".")
