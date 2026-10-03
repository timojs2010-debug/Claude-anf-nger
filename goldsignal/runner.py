"""Dauerbetrieb: scannt nach jedem Kerzenschluss, wertet Thesen aus, sendet Tageszusammenfassung,
meldet Abstürze und Datenausfälle."""
from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import report, scanner
from .datafeed import make_client
from .db import Database, iso, parse_ts, utcnow
from .notify import Notifier
from .sessions import is_market_open
from .sources.oanda import DataSourceError, GRANULARITY_MINUTES

log = logging.getLogger(__name__)


def _summary_due(settings, db: Database, now: datetime) -> bool:
    tz = ZoneInfo(settings.timezone)
    local = now.astimezone(tz)
    hh, mm = (int(x) for x in settings.daily_summary_time.split(":"))
    if (local.hour, local.minute) < (hh, mm) or local.weekday() >= 5:
        return False
    return db.get_state("last_summary_date") != local.date().isoformat()


def run_once(settings, db: Database, notifier: Notifier, client) -> dict:
    now = utcnow()
    out: dict = {"time": iso(now)}
    if db.open_signals():
        done = scanner.evaluate(settings, db, lambda start: client.candles(settings.instrument, "M1", count=5000, start=start), now)
        out["evaluated"] = len(done)
    if is_market_open(now):
        tfm = GRANULARITY_MINUTES[settings.entry_tf]
        expected_bar = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % tfm + tfm)
        last = db.get_state("last_scanned_bar")
        if not last or parse_ts(last) < expected_bar:
            data = scanner.gather_live(settings, client, db, utcnow())
            for w in data.warnings:
                log.warning(w)
            out["scan"] = scanner.scan(settings, db, notifier, data)
            db.set_state("last_data_ok", iso(utcnow()))
    if _summary_due(settings, db, now):
        title, body = scanner.daily_summary(settings, db, now)
        notifier.send(title, body, tags="bar_chart")
        db.set_state("last_summary_date", now.astimezone(ZoneInfo(settings.timezone)).date().isoformat())
        try:
            report.build(settings, db)
        except Exception as e:  # noqa: BLE001
            log.error("Report fehlgeschlagen: %s", e)
    return out


def loop(settings) -> None:
    db = Database(settings.db_path)
    notifier = Notifier(settings)
    client = make_client(settings)
    notifier.send("✅ Gold-Signale gestartet", f"Konto: {settings.oanda_env}, Einstieg {settings.entry_tf}, "
                  f"Risiko {settings.risk_pct} %, Tageslimit {settings.daily_loss_limit_pct} %.", priority="low")
    failures = 0
    try:
        while True:
            try:
                res = run_once(settings, db, notifier, client)
                if res.get("scan", {}).get("new_signals"):
                    log.info("Scan: %s", res["scan"])
                failures = 0
            except DataSourceError as e:
                failures += 1
                log.error("Datenquelle fehlgeschlagen (%d×): %s", failures, e)
                if failures >= 3:
                    scanner.alert_data_problem(settings, db, notifier, utcnow(),
                                               f"Seit {failures} Durchläufen keine Daten von OANDA: {e}")
            except Exception as e:  # noqa: BLE001 - Dauerbetrieb darf nicht sterben
                failures += 1
                log.exception("Fehler im Durchlauf")
                last = db.get_state("last_crash_alert")
                if not last or utcnow() - parse_ts(last) > timedelta(minutes=30):
                    notifier.send("❌ Gold-Signale: Fehler", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-1500:]}",
                                  priority="high", tags="rotating_light")
                    db.set_state("last_crash_alert", iso(utcnow()))
            time.sleep(_sleep_seconds(settings))
    except KeyboardInterrupt:
        log.info("Beendet (Strg+C)")
    except BaseException as e:
        notifier.send("❌ Gold-Signale abgestürzt", f"{type(e).__name__}: {e}", priority="urgent", tags="skull")
        raise
    finally:
        db.close()


def _sleep_seconds(settings) -> float:
    """Bis zum nächsten Kerzenschluss (+5 s), höchstens LOOP_INTERVAL_SEC."""
    now = utcnow()
    tfm = GRANULARITY_MINUTES[settings.entry_tf]
    nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=tfm - now.minute % tfm)
    return max(5.0, min(settings.loop_interval_sec, (nxt - now).total_seconds() + 5))
