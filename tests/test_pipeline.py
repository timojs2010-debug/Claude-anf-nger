"""Ende-zu-Ende: Demo-Backtest, Live-Scan als Wiederholung über synthetische Daten, Auswertung, Report."""
from datetime import timedelta

import numpy as np

from goldsignal import backtest, report, scanner
from goldsignal.datafeed import synthetic
from goldsignal.db import Database
from goldsignal.notify import Notifier
from goldsignal.sources.oanda import tf_delta


def test_backtest_demo(settings):
    b = synthetic(90, "M15", seed=3)
    res = backtest.run(b, settings)
    assert len(res["variants"]) == 7 * 7
    assert res["ranking"]
    v = res["variants"]["donchian_20|basis"]
    assert v["all"]["n"] > 0
    p = v["oos"]["random"]["p_value"]
    assert p is None or 0 < p <= 1
    # keine überlappenden Trades je Variante
    times = [t[0] for t in v["trades"]]
    assert times == sorted(times)
    assert res["summary"][0].startswith("Achtung: DEMO")


def _replay(settings, db, notifier, b, steps=300):
    """Simuliert den Live-Betrieb: Kerze für Kerze scannen."""
    n = len(b.entry)
    for k in range(n - steps, n):
        entry = b.entry.iloc[:k]
        now = (entry.index[-1] + tf_delta(settings.entry_tf) + timedelta(seconds=20)).to_pydatetime()
        last = entry.iloc[-1]
        data = scanner.LiveData(now, entry, {tf: h[h.index + tf_delta(tf) <= now] for tf, h in b.htf.items()},
                                b.usd[b.usd.index < now], b.bond[b.bond.index < now], None, b.events,
                                float(last["bc"]), float(last["ac"]), {"long_pct": 50.0, "short_pct": 50.0},
                                {"label": "neutral", "score": 0.0}, 1000.0, 0.9,
                                {"min_units": 1.0, "units_precision": 0, "margin_rate": 0.05}, [], demo=True)
        scanner.scan(settings, db, notifier, data)
        scanner.evaluate(settings, db, lambda start: b.entry[b.entry.index >= start].iloc[: k + 1], now)


def test_live_replay_evaluate_report(settings):
    settings.min_strength_main = 1
    settings.min_strength_off = 1
    settings.cooldown_minutes = 30
    db = Database(settings.db_path)
    notifier = Notifier(settings, dry_run=True)
    b = synthetic(15, "M15", seed=5, start_price=2400.0)  # niedriger Preis -> Mindestgröße passt öfter
    _replay(settings, db, notifier, b)
    sig = db.signals()
    assert len(sig) > 0
    assert set(sig["status"]) <= {"open", "target", "stop", "expired"}
    closed = sig[sig["status"] != "open"]
    assert len(closed) > 0
    assert np.isfinite(closed["r"]).all()
    # Jede These hat Text, Invalidierung und Faktoren
    assert sig["thesis"].str.contains("These:").all()
    assert sig["invalidation"].str.contains("Stop").all()
    # Benachrichtigungen enthalten alle Pflichtangaben
    assert notifier.sent
    body = notifier.sent[0]["body"]
    for word in ("Einstieg", "Stop", "Ziel", "Größe", "Bisher", "Dafür"):
        assert word in body
    title, text = scanner.daily_summary(settings, db, scanner.utcnow())
    assert "Tageszusammenfassung" in title

    bt = backtest.run(synthetic(60, "M15", seed=9), settings)
    db.save_backtest({"demo": True}, bt)
    path = report.build(settings, db)
    html = open(path, encoding="utf-8").read()
    for word in ("Rangliste", "Live-Journal", "Kalender", "Walk-Forward", "Ehrliche Einschätzung"):
        assert word in html


def test_daily_loss_blocks_notifications(settings):
    db = Database(settings.db_path)
    now = scanner.utcnow()
    db.insert_signal({"created_at": scanner.iso(now - timedelta(hours=1)), "bar_time": scanner.iso(now - timedelta(hours=1)),
                      "strategy": "ma_20_50", "variant": "ma_20_50|basis", "direction": "long", "entry": 1, "stop": 0,
                      "target": 2, "expires_at": scanner.iso(now), "notified": 1, "status": "stop",
                      "exit_time": scanner.iso(now), "r": -1.0, "pnl_acc": -31.0})
    assert scanner.pnl_today(db, settings, now) == -31.0
