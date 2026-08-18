"""Issue #1017/#1169 (Katalog #1170) — ``check_gate_collinearity_decision_required`` blockt beide
Läufe an einem bereits entschiedenen Paar.

Symptom: Blockierender FAIL in beiden Läufen: Paar ``(oos_max_drawdown, oos_min_psr)``, ρ = 0,9290
(AdxAtrMomentumStrategy/PLTR) bzw. 0,9113 (SmaCrossoverStrategy/NVDA), kein Eintrag in
``gate_collinearity_accepted_pairs``.

Root-Cause: die Entscheidung war im Repo bereits getroffen und zweifach dokumentiert — nur nicht an
der Stelle, die der Check liest. ``tournament.json`` führt ``max_drawdown`` in
``gate_consolidation_protected`` ("harte Risikogrenze, #776 explizit behalten") und das
``gate_redundancy_jaccard``-Schema (#811) nennt genau diese Konstellation eine
Kategorienverwechslung: eine Risikoobergrenze und ein Qualitätsgate kovariieren zwangsläufig über
die Exposure, treffen aber unterschiedliche Zulassungsentscheidungen.

Fix: (1) Eintrag in ``tournament.json['gate_collinearity_accepted_pairs']`` mit ``decided_in_issue:
1169``. (2) STRUKTURELL: ``reward.gate_correlations_requiring_decision`` entfernt jedes Paar mit
einem ``gate_consolidation_protected``-Mitglied, BEVOR es ``check_gate_collinearity_decision_
required`` erreicht — die Schutzliste IST die Entscheidung; ``report.py``s einzige Aufrufstelle
liest seither diese gefilterte Funktion statt der rohen ``gate_rank_correlation_matrix``. Die rohe
Matrix selbst (forensische #742-Report-Telemetrie, ``assert_gate_collinearity_guard``) bleibt
unveraendert ungefiltert.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import reward
from automation.optimizer import report as rpt


_TCFG = {
    "eligible_requires_all": ["max_drawdown", "min_psr"],
    "gate_consolidation_protected": ["max_drawdown"],
}


def _collinear_cohort(n=30, *, key_a="oos_max_drawdown", key_b="oos_min_psr"):
    """Reproduziert das #1169-Referenzsymptom: zwei Gate-Deltas, die derselben latenten
    Exposure-Achse folgen (monotone Transformation ⇒ |ρ| nahe 1), analog der etablierten
    #667-``_collinear_cohort``-Konvention (test_issue_667_gate_collinearity.py)."""
    cohort = []
    for i in range(n):
        latent = (i - n / 2) / 10.0
        cohort.append({key_a: latent, key_b: latent * 2.0 + 1.0})
    return cohort


# ── reward.gate_correlations_requiring_decision — reine Funktion ─────────────────────────────────

def test_removes_a_pair_with_a_protected_member():
    result = reward.gate_correlations_requiring_decision(_collinear_cohort(), _TCFG)
    assert ("oos_max_drawdown", "oos_min_psr") not in result


def test_the_raw_unfiltered_matrix_still_contains_the_pair():
    """Die forensische #742-Report-Telemetrie (gate_rank_correlation_matrix/
    assert_gate_collinearity_guard) bleibt UNVERAENDERT — nur die Entscheidungspflicht filtert."""
    raw = reward.gate_rank_correlation_matrix(_collinear_cohort(), _TCFG)
    assert ("oos_max_drawdown", "oos_min_psr") in raw["correlations"]
    assert abs(raw["correlations"][("oos_max_drawdown", "oos_min_psr")]) > 0.9


def test_a_pair_without_any_protected_member_survives_the_filter():
    tcfg = {"eligible_requires_all": ["min_expectancy", "min_psr"],
           "gate_consolidation_protected": ["max_drawdown"]}
    cohort = _collinear_cohort(key_a="oos_min_expectancy", key_b="oos_min_psr")
    result = reward.gate_correlations_requiring_decision(cohort, tcfg)
    assert ("oos_min_expectancy", "oos_min_psr") in result


def test_no_protected_gates_configured_is_a_no_op():
    tcfg = {"eligible_requires_all": ["max_drawdown", "min_psr"]}
    result = reward.gate_correlations_requiring_decision(_collinear_cohort(), tcfg)
    assert ("oos_max_drawdown", "oos_min_psr") in result


# ── end-to-end: die 1169-Symptom-Korrelation erreicht check_gate_collinearity_decision_required
#    nicht mehr, PASSt daher OHNE einen accepted_pairs-Eintrag ─────────────────────────────────

def test_the_1169_symptom_pair_no_longer_requires_a_decision_entry():
    filtered = reward.gate_correlations_requiring_decision(_collinear_cohort(), _TCFG)
    result = inv.check_gate_collinearity_decision_required(
        filtered, threshold=0.90, accepted_pairs=[], policy="require_decision")
    assert result.passed is True


def test_an_unprotected_collinear_pair_still_requires_a_decision_entry():
    """Regressionswaechter gegen Ueberreichweite: der strukturelle Fix darf NUR protected-Paare
    exemptieren, nicht die Entscheidungspflicht insgesamt aushebeln."""
    tcfg = {"eligible_requires_all": ["min_expectancy", "min_psr"],
           "gate_consolidation_protected": ["max_drawdown"]}
    cohort = _collinear_cohort(key_a="oos_min_expectancy", key_b="oos_min_psr")
    filtered = reward.gate_correlations_requiring_decision(cohort, tcfg)
    result = inv.check_gate_collinearity_decision_required(
        filtered, threshold=0.90, accepted_pairs=[], policy="require_decision")
    assert result.passed is False


# ── report.py wiring ───────────────────────────────────────────────────────────────────────────

def test_report_uses_the_filtered_function_not_the_raw_matrix_for_the_decision_check():
    source = Path(rpt.__file__).read_text("utf-8")
    assert "_reward.gate_correlations_requiring_decision(" in source
    # Regressionswaechter: die Entscheidungspflicht-Aufrufstelle darf NICHT mehr direkt auf
    # gate_rank_correlation_matrix(...)["correlations"] zugreifen (das #1169-Symptom).
    assert '_reward.gate_rank_correlation_matrix(trial_gate_deltas, tournament_cfg)["correlations"],\n' \
           '            threshold=' not in source


def test_invariant_registry_wiring_check_still_passes():
    result = rpt._invariant_registry_wiring_check()
    assert result.passed is True


# ── tournament.json — dokumentierte Entscheidung (Fix-Bullet 1) ──────────────────────────────────

def test_shipped_config_documents_the_1169_decision():
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    entries = cfg["gate_collinearity_accepted_pairs"]
    entry = next(e for e in entries if set(e["pair"]) == {"oos_max_drawdown", "oos_min_psr"})
    assert entry["decided_in_issue"] == 1169
    assert entry["rationale"]


def test_shipped_config_max_drawdown_is_protected():
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    assert "max_drawdown" in cfg["gate_consolidation_protected"]


def test_shipped_config_reproduces_the_reference_symptom_as_a_pass():
    """Der volle #1169-Akzeptanzkriterium-Pfad gegen den ECHTEN Datenstand: die im Issue genannte
    Korrelation (ρ=0.929/0.911 zwischen max_drawdown und min_psr) erreicht mit der shipped
    tournament.json weder eine FAIL-Meldung ueber den strukturellen Filter NOCH — waere der Filter
    aus irgendeinem Grund unwirksam — ueber den dokumentierten accepted_pairs-Eintrag."""
    cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    cohort = _collinear_cohort()
    filtered = reward.gate_correlations_requiring_decision(cohort, cfg)
    assert ("oos_max_drawdown", "oos_min_psr") not in filtered
    result = inv.check_gate_collinearity_decision_required(
        filtered, threshold=float(cfg.get("gate_collinearity_threshold", 0.90)),
        accepted_pairs=cfg.get("gate_collinearity_accepted_pairs"),
        policy=str(cfg.get("gate_collinearity_policy", "require_decision")))
    assert result.passed is True
