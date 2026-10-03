"""Nachrichten der letzten 7 Tage (Google-News-RSS) mit einfacher Stimmungsbewertung für Gold.

Die Bewertung ist bewusst simpel und nachvollziehbar: eine Liste von Schlagwort-Mustern,
die für Gold bullisch (+) oder bärisch (-) gelten. Jüngere Meldungen zählen stärker.
"""
from __future__ import annotations

import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

log = logging.getLogger(__name__)

QUERIES = [
    "gold price",
    "Federal Reserve interest rates",
    "US dollar index",
    "US inflation CPI",
]

# (Muster, Gewicht) – positiv = gut für Gold
LEXICON: list[tuple[str, float]] = [
    (r"gold (price )?(rises|rise|gains?|climbs?|jumps?|rall(y|ies)|surges?|soars?|advances?|edges? higher|hits? record|record high|all-time high)", 1.0),
    (r"gold (price )?(falls?|drops?|slips?|slides?|declines?|tumbles?|sinks?|plunges?|retreats?|edges? lower|eases?)", -1.0),
    (r"safe[- ]haven (demand|buying|bid|flows?)", 0.8),
    (r"(geopolitical|war|conflict|tensions?|escalat)", 0.4),
    (r"rate cuts?|cuts? (interest )?rates|easing cycle|dovish|pivot", 0.8),
    (r"rate hikes?|hikes? (interest )?rates|hawkish|higher for longer|tightening", -0.8),
    (r"dollar (weakens?|falls?|slips?|drops?|slides?|declines?|retreats?|eases?)|weaker dollar", 0.7),
    (r"dollar (strengthens?|rises?|gains?|jumps?|climbs?|rall(y|ies)|firms?)|stronger dollar", -0.7),
    (r"yields? (fall|falls|drop|drops|slip|slips|decline|declines|ease|eases)", 0.6),
    (r"yields? (rise|rises|jump|jumps|climb|climbs|surge|surges|spike|spikes)", -0.6),
    (r"central banks? (buy|buying|purchases?|add)", 0.6),
    (r"etf (inflows?)", 0.5),
    (r"etf (outflows?)", -0.5),
    (r"recession (fears?|worries|risk)", 0.4),
    (r"profit[- ]taking", -0.4),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), w) for p, w in LEXICON]


def score_headline(text: str) -> float:
    s = sum(w for rx, w in _COMPILED if rx.search(text))
    return max(-1.0, min(1.0, s))


def label(score: float | None) -> str:
    if score is None or math.isnan(score):
        return "unbekannt"
    if score > 0.15:
        return "bullisch"
    if score < -0.15:
        return "bärisch"
    return "neutral"


def fetch_items(query: str, timeout: float = 15.0) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}+when:7d&hl=en-US&gl=US&ceid=US:en"
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "goldsignal/0.1"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        pub = it.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
        except (TypeError, ValueError):
            ts = None
        if title and ts:
            items.append({"title": title, "time": ts, "query": query})
    return items


def sentiment(items: list[dict], now: datetime | None = None, half_life_days: float = 2.0) -> dict:
    now = now or datetime.now(timezone.utc)
    num = den = 0.0
    scored = []
    seen = set()
    for it in items:
        key = it["title"].lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        age = (now - it["time"]).total_seconds() / 86400
        if age < 0 or age > 7:
            continue
        s = score_headline(it["title"])
        w = 0.5 ** (age / half_life_days)
        if s != 0:
            num += s * w
            den += w
        scored.append({**it, "score": s})
    score = num / den if den else 0.0
    # Gedämpft, wenn nur wenige bewertbare Meldungen vorliegen
    relevant = sum(1 for x in scored if x["score"] != 0)
    score *= min(1.0, relevant / 8)
    top = sorted((x for x in scored if x["score"] != 0), key=lambda x: x["time"], reverse=True)[:6]
    return {"score": round(score, 3), "label": label(score), "n_items": len(scored),
            "n_relevant": relevant, "top": [{"title": t["title"], "score": t["score"],
                                             "time": t["time"].isoformat()} for t in top]}


def news_sentiment(now: datetime | None = None) -> dict:
    """Holt alle Abfragen; fällt eine aus, wird mit den übrigen weitergearbeitet."""
    items, errors = [], []
    for q in QUERIES:
        try:
            items.extend(fetch_items(q))
        except Exception as e:  # noqa: BLE001 - Datenquelle darf ausfallen
            errors.append(f"{q}: {e}")
            log.warning("News-Abfrage fehlgeschlagen (%s): %s", q, e)
    if not items:
        return {"score": None, "label": "unbekannt", "n_items": 0, "n_relevant": 0, "top": [], "errors": errors}
    res = sentiment(items, now)
    res["errors"] = errors
    return res
