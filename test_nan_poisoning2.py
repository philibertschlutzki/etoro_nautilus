import pandas as pd
import numpy as np
import math

period_rets = pd.Series([np.nan, np.nan])
downside_diff = (period_rets - 0.0).clip(upper=0.0)
dd_dev = float(np.sqrt((downside_diff ** 2).mean()))

print("dd_dev:", dd_dev)
print("isna:", pd.isna(dd_dev))
print("math.isnan:", math.isnan(dd_dev))
