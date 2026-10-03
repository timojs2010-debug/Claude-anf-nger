"""OANDA v20 REST-Client (nur lesend – es werden keine Orders gesendet)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from ..db import CANDLE_COLS, Database

log = logging.getLogger(__name__)

GRANULARITY_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D": 1440, "W": 10080}


class DataSourceError(RuntimeError):
    """Eine Datenquelle hat nicht (rechtzeitig) geliefert."""


def tf_delta(granularity: str) -> timedelta:
    return timedelta(minutes=GRANULARITY_MINUTES[granularity])


class OandaClient:
    def __init__(self, token: str, account_id: str = "", base_url: str = "https://api-fxpractice.oanda.com",
                 timeout: float = 20.0, retries: int = 3):
        if not token:
            raise DataSourceError("OANDA_TOKEN fehlt in der .env")
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept-Datetime-Format": "RFC3339",
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        last_err: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise DataSourceError(f"OANDA HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    # Client-Fehler (z. B. Instrument nicht verfügbar) nicht wiederholen
                    try:
                        msg = resp.json().get("errorMessage", resp.text)
                    except ValueError:
                        msg = resp.text
                    raise DataSourceError(f"OANDA {resp.status_code}: {msg}")
                return resp.json()
            except DataSourceError as e:
                last_err = e
                if "OANDA 4" in str(e):
                    raise
            except (requests.RequestException, ValueError) as e:
                last_err = e
            time.sleep(2 ** attempt)
        raise DataSourceError(f"OANDA nicht erreichbar ({path}): {last_err}")

    def _require_account(self) -> str:
        if not self.account_id:
            raise DataSourceError("OANDA_ACCOUNT_ID fehlt in der .env")
        return self.account_id

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_candles(payload: dict, include_incomplete: bool = False) -> pd.DataFrame:
        rows, idx = [], []
        for c in payload.get("candles", []):
            if not c.get("complete", True) and not include_incomplete:
                continue
            mid, bid, ask = c.get("mid", {}), c.get("bid", {}), c.get("ask", {})
            rows.append([
                float(mid.get("o", "nan")), float(mid.get("h", "nan")), float(mid.get("l", "nan")), float(mid.get("c", "nan")),
                float(bid.get("o", "nan")), float(bid.get("h", "nan")), float(bid.get("l", "nan")), float(bid.get("c", "nan")),
                float(ask.get("o", "nan")), float(ask.get("h", "nan")), float(ask.get("l", "nan")), float(ask.get("c", "nan")),
                float(c.get("volume", 0)),
            ])
            idx.append(c["time"])
        df = pd.DataFrame(rows, columns=CANDLE_COLS, index=pd.to_datetime(idx, utc=True))
        df.index.name = "time"
        return df

    def candles(self, instrument: str, granularity: str, count: int = 500,
                start: datetime | None = None, include_incomplete: bool = False) -> pd.DataFrame:
        params: dict = {"granularity": granularity, "price": "MBA", "count": min(count, 5000)}
        if start is not None:
            params["from"] = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self._get(f"/v3/instruments/{instrument}/candles", params)
        return self._parse_candles(data, include_incomplete)

    def history(self, instrument: str, granularity: str, start: datetime, end: datetime | None = None,
                db: Database | None = None) -> pd.DataFrame:
        """Historische Kerzen mit Paginierung; nutzt (und füllt) den SQLite-Cache."""
        end = end or datetime.now(timezone.utc)
        cached = db.load_candles(instrument, granularity, start, end) if db else pd.DataFrame(columns=CANDLE_COLS)
        fetch_from = start
        if not cached.empty and cached.index[0] <= pd.Timestamp(start) + tf_delta(granularity) * 3:
            fetch_from = cached.index[-1].to_pydatetime() + tf_delta(granularity)
        else:
            cached = pd.DataFrame(columns=CANDLE_COLS)
        parts = [cached] if not cached.empty else []
        cursor = fetch_from
        while cursor < end:
            chunk = self.candles(instrument, granularity, count=5000, start=cursor)
            if chunk.empty:
                break
            chunk = chunk[chunk.index <= pd.Timestamp(end)]
            if chunk.empty:
                break
            parts.append(chunk)
            if db:
                db.save_candles(instrument, granularity, chunk)
            nxt = chunk.index[-1].to_pydatetime() + tf_delta(granularity)
            if nxt <= cursor:
                break
            cursor = nxt
            if len(chunk) < 100:  # Ende der verfügbaren Daten erreicht
                break
        if not parts:
            return pd.DataFrame(columns=CANDLE_COLS)
        df = pd.concat(parts)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df

    # ------------------------------------------------------------------
    def position_book(self, instrument: str) -> dict:
        data = self._get(f"/v3/instruments/{instrument}/positionBook")
        book = data.get("positionBook", {})
        long_pct = sum(float(b.get("longCountPercent", 0)) for b in book.get("buckets", []))
        short_pct = sum(float(b.get("shortCountPercent", 0)) for b in book.get("buckets", []))
        total = long_pct + short_pct
        if total <= 0:
            raise DataSourceError("Positionsbuch leer")
        return {"time": book.get("time"), "long_pct": 100 * long_pct / total, "short_pct": 100 * short_pct / total}

    def instrument_info(self, instrument: str) -> dict:
        acc = self._require_account()
        data = self._get(f"/v3/accounts/{acc}/instruments", {"instruments": instrument})
        inst = data["instruments"][0]
        return {
            "min_units": float(inst.get("minimumTradeSize", 1)),
            "units_precision": int(inst.get("tradeUnitsPrecision", 0)),
            "margin_rate": float(inst.get("marginRate", 0.05)),
            "pip_location": int(inst.get("pipLocation", -2)),
        }

    def account_summary(self) -> dict:
        acc = self._require_account()
        a = self._get(f"/v3/accounts/{acc}/summary")["account"]
        return {"balance": float(a["balance"]), "currency": a["currency"], "nav": float(a.get("NAV", a["balance"]))}

    def price(self, instrument: str) -> dict:
        acc = self._require_account()
        p = self._get(f"/v3/accounts/{acc}/pricing", {"instruments": instrument})["prices"][0]
        bid, ask = float(p["bids"][0]["price"]), float(p["asks"][0]["price"])
        return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread": ask - bid,
                "time": p.get("time"), "tradeable": p.get("tradeable", True)}
