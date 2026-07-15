"""Issue #633 — `oos_min_win_rate ≥ 0.25` war ein strukturell unerfüllbarer OR-Arm.

Über 336 Trials war die maximal beobachtete OOS-Win-Rate 0.197 — die Schwelle 0.25 wurde nie
erreicht. `eligible_requires_any = ['min_profit_factor', 'min_win_rate']` kollabierte damit zu
reinem `PF ≥ 1.1`. Trend-/Breakout-Strategien auf 1h-Bars mit asymmetrischem Payoff haben
klassenbedingt niedrige Trefferquoten — eine 25-%-Schwelle ist für diese Klasse kategorial
unpassend.

Fix (Option 1, empfohlen): `oos_min_win_rate` auf 0.15 gesenkt (unter dem beobachteten Maximum,
weiterhin ein echter Filter). `check_any_arm_reachability` (reward.py) warnt fail-loud-artig, wenn
eine ANY-Arm-Schwelle über dem p99 der dokumentierten Kalibrier-Fixture-Verteilung liegt.
"""
import json
import logging
from pathlib import Path

from automation.optimizer.reward import (
    check_any_arm_reachability,
    assert_any_condition_parity,
    _CALIBRATION_FIXTURE_WIN_RATES,
)

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_production_threshold_lowered_below_observed_maximum():
    assert TCFG["oos_min_win_rate"] == 0.15
    assert TCFG["oos_min_win_rate"] < max(_CALIBRATION_FIXTURE_WIN_RATES)


def test_production_config_passes_reachability_check():
    """Die ausgelieferte tournament.json löst KEINE #633-Warnung mehr aus."""
    assert check_any_arm_reachability(TCFG) == []


def test_old_threshold_would_have_been_flagged_unreachable():
    """Regressionstest: die ALTE Schwelle (0.25) hätte die Prüfung korrekt als unerreichbar
    markiert — der Mechanismus hat also tatsächlich Biss."""
    stale_cfg = dict(TCFG, oos_min_win_rate=0.25)
    assert check_any_arm_reachability(stale_cfg) == ["min_win_rate"]


def test_reachability_warning_is_logged(caplog):
    stale_cfg = dict(TCFG, oos_min_win_rate=0.25)
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        check_any_arm_reachability(stale_cfg)
    assert any("#633" in r.getMessage() and "min_win_rate" in r.getMessage() for r in caplog.records)


def test_no_any_clauses_configured_is_noop():
    assert check_any_arm_reachability({"eligible_requires_any": []}) == []
    assert check_any_arm_reachability(None) == []


def test_clause_without_calibration_fixture_is_not_flagged():
    """min_sortino/min_profit_factor haben (noch) keine #633-Kalibrierung ⇒ kein False-Positive,
    egal wie hoch die Schwelle konfiguriert ist."""
    cfg = {"eligible_requires_any": ["min_sortino"], "oos_min_sortino": 999.0}
    assert check_any_arm_reachability(cfg) == []


def test_any_condition_parity_still_green_with_lowered_threshold():
    """Option 1 (Schwelle senken) betrifft NICHT die #593-Klauselmenge — min_win_rate bleibt in
    _ANY_CONDITION_CLAUSES, assert_any_condition_parity bleibt gruen."""
    assert_any_condition_parity(TCFG)  # darf NICHT werfen
