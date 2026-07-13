import pandas as pd
import numpy as np
import pytest
from automation.backtest_runner import _get_annualization_factor

def test_issue_587_annualization_rth():
    # Synthetische RTH-Serie (6.5 h/Tag, 5 Tage/Woche) über ca. 1 Jahr.
    bdays = pd.bdate_range("2025-01-01", "2025-12-31")  # 261 business days in 2025
    
    timestamps = []
    for i, day in enumerate(bdays):
        # Wechselt zwischen 6 und 7 Stunden, um einen Durchschnitt von 6.5 Bars pro Tag zu erhalten
        num_bars = 6 if i % 2 == 0 else 7
        for h in range(num_bars):
            timestamps.append(day + pd.Timedelta(hours=10 + h))
            
    mtm_series = pd.Series(range(len(timestamps)), index=pd.DatetimeIndex(timestamps))
    
    factor = _get_annualization_factor(mtm_series)
    
    # Akzeptanzkriterium: 
    # 1) Wert im Intervall [1500, 1800]
    # 2) Wert nahe 1638 ± 5% (d.h. [1556.1, 1719.9])
    assert 1500.0 <= factor <= 1800.0
    assert pytest.approx(1638.0, rel=0.05) == factor
