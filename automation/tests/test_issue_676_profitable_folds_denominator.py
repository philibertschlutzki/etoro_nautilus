"""Issue #676 — oos_min_profitable_folds_frac: Nenner-Asymmetrie + Redundanz.

1. Nenner-Fix: der Nenner der Fraktion ist die Anzahl EVALUIERBARER Folds (mit tatsächlichem Return),
   nicht die Gesamtzahl der Folds (inkl. No-Trade-Folds). Ein No-Trade-Fold ist Signal-ABWESENHEIT,
   keine Unprofitabilität.
2. Redundanz-Fix: das Gate ist NICHT mehr im DEFAULT ``eligible_requires_all`` — es kann daher für
   ein Symbol mit positiver gepoolter Expectancy/PSR kein Solo-Ablehnungsgrund mehr sein.
"""
import json
from pathlib import Path

import pytest

from automation.backtest_runner import apply_fold_aggregation, _evaluate_oos_eligibility

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def _fold(total_return):
    return {"total_return": total_return, "sortino_ratio": 1.0, "sortino_period": 0.05}


def test_gate_absent_from_default_requires_all():
    """Akzeptanzkriterium: das Gate ist kein Solo-Ablehnungsgrund mehr für Configs mit positiver
    gepoolter Expectancy/PSR — es ist schlicht nicht mehr in der DEFAULT-Konjunktion enthalten."""
    assert "oos_min_profitable_folds_frac" not in TCFG["eligible_requires_all"]
    # Der Metrik-/Schwellen-Key selbst bleibt im Schema erhalten (reaktivierbar, Zero-Hardcoding).
    assert "oos_min_profitable_folds_frac" in TCFG


def test_real_config_combo_like_profile_not_rejected_by_profitable_folds():
    """Ein Combo-artiges Profil (1/4 Folds profitabel, aber positive gepoolte Expectancy/PSR/Excess)
    wird gegen die ECHTE tournament.json NICHT wegen oos_min_profitable_folds abgelehnt (weder als
    Solo- noch als Teilgrund) — das Gate ist gar nicht Teil der Konjunktion."""
    per_fold = [_fold(0.06), _fold(-0.015), _fold(-0.015), _fold(-0.015)]
    oos_metrics = {
        "total_trades": 40, "max_drawdown": 0.05, "win_rate": 0.4,
        "expectancy": 0.001, "total_return": 0.005, "sortino_ratio": 0.5,
        "profit_factor": 1.2, "median_position_notional": 1000.0,
        "psr": 0.9, "oos_excess_return": 0.01, "oos_buyhold_return": 0.0,
    }
    apply_fold_aggregation(oos_metrics, per_fold)
    assert oos_metrics["oos_profitable_folds_frac"] == 0.25  # weiterhin korrekt berechnet (Telemetrie)

    ev = _evaluate_oos_eligibility(oos_metrics, TCFG)
    assert not any("oos_min_profitable_folds" in r for r in ev["oos_rejection_reasons"])


def test_denominator_uses_evaluable_folds_not_total_folds():
    """1 profitabler Fold + 3 No-Trade-Folds ⇒ frac = 1.0, nicht 0.25 (wörtliches
    Akzeptanzkriterium)."""
    per_fold = [_fold(0.03), None, None, None]
    oos_metrics = {"total_return": 0.03, "total_trades": 10}
    apply_fold_aggregation(oos_metrics, per_fold)
    assert oos_metrics["oos_folds_total"] == 4
    assert oos_metrics["oos_folds_evaluable"] == 1
    assert oos_metrics["oos_profitable_folds_frac"] == 1.0


def test_denominator_fix_applies_symmetrically_to_recency_weighting():
    """Dieselbe Nenner-Asymmetrie existierte parallel in der recency-gewichteten Fraktion — auch
    dort duerfen No-Trade-Folds keinen Nenner-Beitrag leisten."""
    per_fold = [_fold(0.03), None, None, None]
    oos_metrics = {"total_return": 0.03, "total_trades": 10}
    apply_fold_aggregation(oos_metrics, per_fold)
    # Der einzige evaluierbare (und profitable) Fold traegt sein gesamtes Recency-Gewicht sowohl im
    # Zaehler als auch im Nenner ⇒ Fraktion = 1.0, unabhaengig von der Halbwertszeit.
    assert oos_metrics["oos_profitable_folds_frac_recency"] == pytest.approx(1.0)


def test_explicit_reactivation_still_works_with_fixed_denominator():
    """Ein Operator, der das Gate bewusst reaktiviert (minimale eigene eligible_requires_all-Liste,
    keine sonstigen Gates), sieht den KORRIGIERTEN (evaluierbare-Folds-)Nenner — kein Rueckfall auf
    die alte Asymmetrie."""
    cfg = {
        "oos_min_trades": 1,
        "oos_min_profitable_folds_frac": 0.5,
        "eligible_requires_all": ["min_trades", "oos_min_profitable_folds_frac"],
    }

    per_fold = [_fold(0.03), None, None, None]
    oos_metrics = {
        "total_trades": 10, "total_return": 0.03, "max_drawdown": 0.05,
        "median_position_notional": 1000.0,
    }
    apply_fold_aggregation(oos_metrics, per_fold)

    ev = _evaluate_oos_eligibility(oos_metrics, cfg)
    assert ev["oos_eligible"] is True
