"""Issue #684 — Expectancy-Gate-Doppelschwelle: statisch 0,001 (Config) vs. kostenadjustiert
7,5e-5 (k_alpha·c_rt).

Root-Cause: fehlt bei gesetztem k_alpha die Live-Kosten-Telemetrie (round_trip_cost_bps), fiel das
effektive Gate STUMM auf das ~13× strengere statische oos_min_expectancy (0.001) zurück — zwei
unabhängig gepflegte Schwellen. Fix: ein aus DEMSELBEN Kostenmodell (backtest.json) abgeleiteter
Default-c_rt hält den Fallback in derselben Grössenordnung wie der Regelpfad.
"""
import json

from automation.backtest_runner import _evaluate_oos_eligibility, _read_default_round_trip_cost_bps


def _oos(expectancy, cost_rt_bps=None):
    m = {
        "total_trades": 30, "max_drawdown": 0.05, "win_rate": 0.5,
        "total_return": 0.05, "expectancy": expectancy,
        "sortino_ratio": 2.0, "profit_factor": 1.5,
        "median_position_notional": 1000.0,
    }
    if cost_rt_bps is not None:
        m["round_trip_cost_bps"] = cost_rt_bps
    return m


def _cfg(**over):
    cfg = {
        "min_trades": 1, "oos_min_trades": 1,
        "oos_min_total_return": 0.0, "oos_min_expectancy": 0.001,
        "oos_min_win_rate": 0.0, "oos_min_sortino": 0.0, "oos_min_profit_factor": 0.0,
        "max_drawdown": 1.0,
        "eligible_requires_all": ["min_trades", "min_expectancy"],
        "eligible_requires_any": ["min_sortino"],
    }
    cfg.update(over)
    return cfg


def test_default_round_trip_cost_bps_reads_real_backtest_config():
    """Der Reader liest aus der ECHTEN backtest.json (commission_bps + spread DEFAULT) — Single
    Source of Truth, kein unabhängig geratener Wert."""
    val = _read_default_round_trip_cost_bps()
    bt = json.loads(open("automation/config/backtest.json", encoding="utf-8").read())
    expected = bt["commission_bps"] + bt["spread_bps_by_asset_class"]["DEFAULT"]
    assert val == expected


def test_fallback_gate_within_small_multiple_of_reference_gate():
    """Akzeptanzkriterium: das effektive Gate ist unabhängig von der Telemetrie-Verfügbarkeit
    ≤ 2× konsistent (statt der alten ~13×-Diskontinuität)."""
    cfg = _cfg(oos_min_expectancy_k_alpha=0.25)
    reference_gate = _evaluate_oos_eligibility(
        _oos(1e-4, cost_rt_bps=3.0), cfg)["effective_expectancy_gate"]  # 7.5e-5 (Referenz, c_rt=3bps)
    fallback_gate = _evaluate_oos_eligibility(
        _oos(1e-4, cost_rt_bps=None), cfg)["effective_expectancy_gate"]  # Config-Default-c_rt

    assert reference_gate is not None and fallback_gate is not None
    ratio = fallback_gate / reference_gate
    assert 0.5 <= ratio <= 2.0, f"Fallback-Gate ist {ratio:.1f}x vom Referenzgate entfernt (> 2x)"
    # Und explizit NICHT mehr das alte statische 0.001 (13x strenger als die Referenz).
    assert fallback_gate < 0.001


def test_cost_source_telemetry_when_available():
    cfg = _cfg(oos_min_expectancy_k_alpha=0.25)
    res = _evaluate_oos_eligibility(_oos(1e-4, cost_rt_bps=3.0), cfg)
    assert res["expectancy_gate_cost_source"] == "telemetry"


def test_cost_source_config_default_when_telemetry_missing():
    cfg = _cfg(oos_min_expectancy_k_alpha=0.25)
    res = _evaluate_oos_eligibility(_oos(1e-4, cost_rt_bps=None), cfg)
    assert res["expectancy_gate_cost_source"] == "config_default"


def test_cost_source_static_when_k_alpha_absent():
    cfg = _cfg()
    res = _evaluate_oos_eligibility(_oos(1e-4, cost_rt_bps=None), cfg)
    assert res["expectancy_gate_cost_source"] == "static"
    assert res["effective_expectancy_gate"] is None


def test_degenerate_zero_trade_trial_still_stamps_cost_source():
    """Issue #577-Parität: auch der Not-Evaluated-Zweig (total_trades<=0) stempelt
    expectancy_gate_cost_source konsistent (kein KeyError bei Downstream-Konsumenten)."""
    cfg = _cfg(oos_min_expectancy_k_alpha=0.25)
    res = _evaluate_oos_eligibility({"total_trades": 0}, cfg)
    assert res["oos_evaluated"] is False
    assert "expectancy_gate_cost_source" in res
