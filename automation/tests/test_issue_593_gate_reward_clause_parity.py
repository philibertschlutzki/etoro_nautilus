"""Issue #593 — ``eligible_requires_any`` hebelte das Sortino-Gate aus.

190/600 Trials passierten die ODER-Klausel über den Profit-Factor, obwohl ihr Sortino negativ war —
eine Häufigkeits-Kennzahl (PF) ODER-verknüpft mit einem Risiko-adjustierten Kriterium (Sortino) ist
genau die Struktur, die ein Optimierer ausnutzt. Fix: der (nach #587–#589 kohärente) Sortino gehört in
``eligible_requires_all`` (HART); ``eligible_requires_any`` reduziert sich auf gleichgerichtete
Häufigkeits-Kennzahlen. ``_any_condition_distance`` spiegelt EXAKT die Config-Klauseln (Parität).
"""
import json
from pathlib import Path

import pytest

from automation.backtest_runner import _evaluate_oos_eligibility
from automation.optimizer.reward import assert_any_condition_parity, _ANY_CONDITION_CLAUSES

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_shipped_config_moves_sortino_to_requires_all():
    assert "min_sortino" in TCFG["eligible_requires_all"]
    assert "min_sortino" not in TCFG["eligible_requires_any"]
    assert set(TCFG["eligible_requires_any"]) == {"min_profit_factor", "min_win_rate"}


def test_any_condition_parity_passes_for_shipped_config():
    # Alle eligible_requires_any-Klauseln der ausgelieferten Config haben einen Distanz-Term.
    assert_any_condition_parity(TCFG)
    assert set(TCFG["eligible_requires_any"]).issubset(_ANY_CONDITION_CLAUSES)


def test_any_condition_parity_fails_loud_on_unknown_clause():
    """Eine Klausel ohne korrespondierenden _any_condition_distance-Term ⇒ ValueError (fail-loud)."""
    bogus = {"eligible_requires_any": ["min_profit_factor", "min_calmar_unsupported"]}
    with pytest.raises(ValueError, match="_any_condition_distance"):
        assert_any_condition_parity(bogus)


def test_negative_sortino_trial_is_not_eligible():
    """Kein Trial mit oos_sortino < oos_min_sortino ist nach dem Fix oos_eligible (Sortino in
    eligible_requires_all). Ein PF > 1.1 rettet ihn NICHT mehr (vorher der Ausweg über die ODER-Klausel).
    """
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    oos = {
        "total_trades": 300, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1,
        "expectancy": 0.01, "sortino_ratio": -6.5,            # negativ, weit unter oos_min_sortino
        "profit_factor": 1.169, "median_position_notional": 1000.0,
        "oos_folds_total": 4, "oos_fold_sortinos": [-6.5, -5.0, -7.0, -6.0],
        "oos_excess_return": 0.02,
    }
    ev = _evaluate_oos_eligibility(oos, cfg)
    assert ev["oos_eligible"] is False
    assert any("oos_min_sortino" in r for r in ev["oos_rejection_reasons"])
