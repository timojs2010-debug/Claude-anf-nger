"""Backtest aller Strategien × Filter mit In-Sample/Out-of-Sample, Zufallsvergleich,
"einfach halten" und Walk-Forward-Auswahl."""
from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
import pandas as pd

from .datafeed import Bundle
from .features import FILTERS, compute_features, factors, level_check, passes_filter
from .metrics import summarize
from .sim import OUTCOME_NAME, OUTCOME_OPEN, precompute
from .sources.oanda import GRANULARITY_MINUTES
from .strategies import STRATEGIES, all_signals, variant_id

log = logging.getLogger(__name__)


def candidate_signals(bundle: Bundle, settings):
    df = bundle.entry
    feat = compute_features(df, settings.entry_tf, bundle.htf, settings, bundle.usd, bundle.bond,
                            bundle.cot, bundle.events)
    sigs = all_signals(df, feat)
    tfm = GRANULARITY_MINUTES[settings.entry_tf]
    pre = precompute(df, feat["atr"], tfm, settings.stop_atr, settings.target_atr, settings.max_hold_hours)
    cands: dict[str, list[dict]] = {}
    for name, s in sigs.items():
        strat = STRATEGIES[name]
        rows = []
        for i in np.flatnonzero(s.to_numpy() != 0):
            d = int(s.iloc[i])
            p = pre[d]
            if p["code"][i] == OUTCOME_OPEN or not np.isfinite(p["r"][i]):
                continue
            if p["spread_ratio"][i] > settings.max_spread_to_stop:
                continue  # Spread frisst zu viel vom Stop – wird auch live nicht gemeldet
            row = feat.iloc[i]
            lvl_ok, lvl_txt = level_check(row, d, p["entry"][i], p["target"][i], row["atr"], strat.kind, settings.round_step)
            pro, contra, _ = factors(d, row, strat.kind, settings.trend_tfs, lvl_ok, lvl_txt)
            rows.append({
                "i": int(i), "time": row["close_time"], "dir": d, "r": float(p["r"][i]),
                "outcome": OUTCOME_NAME[int(p["code"][i])], "exit_idx": int(p["exit_idx"][i]),
                "filters": {f: passes_filter(f, d, row, strat.kind, settings.trend_tfs, lvl_ok) for f in FILTERS},
                "strength": len(pro), "against": len(contra),
                "session": row["session"], "regime": row["regime"],
                "usd_align": int(np.sign(row["usd_vote"]) * d) if pd.notna(row["usd_vote"]) else 0,
                "htf_align": int(all(row.get(f"htf_{tf}") == d for tf in settings.trend_tfs)),
            })
        cands[name] = rows
    return feat, pre, cands


def non_overlapping(rows: list[dict], filt: str) -> list[dict]:
    """Je Variante höchstens ein offener Trade gleichzeitig."""
    out, busy_until = [], -1
    for r in rows:
        if not r["filters"][filt] or r["i"] <= busy_until:
            continue
        out.append(r)
        busy_until = r["exit_idx"]
    return out


def random_baseline(pre, eligible: np.ndarray, trades: list[dict], runs: int, rng: np.random.Generator) -> dict:
    """Gleich viele Käufe/Verkäufe zu zufälligen Zeitpunkten im selben Zeitraum, gleiche Stop/Ziel-Regeln."""
    if not trades:
        return {"avg_r_mean": None, "p_value": None}
    n_long = sum(1 for t in trades if t["dir"] > 0)
    n_short = len(trades) - n_long
    pools = {d: eligible[np.isfinite(pre[d]["r"][eligible])] for d in (1, -1)}
    if (n_long and len(pools[1]) == 0) or (n_short and len(pools[-1]) == 0):
        return {"avg_r_mean": None, "p_value": None}
    total = np.zeros(runs)
    if n_long:
        total += pre[1]["r"][rng.choice(pools[1], size=(runs, n_long))].sum(axis=1)
    if n_short:
        total += pre[-1]["r"][rng.choice(pools[-1], size=(runs, n_short))].sum(axis=1)
    rand_avg = total / len(trades)
    strat_avg = float(np.mean([t["r"] for t in trades]))
    p = float((np.sum(rand_avg >= strat_avg) + 1) / (runs + 1))
    return {"avg_r_mean": round(float(rand_avg.mean()), 4), "avg_r_p95": round(float(np.percentile(rand_avg, 95)), 4),
            "p_value": round(p, 4)}


def buy_and_hold(df: pd.DataFrame, start, end) -> dict:
    seg = df[(df.index >= start) & (df.index < end)]
    if len(seg) < 2:
        return {"pct": None}
    # Kauf zum Ask am Anfang, Verkauf zum Bid am Ende
    pct = 100 * (seg["bc"].iloc[-1] / seg["ao"].iloc[0] - 1)
    return {"pct": round(float(pct), 2), "from": float(seg["ao"].iloc[0]), "to": float(seg["bc"].iloc[-1])}


def _context_table(trades: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for key, labels in (("regime", None), ("session", None), ("usd_align", {1: "Dollar dafür", -1: "Dollar dagegen", 0: "Dollar neutral"}),
                        ("htf_align", {1: "H1/H4 passt", 0: "H1/H4 passt nicht"})):
        groups = defaultdict(list)
        for t in trades:
            g = t[key]
            groups[labels.get(g, g) if labels else g].append(t["r"])
        out[key] = {str(g): summarize(rs) for g, rs in groups.items()}
    return out


def _compact(trades: list[dict]) -> list[list]:
    return [[pd.Timestamp(t["time"]).isoformat(), t["dir"], round(t["r"], 3), t["outcome"], t["session"],
             t["regime"], t["strength"]] for t in trades]


def run(bundle: Bundle, settings) -> dict:
    df = bundle.entry
    feat, pre, cands = candidate_signals(bundle, settings)
    rng = np.random.default_rng(settings.random_seed)
    t0, t1 = df.index[0], df.index[-1]
    split = t0 + (t1 - t0) * (1 - settings.oos_fraction)
    close_times = feat["close_time"]
    seg_mask = {"is": (close_times < split).to_numpy(), "oos": (close_times >= split).to_numpy()}
    valid = np.isfinite(pre[1]["r"]) | np.isfinite(pre[-1]["r"])
    ok_spread = (np.nan_to_num(pre[1]["spread_ratio"], nan=9) <= settings.max_spread_to_stop)
    eligible_all = np.flatnonzero(valid & ok_spread)
    eligible = {k: np.flatnonzero(valid & ok_spread & m) for k, m in seg_mask.items()}

    variants: dict[str, dict] = {}
    all_trades: dict[str, list[dict]] = {}
    for name in STRATEGIES:
        for filt in FILTERS:
            vid = variant_id(name, filt)
            trades = non_overlapping(cands[name], filt)
            all_trades[vid] = trades
            seg = {"is": [t for t in trades if t["time"] < split], "oos": [t for t in trades if t["time"] >= split]}
            res = {"strategy": name, "filter": filt, "all": summarize([t["r"] for t in trades], settings.min_trades_reliable)}
            for k, ts in seg.items():
                m = summarize([t["r"] for t in ts], settings.min_trades_reliable)
                m["long"] = summarize([t["r"] for t in ts if t["dir"] > 0], settings.min_trades_reliable)
                m["short"] = summarize([t["r"] for t in ts if t["dir"] < 0], settings.min_trades_reliable)
                m["random"] = random_baseline(pre, eligible[k], ts, settings.random_runs, rng)
                res[k] = m
            res["context"] = _context_table(trades)
            res["trades"] = _compact(trades)
            variants[vid] = res

    n_tests = len(variants)
    for vid, v in variants.items():
        v["verdict"], v["verdict_level"] = verdict(v, settings, n_tests)

    wf = walk_forward(all_trades, df, settings)
    ranking = sorted(variants.keys(), key=lambda k: (
        variants[k]["oos"]["n"] >= settings.min_trades_reliable,
        variants[k]["oos"]["avg_r"] if variants[k]["oos"]["avg_r"] is not None else -9), reverse=True)
    beats = [k for k in ranking if variants[k]["verdict_level"] >= 2]
    summary_text = honest_summary(variants, ranking, beats, wf, settings, bundle)
    rand_all = np.concatenate([pre[1]["r"][eligible_all], pre[-1]["r"][eligible_all]])
    rand_all = rand_all[np.isfinite(rand_all)]

    return {
        "period": {"start": t0.isoformat(), "end": t1.isoformat(), "split": pd.Timestamp(split).isoformat(),
                   "bars": len(df), "entry_tf": settings.entry_tf},
        "params": {"stop_atr": settings.stop_atr, "target_atr": settings.target_atr, "max_hold_hours": settings.max_hold_hours,
                   "max_spread_to_stop": settings.max_spread_to_stop, "random_runs": settings.random_runs,
                   "oos_fraction": settings.oos_fraction, "trend_tfs": settings.trend_tfs},
        "buy_hold": {"is": buy_and_hold(df, t0, split), "oos": buy_and_hold(df, split, t1 + pd.Timedelta(days=1))},
        "random_trade_avg_r": round(float(rand_all.mean()), 4) if len(rand_all) else None,
        "variants": variants,
        "ranking": ranking,
        "walk_forward": wf,
        "summary": summary_text,
        "warnings": bundle.warnings,
        "demo": bundle.demo,
        "n_tests": n_tests,
    }


def verdict(v: dict, settings, n_tests: int) -> tuple[str, int]:
    """0 = nicht beurteilbar/negativ, 1 = auffällig aber nicht belastbar, 2 = besser als Zufall, 3 = auch streng geprüft."""
    oos, is_ = v["oos"], v["is"]
    p = oos["random"].get("p_value")
    if oos["n"] == 0:
        return "keine Trades im Out-of-Sample-Zeitraum", 0
    if oos["avg_r"] <= 0:
        return "verliert nach Spread (Out-of-Sample)", 0
    if oos["n"] < settings.min_trades_reliable:
        return f"positiv, aber nur {oos['n']} Trades – nicht belastbar", 1
    if p is None or p >= 0.05:
        return f"positiv, aber nicht besser als Zufall (p={p})", 1
    if (is_["avg_r"] or 0) <= 0:
        return "nur im Out-of-Sample positiv, In-Sample negativ – vermutlich Glück", 1
    if p < 0.05 / n_tests:
        return "besser als Zufall – auch nach strenger Korrektur für viele Tests", 3
    return f"besser als Zufall (p={p}); bei {n_tests} getesteten Varianten aber Glück möglich", 2


def walk_forward(all_trades: dict[str, list[dict]], df: pd.DataFrame, settings) -> dict:
    """Pro Abschnitt wird die bis dahin beste Variante gewählt und nur im *folgenden* Abschnitt gemessen."""
    k = max(3, settings.walk_forward_folds)
    t0, t1 = df.index[0], df.index[-1]
    edges = [t0 + (t1 - t0) * i / k for i in range(k + 1)]
    chosen, oos_r = [], []
    for f in range(1, k):
        train_end, test_start, test_end = edges[f], edges[f], edges[f + 1]
        best, best_key = None, None
        for vid, trades in all_trades.items():
            tr = [t["r"] for t in trades if t["time"] < train_end]
            if len(tr) < 10:
                continue
            score = float(np.mean(tr))
            if best is None or score > best:
                best, best_key = score, vid
        if best_key is None:
            chosen.append({"fold": f, "variant": None, "train_avg_r": None, "test": summarize([])})
            continue
        test = [t["r"] for t in all_trades[best_key] if test_start <= t["time"] < test_end]
        oos_r += test
        chosen.append({"fold": f, "from": pd.Timestamp(test_start).isoformat(), "to": pd.Timestamp(test_end).isoformat(),
                       "variant": best_key, "train_avg_r": round(best, 4), "test": summarize(test)})
    return {"folds": chosen, "combined": summarize(oos_r, settings.min_trades_reliable)}


def honest_summary(variants, ranking, beats, wf, settings, bundle) -> list[str]:
    lines = []
    if bundle.demo:
        lines.append("Achtung: DEMO-DATEN. Die Kurse sind zufällig erzeugt – jedes 'gute' Ergebnis ist hier reiner Zufall.")
    if not beats:
        lines.append("Keine Strategie-Variante ist im Out-of-Sample-Test (mit ≥ "
                     f"{settings.min_trades_reliable} Trades) belastbar besser als zufällige Einstiege. "
                     "Ehrliche Schlussfolgerung: Auf dieser Datenbasis gibt es keinen nachweisbaren Vorteil.")
    else:
        strong = [b for b in beats if variants[b]["verdict_level"] >= 3]
        lines.append(f"{len(beats)} von {len(variants)} Varianten schlagen den Zufall im Out-of-Sample-Test (p < 0,05).")
        if not strong:
            lines.append(f"Bei {len(variants)} gleichzeitig getesteten Varianten wären ca. {len(variants) * 0.05:.0f} "
                         "solche 'Treffer' auch rein zufällig zu erwarten – Ergebnisse im Live-Journal bestätigen lassen.")
        else:
            lines.append(f"{len(strong)} Variante(n) bestehen auch die strenge Prüfung (Bonferroni): {', '.join(strong)}.")
    c = wf["combined"]
    if c["n"]:
        lines.append(f"Walk-Forward (jeweils bisher beste Variante, im nächsten Abschnitt gemessen): {c['n']} Trades, "
                     f"Ø {c['avg_r']:+.2f} R, Trefferquote {100 * c['winrate']:.0f} %. "
                     + ("Positiv – die Auswahl der Besten hat sich in neuen Daten gehalten." if c["avg_r"] > 0
                        else "Negativ – wer jeweils die bisher beste Variante nimmt, verliert danach. Typisches Zeichen für Überanpassung."))
    lines.append("Ergebnisse unter 30 Trades sind nicht belastbar. Vergangene Ergebnisse garantieren keine zukünftigen.")
    return lines
