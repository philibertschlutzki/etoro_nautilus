"""Issue #680 — any_arm_unreachable_policy: 'warn' (nur Warnung) ⇒ 'recalibrate' (erzwingt Aktion).

Symptom: any_arm_live_unreachable=['min_win_rate'] in jeder trade-armen Study, aber
any_arm_recalibrated_thresholds blieb IMMER leer, weil die Policy nie ueber 'warn' hinausging —
obwohl der Recalibrate-Mechanismus bereits seit #668 existiert. Fix (#680): DEFAULT auf
'recalibrate' gehoben (Config-only, Mechanik unveraendert).

Issue #812 — SUPERSEDIERT den #680-Default: 'recalibrate' macht die Selektionsprozedur STUDY-
abhaengig und bricht damit die Voraussetzung der DSR-Multiplizitaetskorrektur (Pitfall #248);
p99=0.0 ist seit #788/#759 zudem ein ECHTES Ergebnis, keine Rechtfertigung fuer eine gesenkte
Huerde. Der reale Default ist seit #812 'drop_arm' (siehe test_issue_812_selection_rule_
fingerprint.py fuer die #812-Akzeptanzkriterien) — die Tests hier pruefen weiterhin, dass der
'recalibrate'-MECHANISMUS selbst (jetzt nur noch explizit gewaehlt) funktioniert.
"""
import json
from pathlib import Path

from automation.optimizer.reward import resolve_any_arm_policy

TCFG = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))


def test_real_config_defaults_to_drop_arm_not_warn_or_recalibrate():
    # Issue #812 — der Default wanderte von 'recalibrate' (#680) weiter auf 'drop_arm'.
    assert TCFG.get("any_arm_unreachable_policy") == "drop_arm"


def test_recalibrate_policy_produces_nonempty_thresholds_when_explicitly_selected():
    """Akzeptanzkriterium: any_arm_unreachable_policy='recalibrate' loest weiterhin eine
    dokumentierte Aktion aus, wenn ein Operator es explizit fuer einen Kalibrierlauf waehlt (#812
    haelt den Mechanismus als Option, aendert nur den DEFAULT)."""
    tcfg = {**TCFG, "any_arm_unreachable_policy": "recalibrate"}
    # Realistische, strukturell unter der globalen 0.15-Schwelle liegende Win-Rate-Verteilung
    # (TSLA.ETORO Hourly-Tier-Groessenordnung, siehe oos_min_win_rate-Schema, max ~0.11 beobachtet).
    # Issue #759 — mindestens any_arm_min_observations (Default 10) echte Beobachtungen noetig.
    observed_win_rates = [0.02, 0.05, 0.08, 0.03, 0.06, 0.11, 0.04, 0.07, 0.05, 0.06]
    decision = resolve_any_arm_policy(tcfg, {"min_win_rate": observed_win_rates})
    assert decision["policy"] == "recalibrate"
    assert decision["recalibrated_thresholds"] != {}
    assert "oos_min_win_rate" in decision["recalibrated_thresholds"]
    # Issue #812 — kein Floor mehr: die rekalibrierte Schwelle ist das ROHE p99 der Beobachtungen.
    assert decision["recalibrated_thresholds"]["oos_min_win_rate"] == max(observed_win_rates)


def test_no_silent_collapse_when_arm_is_actually_reachable():
    """Liegt die beobachtete Verteilung UEBER der Schwelle (Arm tatsaechlich erreichbar), aendert
    'recalibrate' nichts — kein unnoetiges Nachschaerfen eines funktionierenden Arms."""
    observed_win_rates = [0.20, 0.25, 0.30, 0.22, 0.28, 0.35, 0.40, 0.18, 0.24, 0.26]
    decision = resolve_any_arm_policy(TCFG, {"min_win_rate": observed_win_rates})
    assert decision["recalibrated_thresholds"] == {}
