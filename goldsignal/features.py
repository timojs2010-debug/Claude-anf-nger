"""Marktkontext je Einstiegskerze: Trend höherer Zeitrahmen, Marktphase, Session, Termine,
Marken, Dollar, Zinsen, COT. Alles nur mit Daten, die zum Kerzenschluss bekannt waren."""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd

from . import indicators as ind
from .sessions import NY, session_of
from .sources.calendar import is_blocked
from .sources.cot import cot_vote
from .sources.oanda import tf_delta

FILTERS = ["basis", "htf", "phase", "session", "termine", "marken", "alle"]
FILTER_LABEL = {
    "basis": "ohne Filter",
    "htf": "Trend H1/H4 passt",
    "phase": "Marktphase passt (ADX)",
    "session": "nur London/NY-Überschneidung",
    "termine": "Sperre um Wirtschaftstermine",
    "marken": "Marken (Vortag/Vorwoche/runde Zahl)",
    "alle": "alle Filter",
}


def ensure_bid_ask(df: pd.DataFrame, default_spread: float) -> pd.DataFrame:
    """Fehlende Bid/Ask-Spalten (z. B. Demo-Daten) aus Mid ± halber Spread erzeugen."""
    df = df.copy()
    half = default_spread / 2
    for p in ("o", "h", "l", "c"):
        if f"b{p}" not in df or df[f"b{p}"].isna().all():
            df[f"b{p}"] = df[p] - half
        if f"a{p}" not in df or df[f"a{p}"].isna().all():
            df[f"a{p}"] = df[p] + half
        df[f"b{p}"] = df[f"b{p}"].fillna(df[p] - half)
        df[f"a{p}"] = df[f"a{p}"].fillna(df[p] + half)
    return df


def trend_vote(df: pd.DataFrame, n: int = 50) -> pd.Series:
    e = ind.ema(df["c"], n)
    return np.sign(df["c"] - e).where(e.notna())


def _align(series: pd.Series, granularity: str, close_times: pd.Series) -> np.ndarray:
    """Wert einer Serie (Index = Kerzenstart) zum Zeitpunkt close_times – erst nach Kerzenschluss."""
    if series is None or series.empty:
        return np.full(len(close_times), np.nan)
    s = series.copy()
    s.index = s.index + tf_delta(granularity)
    left = pd.DataFrame({"t": close_times.values})
    right = pd.DataFrame({"t": s.index, "v": s.values}).dropna().sort_values("t")
    if right.empty:
        return np.full(len(close_times), np.nan)
    left["t"] = pd.to_datetime(left["t"], utc=True)
    right["t"] = pd.to_datetime(right["t"], utc=True)
    m = pd.merge_asof(left.sort_values("t"), right, on="t", direction="backward")
    return m["v"].to_numpy()


def _levels(df: pd.DataFrame) -> pd.DataFrame:
    """Vortages-/Vorwochen-Hoch/-Tief. Handelstag beginnt 17:00 New York."""
    shifted = df.index.tz_convert(NY) + pd.Timedelta(hours=7)
    day_key = pd.Series(shifted.date, index=df.index)
    iso = shifted.isocalendar()
    week_key = pd.Series((iso.year * 100 + iso.week).to_numpy(), index=df.index)
    out = pd.DataFrame(index=df.index)
    for key, name in ((day_key, "day"), (week_key, "week")):
        g = df.groupby(key.values).agg(h=("h", "max"), l=("l", "min"))
        prev = g.shift(1)
        out[f"prev_{name}_high"] = key.map(prev["h"]).to_numpy()
        out[f"prev_{name}_low"] = key.map(prev["l"]).to_numpy()
    return out


def compute_features(df: pd.DataFrame, entry_tf: str, htf: dict[str, pd.DataFrame], settings,
                     usd: pd.DataFrame | None = None, bond: pd.DataFrame | None = None,
                     cot: pd.DataFrame | None = None, events: list[dict] | None = None) -> pd.DataFrame:
    events = events or []
    f = pd.DataFrame(index=df.index)
    close_times = pd.Series(df.index + tf_delta(entry_tf), index=df.index)
    f["close_time"] = close_times
    f["atr"] = ind.atr(df, settings.atr_period)
    f["adx"] = ind.adx(df, 14)
    f["regime"] = np.where(f["adx"] >= settings.adx_trend, "trend", "seitwärts")
    f.loc[f["adx"].isna(), "regime"] = "unbekannt"
    f["spread"] = (df["ac"] - df["bc"]).clip(lower=0)

    for tf, hdf in htf.items():
        f[f"htf_{tf}"] = _align(trend_vote(hdf), tf, close_times) if hdf is not None and not hdf.empty else np.nan

    # Dollar: EUR/USD über EMA50 (H1) = Dollar schwach = gut für Gold
    f["usd_vote"] = _align(trend_vote(usd), "H1", close_times) if usd is not None and not usd.empty else np.nan
    # Zinsen: Anleihekurs über EMA50 = Renditen fallen = gut für Gold
    f["rates_vote"] = _align(trend_vote(bond), "H1", close_times) if bond is not None and not bond.empty else np.nan

    if cot is not None and not cot.empty:
        c = cot.dropna(subset=["available_at"]).sort_values("available_at")
        left = pd.DataFrame({"t": pd.to_datetime(close_times.values, utc=True)})
        right = pd.DataFrame({"t": pd.to_datetime(c["available_at"], utc=True).reset_index(drop=True),
                              "cot_index": c["cot_index"].astype(float).reset_index(drop=True)})
        m = pd.merge_asof(left, right, on="t", direction="backward")
        f["cot_index"] = m["cot_index"].to_numpy()
    else:
        f["cot_index"] = np.nan
    f["cot_vote"] = [cot_vote(x) for x in f["cot_index"]]

    f["session"] = [session_of(t.to_pydatetime()) for t in close_times]
    blocks = [is_blocked(t.to_pydatetime(), events, settings.event_block_hours) if events else None for t in close_times]
    f["event_block"] = [b is not None for b in blocks]
    f["event_title"] = [b["title"] if b else "" for b in blocks]

    lv = _levels(df)
    for c in lv.columns:
        f[c] = lv[c]
    f["news_vote"] = np.nan  # nur live bekannt
    f["book_vote"] = np.nan  # nur live bekannt
    return f


# ----------------------------------------------------------------------
def _key_levels(row: pd.Series, price: float, step: float) -> list[float]:
    lv = [row.get(k) for k in ("prev_day_high", "prev_day_low", "prev_week_high", "prev_week_low")]
    lv = [float(x) for x in lv if x is not None and not (isinstance(x, float) and math.isnan(x))]
    base = math.floor(price / step) * step
    lv += [base - step, base, base + step, base + 2 * step]
    return lv


def level_check(row: pd.Series, direction: int, entry: float, target: float, atr: float, kind: str,
                step: float) -> tuple[bool, str]:
    """Trend/Ausbruch: Weg zum Ziel frei von Gegenmarken. Gegenbewegung: Einstieg nahe einer Unterstützung/Widerstand."""
    levels = _key_levels(row, entry, step)
    if kind == "reversion":
        near = [x for x in levels if abs(x - entry) <= 0.6 * atr and (x - entry) * direction <= 0.1 * atr]
        if near:
            return True, f"Einstieg nahe Marke {near[0]:.2f}"
        return False, "keine stützende Marke in der Nähe"
    lo, hi = sorted((entry + direction * 0.3 * atr, target))
    blocking = [x for x in levels if lo < x < hi]
    if blocking:
        return False, f"Marke {blocking[0]:.2f} liegt vor dem Ziel"
    return True, "Weg zum Ziel frei von wichtigen Marken"


def regime_fits(kind: str, regime: str) -> bool | None:
    if regime == "unbekannt":
        return None
    if kind == "reversion":
        return regime == "seitwärts"
    return regime == "trend"


def passes_filter(name: str, direction: int, row: pd.Series, kind: str, trend_tfs: list[str],
                  level_ok: bool) -> bool:
    if name == "basis":
        return True
    checks = {
        "htf": lambda: all(row.get(f"htf_{tf}") == direction for tf in trend_tfs),
        "phase": lambda: bool(regime_fits(kind, row["regime"])),
        "session": lambda: row["session"] == "overlap",
        "termine": lambda: not bool(row["event_block"]),
        "marken": lambda: level_ok,
    }
    if name == "alle":
        return all(fn() for fn in checks.values())
    return checks[name]()


def _vote_label(v) -> int:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0
    return int(np.sign(v))


def factors(direction: int, row: pd.Series, kind: str, trend_tfs: list[str], level_ok: bool,
            level_txt: str) -> tuple[list[str], list[str], int]:
    """Welche Faktoren sprechen für bzw. gegen das Signal? Liefert (dafür, dagegen, Anzahl bewerteter Faktoren)."""
    pro, contra = [], []
    total = 0

    def add(ok: int, yes: str, no: str):
        nonlocal total
        if ok == 0:
            return
        total += 1
        (pro if ok > 0 else contra).append(yes if ok > 0 else no)

    for tf in trend_tfs:
        v = _vote_label(row.get(f"htf_{tf}"))
        add(v * direction, f"{tf}-Trend in Signalrichtung", f"{tf}-Trend dagegen")
    rf = regime_fits(kind, row["regime"])
    if rf is not None:
        add(1 if rf else -1, f"Marktphase passt ({row['regime']}, ADX {row['adx']:.0f})",
            f"Marktphase passt nicht ({row['regime']}, ADX {row['adx']:.0f})")
    add(_vote_label(row.get("usd_vote")) * direction, "Dollar-Richtung unterstützt", "Dollar-Richtung dagegen")
    add(_vote_label(row.get("rates_vote")) * direction, "Zinsrichtung (US-Anleihen) unterstützt", "Zinsrichtung dagegen")
    add(_vote_label(row.get("news_vote")) * direction, "News-Stimmung unterstützt", "News-Stimmung dagegen")
    add(_vote_label(row.get("cot_vote")) * direction, "COT-Lage (Kontra) unterstützt", "COT: Hedgefonds bereits einseitig in Signalrichtung")
    add(_vote_label(row.get("book_vote")) * direction, "OANDA-Positionsbuch (Kontra) unterstützt", "Positionsbuch: Masse liegt bereits in Signalrichtung")
    add(1 if row["session"] in ("overlap", "london", "newyork") else 0, "Liquide Handelszeit", "")
    add(1 if level_ok else -1, level_txt, level_txt)
    if bool(row["event_block"]):
        add(-1, "", f"Wichtiger Termin in der Nähe: {row['event_title']}")
    return pro, contra, total
