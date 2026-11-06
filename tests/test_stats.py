import math

import pytest

from hlbot.stats import format_stats, summarize, verdict


def tr(pnl, entry=100.0, sl=98.0, qty=1.0, reason="tp", ts=0.0):
    return {"pnl": pnl, "entry": entry, "sl": sl, "qty": qty, "close_reason": reason, "closed_at": ts}


def test_summary_numbers():
    rows = [tr(6), tr(-2, reason="sl"), tr(-2, reason="sl"), tr(6), tr(-1, reason="max hold")]
    s = summarize(rows, start_balance=100)
    assert s["trades"] == 5 and s["wins"] == 2 and s["losses"] == 3
    assert s["win_rate"] == pytest.approx(40)
    assert s["total_pnl"] == pytest.approx(7)
    assert s["profit_factor"] == pytest.approx(12 / 5)
    assert s["avg_r"] == pytest.approx((3 - 1 - 1 + 3 - 0.5) / 5)  # risk = $2 per trade
    assert s["max_drawdown"] == pytest.approx(4)  # 106 -> 102
    assert s["max_drawdown_pct"] == pytest.approx(4 / 106 * 100)
    assert s["worst_losing_streak"] == 2
    assert s["return_pct"] == pytest.approx(7)
    assert s["close_reasons"] == {"tp": 2, "sl": 2, "max hold": 1}


def test_edge_cases_and_verdicts():
    assert summarize([])["trades"] == 0
    assert "No closed trades" in format_stats(summarize([]), "X")
    assert math.isinf(summarize([tr(1)])["profit_factor"])
    assert "too small" in verdict(summarize([tr(1)] * 5))
    assert "Negative" in verdict(summarize([tr(-1)] * 30))
    assert "Positive" in verdict(summarize([tr(3), tr(-1)] * 15))
    text = format_stats(summarize([tr(3), tr(-1)] * 15, 100), "PAPER")
    assert "Trades 30" in text and "profit factor 3.00" in text and "-> $130.00" in text
