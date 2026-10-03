"""Strategien. Jede liefert pro Kerze +1 (Kauf), -1 (Verkauf) oder 0 – berechnet zum Kerzenschluss."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass(frozen=True)
class Strategy:
    name: str
    kind: str  # trend / breakout / reversion
    title: str
    explain: str
    fn: Callable[[pd.DataFrame, pd.DataFrame], pd.Series]


def _sig(long: pd.Series, short: pd.Series) -> pd.Series:
    out = np.where(long.fillna(False), 1, np.where(short.fillna(False), -1, 0))
    return pd.Series(out, index=long.index, dtype=int)


def ma_cross(fast: int, slow: int):
    def fn(df: pd.DataFrame, feat: pd.DataFrame) -> pd.Series:
        f, s = ind.sma(df["c"], fast), ind.sma(df["c"], slow)
        return _sig(ind.cross_up(f, s), ind.cross_down(f, s))
    return fn


def donchian_breakout(n: int = 20):
    def fn(df: pd.DataFrame, feat: pd.DataFrame) -> pd.Series:
        up, lo = ind.donchian(df, n)
        long = (df["c"] > up) & (df["c"].shift(1) <= up.shift(1))
        short = (df["c"] < lo) & (df["c"].shift(1) >= lo.shift(1))
        return _sig(long, short)
    return fn


def rsi_reversion(n: int = 14, low: float = 30, high: float = 70):
    def fn(df: pd.DataFrame, feat: pd.DataFrame) -> pd.Series:
        r = ind.rsi(df["c"], n)
        return _sig(ind.cross_up(r, low), ind.cross_down(r, high))
    return fn


def bollinger_reversion(n: int = 20, k: float = 2.0):
    def fn(df: pd.DataFrame, feat: pd.DataFrame) -> pd.Series:
        lo, _, up = ind.bollinger(df["c"], n, k)
        long = (df["c"].shift(1) < lo.shift(1)) & (df["c"] > lo)
        short = (df["c"].shift(1) > up.shift(1)) & (df["c"] < up)
        return _sig(long, short)
    return fn


def macd_trend(trend_n: int = 200):
    def fn(df: pd.DataFrame, feat: pd.DataFrame) -> pd.Series:
        line, sig = ind.macd(df["c"])
        e = ind.ema(df["c"], trend_n)
        return _sig(ind.cross_up(line, sig) & (df["c"] > e), ind.cross_down(line, sig) & (df["c"] < e))
    return fn


def _tech_score(df: pd.DataFrame) -> pd.Series:
    c = df["c"]
    line, sig = ind.macd(c)
    votes = pd.concat([
        np.sign(ind.ema(c, 20) - ind.ema(c, 50)),
        np.sign(line - sig),
        np.sign(c - ind.ema(c, 200)),
        np.sign(ind.rsi(c, 14) - 50),
    ], axis=1)
    return votes.sum(axis=1, min_count=4)


def combo(df: pd.DataFrame, feat: pd.DataFrame) -> pd.Series:
    """Technik (4 Teilindikatoren einig) + Dollar + Zinsen + News in dieselbe Richtung.
    Fehlt eine Makro-Quelle (z. B. News im Backtest), zählt sie als neutral – der Dollar ist Pflicht."""
    score = _tech_score(df)
    aligned_long = (score == 4) & (score.shift(1) < 4)
    aligned_short = (score == -4) & (score.shift(1) > -4)

    def macro_ok(direction: int) -> pd.Series:
        usd = feat["usd_vote"] == direction
        rates = feat["rates_vote"].fillna(0) != -direction
        news = feat["news_vote"].fillna(0) != -direction
        return usd & rates & news

    return _sig(aligned_long & macro_ok(1), aligned_short & macro_ok(-1))


STRATEGIES: dict[str, Strategy] = {s.name: s for s in [
    Strategy("ma_20_50", "trend", "Trendfolge SMA 20/50",
             "Kauf, wenn der 20er-Durchschnitt den 50er von unten kreuzt (Verkauf umgekehrt).", ma_cross(20, 50)),
    Strategy("ma_50_200", "trend", "Trendfolge SMA 50/200",
             "Langsamere Variante: 50er kreuzt 200er.", ma_cross(50, 200)),
    Strategy("donchian_20", "breakout", "Ausbruch Donchian 20",
             "Kauf, wenn der Schlusskurs über das Hoch der letzten 20 Kerzen steigt (Verkauf: unter das Tief).", donchian_breakout(20)),
    Strategy("rsi_reversion", "reversion", "Gegenbewegung RSI 30/70",
             "Kauf, wenn der RSI(14) von unten über 30 steigt; Verkauf, wenn er von oben unter 70 fällt.", rsi_reversion()),
    Strategy("bollinger_reversion", "reversion", "Gegenbewegung Bollinger 20/2",
             "Kauf, wenn der Kurs von unterhalb des unteren Bands wieder hinein schließt (Verkauf am oberen Band).", bollinger_reversion()),
    Strategy("macd_trend", "trend", "MACD-Kreuzung + EMA200",
             "MACD kreuzt Signallinie, aber nur in Richtung des EMA200-Trends.", macd_trend()),
    Strategy("combo", "trend", "Kombi: Technik + Dollar/Zinsen + News",
             "Signal nur, wenn alle vier Technik-Teilsignale, der Dollar und (falls verfügbar) Zinsen und News übereinstimmen.", combo),
]}


def variant_id(strategy: str, filt: str) -> str:
    return f"{strategy}|{filt}"


def all_signals(df: pd.DataFrame, feat: pd.DataFrame) -> dict[str, pd.Series]:
    return {name: s.fn(df, feat) for name, s in STRATEGIES.items()}
