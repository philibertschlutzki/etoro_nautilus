"""Issue #697 (P1) — Kollineare `eligible_requires_all`-Gates konsolidieren (Alarm konsumieren
statt nur melden).

Root-Cause: die Study-Telemetrie feuerte wiederholt `[#667] Gate-Kollinearität:
|ρ(oos_min_expectancy, oos_min_psr)| = 0.961 > 0.95` — `eligible_requires_all` enthielt trotzdem
BEIDE (`min_expectancy` UND `oos_min_psr`) als harte Konjunktions-Gates. Der #679-Redundanz-Alarm
benannte `oos_min_psr` als das zu behaltende, `min_expectancy` als redundanten Kandidaten, aber
NICHTS konsumierte die Empfehlung.

Fix: `min_expectancy` aus `eligible_requires_all` entfernt (bleibt als weiche Kosten-Breakeven-
Sanity über `reward._normalized_gate_distances` erhalten); `reward.assert_eligible_requires_all_
not_redundant` konsumiert den Alarm fail-loud gegen künftige Regressionen; `confirm.py` ruft diese
Validierung je Study auf und macht das Ergebnis im Winner-Eintrag sichtbar;
`calibration.calibrate_gate_consolidation_false_positive_rate` belegt auf synthetischen H0-Daten,
dass die empirische False-Positive-Winner-Rate durch die Entfernung NICHT steigt.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.reward import (
    assert_eligible_requires_all_not_redundant, _GATE_COLLINEARITY_TO_CONJUNCTION_KEY,
)
from automation.optimizer.calibration import calibrate_gate_consolidation_false_positive_rate

CFG_PATH = Path("automation/config/tournament.json")
CFG = json.loads(CFG_PATH.read_text("utf-8"))
OPT_CFG_PATH = Path("automation/config/optimizer.json")
OPT_CFG = json.loads(OPT_CFG_PATH.read_text("utf-8"))


# ── Config: min_expectancy raus, oos_min_psr bleibt hart ─────────────────────────────────────────
def test_min_expectancy_removed_from_eligible_requires_all():
    assert "min_expectancy" not in CFG["eligible_requires_all"]


def test_eligible_requires_all_matches_documented_default():
    assert CFG["eligible_requires_all"] == [
        "min_trades", "max_drawdown", "oos_min_psr", "oos_min_excess_return",
    ]


def test_oos_min_psr_and_cost_floor_keys_still_present_in_schema():
    """min_expectancy bleibt als Key/Schwelle/Kosten-Floor (oos_min_expectancy_k_alpha) erhalten —
    nur die harte Konjunktions-Mitgliedschaft entfällt (weiche Near-Miss-Distanz bleibt aktiv)."""
    assert "min_expectancy" in CFG
    assert "oos_min_expectancy_k_alpha" in CFG
    assert "oos_min_psr" in CFG["eligible_requires_all"]


def test_schema_documents_the_697_consolidation():
    doc_all = CFG["_schema"]["fields"]["eligible_requires_all"]
    assert "#697" in doc_all
    doc_expectancy = CFG["_schema"]["fields"]["min_expectancy"]
    assert "#697" in doc_expectancy


# ── reward.assert_eligible_requires_all_not_redundant: konsumiert den Alarm ──────────────────────
def _deltas(expectancy, psr, profitable_folds_frac=None, any_condition=None):
    return {
        "oos_min_expectancy": expectancy,
        "oos_min_psr": psr,
        "oos_min_profitable_folds_frac": profitable_folds_frac if profitable_folds_frac is not None else expectancy,
        "oos_min_profit_factor": any_condition if any_condition is not None else expectancy,
    }


def test_no_finding_when_conjunction_already_consolidated():
    """Nach #697 enthält eligible_requires_all kein min_expectancy mehr — selbst bei perfekter
    Kollinearität in der Live-Kohorte gibt es nichts mehr zu konsolidieren (Regelfall)."""
    cohort = [
        _deltas(e, e, profitable_folds_frac=100 - e, any_condition=-e)
        for e in [0.001, 0.002, 0.003, 0.004, -0.001]
    ]
    result = assert_eligible_requires_all_not_redundant(cohort, CFG["eligible_requires_all"])
    assert result == []


def test_finding_fires_if_min_expectancy_is_reintroduced():
    """Regressionswächter: reaktiviert ein Operator 'min_expectancy' versehentlich wieder, UND die
    Live-Kohorte zeigt weiterhin hohe Kollinearität mit oos_min_psr, muss die Validierung das
    melden (statt erneut folgenlos zu bleiben, die #697-Root-Cause)."""
    cohort = [
        _deltas(e, e, profitable_folds_frac=100 - e, any_condition=-e)
        for e in [0.001, 0.002, 0.003, 0.004, -0.001]
    ]
    reintroduced_conjunction = ["min_trades", "max_drawdown", "min_expectancy", "oos_min_psr"]
    result = assert_eligible_requires_all_not_redundant(cohort, reintroduced_conjunction)
    assert result == ["min_expectancy"]


def test_psr_itself_never_flagged():
    """oos_min_psr hat die höchste Konsolidierungs-Priorität (reward._GATE_CONSOLIDATION_PRIORITY)
    und wird nie selbst als redundanter Kandidat gemeldet."""
    cohort = [
        _deltas(e, e, profitable_folds_frac=e, any_condition=e)
        for e in [0.001, 0.002, 0.003, 0.004, 0.005, -0.001]
    ]
    result = assert_eligible_requires_all_not_redundant(
        cohort, ["min_trades", "max_drawdown", "min_expectancy", "oos_min_psr"])
    assert "oos_min_psr" not in result


def test_any_condition_never_mapped_to_a_conjunction_key():
    """'any_condition' repräsentiert die GESAMTE eligible_requires_any-Disjunktion, kein einzelnes
    eligible_requires_all-Mitglied — die Mapping-Tabelle darf es nie auf einen Konjunktions-Key
    abbilden (sonst würde die Validierung fälschlich eligible_requires_any-Klauseln als
    eligible_requires_all-Mitglieder behandeln)."""
    assert _GATE_COLLINEARITY_TO_CONJUNCTION_KEY["any_condition"] is None


def test_empty_cohort_yields_no_finding():
    assert assert_eligible_requires_all_not_redundant([], CFG["eligible_requires_all"]) == []


# ── calibration.calibrate_gate_consolidation_false_positive_rate: Null-Kalibrierbeleg ────────────
def test_removing_redundant_expectancy_gate_does_not_raise_false_positive_rate():
    result = calibrate_gate_consolidation_false_positive_rate(
        n_configs=12, n_periods=200, n_reps=3000, confidence=0.95,
        expectancy_psr_correlation=0.96, seed=42,
    )
    fp_conj = result["fp_rate_conjunction_with_expectancy"]
    fp_psr_only = result["fp_rate_psr_only"]
    # Beide Raten bleiben nahe dem nominellen Niveau (5%), mit Sampling-Toleranz.
    assert fp_psr_only <= 0.09
    assert fp_conj <= 0.09
    # Kernkriterium (#697): das Entfernen des ~96%-kollinearen Expectancy-Gates darf die
    # False-Positive-Rate NICHT MATERIELL erhöhen (die Klausel trug ~keine unabhängige
    # Type-I-Kontrolle bei).
    assert fp_psr_only <= fp_conj + 0.02


def test_less_collinear_expectancy_gate_would_meaningfully_tighten_fp_rate():
    """Kontrastfall: WÄRE Expectancy nur schwach mit PSR korreliert (nicht der reale #697-Fall),
    würde die Konjunktion die FP-Rate sichtbar SENKEN (echter, nicht nur redundanter Type-I-Beitrag)
    — das demonstriert, dass der Null-Kalibrierlauf tatsächlich sensitiv auf die Kollinearität ist,
    nicht trivial IMMER gleiche Raten liefert."""
    result = calibrate_gate_consolidation_false_positive_rate(
        n_configs=12, n_periods=200, n_reps=3000, confidence=0.95,
        expectancy_psr_correlation=0.1, seed=42,
    )
    assert result["fp_rate_conjunction_with_expectancy"] < result["fp_rate_psr_only"]


# ── reward_semantics_version: v12 → v13 (#697 ändert die Eligibility-Definition) ─────────────────
def test_reward_semantics_version_is_13():
    assert OPT_CFG["reward_semantics_version"] == 13


def test_version_is_documented_with_v13_changelog_entry():
    doc = OPT_CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v13" in doc
    for ref in ("#695", "#696", "#697"):
        assert ref in doc, f"v13-Changelog muss {ref} referenzieren"


def test_v13_changelog_documents_eligibility_not_scale_change():
    doc = OPT_CFG["_schema"]["fields"]["reward_semantics_version"]
    v13_start = doc.index("v13 =")
    v13_segment = doc[v13_start:]
    assert "eligib" in v13_segment.lower()


def test_v13_changelog_explains_why_other_catalog_issues_did_not_trigger_bump():
    doc = OPT_CFG["_schema"]["fields"]["reward_semantics_version"]
    v13_segment = doc[doc.index("v13 ="):]
    for ref in ("#695", "#696", "#698", "#699", "#700", "#701", "#702"):
        assert ref in v13_segment, f"v13-Changelog muss begründen, warum {ref} keinen Bump auslöste"


def test_stale_pre_v13_study_is_rejected_fail_loud():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 12}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v12"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), OPT_CFG)


def test_fresh_study_stamps_v13_without_error():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {}
            self.trials = []
            self.study_name = "study_fresh"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    study = _FakeStudy()
    _check_reward_semantics_version(study, OPT_CFG)
    assert study.user_attrs["reward_semantics_version"] == 13


def test_matching_v13_version_is_a_no_op():
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 13}
            self.trials = [object()] * 20
            self.study_name = "study_v13"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    _check_reward_semantics_version(_FakeStudy(), OPT_CFG)  # muss NICHT raisen
