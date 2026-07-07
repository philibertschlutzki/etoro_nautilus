"""Issue #561 — Kommissions-Semantik: commission_bps ist PER ROUND-TRIP, nicht per Leg.

Vor dem Fix verrechnete der FIFO-Match beide Legs (Entry + Exit) mit der VOLLEN Rate, sodass
``commission_bps = 1`` real 2 bps/Round-Trip kostete (2× der dokumentierten Semantik). Der Fix
halbiert die Rate je Leg, sodass die Summe genau ``commission_bps · notional_avg`` EINMAL beträgt.

Akzeptanzkriterien (Issue #561):
- Ein einzelner Round-Trip mit notional 10 000 USD und commission_bps=10 verrechnet EXAKT 10 USD.
- Property: ``expectancy_gross − expectancy_net == round_trip_cost`` bei Nullsignal-Testreihe.
"""
import pandas as pd
from unittest.mock import MagicMock

from automation.backtest_runner import extract_metrics


def _round_trip_fills(qty: float, entry_px: float, exit_px: float, entry_side: str = "BUY"):
    """Ein Round-Trip: Entry (BUY/SELL) → Gegen-Fill (Exit). ts in ns."""
    exit_side = "SELL" if entry_side == "BUY" else "BUY"
    return pd.DataFrame.from_records([
        {"instrument_id": "TSLA.ETORO", "last_qty": qty, "last_px": entry_px,
         "order_side": entry_side, "ts_event": 1_000 * 10**9},
        {"instrument_id": "TSLA.ETORO", "last_qty": qty, "last_px": exit_px,
         "order_side": exit_side, "ts_event": 4_600 * 10**9},
    ])


def _flat_metrics(result):
    return result["metrics"] if isinstance(result, dict) and "metrics" in result else result


def _run(commission_bps, entry_px=100.0, exit_px=100.0, qty=100.0):
    engine = MagicMock()
    engine.trader.generate_fills_report.return_value = _round_trip_fills(qty, entry_px, exit_px)
    result = extract_metrics(engine, starting_capital=10_000.0, commission_bps=commission_bps)
    return _flat_metrics(result)


def test_single_round_trip_charges_exactly_commission_bps_once():
    """notional 10 000 USD, commission_bps=10 ⇒ EXAKT 10 USD Kosten (nicht 20)."""
    # Entry 100 @ 100.0 ⇒ entry_notional = 10 000. Exit @ 100.0 ⇒ Brutto-PnL 0.
    m = _run(commission_bps=10.0)
    assert m["total_trades"] == 1
    # expectancy = pnl / entry_notional (notional-relativ, #546). Netto-PnL = -10 USD auf 10 000.
    net_cost_usd = -m["expectancy"] * 10_000.0
    assert net_cost_usd == 10.0, (
        f"Round-Trip-Kommission muss EXAKT 10 USD sein (per Round-Trip), war {net_cost_usd} USD "
        f"(2× ⇒ 20 USD wäre der alte Doppelbelastungs-Bug, Issue #561)."
    )


def test_round_trip_cost_equals_commission_bps_not_double():
    """Property: expectancy_gross − expectancy_net == commission_bps/1e4 (einmal, nicht 2×)."""
    m_gross = _run(commission_bps=0.0)
    m_net = _run(commission_bps=10.0)
    # Brutto (0 Kommission, gleicher Ein-/Ausstieg) ⇒ expectancy 0.
    assert m_gross["expectancy"] == 0.0
    delta = m_gross["expectancy"] - m_net["expectancy"]
    # 10 bps = 0.001 als notional-relativer Round-Trip-Cost. Doppelbelastung wäre 0.002.
    assert delta == 0.001, f"Round-Trip-Cost muss 10 bps (0.001) sein, war {delta} (0.002 = 2×-Bug)."


def test_commission_scales_linearly_single_charge():
    """commission_bps=1 ⇒ 1 bp Round-Trip (nicht 2 bps): die exakte Signatur aus dem Issue."""
    m = _run(commission_bps=1.0)
    net_cost_usd = -m["expectancy"] * 10_000.0
    # 1 bp auf 10 000 USD = 1 USD (einmal). Der alte Bug ergäbe 2 USD.
    assert round(net_cost_usd, 6) == 1.0
