import numpy as np
from automation.optimizer.deflation import normalize_annualization_factor


def test_issue_798_normalize_annualization_factor_bounding():
    # Extreme frequencies bounded between sqrt(1) = 1.0 and sqrt(252) = 15.8745
    sqrt_f_low = normalize_annualization_factor(n_trades=0, time_span_years=1.0)
    assert sqrt_f_low == 1.0
    
    sqrt_f_high = normalize_annualization_factor(n_trades=10000, time_span_years=1.0)
    assert abs(sqrt_f_high - np.sqrt(252.0)) < 1e-4
    
    # Moderate frequency
    sqrt_f_mod = normalize_annualization_factor(n_trades=100, time_span_years=1.0)
    assert abs(sqrt_f_mod - np.sqrt(100.0)) < 1e-4


def test_issue_798_span_control():
    # Test max ratio across high and low frequency folds within a trial
    f_low = normalize_annualization_factor(n_trades=10, time_span_years=0.5)   # f = 20 -> sqrt(20) = 4.472
    f_high = normalize_annualization_factor(n_trades=500, time_span_years=0.5)  # f = 1000 -> capped 252 -> sqrt(252) = 15.874
    
    span_ratio = f_high / f_low
    assert span_ratio <= 4.58
