"""Module 7 math, with the protocol's worked examples recomputed for Hyperliquid fees and minimums.

Hyperliquid base-tier fees: maker 0.015%, taker 0.045%, + 0.05% slippage -> C = 0.11% (Bybit example used 0.125%,
Bitget 0.13%). Hyperliquid's minimum order is $10 (Bitget's ~5 USDT). Maintenance rate = 1 / (2 x max leverage),
so the examples' "illustrative MMR 1%" is a 50x-max coin.
"""

import pytest

from hlbot.protocol import PROTOCOL as P
from hlbot.rules.sizing import (Fees, SizingInput, ceil_step, floor_step, leverage_for, liq_price, maintenance,
                                round_price, size)

HL = Fees(0.015, 0.045, 0.05, "test")


def run(**kw):
    base = dict(side="long", entry=92.5, sl=92.5 * 0.985, tp=92.5 * 1.051, equity=25.0, available=25.0, risk_pct=10.0,
                half_risk_state=False, sz_decimals=2, margin_tiers=[(0, 50)], fees=HL, funding_8h_pct=0.01)
    base.update(kw)
    return size(SizingInput(**base), P.risk, P.exchange, P.filters)


def test_module7_worked_example_on_hyperliquid():
    """Bybit v2.0 Module 7: equity $25, risk 10%, SL 1.5%, entry $92.50, MMR 1%, TP +5.1%."""
    z = run()
    assert HL.c_pct == pytest.approx(0.11)
    assert z.risk_mode == "normal" and z.planned_r == pytest.approx(2.50)
    # notional = 2.50 / (0.015 + 0.0011) = $155.28 -> qty 1.6787 rounded DOWN to 0.01 = 1.67
    assert z.qty == 1.67 and z.notional == pytest.approx(154.475)
    # leverage = floor(1 / (0.015 + 0.01 + 0.02)) = floor(22.2) = 22x (same as the Bybit example)
    assert z.mmr == pytest.approx(0.01) and z.leverage == 22
    assert z.margin == pytest.approx(7.0216, abs=1e-4)
    # Hyperliquid isolated liq: 92.5 - (7.0216 - 1.5448) / 1.67 / 0.99 = 89.187 (Bybit example: ~89.22)
    assert z.liq == pytest.approx(89.1873, abs=1e-3)
    assert z.liq_buffer_pct == pytest.approx(2.081, abs=1e-3)  # vs SL 91.1125, >= 1.5% required
    # net reward = 1.67 x 4.7175 - maker 0.0232 - taker exit 0.0731 - funding 0.01%/8h over 48h 0.0927 = $7.689
    assert z.net_reward == pytest.approx(7.6893, abs=1e-3)
    assert z.loss_at_sl == pytest.approx(2.48705, abs=1e-4)  # = notional x (SL% + C)
    assert z.net_rr == pytest.approx(3.092, abs=1e-3)        # Bybit example: 1:3.07
    assert z.ok, [c.line() for c in z.checks if not c.ok]


def test_module7_example_on_a_10x_max_coin():
    """Same trade on a coin whose max leverage is 10x (MMR 5%): leverage drops to the coin cap."""
    z = run(margin_tiers=[(0, 10)])
    assert z.mmr == pytest.approx(0.05)
    assert z.leverage == 10  # floor(1 / 0.085) = 11, capped at the coin's 10x
    assert z.margin == pytest.approx(15.4475)
    assert z.liq == pytest.approx(87.6316, abs=1e-3)
    assert z.ok


def test_chunk28_micro_phase_at_one_dollar_fails_on_hyperliquid():
    """Bitget v2.1 chunk 28 at $1. On Bitget ($5 minimum) SL 1.5% passed at 10% and SL 2.5% passed at micro 15%.
    On Hyperliquid ($10 minimum) nothing passes at $1: the minimum needs 16.1% risk even on a 1.5% stop."""
    for sl_pct, need in ((1.5, 16.1), (2.5, 26.1), (3.0, 31.1)):
        z = run(entry=2.0, sl=2.0 * (1 - sl_pct / 100), tp=2.0 * (1 + 4 * sl_pct / 100), equity=1.0, available=1.0,
                sz_decimals=1)
        fail = [c for c in z.checks if not c.ok]
        assert len(fail) == 1 and fail[0].rule == "exchange minimum"
        assert f"{need:.1f}% risk > 15% ceiling" in fail[0].detail


def test_micro_phase_on_hyperliquid_minimum():
    # $1.20, SL 1.5%: 10% risk buys $7.45 (< $10) -> micro raises risk only as far as the minimum needs: 13.4%
    z = run(entry=2.0, sl=1.97, tp=2.0 * 1.06, equity=1.2, available=1.2, sz_decimals=1)
    assert z.risk_mode == "micro" and z.notional == pytest.approx(10.0)
    assert z.risk_pct == pytest.approx(10 * 0.0161 / 1.2 * 100, abs=1e-6)
    assert z.loss_at_sl == pytest.approx(z.planned_r)
    assert z.ok, [c.line() for c in z.checks if not c.ok]
    # same equity, SL 2.5%: needs 21.75% > 15% -> skip
    z = run(entry=2.0, sl=1.95, tp=2.0 * 1.10, equity=1.2, available=1.2, sz_decimals=1)
    assert not z.ok and "21.8% risk" in z.checks[-1].detail


def test_micro_phase_never_in_half_risk_and_not_above_threshold():
    # $5 at 5% half risk, SL 3%: $8.04 < $10 -> micro not allowed in a half-risk state
    z = run(entry=2.0, sl=1.94, tp=2.0 * 1.12, equity=5.0, available=5.0, sz_decimals=1, risk_pct=5.0,
            half_risk_state=True)
    assert not z.ok and "half-risk" in z.checks[-1].detail
    # $5, SL 5%, normal: $9.78 < $10 and $5 < $6 micro threshold -> micro at 10.2%
    z = run(entry=2.0, sl=1.90, tp=2.0 * 1.20, equity=5.0, available=5.0, sz_decimals=1)
    assert z.risk_mode == "micro" and z.risk_pct == pytest.approx(10.22, abs=0.01)
    # $8 (above the $6 micro threshold), SL 13%: 10% buys $5.8 -> plain skip
    z = run(entry=2.0, sl=1.74, tp=2.0 * 1.6, equity=8.0, available=8.0, sz_decimals=1)
    assert not z.ok and "above the $6 micro phase" in z.checks[-1].detail


def test_net_rr_floor_and_funding_direction():
    # TP +4.5% on a 1.5% stop no longer clears 1:3 net
    z = run(tp=92.5 * 1.045)
    assert not z.ok and any(c.rule == "net R/R >= 1:3" and not c.ok for c in z.checks)
    # shorts receive positive funding: no funding cost
    zs = run(side="short", sl=92.5 * 1.015, tp=92.5 * 0.949)
    assert zs.funding_cost == 0 and zs.ok
    # deeply negative funding is a cost for shorts
    zs = run(side="short", sl=92.5 * 1.015, tp=92.5 * 0.949, funding_8h_pct=-0.05)
    assert zs.funding_cost == pytest.approx(zs.notional * 0.0005 / 8 * 48)


def test_loss_check_and_ceiling():
    # coarse size step: rounding down 1.678 -> 1 unit loses far more than 5% of the planned R
    z = run(sz_decimals=0)
    assert any(c.rule == "loss check" and not c.ok for c in z.checks)
    # risk above the ceiling is clamped to 15%
    z = run(risk_pct=40.0)
    assert z.risk_pct == 15.0 and z.loss_at_sl / 25 * 100 <= 15.0


def test_margin_cap_and_wrong_sides():
    z = run(available=5.0)  # $7.02 margin > 80% of $5
    assert any(c.rule == "margin <= 80% of available" and not c.ok for c in z.checks)
    z = run(sl=93.0)
    assert not z.ok and z.checks[0].rule == "bracket sides"
    z = run(sl=92.5 * 0.99)  # 1% stop
    assert any(c.rule == "SL >= 1.5%" and not c.ok and c.hard for c in z.checks)


def test_margin_tiers_and_helpers():
    tiers = [(0, 40), (150_000_000, 20)]
    mm, lev, mmr = maintenance(1000, tiers)
    assert lev == 40 and mmr == pytest.approx(1 / 80) and mm == pytest.approx(12.5)
    mm2, lev2, _ = maintenance(200_000_000, tiers)
    # continuous at the boundary: 150M x 1.25% == 150M x 2.5% - deduction
    assert lev2 == 20 and mm2 == pytest.approx(200e6 * 0.025 - 150e6 * (0.025 - 0.0125))
    assert leverage_for(1.5, 0.01, 2.0, 25) == 22 and leverage_for(0.5, 0.0, 0.0, 25) == 25
    assert liq_price(100, 1, 10, 1, "short", 0.01) == pytest.approx(100 + 9 / 1.01)
    assert floor_step(1.6787, 2) == 1.67 and ceil_step(3.001, 1) == 3.1 and ceil_step(5.0, 1) == 5.0
    assert round_price(1.234567, 2) == 1.2346 and round_price(123456.7, 0) == 123460.0
