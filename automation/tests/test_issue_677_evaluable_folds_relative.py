"""Issue #677 — oos_min_evaluable_folds: absoluter Fold-Zähler bestraft frequenz-heterogene Configs.

1. Redundanz-Fix: das Gate ist NICHT mehr im DEFAULT ``eligible_requires_all`` (die Mindest-
   Stichprobe ist bereits über ``min_trades``/``min_expectancy`` gesichert).
2. Relative-Schwellen-Modus: ein Wert in ``(0, 1]`` wird als Mindest-Anteil an
   ``oos_folds_evaluable`` (Folds MIT Signal) interpretiert, statt eines absoluten Zählers — robust
   gegen variable Fold-Anzahl je Trial. Ein Wert ``>= 1`` bleibt bit-identisch zum Legacy-Zähler.
"""
import json
from pathlib import Path

from automation.backtest_runner import _evaluate_oos_eligibility

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_gate_absent_from_default_requires_all():
    assert "oos_min_evaluable_folds" not in TCFG["eligible_requires_all"]
    assert "oos_min_evaluable_folds" in TCFG  # Schema-Key bleibt erhalten (reaktivierbar)


def test_frequency_heterogeneous_config_not_rejected_against_real_config():
    """Eine Config mit >= oos_min_trades gepoolten OOS-Trades, aber nur 1 evaluierbarem Fold von 4,
    wird gegen die ECHTE tournament.json NICHT wegen oos_min_evaluable_folds abgelehnt."""
    oos_metrics = {
        "total_trades": 40, "max_drawdown": 0.05, "win_rate": 0.4,
        "expectancy": 0.001, "total_return": 0.02, "sortino_ratio": 0.5,
        "profit_factor": 1.2, "median_position_notional": 1000.0,
        "psr": 0.9, "oos_excess_return": 0.01, "oos_buyhold_return": 0.0,
        "oos_folds_total": 4, "oos_folds_evaluable": 1, "oos_fold_sortinos": [0.5],
    }
    ev = _evaluate_oos_eligibility(oos_metrics, TCFG)
    assert not any("oos_min_evaluable_folds" in r for r in ev["oos_rejection_reasons"])


_ABS_CFG = {
    "oos_min_trades": 1, "oos_min_total_return": -1.0, "oos_min_expectancy": -1.0,
    "oos_min_win_rate": 0.0, "oos_min_evaluable_folds": 2, "max_drawdown": 0.3,
    "eligible_requires_all": ["min_trades", "min_evaluable_folds"],
}

_REL_CFG = dict(_ABS_CFG, oos_min_evaluable_folds=0.5)


def _metrics(n_folds_total, n_folds_evaluable, n_valid_sortinos):
    return {
        "total_trades": 40, "max_drawdown": 0.02, "win_rate": 0.4, "total_return": 0.01,
        "expectancy": 0.01, "sortino_ratio": 1.0, "profit_factor": 1.2,
        "median_position_notional": 1000.0,
        "oos_folds_total": n_folds_total, "oos_folds_evaluable": n_folds_evaluable,
        "oos_fold_sortinos": [1.0] * n_valid_sortinos,
    }


def test_absolute_mode_unchanged_for_integer_threshold():
    """>= 1 bleibt der ABSOLUTE Zähler — bit-identisch zum Pre-#677-Verhalten."""
    degenerate = _metrics(n_folds_total=4, n_folds_evaluable=4, n_valid_sortinos=1)
    ev = _evaluate_oos_eligibility(degenerate, _ABS_CFG)
    assert ev["oos_eligible"] is False
    assert any("oos_min_evaluable_folds" in r for r in ev["oos_rejection_reasons"])

    healthy = _metrics(n_folds_total=4, n_folds_evaluable=4, n_valid_sortinos=4)
    ev2 = _evaluate_oos_eligibility(healthy, _ABS_CFG)
    assert ev2["oos_eligible"] is True


def test_relative_mode_accepts_frequency_heterogeneous_config():
    """Nur 2 von 4 Folds haben ueberhaupt Signal (oos_folds_evaluable=2); beide sind valide
    (n_valid_sortinos=2) ⇒ Anteil 2/2=1.0 >= 0.5 ⇒ eligible, obwohl der ABSOLUTE Zähler (2) an der
    alten Schwelle (2) knapp bestanden hätte, ein Trial mit NUR 1 evaluierbarem Fold aber am
    absoluten Zähler gescheitert wäre."""
    freq_heterogeneous = _metrics(n_folds_total=4, n_folds_evaluable=1, n_valid_sortinos=1)
    ev_abs = _evaluate_oos_eligibility(freq_heterogeneous, _ABS_CFG)
    assert ev_abs["oos_eligible"] is False  # absoluter Zaehler: 1 < 2 ⇒ abgelehnt

    ev_rel = _evaluate_oos_eligibility(freq_heterogeneous, _REL_CFG)
    assert ev_rel["oos_eligible"] is True  # relativ: 1/1 = 1.0 >= 0.5 ⇒ eligible


def test_relative_mode_rejects_genuinely_degenerate_config():
    """3 Folds haben Signal, aber nur 1 davon liefert einen validen Sortino (Degeneration TROTZ
    Signal) ⇒ Anteil 1/3 < 0.5 ⇒ weiterhin abgelehnt (das Gate behaelt seine legitime Funktion)."""
    degenerate_with_signal = _metrics(n_folds_total=4, n_folds_evaluable=3, n_valid_sortinos=1)
    ev = _evaluate_oos_eligibility(degenerate_with_signal, _REL_CFG)
    assert ev["oos_eligible"] is False
    assert any("relativ zu Folds mit Signal" in r for r in ev["oos_rejection_reasons"])


def test_relative_mode_falls_back_to_folds_total_when_evaluable_missing():
    """Legacy-Metrics-Dict ohne ``oos_folds_evaluable`` (kein #676-Aufruf) ⇒ Fallback auf
    ``oos_folds_total`` als Nenner (kein Crash, defensiv degradiert)."""
    legacy = {
        "total_trades": 40, "max_drawdown": 0.02, "win_rate": 0.4, "total_return": 0.01,
        "expectancy": 0.01, "sortino_ratio": 1.0, "profit_factor": 1.2,
        "median_position_notional": 1000.0,
        "oos_folds_total": 4, "oos_fold_sortinos": [1.0, 1.0],
    }
    ev = _evaluate_oos_eligibility(legacy, _REL_CFG)
    assert ev["oos_eligible"] is True  # 2/4 = 0.5 >= 0.5
