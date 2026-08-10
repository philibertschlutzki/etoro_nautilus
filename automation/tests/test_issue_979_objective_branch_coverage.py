"""Issue #979 (Katalog C, P0) — der TPE optimiert eine Zielfunktion, deren ordnender Zweig
(``reward_terms.branch == 'per_symbol'``) im Referenzlauf 46cf5070 nur 2.15% der Auswertungen
ausmachte. ``check_objective_branch_coverage`` ist der additive Regressionswächter.
"""
from automation.optimizer import invariants as inv


def _trial(branch):
    return {"reward_terms": {"branch": branch}}


def test_passes_when_ordering_branch_meets_the_threshold():
    trials = [_trial("per_symbol")] * 2 + [_trial("failure")] * 8
    result = inv.check_objective_branch_coverage(trials, min_ordering_fraction=0.10)
    assert result.passed is True
    assert result.actual == 0.2


def test_fails_when_ordering_branch_is_negligible():
    """Reproduziert den Referenzlauf-Befund: 57/2651 = 2.15% < 10%."""
    trials = [_trial("per_symbol")] * 57 + [_trial("failure")] * 2078 + [_trial("unevaluable")] * 516
    result = inv.check_objective_branch_coverage(trials, min_ordering_fraction=0.10)
    assert result.passed is False
    assert result.actual == round(57 / 2651, 4)


def test_noop_without_branch_telemetry():
    result = inv.check_objective_branch_coverage([{"reward_terms": {}}, {}])
    assert result.passed is True
    assert result.actual is None
