"""Issue #981 (Katalog C, P1, Pitfall #312 in AGENTS.md) — ``check_search_made_progress`` konsumiert
``constraint_improvement_rate``, die bei fehlender Selektionsstatistik (#966) auf einer DREIWERTIGEN
Treppenfunktion ({0.0, 0.5, 1.0}) statt einer kontinuierlichen Distanz beruhte. Eine Grösse, die per
Konstruktion keinen Gradienten anzeigen kann, wurde als "der TPE-Sampler hat nachweislich keinen
Gradienten gefunden" fehlinterpretiert — 19 Studies im Referenzlauf 46cf5070.

Fix: ein dritter Invarianten-Zustand ``INCONCLUSIVE`` (``InvariantResult.inconclusive``), ausgelöst
wenn die Eingabe (``constraint_violations_observed``) zu grob quantisiert ist (< 10 verschiedene
Werte) für eine belastbare FAIL-Aussage.
"""
from automation.optimizer import invariants as inv


def _study(strategy, symbol, *, rate, n_modelled, plateau_min, p_eligible=0.0, observed=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "constraint_improvement_rate": rate, "n_modelled_trials": n_modelled,
        "plateau_min_modelled_trials": plateau_min, "p_eligible": p_eligible,
        "constraint_violations_observed": observed,
    }


def test_fails_with_continuous_resolution_input():
    """Kontinuierliche Distanzen (>= 10 verschiedene Werte) -- ein echtes FAIL bleibt ein FAIL."""
    observed = [1.4108880303783595 + i * 0.0001 for i in range(20)]
    result = inv.check_search_made_progress([
        _study("A", "X", rate=-0.01, n_modelled=60, plateau_min=48, observed=observed),
    ])
    assert result.passed is False
    assert result.inconclusive is False
    assert "A/X" in result.actual


def test_inconclusive_with_the_three_valued_staircase_input():
    """Reproduziert den #981-Befund: min_constraint_violation_first/last nehmen nur {0.0, 0.5, 1.0}
    an -- keine belastbare FAIL-Aussage moeglich."""
    result = inv.check_search_made_progress([
        _study("DynamicBreakoutStrategy", "GSAT.ETORO", rate=0.0, n_modelled=51, plateau_min=48,
               observed=[0.5] * 40 + [1.0] * 11),
    ])
    assert result.passed is True
    assert result.inconclusive is True
    assert "DynamicBreakoutStrategy/GSAT.ETORO" in result.detail


def test_passes_cleanly_without_the_fail_condition():
    result = inv.check_search_made_progress([
        _study("A", "X", rate=0.05, n_modelled=60, plateau_min=48, observed=None),
    ])
    assert result.passed is True
    assert result.inconclusive is False


def test_missing_observed_data_falls_back_to_fail_not_inconclusive():
    """Kein constraint_violations_observed-Feld (Pre-#981-Report) -- Legacy-Verhalten (FAIL bleibt
    FAIL, kein stiller INCONCLUSIVE-Freifahrtschein)."""
    result = inv.check_search_made_progress([
        _study("A", "X", rate=-0.01, n_modelled=60, plateau_min=48, observed=None),
    ])
    assert result.passed is False
    assert result.inconclusive is False


def test_to_dict_only_includes_inconclusive_when_true():
    passing = inv.check_search_made_progress([
        _study("A", "X", rate=0.05, n_modelled=60, plateau_min=48),
    ])
    assert "inconclusive" not in passing.to_dict()

    inconclusive = inv.check_search_made_progress([
        _study("A", "X", rate=0.0, n_modelled=51, plateau_min=48, observed=[0.5] * 40 + [1.0] * 11),
    ])
    assert inconclusive.to_dict()["inconclusive"] is True
