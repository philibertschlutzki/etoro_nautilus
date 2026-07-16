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

from automation.backtest_runner import _evaluate_oos_eligibility, _canonical_gate_key
from automation.optimizer.reward import assert_any_condition_parity, _ANY_CONDITION_CLAUSES

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_shipped_config_moves_risk_adjusted_gate_to_requires_all():
    # Issue #614 — das HARTE risikoadjustierte Kriterium ist seit #614 die PSR (min_psr), nicht mehr
    # der annualisierte Sortino (min_sortino, jetzt Telemetrie). Es bleibt in eligible_requires_all
    # (die #593-Anti-ODER-Bypass-Invariante gilt für die PSR statt den Sortino).
    # Issue #649 — die ausgelieferte Config schreibt die Klausel PRÄFIGIERT (``oos_min_psr``); ein
    # Vergleich gegen den blanken Namen ``min_psr`` (wie vor #649) prüft NIE die tatsächlich
    # ausgelieferte Config und wäre selbst ein Exemplar des #649-Fixture-vs-Produktion-Drifts (der
    # Grund, warum die vier Gates in Produktion still tot waren, während der #614-Test grün blieb).
    # Nach ``_canonical_gate_key``-Normalisierung ist die Schreibweise (mit/ohne ``oos_``-Präfix)
    # äquivalent — dieser Test prüft die kanonische Form gegen die ECHTE Datei.
    canonical_all = {_canonical_gate_key(k) for k in TCFG["eligible_requires_all"]}
    assert "min_psr" in canonical_all
    assert "min_sortino" not in canonical_all
    assert "min_psr" not in TCFG["eligible_requires_any"]
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


def test_negative_edge_trial_is_not_eligible():
    """Issue #614 — kein Trial mit niedriger PSR (negativer Edge) ist oos_eligible (min_psr in
    eligible_requires_all). Ein PF > 1.1 rettet ihn NICHT (die #593-Anti-ODER-Bypass-Invariante gilt
    jetzt für die PSR). Ein negativer per-Perioden-Sortino ⇒ PSR < 0.5 < oos_min_psr(0.75)."""
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    oos = {
        "total_trades": 300, "max_drawdown": 0.05, "win_rate": 0.5, "total_return": 0.1,
        "expectancy": 0.01, "sortino_ratio": -6.5, "psr": 0.10,   # negativer Edge ⇒ niedrige PSR
        "profit_factor": 1.169, "median_position_notional": 1000.0,
        "oos_folds_total": 4, "oos_fold_sortinos": [-6.5, -5.0, -7.0, -6.0],
        "oos_excess_return": 0.02,
    }
    ev = _evaluate_oos_eligibility(oos, cfg)
    assert ev["oos_eligible"] is False
    assert any("oos_min_psr" in r for r in ev["oos_rejection_reasons"])
