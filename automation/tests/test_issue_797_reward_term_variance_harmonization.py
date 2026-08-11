from automation.optimizer.invariants import check_reward_term_variance


def test_issue_797_inactive_zero_penalty_terms_pass():
    # Trials with base varying but inactive penalty terms (all 0.0)
    trials = [
        {
            "oos_evaluated": True,
            "reward_terms": {
                "base": 1.0 + i * 0.1,
                "divergence": 0.0,
                "dd_penalty": 0.0,
                "param_pen": 0.0,
                "turnover": 0.0,
                "fold_dispersion": 0.0,
                "tie_breaker": 0.0,
            }
        }
        for i in range(10)
    ]
    
    res = check_reward_term_variance(trials)
    assert res.passed is True
    assert res.actual == []


def test_issue_797_constant_nonzero_penalty_terms_fail():
    # Trials with constant NON-ZERO penalty term -> genuine plateau defect
    trials = [
        {
            "oos_evaluated": True,
            "reward_terms": {
                "base": 1.0 + i * 0.1,
                "divergence": 0.5,  # constant non-zero
                "dd_penalty": 0.0,
                "param_pen": 0.0,
                "turnover": 0.0,
                "fold_dispersion": 0.0,
                "tie_breaker": 0.0,
            }
        }
        for i in range(10)
    ]
    
    res = check_reward_term_variance(trials)
    assert res.passed is False
    assert "divergence" in res.actual
