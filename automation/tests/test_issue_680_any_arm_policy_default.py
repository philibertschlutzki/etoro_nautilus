"""Issue #680 — any_arm_unreachable_policy: 'warn' (nur Warnung) ⇒ 'recalibrate' (erzwingt Aktion).

Symptom: any_arm_live_unreachable=['min_win_rate'] in jeder trade-armen Study, aber
any_arm_recalibrated_thresholds blieb IMMER leer, weil die Policy nie ueber 'warn' hinausging —
obwohl der Recalibrate-Mechanismus bereits seit #668 existiert. Fix: DEFAULT auf 'recalibrate'
gehoben (Config-only, Mechanik unveraendert).
"""
import json
from pathlib import Path

from automation.optimizer.reward import resolve_any_arm_policy

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_real_config_defaults_to_recalibrate_not_warn():
    assert TCFG.get("any_arm_unreachable_policy") == "recalibrate"


def test_recalibrate_policy_produces_nonempty_thresholds_against_real_config():
    """Akzeptanzkriterium: any_arm_unreachable_policy loest eine dokumentierte Aktion aus;
    any_arm_recalibrated_thresholds ist bei aktiver Recalibration NICHT leer."""
    # Realistische, strukturell unter der globalen 0.15-Schwelle liegende Win-Rate-Verteilung
    # (TSLA.ETORO Hourly-Tier-Groessenordnung, siehe oos_min_win_rate-Schema, max ~0.11 beobachtet).
    # Issue #759 — mindestens any_arm_min_observations (Default 10) echte Beobachtungen noetig.
    observed_win_rates = [0.02, 0.05, 0.08, 0.03, 0.06, 0.11, 0.04, 0.07, 0.05, 0.06]
    decision = resolve_any_arm_policy(TCFG, {"min_win_rate": observed_win_rates})
    assert decision["policy"] == "recalibrate"
    assert decision["recalibrated_thresholds"] != {}
    assert "oos_min_win_rate" in decision["recalibrated_thresholds"]
    # Die rekalibrierte Schwelle liegt NIE unter dem globalen Floor (min_win_rate_recalibration_floor).
    floor = float(TCFG.get("min_win_rate_recalibration_floor", 0.05))
    assert decision["recalibrated_thresholds"]["oos_min_win_rate"] >= floor


def test_no_silent_collapse_when_arm_is_actually_reachable():
    """Liegt die beobachtete Verteilung UEBER der Schwelle (Arm tatsaechlich erreichbar), aendert
    'recalibrate' nichts — kein unnoetiges Nachschaerfen eines funktionierenden Arms."""
    observed_win_rates = [0.20, 0.25, 0.30, 0.22, 0.28, 0.35, 0.40, 0.18, 0.24, 0.26]
    decision = resolve_any_arm_policy(TCFG, {"min_win_rate": observed_win_rates})
    assert decision["recalibrated_thresholds"] == {}
