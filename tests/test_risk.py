import math

from hlbot.risk import (
    SizingInput,
    liq_hyperliquid,
    liq_protocol,
    protocol_max_leverage,
    round_price,
    round_size,
    size_position,
)


def base(**kw):
    d = dict(
        side="long", entry=100.0, sl=97.0, tp=109.0, balance=11.0, available=11.0,
        coin_max_leverage=40, sz_decimals=3, mode="A",
    )
    d.update(kw)
    return SizingInput(**d)


def test_protocol_leverage_formula():
    assert protocol_max_leverage(1.5, 25) == 25  # 1/0.02 = 50 -> cap
    assert protocol_max_leverage(5.0, 25) == 18  # 1/0.055 = 18.18
    assert protocol_max_leverage(6.5, 25) == 14  # below the 15x floor


def test_liq_formulas_match_protocol():
    assert math.isclose(liq_protocol(100, 25, "long"), 100 * (1 - 0.04 + 0.005))
    assert math.isclose(liq_protocol(100, 25, "short"), 100 * (1 + 0.04 - 0.005))
    # Hyperliquid estimate is beyond entry on the right side
    assert liq_hyperliquid(100, 10, "long", 40) < 100
    assert liq_hyperliquid(100, 10, "short", 40) > 100


def test_mode_a_matches_protocol_numbers():
    s = size_position(base())  # SL 3% -> 25x, 80% margin
    assert s.ok, s.reasons
    # formula says 25x, but on Hyperliquid 25x would liquidate before a 3% stop -> 23x
    assert s.leverage == 23
    assert math.isclose(s.margin, 8.8, rel_tol=0.01)
    assert 65 < s.loss_pct_of_margin < 75


def test_mode_a_rejects_wide_sl_under_15x_floor():
    s = size_position(base(sl=93.0, tp=121.0))  # 7% SL -> 13x
    assert not s.ok
    assert any("15x floor" in r for r in s.reasons)


def test_mode_a_rejects_low_max_leverage_coin():
    s = size_position(base(coin_max_leverage=10))
    assert not s.ok
    assert any("coin max leverage" in r for r in s.reasons)


def test_mode_b_caps_loss_at_risk_pct():
    s = size_position(base(mode="B", mode_b_risk_pct=15.0, balance=100.0, available=100.0))
    assert s.ok, s.reasons
    assert 13.0 <= s.loss_pct_of_balance <= 15.5
    assert s.margin <= 85.0 + 1e-6


def test_min_sl_floor_enforced():
    s = size_position(base(sl=99.0, tp=103.0))
    assert any("hard floor" in r for r in s.reasons)


def test_min_rr_enforced():
    s = size_position(base(tp=105.0))
    assert any("R/R" in r for r in s.reasons)


def test_min_notional_enforced():
    s = size_position(base(margin_cap=0.30))  # 0.30 * 25 = $7.5 < $10
    assert any("minimum" in r for r in s.reasons)


def test_short_side():
    s = size_position(base(side="short", entry=100.0, sl=103.0, tp=91.0))
    assert s.ok, s.reasons
    assert s.liq_protocol > 103.0


def test_leverage_lowered_so_liq_is_beyond_sl():
    from hlbot.risk import safe_leverage
    lev = safe_leverage(100, 97, "long", 40, 25)
    assert liq_hyperliquid(100, lev, "long", 40) < 97
    assert liq_hyperliquid(100, lev + 1, "long", 40) >= 97


def test_rounding():
    assert round_size(1.23456, 3) == 1.234
    assert round_price(65432.17, 5) == 65432.0  # 5 sig figs, max 1 decimal
    assert round_price(0.123456789, 0) == 0.12346
