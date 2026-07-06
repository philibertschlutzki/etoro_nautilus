import math
import numpy as np

sortino_raw = np.nan

# If sortino_raw is NaN, let's see min and max
print(min(sortino_raw, 50.0))
print(max(-50.0, min(sortino_raw, 50.0)))
