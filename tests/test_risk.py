from goldsignal import risk


def test_normal_size():
    ps = risk.position_size(1000, 1.0, stop_dist_usd=4.0, price_usd=3800, usd_to_acc=0.9)
    assert ps.status == risk.STATUS_OK
    assert ps.units == 2  # 10 € / (4 $ * 0.9) = 2,77 -> abgerundet 2
    assert ps.risk_acc <= 10
    assert ps.margin_acc == round(2 * 3800 * 0.9 * 0.05, 2)  # Hebel 1:20


def test_too_large_for_account_is_flagged_not_rounded_up():
    ps = risk.position_size(800, 1.0, stop_dist_usd=15.0, price_usd=3800, usd_to_acc=0.9)
    assert ps.status == risk.STATUS_TOO_LARGE
    assert ps.units == 0
    assert ps.min_unit_risk_acc == 13.5
    assert ps.max_stop_usd == round(8 / 0.9, 2)


def test_risk_capped_at_max():
    ps = risk.position_size(1000, 5.0, stop_dist_usd=1.0, price_usd=3800, usd_to_acc=1.0, max_risk_pct=2.0)
    assert ps.risk_target_acc == 20


def test_margin_cap():
    # Sehr enger Stop -> viele Unzen, aber Margin max. 50 % des Kontos
    ps = risk.position_size(1000, 1.0, stop_dist_usd=0.5, price_usd=3800, usd_to_acc=1.0)
    assert ps.units == 2  # 500 / (3800*0.05) = 2,6
    assert "Margin" in ps.note


def test_daily_loss():
    assert risk.daily_loss_reached(-30, 1000, 3.0)
    assert not risk.daily_loss_reached(-29.9, 1000, 3.0)
