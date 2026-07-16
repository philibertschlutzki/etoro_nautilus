"""Issue #660 — `min_win_rate`-OR-Arm weiterhin unerreichbar (erweitert #633).

`oos_min_win_rate = 0.15` liegt über der in EINEM SPEZIFISCHEN Lauf (TSLA.ETORO Hourly-Tier)
beobachteten OOS-Win-Rate (max ~0.11) — obwohl 0.15 UNTER dem #633-Cross-Strategy-Kalibrier-Fixture-
p99 (0.197) liegt und die bestehende, config-load-time `check_any_arm_reachability` (#633) die
Schwelle daher als 'erreichbar' einstuft. Der `Requires ANY of ['min_profit_factor',
'min_win_rate']`-Arm kollabiert für DIESEN Lauf still auf ein reines `PF >= 1.1`-Gate, ohne dass die
statische Prüfung (die noch keine Trials kennt) das je erkennen könnte.

Fix: `check_any_arm_reachability_live` (reward.py) prüft NACH Studienabschluss die konfigurierte
Schwelle gegen das p99 der TATSÄCHLICH in DIESER Study beobachteten Win-Rate-Verteilung
(`_emit_study_summary`, run_optimization.py) — ergänzend zum #633-Fixture-Check, nicht als Ersatz.
"""
import logging

import pytest

from automation.optimizer.reward import check_any_arm_reachability_live


_TCFG = {"eligible_requires_any": ["min_profit_factor", "min_win_rate"], "oos_min_win_rate": 0.15}


def test_unreachable_when_observed_p99_below_threshold(caplog):
    """Kernfall (#660): beobachtetes p99 (~0.11, TSLA.ETORO-Hourly-Signatur) < Schwelle (0.15) ⇒
    WARNING, Klausel als unreachable gemeldet."""
    observed = {"min_win_rate": [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11] * 3}
    with caplog.at_level(logging.WARNING, logger="optimizer"):
        unreachable = check_any_arm_reachability_live(_TCFG, observed)
    assert unreachable == ["min_win_rate"]
    assert any("[#660]" in r.message for r in caplog.records)


def test_reachable_when_observed_p99_above_threshold():
    """Beobachtetes p99 über der Schwelle ⇒ kein Warning, keine gemeldete Klausel."""
    observed = {"min_win_rate": [0.10, 0.15, 0.18, 0.20, 0.22, 0.25, 0.30] * 3}
    unreachable = check_any_arm_reachability_live(_TCFG, observed)
    assert unreachable == []


def test_no_judgement_with_too_few_samples():
    """< 5 beobachtete Werte ⇒ kein Urteil (zu wenig Daten für ein p99, kein False-Positive)."""
    observed = {"min_win_rate": [0.05, 0.06, 0.07]}
    unreachable = check_any_arm_reachability_live(_TCFG, observed)
    assert unreachable == []


def test_inactive_when_clause_not_in_eligible_requires_any():
    tcfg = {"eligible_requires_any": ["min_profit_factor"], "oos_min_win_rate": 0.15}
    observed = {"min_win_rate": [0.01] * 10}
    unreachable = check_any_arm_reachability_live(tcfg, observed)
    assert unreachable == []


def test_inactive_when_threshold_missing():
    tcfg = {"eligible_requires_any": ["min_profit_factor", "min_win_rate"]}
    observed = {"min_win_rate": [0.01] * 10}
    unreachable = check_any_arm_reachability_live(tcfg, observed)
    assert unreachable == []


def test_no_observed_values_is_a_no_op():
    unreachable = check_any_arm_reachability_live(_TCFG, {})
    assert unreachable == []
    unreachable = check_any_arm_reachability_live(_TCFG, None)
    assert unreachable == []


def test_complements_not_replaces_static_fixture_check():
    """Akzeptanzkriterium (#660): die statische #633-Prüfung bleibt unverändert grün für 0.15
    (< Fixture-p99=0.197) — die LIVE-Prüfung ist eine ZUSÄTZLICHE, studien-spezifische Diagnose,
    kein Ersatz."""
    from automation.optimizer.reward import check_any_arm_reachability
    assert check_any_arm_reachability(_TCFG) == []   # #633: 0.15 < 0.197 ⇒ "erreichbar"
    # ... aber DIESE Study beobachtete strukturell weniger:
    observed = {"min_win_rate": [0.05, 0.07, 0.09, 0.10, 0.11] * 3}
    assert check_any_arm_reachability_live(_TCFG, observed) == ["min_win_rate"]
