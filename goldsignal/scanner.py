"""Live-Betrieb: Daten holen, Signale erkennen, Thesen speichern, melden, auswerten."""
from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import risk, thesis
from .datafeed import load_events, synthetic
from .db import Database, iso, parse_ts, utcnow
from .features import FILTERS, compute_features, ensure_bid_ask, factors, level_check, passes_filter
from .metrics import summarize
from .notify import Notifier
from .sessions import MAIN_SESSIONS, SESSION_LABEL, in_quiet_hours, is_market_open, risk_flags
from .sim import build_levels, evaluate_trade
from .sources import calendar as cal
from .sources import cot as cot_src
from .sources import news as news_src
from .sources.oanda import GRANULARITY_MINUTES, DataSourceError, OandaClient, tf_delta
from .strategies import STRATEGIES, all_signals, variant_id

log = logging.getLogger(__name__)


@dataclass
class LiveData:
    now: datetime
    entry: pd.DataFrame
    htf: dict[str, pd.DataFrame]
    usd: pd.DataFrame | None
    bond: pd.DataFrame | None
    cot_row: dict | None
    events: list[dict]
    bid: float
    ask: float
    book: dict | None
    news: dict
    account: float
    usd_to_acc: float
    inst: dict
    warnings: list[str] = field(default_factory=list)
    demo: bool = False


# ----------------------------------------------------------------------
# Daten sammeln
# ----------------------------------------------------------------------
def _cached_json(db: Database, key: str, max_age: timedelta, fetch, now: datetime):
    raw = db.get_state(key)
    if raw:
        try:
            obj = json.loads(raw)
            if now - parse_ts(obj["_at"]) < max_age:
                return obj["data"]
        except (KeyError, ValueError):
            pass
    data = fetch()
    db.set_state(key, json.dumps({"_at": iso(now), "data": data}, default=str, ensure_ascii=False))
    return data


def _usd_to_acc(client: OandaClient, currency: str) -> float:
    if currency == "USD":
        return 1.0
    try:
        return 1.0 / client.price(f"{currency}_USD")["mid"]
    except DataSourceError:
        return client.price(f"USD_{currency}")["mid"]


def gather_live(settings, client: OandaClient, db: Database, now: datetime | None = None) -> LiveData:
    now = now or utcnow()
    w: list[str] = []
    entry = client.candles(settings.instrument, settings.entry_tf, count=400)
    if entry.empty:
        raise DataSourceError("Keine aktuellen XAU_USD-Kerzen erhalten")
    htf = {tf: client.candles(settings.instrument, tf, count=300) for tf in settings.trend_tfs}
    usd = bond = None
    try:
        usd = client.candles(settings.usd_proxy_instrument, "H1", count=200)
    except DataSourceError as e:
        w.append(f"Dollar-Proxy fehlt: {e}")
    skip_until = db.get_state("bond_skip_until")
    if settings.bond_instrument and not (skip_until and now < parse_ts(skip_until)):
        try:
            bond = client.candles(settings.bond_instrument, "H1", count=200)
        except DataSourceError as e:
            w.append(f"Anleihen-CFD fehlt (Zinsrichtung entfällt, neuer Versuch in 12 Std.): {e}")
            db.set_state("bond_skip_until", iso(now + timedelta(hours=12)))
    try:
        px = client.price(settings.instrument)
        bid, ask = px["bid"], px["ask"]
    except DataSourceError as e:
        w.append(f"Live-Preis fehlt, nutze letzte Kerze: {e}")
        last = entry.iloc[-1]
        bid, ask = float(last["bc"]), float(last["ac"])
    book = None
    if settings.positionbook_enabled:
        try:
            book = _cached_json(db, "cache_book", timedelta(minutes=20), lambda: client.position_book(settings.instrument), now)
        except DataSourceError as e:
            w.append(f"Positionsbuch fehlt: {e}")
    news = {"score": None, "label": "unbekannt", "top": []}
    if settings.news_enabled:
        try:
            news = _cached_json(db, "cache_news", timedelta(minutes=30), lambda: news_src.news_sentiment(now), now)
        except Exception as e:  # noqa: BLE001
            w.append(f"News fehlen: {e}")
    cot_row = None
    if settings.cot_enabled:
        try:
            def _cot():
                df = cot_src.fetch_cot(weeks=60)
                last = df.iloc[-1]
                return {"net": float(last["net"]), "cot_index": float(last["cot_index"]) if pd.notna(last["cot_index"]) else None,
                        "report_date": last["report_date"].isoformat()}
            cot_row = _cached_json(db, "cache_cot", timedelta(hours=12), _cot, now)
        except Exception as e:  # noqa: BLE001
            w.append(f"COT fehlt: {e}")
    fetch_cal = db.get_state("calendar_fetched", "")
    fetch_live = not fetch_cal or now - parse_ts(fetch_cal) > timedelta(hours=6)
    events, ew = load_events(settings, db, now - timedelta(days=2), now + timedelta(days=7), fetch_live)
    if fetch_live and not ew:
        db.set_state("calendar_fetched", iso(now))
    w += ew

    account, currency = settings.account_size, settings.account_currency
    if settings.use_account_balance:
        try:
            summ = client.account_summary()
            account, currency = summ["balance"], summ["currency"]
        except DataSourceError as e:
            w.append(f"Kontostand nicht abrufbar, nutze ACCOUNT_SIZE: {e}")
    try:
        usd_to_acc = _usd_to_acc(client, currency)
    except DataSourceError as e:
        w.append(f"Währungsumrechnung fehlt, nutze 1:1: {e}")
        usd_to_acc = 1.0
    try:
        inst = _cached_json(db, "cache_inst", timedelta(days=1), lambda: client.instrument_info(settings.instrument), now)
    except DataSourceError as e:
        w.append(f"Instrument-Daten fehlen, nutze Standardwerte: {e}")
        inst = {"min_units": settings.min_trade_units, "units_precision": 0, "margin_rate": 1 / settings.max_leverage}
    return LiveData(now, ensure_bid_ask(entry, settings.default_spread), htf, usd, bond, cot_row, events, bid, ask,
                    book, news, account, usd_to_acc, inst, w)


def gather_demo(settings, now: datetime | None = None, seed: int = 11) -> LiveData:
    """Synthetische Daten, abgeschnitten an der letzten Kerze mit einem Signal – zum Ausprobieren ohne Token."""
    b = synthetic(days=20, tf=settings.entry_tf, seed=seed)
    feat = compute_features(b.entry, settings.entry_tf, b.htf, settings, b.usd, b.bond, None, b.events)
    any_sig = sum(s.abs() for s in all_signals(b.entry, feat).values())
    hits = np.flatnonzero(any_sig.to_numpy() > 0)
    cut = int(hits[-1]) + 1 if len(hits) else len(b.entry)
    entry = b.entry.iloc[:cut]
    last = entry.iloc[-1]
    now = now or (entry.index[-1] + tf_delta(settings.entry_tf) + timedelta(seconds=30)).to_pydatetime()
    htf = {k: v[v.index + tf_delta(k) <= now] for k, v in b.htf.items()}
    return LiveData(now, entry, htf, b.usd[b.usd.index < now], b.bond[b.bond.index < now],
                    {"net": 150000.0, "cot_index": 70.0, "report_date": "demo"},
                    b.events, float(last["bc"]), float(last["ac"]), {"long_pct": 64.0, "short_pct": 36.0},
                    {"score": 0.2, "label": "bullisch", "top": []}, settings.account_size, 1 / 1.10,
                    {"min_units": settings.min_trade_units, "units_precision": 0, "margin_rate": 0.05},
                    ["DEMO-DATEN – keine echten Kurse"], demo=True)


# ----------------------------------------------------------------------
# Hilfen
# ----------------------------------------------------------------------
def _sign(x) -> float:
    return float(np.sign(x)) if x is not None and pd.notna(x) else np.nan


def news_vote(news: dict) -> float:
    lab = news.get("label")
    return 1.0 if lab == "bullisch" else -1.0 if lab == "bärisch" else (0.0 if lab == "neutral" else np.nan)


def book_vote(book: dict | None) -> float:
    if not book:
        return np.nan
    lp = book.get("long_pct", 50)
    return -1.0 if lp >= 60 else 1.0 if lp <= 40 else 0.0


def _vote_desc(v, pos: str, neg: str) -> str:
    if v is None or pd.isna(v):
        return "unbekannt"
    return pos if v > 0 else neg if v < 0 else "neutral"


def variant_stats(db: Database, vid: str, backtest: dict | None, min_reliable: int) -> dict:
    closed = db.signals("variant=? AND status!='open'", (vid,))
    live = summarize(closed["r"].tolist() if not closed.empty else [], min_reliable)
    bt = None
    if backtest:
        v = backtest["results"]["variants"].get(vid)
        if v:
            bt = {"oos": v["oos"], "verdict": v["verdict"], "level": v["verdict_level"]}
    return {"live": live, "backtest": bt}


def stats_line(st: dict) -> str:
    lv = st["live"]
    parts = []
    if lv["n"]:
        parts.append(f"Live {round(lv['winrate'] * lv['n'])}/{lv['n']} Treffer ({100 * lv['winrate']:.0f} %, Ø {lv['avg_r']:+.2f} R"
                     + ("" if lv["reliable"] else ", noch nicht belastbar") + ")")
    else:
        parts.append("Live: noch keine abgeschlossenen Signale")
    bt = st.get("backtest")
    if bt and bt["oos"]["n"]:
        o = bt["oos"]
        parts.append(f"Backtest OOS {100 * o['winrate']:.0f} %, Ø {o['avg_r']:+.2f} R (n={o['n']})")
    return " | ".join(parts)


def _match(vid: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(vid, p) for p in patterns)


def pnl_today(db: Database, settings, now: datetime) -> float:
    tz = ZoneInfo(settings.timezone)
    day_start = datetime.combine(now.astimezone(tz).date(), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    df = db.signals("notified=1 AND status!='open' AND exit_time>=?", (iso(day_start),))
    return float(df["pnl_acc"].fillna(0).sum()) if not df.empty else 0.0


# ----------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------
def scan(settings, db: Database, notifier: Notifier, data: LiveData) -> dict:
    now = data.now
    tfm = GRANULARITY_MINUTES[settings.entry_tf]
    df = data.entry
    last_close = df.index[-1] + tf_delta(settings.entry_tf)
    result = {"new_signals": 0, "notified": None, "warnings": data.warnings, "skipped": []}

    if is_market_open(now) and now - last_close.to_pydatetime() > timedelta(minutes=settings.stale_data_minutes):
        alert_data_problem(settings, db, notifier, now, f"Letzte Kerze von {last_close:%d.%m. %H:%M} UTC – keine frischen Daten.")
        result["skipped"].append("Daten veraltet")
        return result
    if db.get_state("last_scanned_bar") == iso(df.index[-1]):
        result["skipped"].append("Kerze bereits geprüft")
        return result

    cot_df = None
    if data.cot_row and data.cot_row.get("cot_index") is not None:
        cot_df = pd.DataFrame([{"available_at": df.index[0] - timedelta(days=1), "cot_index": data.cot_row["cot_index"]}])
    feat = compute_features(df, settings.entry_tf, data.htf, settings, data.usd, data.bond, cot_df, data.events)
    feat.loc[feat.index[-1], "news_vote"] = news_vote(data.news)
    feat.loc[feat.index[-1], "book_vote"] = book_vote(data.book)
    row = feat.iloc[-1]
    sigs = all_signals(df, feat)
    backtest = db.latest_backtest()

    upcoming = [f"{e['title']} {e['time'].astimezone(ZoneInfo(settings.timezone)):%a %H:%M}"
                for e in cal.upcoming(now, data.events, 24)]
    live_ctx = {
        "news_label": data.news.get("label", "unbekannt"),
        "usd_desc": _vote_desc(row["usd_vote"], "schwächer (gut für Gold)", "stärker (schlecht für Gold)"),
        "rates_desc": _vote_desc(row["rates_vote"], "fallende Renditen (gut für Gold)", "steigende Renditen (schlecht für Gold)"),
        "upcoming": upcoming,
        "cot_desc": cot_src.describe(data.cot_row),
        "book_desc": (f"{data.book['long_pct']:.0f} % der OANDA-Kunden long" if data.book else "keine Daten"),
    }

    inserted: list[dict] = []
    for name, s in sigs.items():
        d = int(s.iloc[-1])
        if d == 0:
            continue
        strat = STRATEGIES[name]
        atr = float(row["atr"])
        lv = build_levels(d, data.bid, data.ask, atr, settings.stop_atr, settings.target_atr)
        lvl_ok, lvl_txt = level_check(row, d, lv.entry, lv.target, atr, strat.kind, settings.round_step)
        pro, contra, total = factors(d, row, strat.kind, settings.trend_tfs, lvl_ok, lvl_txt)
        expires = now + timedelta(hours=settings.max_hold_hours)
        flags = risk_flags(now, expires)
        if lv.spread_ratio > settings.max_spread_to_stop:
            flags.append(f"Spread {lv.spread:.2f} = {100 * lv.spread_ratio:.0f} % des Stops (zu teuer)")
        size = risk.position_size(data.account, settings.risk_pct, lv.risk, lv.entry, data.usd_to_acc,
                                  data.inst["min_units"], data.inst.get("units_precision", 0), settings.max_leverage,
                                  data.inst.get("margin_rate"), settings.max_risk_pct, settings.max_margin_share)
        base = {
            "created_at": iso(now), "bar_time": iso(df.index[-1]), "strategy": name,
            "direction": "long" if d > 0 else "short", "entry": round(lv.entry, 3), "stop": round(lv.stop, 3),
            "target": round(lv.target, 3), "atr": round(atr, 3), "spread": round(lv.spread, 3),
            "expires_at": iso(expires), "units": size.units, "risk_acc": size.risk_acc, "size_status": size.status,
            "strength": len(pro), "strength_max": total, "factors_for": pro, "factors_against": contra,
            "session": row["session"], "regime": row["regime"], "news_label": data.news.get("label"),
            "risk_flags": flags,
        }
        fired = [f for f in FILTERS if passes_filter(f, d, row, strat.kind, settings.trend_tfs, lvl_ok)]
        extra = [f for f in fired if f != "basis"]
        label = f"{name}|Filter erfüllt: {', '.join(extra) if extra else 'keine'}"
        text, inval, ctx = thesis.build({**base, "variant": label}, row, live_ctx, settings)
        for filt in fired:
            sig = {**base, "variant": variant_id(name, filt), "thesis": text, "invalidation": inval,
                   "context": {**ctx, "size": size.to_dict(), "spread_ratio": round(lv.spread_ratio, 3), "demo": data.demo}}
            sid = db.insert_signal(sig)
            if sid:
                inserted.append({**sig, "id": sid, "_size": size, "_lv": lv, "_dir": d})
    db.set_state("last_scanned_bar", iso(df.index[-1]))
    result["new_signals"] = len(inserted)
    if inserted:
        result["notified"] = maybe_notify(settings, db, notifier, inserted, backtest, now, row, data)
    return result


def maybe_notify(settings, db, notifier, inserted, backtest, now, row, data) -> dict | None:
    cands = [s for s in inserted if _match(s["variant"], settings.notify_variants)]
    if not cands:
        return None
    by_dir: dict[int, list[dict]] = {}
    for s in cands:
        by_dir.setdefault(s["_dir"], []).append(s)
    best_dir = max(by_dir, key=lambda d: max(x["strength"] for x in by_dir[d]))
    group = by_dir[best_dir]
    conflict = len(by_dir) > 1

    def score(s):
        st = variant_stats(db, s["variant"], backtest, settings.min_trades_reliable)
        bt = st["backtest"]["oos"]["avg_r"] if st["backtest"] and st["backtest"]["oos"]["avg_r"] is not None else -1
        return (s["strength"], bt, FILTERS.index(s["variant"].split("|")[1]))

    primary = max(group, key=score)
    session = row["session"]
    min_strength = settings.min_strength_main if session in MAIN_SESSIONS else settings.min_strength_off
    reasons = []
    if primary["strength"] < min_strength:
        reasons.append(f"Stärke {primary['strength']} < {min_strength} ({SESSION_LABEL[session]})")
    if len(primary["factors_against"]) >= primary["strength"]:
        reasons.append(f"mehr/gleich viele Faktoren dagegen ({len(primary['factors_against'])}) als dafür")
    if in_quiet_hours(now, settings.quiet_hours, settings.timezone):
        reasons.append("Ruhezeit")
    last = db.get_state("last_notify_at")
    if last and now - parse_ts(last) < timedelta(minutes=settings.cooldown_minutes):
        reasons.append("Pause nach letztem Signal")
    pnl = pnl_today(db, settings, now)
    if risk.daily_loss_reached(pnl, data.account, settings.daily_loss_limit_pct):
        reasons.append(f"Tagesverlust-Grenze erreicht ({pnl:.2f})")
    if primary["_size"].status != risk.STATUS_OK and not settings.notify_too_large:
        reasons.append(primary["_size"].status)
    if primary["_lv"].spread_ratio > settings.max_spread_to_stop:
        reasons.append("Spread zu hoch im Verhältnis zum Stop")
    if conflict:
        reasons_conf = "Achtung: andere Strategien zeigen gleichzeitig in die Gegenrichtung."
    else:
        reasons_conf = ""
    if reasons:
        log.info("Signal %s nicht gemeldet: %s", primary["variant"], "; ".join(reasons))
        return {"sent": False, "reasons": reasons, "variant": primary["variant"]}

    st = variant_stats(db, primary["variant"], backtest, settings.min_trades_reliable)
    title, body = format_message(primary, group, st, session, settings, data, reasons_conf)
    ok = notifier.send(title, body, priority="high" if primary["strength"] >= min_strength + 2 else "default",
                       tags="chart_with_upwards_trend" if best_dir > 0 else "chart_with_downwards_trend")
    if ok:
        db.update_signal(primary["id"], notified=1)
        db.set_state("last_notify_at", iso(now))
    return {"sent": ok, "variant": primary["variant"], "title": title}


def format_message(p, group, st, session, settings, data, extra: str) -> tuple[str, str]:
    f = thesis.fmt
    tz = ZoneInfo(settings.timezone)
    d = p["_dir"]
    size = p["_size"]
    cur = settings.account_currency
    word = "KAUF" if d > 0 else "VERKAUF"
    icon = "🟢" if d > 0 else "🔴"
    crv = settings.target_atr / settings.stop_atr
    title = f"{icon} {word} Gold {f(p['entry'])} | Stärke {p['strength']}/{p['strength_max']}"
    if size.status != risk.STATUS_OK:
        title = "⚠️ " + title + f" | {size.status.upper()}"
    lines = [
        f"Einstieg: {f(p['entry'])} ({'Ask' if d > 0 else 'Bid'}, jetzt, Market-Order)",
        f"Stop: {f(p['stop'])} ({f(abs(p['entry'] - p['stop']))} $)",
        f"Ziel: {f(p['target'])} ({f(abs(p['target'] - p['entry']))} $) | CRV {f(crv)} vor Spread, Spread {f(p['spread'])} $",
    ]
    if size.status == risk.STATUS_OK:
        lines.append(f"Größe: {size.units:g} Unze(n) = {size.units:g} Einheiten XAU_USD | Risiko {f(size.risk_acc)} {cur} "
                     f"({f(size.risk_pct)} %) | Margin ~{size.margin_acc:.0f} {cur}")
        if size.note:
            lines.append(f"Hinweis: {size.note}")
    else:
        lines.append(f"Größe: ⚠️ {size.status} – {size.note}")
        lines.append(f"(Mit {size.min_units:g} Unze wären es {f(size.min_unit_risk_acc)} {cur}; größter passender Stop: {f(size.max_stop_usd)} $)")
    lines.append(f"Gültig bis: {datetime.fromisoformat(p['expires_at']).astimezone(tz):%H:%M} Uhr – danach These verfallen")
    others = len(group) - 1
    lines.append(f"Strategie: {STRATEGIES[p['strategy']].title} [{p['variant'].split('|')[1]}]"
                 + (f" + {others} weitere Variante(n) gleicher Richtung" if others else ""))
    lines.append(f"Bisher: {stats_line(st)}")
    lines.append(f"Session: {SESSION_LABEL[session]} – Stufe {'normal' if session in MAIN_SESSIONS else 'nur starke Signale'}")
    lines.append("Dafür: " + ("; ".join(p["factors_for"]) or "–"))
    if p["factors_against"]:
        lines.append("Dagegen: " + "; ".join(p["factors_against"]))
    lines.append("Scheitert, wenn: " + p["invalidation"])
    if p["risk_flags"]:
        lines.append("Risiko: " + "; ".join(p["risk_flags"]))
    if extra:
        lines.append(extra)
    if data.demo:
        lines.append("DEMO – keine echten Kurse.")
    lines.append("Kein Anlagerat. Signal = Wahrscheinlichkeit, keine Garantie.")
    return title, "\n".join(lines)


def alert_data_problem(settings, db: Database, notifier: Notifier, now: datetime, msg: str) -> None:
    last = db.get_state("last_data_alert")
    if last and now - parse_ts(last) < timedelta(hours=2):
        return
    notifier.send("⚠️ Gold-Signale: Datenproblem", msg, priority="high", tags="warning")
    db.set_state("last_data_alert", iso(now))


# ----------------------------------------------------------------------
# Auswertung offener Thesen
# ----------------------------------------------------------------------
def evaluate(settings, db: Database, candles_fn, now: datetime | None = None) -> list[dict]:
    """candles_fn(start) -> DataFrame mit M1- (oder feineren) Kerzen inkl. Bid/Ask ab start."""
    now = now or utcnow()
    open_sigs = db.open_signals()
    if not open_sigs:
        return []
    start = min(parse_ts(s["created_at"]) for s in open_sigs) - timedelta(minutes=1)
    df = ensure_bid_ask(candles_fn(start), settings.default_spread)
    done = []
    for s in open_sigs:
        d = 1 if s["direction"] == "long" else -1
        created, expires = parse_ts(s["created_at"]), parse_ts(s["expires_at"])
        sub = df[df.index >= pd.Timestamp(created).floor("1min") + timedelta(minutes=1)]
        res = evaluate_trade(sub, 1, d, s["entry"], s["stop"], s["target"], created, expires)
        if res["status"] == "open" and now > expires + timedelta(minutes=30):
            # Ablauf ohne Daten (z. B. Wochenende): zum letzten bekannten Kurs schließen
            px = float(sub["bc"].iloc[-1] if d > 0 else sub["ac"].iloc[-1]) if not sub.empty else s["entry"]
            res = {"status": "expired", "exit_price": px, "exit_time": expires,
                   "r": d * (px - s["entry"]) / abs(s["entry"] - s["stop"])}
        if res["status"] == "open":
            continue
        pnl = round(res["r"] * (s["risk_acc"] or 0), 2)
        db.update_signal(s["id"], status=res["status"], exit_price=round(res["exit_price"], 3),
                         exit_time=iso(res["exit_time"]), r=round(res["r"], 3), pnl_acc=pnl)
        done.append({**s, **res, "pnl_acc": pnl})
    return done


# ----------------------------------------------------------------------
# Tageszusammenfassung
# ----------------------------------------------------------------------
def daily_summary(settings, db: Database, now: datetime) -> tuple[str, str]:
    tz = ZoneInfo(settings.timezone)
    local = now.astimezone(tz)
    day_start = datetime.combine(local.date(), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    new = db.signals("created_at>=?", (iso(day_start),))
    closed = db.signals("status!='open' AND exit_time>=?", (iso(day_start),))
    open_ = db.signals("status='open'")
    lines = [f"Neue Thesen heute: {len(new)} (gemeldet: {int(new['notified'].sum()) if not new.empty else 0})"]
    if not closed.empty:
        notified = closed[closed["notified"] == 1]
        for label, part in (("Alle Varianten", closed), ("Gemeldete", notified)):
            if part.empty:
                continue
            m = summarize(part["r"].tolist())
            lines.append(f"{label}: {int((part['status'] == 'target').sum())} Ziel, {int((part['status'] == 'stop').sum())} Stop, "
                         f"{int((part['status'] == 'expired').sum())} abgelaufen | Σ {m['total_r']:+.2f} R")
        pnl = float(notified["pnl_acc"].fillna(0).sum()) if not notified.empty else 0.0
        lines.append(f"Ergebnis gemeldeter Signale (bei empfohlener Größe): {pnl:+.2f} {settings.account_currency}")
    else:
        lines.append("Heute keine abgeschlossenen Thesen.")
    lines.append(f"Noch offen: {len(open_)}")
    all_closed = db.signals("status!='open'")
    if not all_closed.empty:
        g = all_closed.groupby("variant")["r"].agg(["count", "mean"]).sort_values("mean", ascending=False)
        g = g[g["count"] >= 5]
        if not g.empty:
            lines.append("Beste Varianten live (≥5 Trades): " + ", ".join(
                f"{k} {v['mean']:+.2f} R (n={int(v['count'])})" for k, v in g.head(3).iterrows()))
    return f"📊 Gold-Signale – Tageszusammenfassung {local:%d.%m.%Y}", "\n".join(lines)
