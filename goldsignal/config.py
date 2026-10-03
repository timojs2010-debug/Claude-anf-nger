"""Konfiguration aus .env bzw. Umgebungsvariablen. Keine Schlüssel im Code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv ist in requirements.txt
    load_dotenv = None


def _raw(name: str) -> str:
    raw = os.environ.get(name, "").strip()
    # "KEY=   # Kommentar" liefert bei python-dotenv den Kommentar als Wert -> als leer behandeln
    return "" if raw.startswith("#") else raw


def _str(name: str, default: str = "") -> str:
    return _raw(name) or default


def _float(name: str, default: float) -> float:
    raw = _raw(name)
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise ValueError(f"Konfiguration {name}={raw!r} ist keine Zahl") from None


def _int(name: str, default: int) -> int:
    return int(_float(name, default))


def _bool(name: str, default: bool) -> bool:
    raw = _raw(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "ja", "on")


def _list(name: str, default: str) -> list[str]:
    raw = _str(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    # OANDA
    oanda_token: str = ""
    oanda_account_id: str = ""
    oanda_env: str = "practice"  # practice = Demokonto, live = Echtgeld
    instrument: str = "XAU_USD"
    usd_proxy_instrument: str = "EUR_USD"
    bond_instrument: str = "USB10Y_USD"

    # Konto & Risiko
    account_size: float = 1000.0
    account_currency: str = "EUR"
    use_account_balance: bool = True
    risk_pct: float = 1.0
    max_risk_pct: float = 2.0
    max_leverage: float = 20.0
    daily_loss_limit_pct: float = 3.0
    min_trade_units: float = 1.0  # Fallback, falls OANDA-Abfrage scheitert
    max_margin_share: float = 0.5  # max. Anteil des Kontos als Margin für einen Trade

    # Zeitrahmen & Signal-Parameter
    entry_tf: str = "M15"
    trend_tfs: list[str] = field(default_factory=lambda: ["H1", "H4"])
    atr_period: int = 14
    stop_atr: float = 1.5
    target_atr: float = 2.0
    max_hold_hours: float = 4.0
    max_spread_to_stop: float = 0.20
    default_spread: float = 0.40  # USD, Fallback wenn kein Bid/Ask vorhanden
    adx_trend: float = 22.0
    round_step: float = 50.0
    event_block_hours: float = 1.5

    # Benachrichtigung
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    cooldown_minutes: int = 60
    min_strength_main: int = 3   # London/New York
    min_strength_off: int = 5    # Asien/Nacht: nur starke Signale
    quiet_hours: str = ""        # z. B. "23:00-07:00" (lokale Zeit)
    notify_variants: list[str] = field(default_factory=lambda: ["*"])
    notify_too_large: bool = True
    daily_summary_time: str = "22:30"
    timezone: str = "Europe/Berlin"

    # Datenquellen
    news_enabled: bool = True
    calendar_enabled: bool = True
    cot_enabled: bool = True
    positionbook_enabled: bool = True
    events_csv: str = "data/events.csv"

    # Claude (optional)
    claude_thesis: bool = False
    claude_model: str = "claude-opus-5-5"

    # Pfade & Betrieb
    db_path: str = "data/goldsignal.db"
    report_path: str = "reports/report.html"
    loop_interval_sec: int = 60
    stale_data_minutes: int = 45

    # Backtest
    backtest_days: int = 365
    oos_fraction: float = 0.3
    walk_forward_folds: int = 6
    random_runs: int = 300
    min_trades_reliable: int = 30
    random_seed: int = 42

    @property
    def oanda_base_url(self) -> str:
        if self.oanda_env == "live":
            return "https://api-fxtrade.oanda.com"
        return "https://api-fxpractice.oanda.com"

    @property
    def has_oanda(self) -> bool:
        return bool(self.oanda_token)


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    if load_dotenv is not None:
        path = Path(env_file) if env_file else Path.cwd() / ".env"
        if path.exists():
            load_dotenv(path, override=False)

    s = Settings(
        oanda_token=_str("OANDA_TOKEN"),
        oanda_account_id=_str("OANDA_ACCOUNT_ID"),
        oanda_env=_str("OANDA_ENV", "practice").lower(),
        instrument=_str("INSTRUMENT", "XAU_USD"),
        usd_proxy_instrument=_str("USD_PROXY_INSTRUMENT", "EUR_USD"),
        bond_instrument=_str("BOND_INSTRUMENT", "USB10Y_USD"),
        account_size=_float("ACCOUNT_SIZE", 1000.0),
        account_currency=_str("ACCOUNT_CURRENCY", "EUR").upper(),
        use_account_balance=_bool("USE_ACCOUNT_BALANCE", True),
        risk_pct=_float("RISK_PCT", 1.0),
        max_risk_pct=_float("MAX_RISK_PCT", 2.0),
        max_leverage=_float("MAX_LEVERAGE", 20.0),
        daily_loss_limit_pct=_float("DAILY_LOSS_LIMIT_PCT", 3.0),
        min_trade_units=_float("MIN_TRADE_UNITS", 1.0),
        max_margin_share=_float("MAX_MARGIN_SHARE", 0.5),
        entry_tf=_str("ENTRY_TF", "M15").upper(),
        trend_tfs=[x.upper() for x in _list("TREND_TFS", "H1,H4")],
        atr_period=_int("ATR_PERIOD", 14),
        stop_atr=_float("STOP_ATR", 1.5),
        target_atr=_float("TARGET_ATR", 2.0),
        max_hold_hours=_float("MAX_HOLD_HOURS", 4.0),
        max_spread_to_stop=_float("MAX_SPREAD_TO_STOP", 0.20),
        default_spread=_float("DEFAULT_SPREAD", 0.40),
        adx_trend=_float("ADX_TREND", 22.0),
        round_step=_float("ROUND_STEP", 50.0),
        event_block_hours=_float("EVENT_BLOCK_HOURS", 1.5),
        ntfy_server=_str("NTFY_SERVER", "https://ntfy.sh").rstrip("/"),
        ntfy_topic=_str("NTFY_TOPIC"),
        ntfy_token=_str("NTFY_TOKEN"),
        telegram_bot_token=_str("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_str("TELEGRAM_CHAT_ID"),
        cooldown_minutes=_int("COOLDOWN_MINUTES", 60),
        min_strength_main=_int("MIN_STRENGTH_MAIN", 3),
        min_strength_off=_int("MIN_STRENGTH_OFF", 5),
        quiet_hours=_str("QUIET_HOURS"),
        notify_variants=_list("NOTIFY_VARIANTS", "*"),
        notify_too_large=_bool("NOTIFY_TOO_LARGE", True),
        daily_summary_time=_str("DAILY_SUMMARY_TIME", "22:30"),
        timezone=_str("TIMEZONE", "Europe/Berlin"),
        news_enabled=_bool("NEWS_ENABLED", True),
        calendar_enabled=_bool("CALENDAR_ENABLED", True),
        cot_enabled=_bool("COT_ENABLED", True),
        positionbook_enabled=_bool("POSITIONBOOK_ENABLED", True),
        events_csv=_str("EVENTS_CSV", "data/events.csv"),
        claude_thesis=_bool("CLAUDE_THESIS", False),
        claude_model=_str("CLAUDE_MODEL", "claude-opus-5-5"),
        db_path=_str("DB_PATH", "data/goldsignal.db"),
        report_path=_str("REPORT_PATH", "reports/report.html"),
        loop_interval_sec=_int("LOOP_INTERVAL_SEC", 60),
        stale_data_minutes=_int("STALE_DATA_MINUTES", 45),
        backtest_days=_int("BACKTEST_DAYS", 365),
        oos_fraction=_float("OOS_FRACTION", 0.3),
        walk_forward_folds=_int("WALK_FORWARD_FOLDS", 6),
        random_runs=_int("RANDOM_RUNS", 300),
        min_trades_reliable=_int("MIN_TRADES_RELIABLE", 30),
        random_seed=_int("RANDOM_SEED", 42),
    )
    validate(s)
    return s


def validate(s: Settings) -> None:
    if s.oanda_env not in ("practice", "live"):
        raise ValueError("OANDA_ENV muss 'practice' oder 'live' sein")
    if not 0 < s.risk_pct <= s.max_risk_pct:
        raise ValueError(f"RISK_PCT muss zwischen 0 und MAX_RISK_PCT ({s.max_risk_pct}) liegen")
    if s.max_risk_pct > 2.0:
        raise ValueError("MAX_RISK_PCT über 2 % ist für dieses Konto nicht vorgesehen")
    if s.entry_tf not in ("M5", "M15"):
        raise ValueError("ENTRY_TF muss M5 oder M15 sein (Intraday-Einstiege)")
    if s.stop_atr <= 0 or s.target_atr <= 0:
        raise ValueError("STOP_ATR und TARGET_ATR müssen positiv sein")
