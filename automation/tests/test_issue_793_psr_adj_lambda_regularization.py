import math
from automation.optimizer.deflation import psr_adjusted


def test_issue_793_psr_adjusted_regularization():
    # Large z-score should not explode or saturate unregularized
    z_large = 100.0
    psr_adj = psr_adjusted(z_large, lmbda=0.05)
    
    # max possible value is 1 / sqrt(0.05) ~ 4.4721
    assert psr_adj is not None
    assert psr_adj < 4.4722
    assert psr_adj > 4.4500


def test_issue_793_psr_adjusted_zero_and_negative():
    assert psr_adjusted(0.0) == 0.0
    
    neg_z = psr_adjusted(-10.0, lmbda=0.05)
    assert neg_z is not None
    assert neg_z < 0.0
    assert math.isfinite(neg_z)


def test_issue_793_psr_adjusted_none_and_nan():
    assert psr_adjusted(None) is None
    assert psr_adjusted(float("nan")) is None
