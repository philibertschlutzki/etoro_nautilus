"""Issue #955/#1121 (Katalog #960) — ``check_objective_branch_coverage`` ist eine Umbenennung von
``p_eligible``.

Symptom (B-13): ``branch == 'per_symbol'`` ⟺ ``oos_eligible`` in exakt 1804 von 1804 Trials über
drei Läufe. Der Check feuerte in 5/29 bzw. 11/42 Studies und meldete "die Suche hat über den
Grossteil ... keine ordnende Qualitätsinformation" — obwohl die globale Eligibility-Rate mit
31,41 % gesund war.

Root-Cause: zwei Namen für dieselbe Grösse erzeugen zwei Befunde für eine Beobachtung und
überzeichnen die Fehlerlage im Report.

Fix: die Funktion (gleicher Name — ``sweep._apply_search_budget_proposal``/``report._search_
budget_proposal_section`` (#1082) lesen NUR über den Namen, siehe dortige Docstrings) misst jetzt
den Anteil ineligibler Trials mit definierter Selektionsstatistik — eine Grösse, die ``p_eligible``
strukturell NICHT duplizieren kann.

Akzeptanzkriterium: kein Report enthält zwei Checks, deren Offender-Menge identisch ist.
"""
from automation.optimizer import invariants as inv


def _trial(oos_evaluated=True, oos_eligible=False, selection_statistic_available=None):
    return {
        "oos_evaluated": oos_evaluated, "oos_eligible": oos_eligible,
        "oos_selection_statistic_available": selection_statistic_available,
    }


def test_cannot_structurally_duplicate_p_eligible():
    """Ein Fall, der p_eligible (fraction eligible) auf 0.0 fixiert, aber die neue Groesse frei
    variieren laesst -- der Beweis, dass die beiden Groessen unabhaengig sind."""
    all_ineligible_all_measured = [_trial(True, False, True)] * 10
    all_ineligible_none_measured = [_trial(True, False, False)] * 10
    result_measured = inv.check_objective_branch_coverage(all_ineligible_all_measured)
    result_unmeasured = inv.check_objective_branch_coverage(all_ineligible_none_measured)
    # Beide Szenarien haben p_eligible == 0.0 (keine eligiblen Trials in beiden), aber die neue
    # Groesse unterscheidet sie vollstaendig (1.0 vs. 0.0) -- keine 1:1-Korrelation zu p_eligible.
    assert result_measured.actual == 1.0
    assert result_unmeasured.actual == 0.0


def test_ignores_eligible_trials_entirely():
    """Die Eligibility-Rate selbst darf keinen Einfluss auf das Ergebnis haben -- nur die
    ineligible Teilmenge zaehlt."""
    few_eligible = [_trial(True, True, None)] * 2 + [_trial(True, False, True)] * 18
    many_eligible = [_trial(True, True, None)] * 18 + [_trial(True, False, True)] * 2
    r_few = inv.check_objective_branch_coverage(few_eligible)
    r_many = inv.check_objective_branch_coverage(many_eligible)
    assert r_few.actual == 1.0
    assert r_many.actual == 1.0


def test_fails_when_most_rejections_are_unmeasurable():
    trials = (
        [_trial(True, False, True)] * 1
        + [_trial(True, False, False)] * 19
    )
    result = inv.check_objective_branch_coverage(trials)
    assert result.passed is False
    assert result.actual == 0.05


def test_passes_at_the_default_threshold():
    trials = [_trial(True, False, True)] * 1 + [_trial(True, False, False)] * 9
    result = inv.check_objective_branch_coverage(trials, min_measured_fraction=0.10)
    assert result.passed is True
    assert result.actual == 0.1


def test_ignores_trials_never_oos_evaluated():
    trials = [
        _trial(oos_evaluated=False, oos_eligible=False, selection_statistic_available=False),
        _trial(True, False, True), _trial(True, False, True),
    ]
    result = inv.check_objective_branch_coverage(trials)
    assert result.actual == 1.0


def test_does_not_read_reward_terms_branch_at_all():
    """Regressionswaechter gegen eine Wiederkehr der alten Semantik: ein Trial mit
    reward_terms.branch=='per_symbol', aber OHNE die neuen Felder, darf das Ergebnis nicht
    beeinflussen (die Funktion liest reward_terms ueberhaupt nicht mehr)."""
    trials = [{"reward_terms": {"branch": "per_symbol"}}] * 10
    result = inv.check_objective_branch_coverage(trials)
    assert result.passed is True
    assert result.actual is None  # keine oos_evaluated-Trials nach neuer Definition
