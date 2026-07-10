"""Issue #552 — Benchmark-relatives Excess-Return-(Alpha-)Gate statt absolutem Return-Gate.

Ein absolutes ``oos_min_total_return`` vermischt Markt-Beta mit Strategie-Alpha: In einem
steigenden Markt ist +0.5 % durch bloßes Long-Bias trivial. Der Fix berechnet den Buy&Hold-Return
des Symbols über EXAKT dasselbe (halb-offene, deduplizierte) OOS-Fenster und ermöglicht ein
opt-in Gate auf ``oos_excess_return = oos_total_return − oos_buyhold_return``.
"""
import numpy as np
import pandas as pd
import pytest

import automation.backtest_runner as br
from automation.backtest_runner import _evaluate_oos_eligibility, extract_metrics

DAY_NS = 86400 * 1_000_000_000
HOUR_NS = 3600 * 1_000_000_000


def _fill(iid, price, qty, ts_ns, side):
    return {"instrument_id": iid, "price": price, "quantity": qty,
            "ts_event": ts_ns, "side": side, "commission": 0}


def test_buyhold_and_excess_telemetry_present():
    """extract_metrics schreibt oos_buyhold_return + oos_excess_return, wenn eine Benchmark vorliegt."""
    from unittest.mock import MagicMock
    start_ns = 1000 * DAY_NS
    wf = {"is_window_days": 2, "oos_window_days": 2, "splits": 1, "embargo_period_days": 0}
    # OOS-Fenster: [start+2d, start+4d).
    idx_ns = list(range(start_ns, start_ns + 4 * DAY_NS + 1, HOUR_NS))
    idx = pd.to_datetime(idx_ns, unit="ns")
    # Portfolio-Equity flach steigend; Benchmark (Symbol-Close) steigt im OOS stark (Bull-Markt).
    equity = pd.Series(np.linspace(10000.0, 10100.0, len(idx)), index=idx)
    close = pd.Series(np.linspace(100.0, 100.0, len(idx)), index=idx)
    # Close im OOS-Fenster von 100 auf 120 (Buy&Hold +20 %).
    oos_mask = (np.array(idx_ns) >= start_ns + 2 * DAY_NS)
    close.iloc[oos_mask] = np.linspace(100.0, 120.0, oos_mask.sum())

    # Zwei OOS-Round-Trips, damit oos_metrics evaluiert wird.
    e = start_ns + 2 * DAY_NS
    fills = pd.DataFrame([
        _fill("X.Y", 100.0, 1.0, e + HOUR_NS, "BUY"),
        _fill("X.Y", 101.0, 1.0, e + 2 * HOUR_NS, "SELL"),
        _fill("X.Y", 100.0, 1.0, e + 3 * HOUR_NS, "BUY"),
        _fill("X.Y", 102.0, 1.0, e + 4 * HOUR_NS, "SELL"),
    ])
    engine = MagicMock()
    engine.trader.generate_fills_report.return_value = fills

    res = extract_metrics(engine, 10000.0, None, wf, start_ns, mtm_series=equity, benchmark_series=close)
    oos = res["oos_metrics"]
    assert "oos_buyhold_return" in oos
    # Buy&Hold klar positiv (Bull-Markt); der exakte Wert (~0.196) folgt aus der halb-offenen
    # OOS-Fensterung (#551-Konvention: der Bar exakt am exklusiven Fenster-Ende zählt nicht mehr).
    assert 0.15 < oos["oos_buyhold_return"] < 0.21
    # Kernkontrakt: excess == Strategie-Return − Buy&Hold-Return (identisches OOS-Fenster).
    assert "oos_excess_return" in oos
    assert oos["oos_excess_return"] == pytest.approx(
        (oos["total_return"] or 0.0) - oos["oos_buyhold_return"], abs=1e-12)
    # Kein Alpha trotz Bull-Markt ⇒ Excess deutlich negativ.
    assert oos["oos_excess_return"] < 0.0


def test_excess_gate_rejects_beta_without_alpha():
    """Bull-Markt-Long-Bias ohne Alpha wird über das (opt-in) Excess-Gate abgelehnt."""
    oos_metrics = {
        "total_trades": 20, "total_return": 0.01, "max_drawdown": 0.05,
        "win_rate": 0.5, "expectancy": 0.02, "profit_factor": 1.5,
        "median_position_notional": 1000.0,
        "oos_buyhold_return": 0.20, "oos_excess_return": 0.01 - 0.20,  # = −0.19
    }
    cfg = {
        "oos_min_trades": 1, "oos_min_total_return": 0.005, "oos_min_excess_return": 0.0,
        "eligible_requires_all": ["min_trades", "min_total_return", "min_excess_return"],
    }
    ev = _evaluate_oos_eligibility(oos_metrics, cfg)
    assert ev["oos_eligible"] is False
    assert any("oos_min_excess_return" in r for r in ev["oos_rejection_reasons"])


def test_excess_gate_passes_true_alpha():
    """Excess > 0 (schlägt Buy&Hold) ⇒ Gate erfüllt."""
    oos_metrics = {
        "total_trades": 20, "total_return": 0.25, "max_drawdown": 0.05,
        "win_rate": 0.6, "expectancy": 0.03, "profit_factor": 2.0,
        "median_position_notional": 1000.0,
        "oos_buyhold_return": 0.20, "oos_excess_return": 0.05,
    }
    cfg = {
        "oos_min_trades": 1, "oos_min_total_return": 0.005, "oos_min_excess_return": 0.0,
        "eligible_requires_all": ["min_trades", "min_total_return", "min_excess_return"],
    }
    ev = _evaluate_oos_eligibility(oos_metrics, cfg)
    assert ev["oos_eligible"] is True


def test_gate_inactive_when_threshold_absent_legacy_absolute():
    """Zero-Hardcoding: ohne oos_min_excess_return greift das Legacy-Absolut-Gate (rückwärtskompatibel)."""
    oos_metrics = {
        "total_trades": 20, "total_return": 0.01, "max_drawdown": 0.05,
        "win_rate": 0.5, "expectancy": 0.02, "profit_factor": 1.5,
        "median_position_notional": 1000.0,
        "oos_buyhold_return": 0.20, "oos_excess_return": -0.19,
    }
    cfg = {  # kein oos_min_excess_return, kein min_excess_return in requires_all
        "oos_min_trades": 1, "oos_min_total_return": 0.005,
        "eligible_requires_all": ["min_trades", "min_total_return"],
    }
    ev = _evaluate_oos_eligibility(oos_metrics, cfg)
    assert ev["oos_eligible"] is True  # total_return 0.01 >= 0.005, Excess ignoriert

def test_degenerate_trial_expectancy_gate():
    """Issue #577: Degenerate (zero-trade) trials must correctly stamp the effective expectancy gate."""
    cfg = {
        "oos_min_expectancy_k_alpha": 2.5
    }
    # For degenerated trials n_trades is 0, but we want the expectancy gate calculation to happen
    oos_metrics = {
        "total_trades": 0,
        "round_trip_cost_bps": 3.0
    }

    res = _evaluate_oos_eligibility(oos_metrics, cfg)
    assert res["oos_evaluated"] is False
    assert res["effective_expectancy_gate"] == pytest.approx(2.5 * 3.0 / 10000.0, abs=1e-9)
