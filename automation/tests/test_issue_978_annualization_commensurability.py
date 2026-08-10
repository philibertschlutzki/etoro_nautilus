"""Issue #978 (Katalog C, P0) — der Annualisierungsfaktor F variiert um Faktor 5 zwischen Folds und
um bis zu 3.59 INNERHALB eines einzigen Trials, weil ``_get_annualization_factor`` ihn empirisch aus
der je-Fold-Beobachtungszahl ableitet (ereignisgetrieben, also eine Funktion der Entscheidungs-
variablen der Optimierung selbst). Referenzlauf-Beispiel (Trial 0, SmaCrossover/GSAT): vier Folds
desselben Trials annualisieren mit sqrt(F) = 36.83/35.94/34.08/33.97 — vier verschiedene Faktoren für
vier gleich lange Kalenderfenster.

``check_annualization_commensurability`` ist der Regressionswächter: max(sqrt(F))/min(sqrt(F)) muss
je Trial <= 1.05 bleiben.
"""
from automation.optimizer import invariants as inv


def _trial(annualized, period):
    return {"per_fold_oos_sortino": annualized, "per_fold_oos_sortino_period": period}


def test_passes_when_annualization_factor_is_commensurable_across_folds():
    # sqrt(F) identisch (20.0) über alle vier Folds.
    result = inv.check_annualization_commensurability([
        _trial([2.0, 3.0, -1.0, 4.0], [0.1, 0.15, -0.05, 0.2]),
    ])
    assert result.passed is True


def test_fails_on_the_reference_run_example():
    """Reproduziert den Referenzlauf-Befund: sqrt(F) = 36.83/35.94/34.08/33.97 innerhalb EINES
    Trials, Spannweite 36.83/33.97 = 1.0842 > 1.05."""
    result = inv.check_annualization_commensurability([
        _trial([36.83, 35.94, 34.08, 33.97], [1.0, 1.0, 1.0, 1.0]),
    ])
    assert result.passed is False
    assert result.actual == round(36.83 / 33.97, 4)


def test_passes_at_exactly_the_threshold():
    result = inv.check_annualization_commensurability([
        _trial([1.05, 1.0], [1.0, 1.0]),
    ], max_ratio=1.05)
    assert result.passed is True


def test_ignores_trials_with_fewer_than_two_usable_folds():
    result = inv.check_annualization_commensurability([
        _trial([2.0], [0.1]),
        _trial([], []),
        {"per_fold_oos_sortino": None, "per_fold_oos_sortino_period": None},
    ])
    assert result.passed is True
    assert result.actual is None


def test_ignores_folds_with_zero_or_none_period_sortino():
    # Dritter Fold hat period=0 (uebersprungen), verbleibende zwei Folds sind kommensurabel.
    result = inv.check_annualization_commensurability([
        _trial([2.0, 2.0, 5.0], [0.1, 0.1, 0.0]),
    ])
    assert result.passed is True


def test_accepts_the_trial_user_attr_key_names_as_a_fallback():
    """report._study_record uebergibt trial_attrs mit den User-Attr-Schluesselnamen
    (oos_fold_sortinos/oos_fold_sortino_periods), nicht den Log-Event-Namen."""
    result = inv.check_annualization_commensurability([
        {"oos_fold_sortinos": [36.83, 33.97], "oos_fold_sortino_periods": [1.0, 1.0]},
    ])
    assert result.passed is False


def test_worst_trial_across_the_study_is_reported():
    result = inv.check_annualization_commensurability([
        _trial([10.0, 10.0], [1.0, 1.0]),  # commensurable
        _trial([10.0, 20.0], [1.0, 1.0]),  # ratio 2.0 -- worst
        _trial([10.0, 10.5], [1.0, 1.0]),  # ratio 1.05
    ])
    assert result.passed is False
    assert result.actual == 2.0
