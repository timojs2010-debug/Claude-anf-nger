"""Befehlszeile: python -m goldsignal <befehl>"""
from __future__ import annotations

import argparse
import logging
import sys

from . import backtest as bt
from . import report, risk, runner, scanner
from .config import load_settings
from .datafeed import load_history, make_client, synthetic
from .db import Database, utcnow
from .notify import Notifier
from .sources.oanda import DataSourceError


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


def _demo_db(s) -> None:
    """Demo-Läufe nie in die echte Datenbank schreiben."""
    if s.db_path.endswith(".db"):
        s.db_path = s.db_path[:-3] + "_demo.db"
    s.report_path = s.report_path.replace(".html", "_demo.html")


def cmd_backtest(a, s):
    if a.demo:
        _demo_db(s)
    db = Database(s.db_path)
    days = a.days or s.backtest_days
    if a.demo:
        bundle = synthetic(days, s.entry_tf, seed=a.seed)
    else:
        print(f"Lade {days} Tage {s.entry_tf}-Kerzen von OANDA (wird zwischengespeichert) …")
        bundle = load_history(s, db, days)
    for w in bundle.warnings:
        print("Hinweis:", w)
    print(f"Backtest über {len(bundle.entry)} Kerzen, {len(bt.STRATEGIES)} Strategien × 7 Filter …")
    res = bt.run(bundle, s)
    db.save_backtest({"days": days, "demo": a.demo, "entry_tf": s.entry_tf}, res)
    print()
    print(f"{'Rang':<5}{'Variante':<34}{'OOS n':>6}{'Treffer':>9}{'Ø R':>8}{'PF':>6}{'p':>7}  Urteil")
    for i, vid in enumerate(res["ranking"][:15], 1):
        v = res["variants"][vid]
        o = v["oos"]
        wr = f"{100 * o['winrate']:.0f} %" if o["winrate"] is not None else "–"
        ar = f"{o['avg_r']:+.2f}" if o["avg_r"] is not None else "–"
        pf = f"{o['profit_factor']:.2f}" if o["profit_factor"] is not None else "–"
        p = o["random"].get("p_value")
        print(f"{i:<5}{vid:<34}{o['n']:>6}{wr:>9}{ar:>8}{pf:>6}{(p if p is not None else '–'):>7}  {v['verdict']}")
    print()
    for line in res["summary"]:
        print("•", line)
    bh = res["buy_hold"]
    print(f"• Einfach halten: In-Sample {bh['is'].get('pct')} %, Out-of-Sample {bh['oos'].get('pct')} %")
    path = report.build(s, db)
    print(f"\nReport: {path}")


def cmd_scan(a, s):
    if a.demo:
        _demo_db(s)
    db = Database(s.db_path)
    notifier = Notifier(s, dry_run=a.dry_run)
    if a.demo:
        data = scanner.gather_demo(s, seed=a.seed)
    else:
        data = scanner.gather_live(s, make_client(s), db)
    for w in data.warnings:
        print("Hinweis:", w)
    res = scanner.scan(s, db, notifier, data)
    print(f"Neue Thesen: {res['new_signals']}", "| übersprungen: " + ", ".join(res["skipped"]) if res["skipped"] else "")
    if res.get("notified"):
        n = res["notified"]
        print("Meldung:", "gesendet" if n.get("sent") else "nicht gesendet – " + "; ".join(n.get("reasons", [])))


def cmd_evaluate(a, s):
    db = Database(s.db_path)
    client = make_client(s)
    done = scanner.evaluate(s, db, lambda start: client.candles(s.instrument, "M1", count=5000, start=start))
    for d in done:
        print(f"#{d['id']} {d['variant']} {d['direction']}: {d['status']} {d['r']:+.2f} R")
    print(f"{len(done)} These(n) abgeschlossen, {len(db.open_signals())} offen.")


def cmd_report(a, s):
    if a.demo:
        _demo_db(s)
    db = Database(s.db_path)
    print("Report:", report.build(s, db, a.out))


def cmd_loop(a, s):
    if not s.has_oanda:
        sys.exit("OANDA_TOKEN fehlt – bitte .env anlegen (siehe .env.example).")
    runner.loop(s)


def cmd_size(a, s):
    account = a.account or s.account_size
    entry = a.entry
    usd_to_acc = 1.0 / a.eurusd if s.account_currency == "EUR" else 1.0
    min_units, prec, mr = s.min_trade_units, 0, 1 / s.max_leverage
    if s.has_oanda and not a.offline:
        try:
            c = make_client(s)
            info = c.instrument_info(s.instrument)
            min_units, prec, mr = info["min_units"], info["units_precision"], info["margin_rate"]
            if entry is None:
                entry = c.price(s.instrument)["mid"]
            if s.account_currency == "EUR" and a.eurusd == 1.10:
                usd_to_acc = 1.0 / c.price("EUR_USD")["mid"]
        except DataSourceError as e:
            print("Hinweis: OANDA nicht erreichbar, nutze Standardwerte:", e)
    if entry is None:
        sys.exit("Bitte --entry PREIS angeben (oder OANDA-Zugang konfigurieren).")
    ps = risk.position_size(account, a.risk or s.risk_pct, a.stop, entry, usd_to_acc, min_units, prec,
                            s.max_leverage, mr, s.max_risk_pct, s.max_margin_share)
    cur = s.account_currency
    print(f"Konto {account:.2f} {cur}, Risiko {a.risk or s.risk_pct} % = {ps.risk_target_acc:.2f} {cur}, "
          f"Stop-Abstand {a.stop:.2f} $, Preis {entry:.2f} $")
    print(f"Status: {ps.status}")
    print(f"Größe: {ps.units:g} Unze(n) (OANDA-Einheiten XAU_USD), Mindestgröße {ps.min_units:g}")
    print(f"Tatsächliches Risiko: {ps.risk_acc:.2f} {cur} ({ps.risk_pct:.2f} %)")
    print(f"Margin: {ps.margin_acc:.2f} {cur}, Positionswert {ps.notional_acc:.2f} {cur}, Hebel genutzt {ps.leverage_used:.1f}:1 "
          f"(max. {s.max_leverage:g}:1)")
    print(f"Größter Stop, der mit Mindestgröße ins Risiko passt: {ps.max_stop_usd:.2f} $")
    if ps.note:
        print("Hinweis:", ps.note)


def cmd_summary(a, s):
    db = Database(s.db_path)
    title, body = scanner.daily_summary(s, db, utcnow())
    Notifier(s, dry_run=not a.send).send(title, body)


def cmd_test_notify(a, s):
    n = Notifier(s)
    if not n.configured:
        print("Keine Benachrichtigung konfiguriert (NTFY_TOPIC oder TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID).")
    ok = n.send("🔔 Test: Gold-Signale", "Wenn du das liest, funktionieren die Push-Nachrichten.")
    print("gesendet" if ok else "fehlgeschlagen – siehe Log")


def cmd_check(a, s):
    """Prüft alle Datenquellen einzeln."""
    from .sources import calendar as cal
    from .sources import cot, news

    def check(name, fn):
        try:
            res = fn()
            print(f"✓ {name}: {res}")
        except Exception as e:  # noqa: BLE001
            print(f"✗ {name}: {e}")

    if s.has_oanda:
        c = make_client(s)
        check("OANDA Konto", c.account_summary)
        check("OANDA XAU_USD Instrument", lambda: c.instrument_info(s.instrument))
        check("OANDA Preis", lambda: c.price(s.instrument))
        check("OANDA Kerzen M15", lambda: f"{len(c.candles(s.instrument, 'M15', 10))} Kerzen")
        check("OANDA Positionsbuch", lambda: c.position_book(s.instrument))
        check(f"OANDA {s.usd_proxy_instrument}", lambda: c.price(s.usd_proxy_instrument))
        check(f"OANDA {s.bond_instrument}", lambda: f"{len(c.candles(s.bond_instrument, 'H1', 10))} Kerzen")
    else:
        print("✗ OANDA: kein Token konfiguriert")
    def _news():
        res = news.news_sentiment()
        if not res["n_items"]:
            raise RuntimeError("; ".join(res.get("errors", [])) or "keine Meldungen")
        return f"{res['label']} (Wert {res['score']}, {res['n_relevant']}/{res['n_items']} Meldungen bewertet)"

    check("News (Google RSS)", _news)
    check("Wirtschaftskalender", lambda: f"{len(cal.fetch_live_week())} wichtige USD-Termine diese Woche")
    check("COT-Report", lambda: cot.describe(cot.fetch_cot(60).iloc[-1].to_dict()))


def main(argv=None):
    p = argparse.ArgumentParser(prog="goldsignal", description="Gold-Signalsystem XAU_USD (OANDA) – nur Signale, kein Handel.")
    p.add_argument("--env", help="Pfad zur .env-Datei")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="Historischer Test aller Strategien × Filter")
    b.add_argument("--days", type=int)
    b.add_argument("--demo", action="store_true", help="synthetische Daten statt OANDA")
    b.add_argument("--seed", type=int, default=7)
    b.set_defaults(fn=cmd_backtest)

    sc = sub.add_parser("scan", help="Einmal nach Signalen suchen (letzte abgeschlossene Kerze)")
    sc.add_argument("--demo", action="store_true")
    sc.add_argument("--seed", type=int, default=11)
    sc.add_argument("--dry-run", action="store_true", help="Meldung nur anzeigen, nicht senden")
    sc.set_defaults(fn=cmd_scan)

    sub.add_parser("evaluate", help="Offene Thesen auswerten (Ziel/Stop/abgelaufen)").set_defaults(fn=cmd_evaluate)

    r = sub.add_parser("report", help="HTML-Report erzeugen")
    r.add_argument("--out")
    r.add_argument("--demo", action="store_true", help="Report der Demo-Datenbank")
    r.set_defaults(fn=cmd_report)

    sub.add_parser("loop", help="Dauerbetrieb").set_defaults(fn=cmd_loop)

    z = sub.add_parser("size", help="Positionsgrößenrechner")
    z.add_argument("--stop", type=float, required=True, help="Stop-Abstand in USD je Unze")
    z.add_argument("--entry", type=float, help="Einstiegspreis (sonst aktueller OANDA-Kurs)")
    z.add_argument("--account", type=float, help="Kontogröße (sonst ACCOUNT_SIZE)")
    z.add_argument("--risk", type=float, help="Risiko in %% (Standard RISK_PCT, max. MAX_RISK_PCT)")
    z.add_argument("--eurusd", type=float, default=1.10, help="EUR/USD-Kurs für die Umrechnung (offline)")
    z.add_argument("--offline", action="store_true")
    z.set_defaults(fn=cmd_size)

    sm = sub.add_parser("summary", help="Tageszusammenfassung anzeigen/senden")
    sm.add_argument("--send", action="store_true")
    sm.set_defaults(fn=cmd_summary)

    sub.add_parser("test-notify", help="Testnachricht aufs Handy senden").set_defaults(fn=cmd_test_notify)
    sub.add_parser("check", help="Alle Datenquellen prüfen").set_defaults(fn=cmd_check)

    a = p.parse_args(argv)
    _setup_logging(a.verbose)
    s = load_settings(a.env)
    try:
        a.fn(a, s)
    except DataSourceError as e:
        sys.exit(f"Datenquelle fehlgeschlagen: {e}")


if __name__ == "__main__":
    main()
