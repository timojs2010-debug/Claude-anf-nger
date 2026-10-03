"""Kennzahlen für Trade-Listen (Backtest und Live-Journal)."""
from __future__ import annotations

import math

import numpy as np


def max_losing_streak(r: np.ndarray) -> int:
    best = cur = 0
    for x in r:
        if x < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def max_drawdown_r(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float(np.max(peak - eq)) if len(eq) else 0.0


def summarize(r, min_reliable: int = 30) -> dict:
    r = np.asarray([x for x in r if x is not None and not (isinstance(x, float) and math.isnan(x))], dtype=float)
    n = len(r)
    if n == 0:
        return {"n": 0, "winrate": None, "avg_r": None, "total_r": 0.0, "profit_factor": None,
                "max_loss_streak": 0, "max_dd_r": 0.0, "reliable": False}
    wins = r[r > 0]
    losses = r[r < 0]
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else (float("inf") if wins.sum() > 0 else None)
    return {
        "n": n,
        "winrate": round(float(len(wins) / n), 4),
        "avg_r": round(float(r.mean()), 4),
        "total_r": round(float(r.sum()), 3),
        "profit_factor": None if pf is None else (round(pf, 3) if math.isfinite(pf) else 999.0),
        "max_loss_streak": max_losing_streak(r),
        "max_dd_r": round(max_drawdown_r(r), 3),
        "reliable": n >= min_reliable,
    }


def fmt_pct(x) -> str:
    return "–" if x is None else f"{100 * x:.0f} %"


def fmt_r(x) -> str:
    return "–" if x is None else f"{x:+.2f} R"


def fmt_pf(x) -> str:
    if x is None:
        return "–"
    return "∞" if x >= 999 else f"{x:.2f}"
