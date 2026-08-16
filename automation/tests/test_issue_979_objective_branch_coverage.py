"""Issue #979 (Katalog C, P0) — SUPERSEDED durch #955/#1121 (Katalog #960).

Die ursprüngliche Fassung von ``check_objective_branch_coverage`` mass den Anteil der Trials mit
``reward_terms.branch == 'per_symbol'`` (dem ordnenden Reward-Zweig). B-13 (Katalog #960, drei
Läufe, 1804 Trials) zeigte: ``branch == 'per_symbol'`` ist per Konstruktion IDENTISCH zu
``oos_eligible`` — die alte Fassung war damit eine blosse Umbenennung von ``p_eligible`` (bereits
je Study exportiert), keine eigenständige Diagnose. Zwei Namen für dieselbe Beobachtung erzeugten
zwei Befunde im Report und überzeichneten die Fehlerlage.

#955/#1121 definiert die Funktion neu (gleicher Name — der Suchbudget-Deprioritisierungsmechanismus
in ``sweep._apply_search_budget_proposal``/``report._search_budget_proposal_section`` liest über
den Namen, nicht die Semantik): sie misst jetzt den Anteil ineligibler Trials mit definierter
Selektionsstatistik — siehe ``test_issue_955_1121_objective_branch_coverage_non_duplicate.py`` für
die neuen Akzeptanztests. Diese Datei bleibt als historischer Marker erhalten.
"""
from automation.optimizer import invariants as inv


def _trial(oos_evaluated, oos_eligible, selection_statistic_available):
    return {
        "oos_evaluated": oos_evaluated, "oos_eligible": oos_eligible,
        "oos_selection_statistic_available": selection_statistic_available,
    }


def test_no_ineligible_trials_is_not_applicable():
    trials = [_trial(True, True, True)] * 5
    result = inv.check_objective_branch_coverage(trials)
    assert result.passed is True
    assert result.actual is None


def test_measures_the_ineligible_cohort_not_the_eligible_fraction():
    """Selbst bei einer NIEDRIGEN Eligibility-Rate (p_eligible) darf der Check bestehen, solange
    die ineligible Kohorte mehrheitlich MESSBAR war -- die neue Groesse ist von p_eligible
    unabhaengig."""
    trials = (
        [_trial(True, True, True)] * 2  # 2 eligible
        + [_trial(True, False, True)] * 8  # 8 ineligible, aber alle gemessen
    )
    result = inv.check_objective_branch_coverage(trials)
    assert result.passed is True
    assert result.actual == 1.0
