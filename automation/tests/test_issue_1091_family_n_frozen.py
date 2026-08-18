"""Issue #1091 (Katalog #924) — die familienweite Multiplizität wird VOR dem ersten Trial aus dem
geplanten Budget eingefroren, statt bei jedem Report-Build aus den bereits exportierten Proposals
neu zu akkumulieren.

Root-Cause: ``report._family_n_from_proposals`` summiert ``deflation_n_eligible`` über die
BEREITS exportierten Proposals eines Symbols — ein Zwischenreport (Progress-/Fail-Fast-Probe,
#1083), der VOR Abschluss aller Strategien-Studies dieses Symbols gebaut wird, sieht zwangsläufig
nur eine Teilmenge und meldet eine kleinere Zahl als ein späterer Report derselben Familie
(280 → 321 → 434 im #1091-Symptom). ``sweep._family_n_frozen_from_studies`` summiert stattdessen
``study.user_attrs['n_trials_budget']`` — ab Study-Erstellung bekannt, unabhängig vom Fortschritt
der Geschwister-Strategien.
"""
import optuna
import pytest

from automation.optimizer import sweep


def _study_with_budget(n_trials_budget: int, n_trials_seen: int = 0):
    study = optuna.create_study(direction="maximize")
    study.set_user_attr("n_trials_budget", n_trials_budget)
    for i in range(n_trials_seen):
        trial = study.ask()
        study.tell(trial, float(i))
    return study


def test_frozen_family_n_sums_planned_budget_not_observed_trials():
    pairs = [
        ("StratA", "NVDA.ETORO", "OK"),
        ("StratB", "NVDA.ETORO", "OK"),
        ("StratC", "NVDA.ETORO", "OK"),
    ]
    # Nur StratA hat bereits Trials gesehen — trotzdem zaehlt der GEPLANTE Budget aller drei.
    studies = [
        _study_with_budget(100, n_trials_seen=3),
        _study_with_budget(100, n_trials_seen=0),
        _study_with_budget(100, n_trials_seen=0),
    ]
    frozen = sweep._family_n_frozen_from_studies(pairs, studies)
    assert frozen == {"NVDA.ETORO": 300}


def test_frozen_family_n_is_stable_regardless_of_how_many_trials_have_completed():
    """Kernbehauptung #1091: derselbe eingefrorene Wert, egal ob VOR oder NACH den Trials
    gelesen — anders als die alte, proposal-akkumulierende Zahl."""
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    early = sweep._family_n_frozen_from_studies(pairs, [_study_with_budget(200, n_trials_seen=0)])
    late = sweep._family_n_frozen_from_studies(pairs, [_study_with_budget(200, n_trials_seen=200)])
    assert early == late == {"TSLA.ETORO": 200}


def test_frozen_family_n_falls_back_to_trial_count_without_budget_stamp():
    study = optuna.create_study(direction="maximize")
    for i in range(5):
        trial = study.ask()
        study.tell(trial, float(i))
    frozen = sweep._family_n_frozen_from_studies([("StratA", "AAPL.ETORO", "OK")], [study])
    assert frozen == {"AAPL.ETORO": 5}


def test_frozen_family_n_skips_missing_studies():
    pairs = [("StratA", "AAPL.ETORO", "OK"), ("StratB", "AAPL.ETORO", "FAILED")]
    studies = [_study_with_budget(50), None]
    frozen = sweep._family_n_frozen_from_studies(pairs, studies)
    assert frozen == {"AAPL.ETORO": 50}


# ── invariants.check_family_n_stability ──────────────────────────────────────────────────────────
from automation.optimizer import invariants as inv


def test_family_n_stability_requires_exact_equality_since_1158():
    """Issue #1006/#1158 (Katalog #1170) — die vormalige 5%-Toleranz verdeckte genau das reale
    #1158-Symptom (frozen=0/observed=1, eine einzelne excluded_degenerate-Study); seit ``sweep.
    _family_members``/``report._family_n_stages`` dieselbe Ausschluss-Semantik teilen, ist
    ``frozen == observed`` tautologisch garantiert -- JEDE Differenz ist ein echter Befund, keine
    Toleranzschwelle mehr."""
    assert inv.check_family_n_stability({"NVDA.ETORO": 434}, {"NVDA.ETORO": 420}).passed is False
    assert inv.check_family_n_stability({"NVDA.ETORO": 434}, {"NVDA.ETORO": 434}).passed is True


def test_family_n_stability_fails_on_the_1158_reference_symptom():
    """frozen=0, observed=1 -- das exakte NVDA/SqueezeBreakout-Symptom aus #1158. Vor dem Fix wurde
    dieser Fall durch den ``frozen <= 0: continue``-Skip NIE ausgewertet."""
    result = inv.check_family_n_stability({"NVDA.ETORO": 0}, {"NVDA.ETORO": 1})
    assert result.passed is False
    assert result.actual["NVDA.ETORO"]["difference"] == 1


def test_family_n_stability_fails_on_large_gap_matching_1091_symptom():
    """Reproduziert das #1091-Symptom: 280/321/434 ueber drei Lesungen derselben Familie.

    Issue #979/#1133 — severity ist seit #977/#1131 (per-Strategie-Frozen-Quelle) 'blocking'
    statt 'high': die Hochstufung war an #1131 gebunden (siehe check_family_n_stability-Docstring),
    dessen Fix Voraussetzung dafuer war, dass eine Abweichung wieder ein echter Befund ist."""
    for observed in (280, 321):
        result = inv.check_family_n_stability({"NVDA.ETORO": 434}, {"NVDA.ETORO": observed})
        assert result.passed is False
        assert result.severity == "blocking"
        assert "NVDA.ETORO" in result.actual


def test_family_n_stability_not_applicable_without_overlap():
    assert inv.check_family_n_stability({}, {"NVDA.ETORO": 100}).passed is True
    assert inv.check_family_n_stability({"NVDA.ETORO": 100}, {}).passed is True
