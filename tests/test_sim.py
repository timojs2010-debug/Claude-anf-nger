import numpy as np
import pandas as pd

from goldsignal.sim import build_levels, evaluate_trade, precompute


def _bars(mids, spread=0.4, rng_w=0.5):
    idx = pd.date_range("2025-03-03 10:00", periods=len(mids), freq="15min", tz="UTC")
    m = np.array(mids, dtype=float)
    df = pd.DataFrame({"o": m, "h": m + rng_w, "l": m - rng_w, "c": m}, index=idx)
    for p in "ohlc":
        df["b" + p] = df[p] - spread / 2
        df["a" + p] = df[p] + spread / 2
    return df


def test_levels_include_spread():
    lv = build_levels(1, 2000.0, 2000.4, atr=4.0, stop_atr=1.5, target_atr=2.0)
    assert lv.entry == 2000.4 and lv.stop == 1994.4 and lv.target == 2008.4
    assert abs(lv.spread_ratio - 0.4 / 6) < 1e-9


def test_target_hit_long():
    df = _bars([2000, 2003, 2006, 2010, 2012])
    t0 = df.index[0].to_pydatetime()
    res = evaluate_trade(df, 15, 1, 2000.2, 1994.2, 2008.2, t0, t0 + pd.Timedelta(hours=4))
    assert res["status"] == "target"
    assert abs(res["r"] - 8 / 6) < 1e-9


def test_stop_first_when_both_hit():
    df = _bars([2000, 2000], rng_w=20)
    t0 = df.index[0].to_pydatetime()
    res = evaluate_trade(df, 15, 1, 2000.2, 1994.2, 2008.2, t0, t0 + pd.Timedelta(hours=4))
    assert res["status"] == "stop" and res["r"] <= -1


def test_expiry_and_open():
    df = _bars([2000] * 30)
    t0 = df.index[0].to_pydatetime()
    res = evaluate_trade(df, 15, -1, 1999.8, 2005.8, 1991.8, t0, t0 + pd.Timedelta(hours=1))
    assert res["status"] == "expired"
    assert res["r"] < 0  # Spread kostet: Verkauf zum Bid, Rückkauf zum Ask
    res2 = evaluate_trade(df, 15, -1, 1999.8, 2005.8, 1991.8, t0, t0 + pd.Timedelta(hours=48))
    assert res2["status"] == "open"


def test_precompute_shape():
    df = _bars(2000 + np.cumsum(np.random.default_rng(3).normal(0, 2, 200)))
    atr = pd.Series(3.0, index=df.index)
    pre = precompute(df, atr, 15, 1.5, 2.0, 2)
    assert set(pre) == {1, -1}
    r = pre[1]["r"]
    assert np.isfinite(r).sum() > 100
    assert np.nanmax(r) <= 2.0 / 1.5 + 1e-9
