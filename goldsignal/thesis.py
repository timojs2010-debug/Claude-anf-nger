"""Prüfbare These je Signal (deutsch), optional von Claude sprachlich ausformuliert."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .sessions import SESSION_LABEL
from .strategies import STRATEGIES

log = logging.getLogger(__name__)

INVALIDATION_BY_KIND = {
    "trend": "der Kurs auf {tf} wieder gegen die Trendrichtung schließt (Durchschnitte kreuzen zurück) oder der H1-Trend dreht",
    "breakout": "der Kurs zurück in den alten Kanal fällt (Fehlausbruch) – ein Schluss jenseits von {lvl:.2f}",
    "reversion": "der Kurs ein neues Extrem markiert und erneut außerhalb des Bands/RSI-Extrems schließt",
}


def fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def build(sig: dict, row, live_ctx: dict, settings) -> tuple[str, str, dict]:
    """Liefert (These-Text, Invalidierung, Kontext-Dict)."""
    strat = STRATEGIES[sig["strategy"]]
    d = 1 if sig["direction"] == "long" else -1
    tz = ZoneInfo(settings.timezone)
    exp = datetime.fromisoformat(sig["expires_at"]).astimezone(tz)
    word = "KAUF" if d > 0 else "VERKAUF"
    move = "steigt" if d > 0 else "fällt"

    lvl = sig["entry"] - d * 0.5 * sig["atr"]
    inval = [
        f"Stop {fmt(sig['stop'])} wird erreicht",
        INVALIDATION_BY_KIND[strat.kind].format(tf=settings.entry_tf, lvl=lvl),
        f"bis {exp:%H:%M} Uhr das Ziel nicht erreicht ist (These verfällt)",
    ]
    if live_ctx.get("upcoming"):
        inval.append(f"ein Wirtschaftstermin ({live_ctx['upcoming'][0]}) den Markt dreht")

    ctx = {
        "marktphase": f"{row['regime']} (ADX {row['adx']:.0f})",
        "session": SESSION_LABEL.get(row["session"], row["session"]),
        "news": live_ctx.get("news_label", "unbekannt"),
        "dollar": live_ctx.get("usd_desc", "unbekannt"),
        "zinsen": live_ctx.get("rates_desc", "unbekannt"),
        "termine": live_ctx.get("upcoming") or ["keine wichtigen in den nächsten 24 Std."],
        "cot": live_ctx.get("cot_desc", "keine Daten"),
        "positionsbuch": live_ctx.get("book_desc", "keine Daten"),
        "trend_htf": {tf: _dir_word(row.get(f"htf_{tf}")) for tf in settings.trend_tfs},
    }

    lines = [
        f"{word} XAU/USD – {strat.title} ({sig['variant'].split('|')[1]})",
        f"These: Gold {move} bis {exp:%H:%M} Uhr von {fmt(sig['entry'])} auf {fmt(sig['target'])}, "
        f"bevor {fmt(sig['stop'])} erreicht wird (Stop {settings.stop_atr:g}×ATR, Ziel {settings.target_atr:g}×ATR, ATR {sig['atr']:.2f}).",
        f"Auslöser: {strat.explain}",
        "Dafür: " + ("; ".join(sig["factors_for"]) if sig["factors_for"] else "–"),
        "Dagegen: " + ("; ".join(sig["factors_against"]) if sig["factors_against"] else "–"),
        "Scheitert, wenn: " + "; ".join(inval),
        f"Kontext: Phase {ctx['marktphase']}, Session {ctx['session']}, News {ctx['news']}, Dollar {ctx['dollar']}, "
        f"Zinsen {ctx['zinsen']}, Trend " + ", ".join(f"{k} {v}" for k, v in ctx["trend_htf"].items()) + f", {ctx['cot']}",
    ]
    if sig.get("risk_flags"):
        lines.append("Risiko: " + "; ".join(sig["risk_flags"]))
    text = "\n".join(lines)

    if settings.claude_thesis:
        better = claude_thesis(sig, ctx, inval, settings)
        if better:
            text = better + "\n\n---\n" + text
    return text, "; ".join(inval), ctx


def _dir_word(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "unbekannt"
    if v != v:  # NaN
        return "unbekannt"
    return "aufwärts" if v > 0 else "abwärts" if v < 0 else "neutral"


def claude_thesis(sig: dict, ctx: dict, inval: list[str], settings) -> str | None:
    """Optional: Claude formuliert eine kurze, nüchterne These auf Deutsch. Fehler -> None (Standardtext bleibt)."""
    try:
        import anthropic
    except ImportError:
        log.warning("CLAUDE_THESIS=true, aber Paket 'anthropic' ist nicht installiert (pip install anthropic)")
        return None
    payload = {
        "richtung": sig["direction"], "strategie": STRATEGIES[sig["strategy"]].title, "variante": sig["variant"],
        "einstieg": sig["entry"], "stop": sig["stop"], "ziel": sig["target"], "verfall": sig["expires_at"],
        "dafuer": sig["factors_for"], "dagegen": sig["factors_against"], "scheitert_wenn": inval, "kontext": ctx,
        "risiko_hinweise": sig.get("risk_flags", []),
    }
    system = ("Du formulierst für ein Lern-Journal kurze, nüchterne Handelsthesen zu Gold (XAU/USD) auf Deutsch. "
              "Keine Anlageberatung, keine Übertreibungen, keine erfundenen Fakten – nutze nur die gelieferten Daten. "
              "Format: 3–5 Sätze: These, wichtigste Argumente dafür, wichtigstes Gegenargument, woran sie scheitert.")
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=settings.claude_model,
            max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIStatusError as e:
        log.warning("Claude-API Fehler %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("Claude-API nicht erreichbar: %s", e)
        return None
    if resp.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return text or None
