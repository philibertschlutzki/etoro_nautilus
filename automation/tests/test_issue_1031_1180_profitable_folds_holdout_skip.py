"""Issue #1031/#1180 (P1) — ``oos_min_profitable_folds_frac`` ist am Holdout ein umbenanntes
Return-Gate.

Symptom (B-12): am Einfenster-Holdout (``oos_folds_total == 1``, tournament.json aggregation_note:
"pooled == der eine Fold") kann ``profitable_folds_frac`` nur 0 oder 1 sein — exakt dieselben zwei
Werte, die ``sign(oos_total_return)`` bereits trägt (ρ=1,0). Das Gate trägt am Holdout-Punkt keine
zusätzliche Information, erhöht aber die Zahl der konjunktiv verknüpften Bedingungen.

Fix: das Gate wird nur ausgewertet, wenn ``oos_folds_total >= 2``; darunter bleibt
``oos_gate_deltas`` ohne den Eintrag (SKIPPED_SINGLE_FOLD), und es kann keine Study mehr am Holdout
ablehnen.
"""
import json
from pathlib import Path

from automation.backtest_runner import _evaluate_oos_eligibility

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def _cfg_with_gate_reactivated():
    return {
        "oos_min_trades": 1,
        "oos_min_profitable_folds_frac": 0.5,
        "eligible_requires_all": ["min_trades", "min_profitable_folds_frac"],
    }


def test_single_fold_holdout_skips_the_gate_and_omits_its_delta():
    cfg = _cfg_with_gate_reactivated()
    # Einfenster-Holdout: oos_folds_total=1, negativer Return (das Gate WUERDE unter der alten
    # Logik hier failen, da 0 profitable Folds von 1).
    oos_metrics = {
        "total_trades": 10, "total_return": -0.02, "max_drawdown": 0.05,
        "median_position_notional": 1000.0, "oos_folds_total": 1, "oos_folds_evaluable": 1,
        "oos_profitable_folds": 0,
    }
    ev = _evaluate_oos_eligibility(oos_metrics, cfg)
    assert "oos_min_profitable_folds_frac" not in ev["oos_gate_deltas"]
    assert not any("SKIPPED" not in r and "profitable_folds" in r for r in ev["oos_rejection_reasons"])
    # Das Gate selbst darf am Einfoldigen Holdout keine Study mehr ablehnen.
    assert ev["oos_eligible"] is True


def test_multi_fold_sweep_still_evaluates_the_gate_normally():
    """Die 4-Fold-OOS-Sweep-Phase ist von diesem Fix unveraendert: das Gate bleibt dort aktiv und
    kann weiterhin binden."""
    cfg = _cfg_with_gate_reactivated()
    oos_metrics = {
        "total_trades": 40, "total_return": 0.01, "max_drawdown": 0.05,
        "median_position_notional": 1000.0, "oos_folds_total": 4, "oos_folds_evaluable": 4,
        "oos_profitable_folds": 1,  # 1/4 = 0.25 < 0.5 -> failt
    }
    ev = _evaluate_oos_eligibility(oos_metrics, cfg)
    assert "oos_min_profitable_folds_frac" in ev["oos_gate_deltas"]
    assert ev["oos_gate_deltas"]["oos_min_profitable_folds_frac"] < 0
    assert ev["oos_eligible"] is False


def test_two_fold_holdout_still_evaluates_the_gate():
    """Die Schwelle ist '< 2', nicht '<= 2' -- ein Zwei-Fenster-Holdout bleibt auswertbar."""
    cfg = _cfg_with_gate_reactivated()
    oos_metrics = {
        "total_trades": 20, "total_return": 0.01, "max_drawdown": 0.05,
        "median_position_notional": 1000.0, "oos_folds_total": 2, "oos_folds_evaluable": 2,
        "oos_profitable_folds": 0,  # 0/2 = 0.0 < 0.5 -> failt
    }
    ev = _evaluate_oos_eligibility(oos_metrics, cfg)
    assert "oos_min_profitable_folds_frac" in ev["oos_gate_deltas"]
    assert ev["oos_eligible"] is False
