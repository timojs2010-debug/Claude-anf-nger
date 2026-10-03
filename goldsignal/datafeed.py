"""Lädt alle Daten für Backtest und Scan – mit sauberem Weiterarbeiten, wenn eine Quelle ausfällt.
Enthält zusätzlich einen Demo-Datengenerator (synthetische Kurse), um ohne OANDA-Token zu testen."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .db import Database
from .features import ensure_bid_ask
from .sources import calendar as cal
from .sources import cot as cot_src
from .sources.oanda import GRANULARITY_MINUTES, DataSourceError, OandaClient

log = logging.getLogger(__name__)


@dataclass
class Bundle:
    entry: pd.DataFrame
    htf: dict[str, pd.DataFrame]
    usd: pd.DataFrame | None = None
    bond: pd.DataFrame | None = None
    cot: pd.DataFrame | None = None
    events: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    demo: bool = False


def make_client(settings) -> OandaClient:
    return OandaClient(settings.oanda_token, settings.oanda_account_id, settings.oanda_base_url)


def load_events(settings, db: Database, start: datetime, end: datetime, fetch_live: bool = True) -> tuple[list[dict], list[str]]:
    warnings = []
    events = cal.builtin_events(start, end) + cal.csv_events(settings.events_csv, start, end)
    if settings.calendar_enabled and fetch_live:
        try:
            live = cal.fetch_live_week()
            db.save_events(live)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Wirtschaftskalender (live) nicht verfügbar: {e}")
    events += db.load_events(start, end)
    return cal.merge(events), warnings


def load_cot(settings, warnings: list[str]) -> pd.DataFrame | None:
    if not settings.cot_enabled:
        return None
    try:
        return cot_src.fetch_cot()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"COT-Report nicht verfügbar: {e}")
        return None


def load_history(settings, db: Database, days: int, client: OandaClient | None = None) -> Bundle:
    client = client or make_client(settings)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    warmup = timedelta(days=30)  # Vorlauf für Indikatoren auf höheren Zeitrahmen
    warnings: list[str] = []
    entry = client.history(settings.instrument, settings.entry_tf, start, end, db)
    if entry.empty:
        raise DataSourceError("Keine Kerzendaten für XAU_USD erhalten")
    htf = {tf: client.history(settings.instrument, tf, start - warmup, end, db) for tf in settings.trend_tfs}
    usd = bond = None
    try:
        usd = client.history(settings.usd_proxy_instrument, "H1", start - warmup, end, db)
    except DataSourceError as e:
        warnings.append(f"{settings.usd_proxy_instrument} nicht verfügbar: {e}")
    if settings.bond_instrument:
        try:
            bond = client.history(settings.bond_instrument, "H1", start - warmup, end, db)
        except DataSourceError as e:
            warnings.append(f"Anleihen-CFD {settings.bond_instrument} nicht verfügbar (Zinsrichtung entfällt): {e}")
    cot = load_cot(settings, warnings)
    events, w = load_events(settings, db, start - warmup, end + timedelta(days=7))
    warnings += w
    entry = ensure_bid_ask(entry, settings.default_spread)
    return Bundle(entry, htf, usd, bond, cot, events, warnings)


# ----------------------------------------------------------------------
# Demo-Daten
# ----------------------------------------------------------------------
def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {}
    for p in ("", "b", "a"):
        agg.update({f"{p}o": "first", f"{p}h": "max", f"{p}l": "min", f"{p}c": "last"})
    agg["v"] = "sum"
    agg = {k: v for k, v in agg.items() if k in df.columns}
    return df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["c"])


def synthetic(days: int = 365, tf: str = "M15", seed: int = 7, start_price: float = 3300.0,
              end: datetime | None = None) -> Bundle:
    """Zufallsdaten mit wechselnden Trendphasen und Tagesrhythmus der Volatilität.
    Sie enthalten KEINEN echten Vorteil – ideal, um zu sehen, dass Strategien dann wie Zufall abschneiden."""
    rng = np.random.default_rng(seed)
    minutes = GRANULARITY_MINUTES[tf]
    end = (end or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    end = end - timedelta(minutes=end.minute % minutes)
    start = end - timedelta(days=days + 30)
    idx = pd.date_range(start, end, freq=f"{minutes}min", tz="UTC")
    ny = idx.tz_convert("America/New_York")
    open_mask = ~((ny.weekday == 5) | ((ny.weekday == 4) & (ny.hour >= 17)) | ((ny.weekday == 6) & (ny.hour < 18))
                  | (ny.hour == 17))
    idx = idx[open_mask]
    n = len(idx)
    hours = idx.hour.to_numpy()
    vol_profile = np.where((hours >= 12) & (hours < 17), 1.6, np.where((hours >= 7) & (hours < 12), 1.2, 0.7))
    base_vol = 0.0011 * np.sqrt(minutes / 15)
    regime_drift = np.zeros(n)
    i = 0
    while i < n:
        length = int(rng.integers(200, 1500))
        regime_drift[i:i + length] = rng.normal(0, 0.00003)
        i += length
    rets = regime_drift + rng.standard_t(4, n) * base_vol * vol_profile / np.sqrt(2)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]])
    wick = np.abs(rng.normal(0, base_vol * 0.6, n)) * close * vol_profile
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - np.abs(rng.normal(0, base_vol * 0.6, n)) * close * vol_profile
    spread = np.where(vol_profile > 1, 0.30, 0.55) + np.abs(rng.normal(0, 0.05, n))
    df = pd.DataFrame({"o": open_, "h": high, "l": low, "c": close, "v": rng.integers(50, 500, n).astype(float)}, index=idx)
    for p in ("o", "h", "l", "c"):
        df[f"b{p}"] = df[p] - spread / 2
        df[f"a{p}"] = df[p] + spread / 2
    df.index.name = "time"

    def proxy(scale: float, corr: float, price0: float) -> pd.DataFrame:
        noise = rng.normal(0, base_vol * scale, n)
        r = corr * rets * scale + np.sqrt(1 - corr ** 2) * noise
        c = price0 * np.exp(np.cumsum(r))
        o = np.concatenate([[price0], c[:-1]])
        p = pd.DataFrame({"o": o, "h": np.maximum(o, c), "l": np.minimum(o, c), "c": c}, index=idx)
        return resample(p, "1h")

    usd = proxy(0.4, 0.3, 1.10)    # EUR/USD leicht positiv mit Gold korreliert
    bond = proxy(0.3, 0.2, 110.0)  # Anleihe leicht positiv (fallende Renditen = Gold steigt)
    htf = {"H1": resample(df, "1h"), "H4": resample(df, "4h")}
    events = cal.builtin_events(start, end + timedelta(days=7))
    cut = end - timedelta(days=days)
    return Bundle(df[df.index >= cut], htf, usd, bond, None, events,
                  ["DEMO-DATEN: synthetische Kurse ohne echten Vorteil – nur zum Ausprobieren."], demo=True)
