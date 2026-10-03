"""SQLite-Datenbank: Thesen/Signale, Kerzen-Cache, Termine, Backtest-Ergebnisse, Zustand."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    bar_time TEXT NOT NULL,
    strategy TEXT NOT NULL,
    variant TEXT NOT NULL,
    direction TEXT NOT NULL,          -- long / short
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL NOT NULL,
    atr REAL,
    spread REAL,
    expires_at TEXT NOT NULL,
    units REAL,
    risk_acc REAL,
    size_status TEXT,
    strength INTEGER,
    strength_max INTEGER,
    factors_for TEXT,
    factors_against TEXT,
    thesis TEXT,
    invalidation TEXT,
    context TEXT,
    session TEXT,
    regime TEXT,
    news_label TEXT,
    risk_flags TEXT,
    notified INTEGER DEFAULT 0,
    status TEXT DEFAULT 'open',       -- open / target / stop / expired
    exit_price REAL,
    exit_time TEXT,
    r REAL,
    pnl_acc REAL,
    UNIQUE(strategy, variant, bar_time)
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

CREATE TABLE IF NOT EXISTS candles (
    instrument TEXT NOT NULL,
    granularity TEXT NOT NULL,
    time TEXT NOT NULL,
    o REAL, h REAL, l REAL, c REAL,
    bo REAL, bh REAL, bl REAL, bc REAL,
    ao REAL, ah REAL, al REAL, ac REAL,
    v REAL,
    PRIMARY KEY (instrument, granularity, time)
);

CREATE TABLE IF NOT EXISTS events (
    time TEXT NOT NULL,
    title TEXT NOT NULL,
    impact TEXT,
    currency TEXT,
    source TEXT,
    PRIMARY KEY (time, title)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    params TEXT,
    results TEXT
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

CANDLE_COLS = ["o", "h", "l", "c", "bo", "bh", "bl", "bc", "ao", "ah", "al", "ac", "v"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | pd.Timestamp) -> str:
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- Zustand ----------
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    # ---------- Signale ----------
    def insert_signal(self, sig: dict) -> int | None:
        data = dict(sig)
        for k in ("factors_for", "factors_against", "context", "risk_flags"):
            if k in data and not isinstance(data[k], str):
                data[k] = json.dumps(data[k], ensure_ascii=False, default=str)
        cols = ",".join(data)
        qs = ",".join("?" for _ in data)
        try:
            cur = self.conn.execute(f"INSERT INTO signals({cols}) VALUES({qs})", list(data.values()))
        except sqlite3.IntegrityError:
            return None  # Signal für diese Kerze schon gespeichert
        self.conn.commit()
        return cur.lastrowid

    def update_signal(self, sig_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE signals SET {sets} WHERE id=?", [*fields.values(), sig_id])
        self.conn.commit()

    def signals(self, where: str = "1=1", params: Iterable[Any] = ()) -> pd.DataFrame:
        df = pd.read_sql_query(f"SELECT * FROM signals WHERE {where} ORDER BY created_at", self.conn, params=list(params))
        return df

    def open_signals(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM signals WHERE status='open' ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # ---------- Kerzen-Cache ----------
    def save_candles(self, instrument: str, granularity: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        rows = []
        for ts, r in df.iterrows():
            rows.append([instrument, granularity, iso(ts)] + [None if pd.isna(r.get(c)) else float(r.get(c)) for c in CANDLE_COLS])
        self.conn.executemany(
            f"INSERT OR REPLACE INTO candles(instrument, granularity, time, {','.join(CANDLE_COLS)}) "
            f"VALUES(?,?,?,{','.join('?' for _ in CANDLE_COLS)})",
            rows,
        )
        self.conn.commit()

    def load_candles(self, instrument: str, granularity: str, start: datetime | None = None,
                     end: datetime | None = None) -> pd.DataFrame:
        q = "SELECT * FROM candles WHERE instrument=? AND granularity=?"
        p: list[Any] = [instrument, granularity]
        if start is not None:
            q += " AND time>=?"
            p.append(iso(start))
        if end is not None:
            q += " AND time<=?"
            p.append(iso(end))
        df = pd.read_sql_query(q + " ORDER BY time", self.conn, params=p)
        if df.empty:
            return pd.DataFrame(columns=CANDLE_COLS)
        df.index = pd.to_datetime(df["time"], utc=True)
        df.index.name = "time"
        return df[CANDLE_COLS].astype(float)

    # ---------- Termine ----------
    def save_events(self, events: list[dict]) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO events(time, title, impact, currency, source) VALUES(?,?,?,?,?)",
            [(iso(e["time"]), e["title"], e.get("impact"), e.get("currency"), e.get("source")) for e in events],
        )
        self.conn.commit()

    def load_events(self, start: datetime, end: datetime) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE time>=? AND time<=? ORDER BY time", (iso(start), iso(end))
        ).fetchall()
        return [{**dict(r), "time": parse_ts(r["time"])} for r in rows]

    # ---------- Backtests ----------
    def save_backtest(self, params: dict, results: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO backtest_runs(created_at, params, results) VALUES(?,?,?)",
            (iso(utcnow()), json.dumps(params, default=str), json.dumps(results, default=str, ensure_ascii=False)),
        )
        self.conn.commit()
        return cur.lastrowid

    def latest_backtest(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {"id": row["id"], "created_at": row["created_at"],
                "params": json.loads(row["params"]), "results": json.loads(row["results"])}
