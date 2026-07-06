import pandas as pd
import numpy as np
import math

from automation.backtest_runner import _calculate_stats

# Create series where dd_dev ends up NaN or 0
mtm_vals = [1.0] * 10
mtm_series = pd.Series(mtm_vals)
period_rets = mtm_series.pct_change().dropna()
print("period_rets:\n", period_rets)

downside_diff = (period_rets - 0.0).clip(upper=0.0)
dd_dev = float(np.sqrt((downside_diff ** 2).mean()))

print("dd_dev:", dd_dev)
print("isna:", pd.isna(dd_dev))
