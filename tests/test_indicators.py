import numpy as np
import pandas as pd

from goldsignal import indicators as ind


def _df(n=300, seed=1):
    rng = np.random.default_rng(seed)
    c = 2000 + np.cumsum(rng.normal(0, 2, n))
    o = np.concatenate([[2000], c[:-1]])
    return pd.DataFrame({"o": o, "h": np.maximum(o, c) + 1, "l": np.minimum(o, c) - 1, "c": c},
                        index=pd.date_range("2025-01-06", periods=n, freq="15min", tz="UTC"))


def test_rsi_bounds_and_trend():
    df = _df()
    r = ind.rsi(df["c"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    up = pd.Series(np.arange(100, dtype=float))
    assert ind.rsi(up).iloc[-1] == 100


def test_atr_positive_adx_range():
    df = _df()
    assert (ind.atr(df).dropna() > 0).all()
    a = ind.adx(df).dropna()
    assert ((a >= 0) & (a <= 100)).all()


def test_donchian_has_no_lookahead():
    df = _df()
    up, lo = ind.donchian(df, 20)
    i = 100
    assert up.iloc[i] == df["h"].iloc[i - 20:i].max()
    assert lo.iloc[i] == df["l"].iloc[i - 20:i].min()


def test_cross():
    a = pd.Series([1, 2, 3, 2, 1], dtype=float)
    assert ind.cross_up(a, 2.5).tolist() == [False, False, True, False, False]
    assert ind.cross_down(a, 2.5).tolist() == [False, False, False, True, False]
