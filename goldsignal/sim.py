"""Trade-Simulation mit Bid/Ask: Kauf zum Ask, Ausstieg zum Bid (Verkauf umgekehrt).
Dadurch steckt der Spread automatisch in jedem Ergebnis (in R)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

OUTCOME_TARGET, OUTCOME_STOP, OUTCOME_EXPIRED, OUTCOME_OPEN = 1, -1, 0, 9
OUTCOME_NAME = {OUTCOME_TARGET: "target", OUTCOME_STOP: "stop", OUTCOME_EXPIRED: "expired", OUTCOME_OPEN: "open"}


@dataclass
class Levels:
    entry: float
    stop: float
    target: float
    risk: float          # Abstand Einstieg–Stop in USD je Unze
    spread: float
    spread_ratio: float  # Spread / Stop-Abstand


def build_levels(direction: int, bid: float, ask: float, atr: float, stop_atr: float, target_atr: float) -> Levels:
    entry = ask if direction > 0 else bid
    dist = stop_atr * atr
    stop = entry - direction * dist
    target = entry + direction * target_atr * atr
    spread = max(ask - bid, 0.0)
    return Levels(entry, stop, target, dist, spread, spread / dist if dist > 0 else float("inf"))


def _walk(direction: int, entry: float, stop: float, target: float, start: int, end_time: np.datetime64,
          close_t: np.ndarray, bo, bh, bl, bc, ao, ah, al, ac) -> tuple[int, float, int]:
    """Geht Kerzen ab start durch. Rückgabe (Ergebnis-Code, Ausstiegskurs, Index)."""
    n = len(close_t)
    last = -1
    for j in range(start, n):
        if close_t[j] > end_time:
            break
        last = j
        if direction > 0:
            hit_stop = bl[j] <= stop
            hit_target = bh[j] >= target
            if hit_stop:  # beide getroffen -> konservativ Stop zuerst
                return OUTCOME_STOP, min(stop, bo[j]), j
            if hit_target:
                return OUTCOME_TARGET, target, j
        else:
            hit_stop = ah[j] >= stop
            hit_target = al[j] <= target
            if hit_stop:
                return OUTCOME_STOP, max(stop, ao[j]), j
            if hit_target:
                return OUTCOME_TARGET, target, j
    if last < 0:
        return OUTCOME_OPEN, float("nan"), -1
    if last == n - 1 and close_t[last] < end_time:
        return OUTCOME_OPEN, float("nan"), last  # Ablaufzeit noch nicht erreicht
    exit_px = bc[last] if direction > 0 else ac[last]
    return OUTCOME_EXPIRED, float(exit_px), last


def r_multiple(direction: int, entry: float, exit_px: float, risk: float) -> float:
    return direction * (exit_px - entry) / risk if risk > 0 else 0.0


def _arrays(df: pd.DataFrame, tf_minutes: int):
    close_t = (df.index + pd.Timedelta(minutes=tf_minutes)).values.astype("datetime64[ns]")
    cols = [df[c].to_numpy(dtype=float) for c in ("bo", "bh", "bl", "bc", "ao", "ah", "al", "ac")]
    return close_t, cols


def evaluate_trade(df: pd.DataFrame, tf_minutes: int, direction: int, entry: float, stop: float, target: float,
                   entry_time: datetime, expires_at: datetime) -> dict:
    """Live-Auswertung einer gespeicherten These anhand von Kerzen ab Einstieg."""
    df = df[df.index >= pd.Timestamp(entry_time).floor(f"{tf_minutes}min")]
    if df.empty:
        return {"status": "open"}
    close_t, cols = _arrays(df, tf_minutes)
    code, px, j = _walk(direction, entry, stop, target, 0, np.datetime64(pd.Timestamp(expires_at).tz_convert(None)),
                        close_t, *cols)
    if code == OUTCOME_OPEN:
        return {"status": "open"}
    risk = abs(entry - stop)
    return {"status": OUTCOME_NAME[code], "exit_price": px,
            "exit_time": pd.Timestamp(close_t[j]).tz_localize("UTC").to_pydatetime(),
            "r": r_multiple(direction, entry, px, risk)}


def precompute(df: pd.DataFrame, atr: pd.Series, tf_minutes: int, stop_atr: float, target_atr: float,
               max_hold_hours: float) -> dict[int, dict[str, np.ndarray]]:
    """Für jede Signalkerze i (Einstieg zur Eröffnung von i+1) das Ergebnis eines Long- und Short-Trades.
    Strategien und Zufalls-Einstiege werden danach nur noch nachgeschlagen."""
    n = len(df)
    close_t, (bo, bh, bl, bc, ao, ah, al, ac) = _arrays(df, tf_minutes)
    open_t = df.index.values.astype("datetime64[ns]")
    atr_v = atr.to_numpy(dtype=float)
    hold = np.timedelta64(int(max_hold_hours * 3600), "s")
    out: dict[int, dict[str, np.ndarray]] = {}
    for d in (1, -1):
        r = np.full(n, np.nan)
        code = np.full(n, OUTCOME_OPEN, dtype=int)
        exit_idx = np.full(n, -1, dtype=int)
        entry_a = np.full(n, np.nan)
        stop_a = np.full(n, np.nan)
        target_a = np.full(n, np.nan)
        sr = np.full(n, np.nan)
        for i in range(n - 1):
            a = atr_v[i]
            if not np.isfinite(a) or a <= 0:
                continue
            j0 = i + 1
            # Lücke (Wochenende/Pause) zwischen Signal und Einstieg: kein Trade
            if open_t[j0] - close_t[i] > np.timedelta64(2 * tf_minutes, "m"):
                continue
            lv = build_levels(d, bo[j0], ao[j0], a, stop_atr, target_atr)
            c, px, j = _walk(d, lv.entry, lv.stop, lv.target, j0, open_t[j0] + hold, close_t,
                             bo, bh, bl, bc, ao, ah, al, ac)
            entry_a[i], stop_a[i], target_a[i], sr[i] = lv.entry, lv.stop, lv.target, lv.spread_ratio
            code[i], exit_idx[i] = c, j
            if c != OUTCOME_OPEN:
                r[i] = r_multiple(d, lv.entry, px, lv.risk)
        out[d] = {"r": r, "code": code, "exit_idx": exit_idx, "entry": entry_a, "stop": stop_a,
                  "target": target_a, "spread_ratio": sr}
    return out


def expiry_time(entry_time: datetime, hours: float) -> datetime:
    return entry_time + timedelta(hours=hours)
