"""Issue #550 — Fold-Konsistenz-Gate + einheitliche Fold-Aggregation.

Ohne Fold-Konsistenz-Kriterium konnte eine Strategie mit einem starken und drei schwachen Folds
(gepoolt knapp positiv) das Gate passieren, obwohl sie in 3 von 4 Folds unprofitabel ist. Zudem
mischte das Gate pooled/fold/compound-Aggregation. Der Fix aggregiert die vier risikoadjustierten
Kennzahlen einheitlich als Fold-Median und führt das deklarative ``oos_min_profitable_folds_frac``-
Gate ein (opt-in, rückwärtskompatibel).
"""
import pytest

from automation.backtest_runner import apply_fold_aggregation, _evaluate_oos_eligibility


def _fold(total_return, sortino=1.0, win_rate=0.5, expectancy=0.02, profit_factor=1.5, total_trades=10):
    return {
        "total_return": total_return, "sortino_ratio": sortino, "win_rate": win_rate,
        "expectancy": expectancy, "profit_factor": profit_factor, "total_trades": total_trades,
        "max_drawdown": 0.05,
    }


CFG = {
    "oos_min_trades": 1,
    "oos_min_total_return": 0.0,
    "oos_min_expectancy": 0.0,
    "oos_min_win_rate": 0.0,
    "oos_min_profitable_folds_frac": 0.5,
    # Issue #649 — der condition_map-Handler heisst ``min_profitable_folds_frac`` (kanonische Form
    # der Config-Klausel ``oos_min_profitable_folds_frac``), NICHT ``min_profitable_folds`` (die alte,
    # bereits vor #649 abweichende Handler-Bezeichnung).
    "eligible_requires_all": ["min_trades", "min_profitable_folds_frac"],
}


def test_one_strong_three_weak_folds_rejected():
    """Akzeptanzkriterium: 1 starker + 3 schwache Folds (gepoolt knapp positiv) ⇒ abgelehnt."""
    per_fold = [_fold(0.06), _fold(-0.015), _fold(-0.015), _fold(-0.015)]  # 1/4 profitabel
    oos_metrics = {"sortino_ratio": 0.2, "win_rate": 0.4, "expectancy": 0.001,
                   "profit_factor": 1.05, "total_return": 0.005, "total_trades": 40,
                   "median_position_notional": 1000.0, "max_drawdown": 0.1}
    apply_fold_aggregation(oos_metrics, per_fold)
    assert oos_metrics["oos_profitable_folds"] == 1
    assert oos_metrics["oos_folds_total"] == 4

    ev = _evaluate_oos_eligibility(oos_metrics, CFG)
    assert ev["oos_eligible"] is False
    assert any("oos_min_profitable_folds" in r for r in ev["oos_rejection_reasons"])


def test_consistent_folds_pass():
    """3 von 4 Folds profitabel (frac 0.75 >= 0.5) ⇒ Bedingung erfüllt."""
    per_fold = [_fold(0.02), _fold(0.01), _fold(-0.005), _fold(0.015)]  # 3/4 profitabel
    oos_metrics = {"sortino_ratio": 1.0, "win_rate": 0.5, "expectancy": 0.02,
                   "profit_factor": 1.5, "total_return": 0.04, "total_trades": 40,
                   "median_position_notional": 1000.0, "max_drawdown": 0.05}
    apply_fold_aggregation(oos_metrics, per_fold)
    ev = _evaluate_oos_eligibility(oos_metrics, CFG)
    assert ev["oos_eligible"] is True


def test_gate_inactive_when_threshold_absent():
    """Zero-Hardcoding: fehlt oos_min_profitable_folds_frac ⇒ Bedingung inaktiv (rückwärtskompatibel)."""
    per_fold = [_fold(0.06), _fold(-0.015), _fold(-0.015), _fold(-0.015)]  # 1/4 profitabel
    oos_metrics = {"sortino_ratio": 0.2, "win_rate": 0.4, "expectancy": 0.02,
                   "profit_factor": 1.5, "total_return": 0.005, "total_trades": 40,
                   "median_position_notional": 1000.0, "max_drawdown": 0.05}
    apply_fold_aggregation(oos_metrics, per_fold)
    cfg_no_key = {"oos_min_trades": 1, "eligible_requires_all": ["min_trades", "min_profitable_folds_frac"]}
    ev = _evaluate_oos_eligibility(oos_metrics, cfg_no_key)
    # Ohne Schwelle greift min_profitable_folds_frac nicht ⇒ nur min_trades zählt (erfüllt).
    assert ev["oos_eligible"] is True


def test_gate_metrics_aggregation_split():
    """Issue #589 — sortino_ratio bleibt GEPOOLT (kohärent mit total_return), NICHT Fold-Median.
    Häufigkeitsmetriken bleiben ebenfalls pooled (Issue #574)."""
    import statistics
    per_fold = [
        _fold(0.02, sortino=2.0, win_rate=0.8, expectancy=0.03, profit_factor=3.0),
        _fold(-0.01, sortino=-1.0, win_rate=0.2, expectancy=-0.01, profit_factor=0.5),
        _fold(0.01, sortino=1.0, win_rate=0.6, expectancy=0.02, profit_factor=2.0),
    ]
    # Diese "gepoolten" Startwerte simulieren die Gesamt-Auswertung VOR apply_fold_aggregation
    oos_metrics = {"sortino_ratio": 99.0, "win_rate": 0.55, "expectancy": 0.015,
                   "profit_factor": 1.7, "total_return": 0.02, "total_trades": 30}
    apply_fold_aggregation(oos_metrics, per_fold)

    # Issue #589 — Sortino bleibt gepoolt (UNVERÄNDERT); der Fold-Median ist nur noch forensisch.
    assert oos_metrics["sortino_ratio"] == 99.0
    assert oos_metrics["sortino_ratio_fold_median"] == statistics.median([2.0, -1.0, 1.0])

    # Win Rate, Expectancy, Profit Factor bleiben pooled und werden zusätzlich unter _pooled gespeichert
    assert oos_metrics["win_rate"] == 0.55
    assert oos_metrics["win_rate_pooled"] == 0.55
    assert oos_metrics["expectancy"] == 0.015
    assert oos_metrics["expectancy_pooled"] == 0.015
    assert oos_metrics["profit_factor"] == 1.7
    assert oos_metrics["profit_factor_pooled"] == 1.7

    # total_return bleibt UNBERÜHRT (compoundiert an anderer Stelle).
    assert oos_metrics["total_return"] == 0.02


def test_profitable_folds_telemetry_present():
    """Telemetrie: oos_profitable_folds / oos_folds_total / oos_folds_evaluable / frac im Metrics-Dict.

    Issue #676 — der Nenner der Fraktion ist ``oos_folds_evaluable`` (Folds mit tatsächlichem
    Return), NICHT ``oos_folds_total`` (zählt den No-Trade-Fold ungefiltert mit): 2 profitable von
    3 EVALUIERBAREN Folds (der None-Fold ist Signal-ABWESENHEIT, keine Unprofitabilität) ⇒ 2/3, nicht
    2/4."""
    per_fold = [_fold(0.02), _fold(-0.01), None, _fold(0.01)]  # None-Fold (0 Trades) ignoriert
    oos_metrics = {"total_return": 0.02, "total_trades": 20}
    apply_fold_aggregation(oos_metrics, per_fold)
    assert oos_metrics["oos_folds_total"] == 4
    assert oos_metrics["oos_folds_evaluable"] == 3
    assert oos_metrics["oos_profitable_folds"] == 2
    assert oos_metrics["oos_profitable_folds_frac"] == pytest.approx(2.0 / 3.0)


def test_denominator_excludes_no_trade_folds_from_gate_acceptance_criterion():
    """Issue #676 — wörtliches Akzeptanzkriterium: 1 profitabler Fold + 3 No-Trade-Folds ⇒
    frac = 1.0 (nicht 0.25, wie es der alte n_folds_total-Nenner ergeben hätte)."""
    per_fold = [_fold(0.02), None, None, None]
    oos_metrics = {"total_return": 0.02, "total_trades": 10}
    apply_fold_aggregation(oos_metrics, per_fold)
    assert oos_metrics["oos_folds_total"] == 4
    assert oos_metrics["oos_folds_evaluable"] == 1
    assert oos_metrics["oos_profitable_folds"] == 1
    assert oos_metrics["oos_profitable_folds_frac"] == 1.0

    ev = _evaluate_oos_eligibility(oos_metrics, CFG)
    assert not any("oos_min_profitable_folds" in r for r in ev["oos_rejection_reasons"])
