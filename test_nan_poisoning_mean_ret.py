import pandas as pd
import numpy as np
import math

mar = 0.0
period_rets = pd.Series([1.0, -1.0])
downside_diff = (period_rets - mar).clip(upper=0.0)
dd_dev = float(np.sqrt((downside_diff ** 2).mean()))

mean_ret = np.nan # Simulate nan mean_ret for whatever reason

annualization_factor = 252
sortino_raw = ((mean_ret - mar) / dd_dev) * math.sqrt(annualization_factor)
print("sortino_raw:", sortino_raw)

# This will result in NaN, wait! Python's min/max propagates NaN.
sortino = max(-50.0, min(sortino_raw, 50.0))
print("sortino:", sortino)
print("isna:", pd.isna(sortino))
