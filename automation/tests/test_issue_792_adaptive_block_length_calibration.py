import numpy as np
from automation.optimizer.bootstrap import optimal_block_length, lag1_autocorrelation
from automation.optimizer.deflation import bootstrap_psr_z


def test_issue_792_optimal_block_length_nan_handling():
    data = [1.0, np.nan, 2.0, -1.0, 0.5, np.inf, 1.5, -0.5]
    bl = optimal_block_length(data)
    assert isinstance(bl, int)
    assert bl >= 1


def test_issue_792_optimal_block_length_politis_white_scaling():
    # AR(1) process with high autocorrelation
    rng = np.random.default_rng(42)
    n = 200
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.8 * x[i-1] + rng.normal()
        
    bl = optimal_block_length(x)
    assert isinstance(bl, int)
    assert 1 <= bl <= n // 2


def test_issue_792_bootstrap_psr_z_availability_fallback():
    # Data with constant non-zero returns where bootstrap std might degenerate
    returns = [0.01, 0.01, 0.01, 0.01, 0.01]
    z, se = bootstrap_psr_z(returns)
    assert z is not None
    assert se is not None
    assert se > 0
