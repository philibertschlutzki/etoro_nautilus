from automation.optimizer.gate import evaluate_soft_guard_penalty
from automation.optimizer.invariants import check_objective_branch_coverage


def test_issue_799_evaluate_soft_guard_penalty():
    # Trade count >= min_trades (30) -> zero penalty
    assert evaluate_soft_guard_penalty(n_trades=30, min_trades=30) == 0.0
    assert evaluate_soft_guard_penalty(n_trades=40, min_trades=30) == 0.0
    
    # Trade count < min_trades -> continuous quadratic penalty
    p15 = evaluate_soft_guard_penalty(n_trades=15, min_trades=30, max_penalty=50.0)
    # deficit ratio = (30-15)/30 = 0.5 -> penalty = -50 * (0.5^2) = -12.5
    assert abs(p15 - (-12.5)) < 1e-4
    
    p0 = evaluate_soft_guard_penalty(n_trades=0, min_trades=30, max_penalty=50.0)
    assert abs(p0 - (-50.0)) < 1e-4


def test_issue_799_objective_branch_coverage():
    """Issue #955/#1121 (Katalog #960) — check_objective_branch_coverage misst seit diesem Fix den
    Anteil ineligibler Trials mit definierter Selektionsstatistik, nicht mehr
    reward_terms.branch=='per_symbol' (siehe dortiger Docstring)."""
    trials = [
        {"oos_evaluated": True, "oos_eligible": True,
         "oos_selection_statistic_available": True} for _ in range(10)
    ] + [
        {"oos_evaluated": True, "oos_eligible": False,
         "oos_selection_statistic_available": True} for _ in range(25)
    ] + [
        {"oos_evaluated": True, "oos_eligible": False,
         "oos_selection_statistic_available": False} for _ in range(75)
    ]

    # 25 / 100 ineligible Trials gemessen = 25% >= 20%
    res = check_objective_branch_coverage(trials, min_measured_fraction=0.20)
    assert res.passed is True
    assert res.actual == 0.25
