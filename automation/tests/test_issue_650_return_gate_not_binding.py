"""Issue #650 — `min_total_return = 0.005` war das quasi-alleinige, nicht-risikoadjustierte
Selektions-Gate.

Mit den vier toten Gates (#649) reduzierte sich das harte Selektions-Gate faktisch auf
`min_trades ∧ min_total_return(0,005) ∧ max_drawdown ∧ min_expectancy` — ein Trial mit hohem PSR und
positivem Alpha, aber Return zwischen 0% und 0,5%, wurde ALLEIN am Return-Gate verworfen.

Fix: `min_total_return` aus `eligible_requires_all` entfernt (#650); die Profitabilitätsentscheidung
tragen die risikoadjustierten Gates `oos_min_psr`/`oos_min_excess_return` (jetzt real durchsetzbar,
#649).

Dieser Test lädt die ECHTE `tournament.json` und reproduziert exakt das #650-Akzeptanzkriterium:
ein Trial mit `total_return=0.003, psr=0.9, excess=0.02` ist eligible; ein Trial mit
`total_return=0.05, psr=0.4, excess=-0.01` ist NICHT eligible.
"""
import json
from pathlib import Path

from automation.backtest_runner import _evaluate_oos_eligibility

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def _oos(*, total_return, psr, excess, sortino=1.5, win_rate=0.4, pf=1.3, trades=40,
         alpha_tstat=2.5):
    return {
        "total_trades": trades, "max_drawdown": 0.05, "win_rate": win_rate,
        "total_return": total_return, "expectancy": 0.02, "sortino_ratio": sortino, "psr": psr,
        "profit_factor": pf, "median_position_notional": 1000.0,
        "oos_folds_total": 4, "oos_fold_sortinos": [sortino] * 4,
        # Alle 4 Folds profitabel — isoliert den Test auf die Return-/PSR-/Excess-Gates (das
        # #550-Fold-Konsistenz-Gate ist ein orthogonales Kriterium, nicht Gegenstand von #650).
        "oos_profitable_folds": 4,
        "oos_excess_return": excess,
        # Issue #1093/#1241 — t(α) ist seither Teil von eligible_requires_all (TCFG laedt die
        # ECHTE tournament.json); ein Default über der Schwelle isoliert diese Tests weiterhin auf
        # die Return-/PSR-/Excess-Gates, nicht Gegenstand von #650.
        "oos_alpha_tstat": alpha_tstat,
    }


def test_min_total_return_removed_from_hard_gate():
    """Strukturelle Voraussetzung des Fixes: min_total_return ist kein hartes Gate mehr."""
    assert "min_total_return" not in TCFG["eligible_requires_all"]


def test_low_return_high_psr_positive_alpha_trial_is_eligible():
    """#650-Akzeptanzkriterium: total_return=0.003 (< der alten 0.005-Schwelle), psr=0.9 (hoch),
    excess=0.02 (positives Alpha) ⇒ eligible — das Return-Gate darf das NICHT mehr allein verwerfen."""
    oos = _oos(total_return=0.003, psr=0.9, excess=0.02)
    ev = _evaluate_oos_eligibility(oos, TCFG)
    assert ev["oos_eligible"] is True, ev["oos_rejection_reasons"]


def test_high_return_low_psr_negative_alpha_trial_is_not_eligible():
    """#650-Akzeptanzkriterium: total_return=0.05 (hoch) rettet NICHT, wenn psr=0.4 (niedrig) und
    excess=-0.01 (unterbietet Buy&Hold) — die risikoadjustierten Gates diskriminieren jetzt korrekt,
    nicht mehr der absolute Return."""
    oos = _oos(total_return=0.05, psr=0.4, excess=-0.01)
    ev = _evaluate_oos_eligibility(oos, TCFG)
    assert ev["oos_eligible"] is False
    reasons = " ".join(ev["oos_rejection_reasons"])
    assert "oos_min_psr" in reasons or "oos_min_excess_return" in reasons


def test_return_gate_delta_no_longer_drives_rejection():
    """Ein Trial mit Return knapp unter der alten 0.005-Schwelle, aber starkem risikoadjustierten
    Profil, wird NICHT mehr wegen total_return verworfen (die Rejection-Gründe, falls vorhanden,
    dürfen 'oos_min_total_return' nicht enthalten — die Klausel ist nicht mehr Teil des Gates)."""
    oos = _oos(total_return=0.001, psr=0.95, excess=0.03, sortino=2.0, pf=1.5)
    ev = _evaluate_oos_eligibility(oos, TCFG)
    assert not any("oos_min_total_return" in r for r in ev["oos_rejection_reasons"])
    assert ev["oos_eligible"] is True, ev["oos_rejection_reasons"]
