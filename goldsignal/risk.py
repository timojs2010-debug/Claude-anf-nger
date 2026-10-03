"""Positionsgrößenrechner und Tagesverlust-Grenze."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

STATUS_OK = "ok"
STATUS_TOO_LARGE = "zu groß für Konto"
STATUS_MARGIN = "Margin zu hoch"


@dataclass
class PositionSize:
    units: float              # Unzen (OANDA-Einheiten für XAU_USD)
    risk_target_acc: float    # gewünschtes Risiko in Kontowährung
    risk_acc: float           # tatsächliches Risiko mit gerundeter Größe
    risk_pct: float           # tatsächliches Risiko in % des Kontos
    margin_acc: float         # benötigte Margin in Kontowährung
    notional_acc: float
    leverage_used: float
    min_units: float
    min_unit_risk_acc: float  # Risiko bei Mindestgröße
    max_stop_usd: float       # größter Stop (USD je Unze), der mit Mindestgröße noch ins Risiko passt
    status: str
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def position_size(account: float, risk_pct: float, stop_dist_usd: float, price_usd: float,
                  usd_to_acc: float = 1.0, min_units: float = 1.0, units_precision: int = 0,
                  max_leverage: float = 20.0, margin_rate: float | None = None,
                  max_risk_pct: float = 2.0, max_margin_share: float = 0.5) -> PositionSize:
    """Berechnet die Größe so, dass ein Stop-Treffer höchstens risk_pct des Kontos kostet.

    usd_to_acc: Umrechnung 1 USD -> Kontowährung (bei EUR-Konto = 1 / EURUSD).
    Wird die Mindestgröße nicht erreicht, wird NICHT aufgerundet, sondern das Signal markiert.
    """
    if account <= 0 or stop_dist_usd <= 0 or price_usd <= 0:
        raise ValueError("Konto, Stop-Abstand und Preis müssen positiv sein")
    risk_pct = min(risk_pct, max_risk_pct)
    margin_rate = max(margin_rate or 0.0, 1.0 / max_leverage)  # EU-Privatkunde: Gold max. 1:20 -> 5 %
    risk_target = account * risk_pct / 100
    risk_per_unit_acc = stop_dist_usd * usd_to_acc
    raw_units = risk_target / risk_per_unit_acc
    factor = 10 ** units_precision
    units = math.floor(raw_units * factor) / factor

    max_margin = account * max_margin_share
    margin_per_unit = price_usd * usd_to_acc * margin_rate
    margin_cap_units = math.floor(max_margin / margin_per_unit * factor) / factor
    note = ""
    status = STATUS_OK
    if units < min_units:
        status = STATUS_TOO_LARGE
        note = (f"Stop {stop_dist_usd:.2f} USD zu weit: Mindestgröße {min_units:g} Unze(n) würde "
                f"{min_units * risk_per_unit_acc:.2f} riskieren (Ziel {risk_target:.2f}). Nicht handeln – Risiko NICHT erhöhen.")
        units = 0.0
    elif units > margin_cap_units:
        if margin_cap_units < min_units:
            status = STATUS_MARGIN
            note = "Margin für Mindestgröße übersteigt den erlaubten Kontoanteil."
            units = 0.0
        else:
            units = margin_cap_units
            note = "Größe durch Margin-/Hebelgrenze begrenzt."

    risk_acc = units * risk_per_unit_acc
    notional = units * price_usd * usd_to_acc
    return PositionSize(
        units=units,
        risk_target_acc=round(risk_target, 2),
        risk_acc=round(risk_acc, 2),
        risk_pct=round(100 * risk_acc / account, 2),
        margin_acc=round(notional * margin_rate, 2),
        notional_acc=round(notional, 2),
        leverage_used=round(notional / account, 2),
        min_units=min_units,
        min_unit_risk_acc=round(min_units * risk_per_unit_acc, 2),
        max_stop_usd=round(risk_target / (min_units * usd_to_acc), 2),
        status=status,
        note=note,
    )


def daily_loss_reached(pnl_today_acc: float, account: float, limit_pct: float) -> bool:
    return pnl_today_acc <= -account * limit_pct / 100
