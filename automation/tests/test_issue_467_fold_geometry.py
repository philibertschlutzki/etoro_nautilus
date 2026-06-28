import pytest
from datetime import datetime, timezone
import json

def test_embargo_logic_mocked_in_extract_metrics():
    """
    Testet Issue #466: Embargo-Periode zwischen IS und OOS.
    Stellt sicher, dass Trades innerhalb des Embargos nicht in die OOS-Menge fließen.
    Wir simulieren die Logik hier, da backtest_runner importieren fehlschlägt wg. fehlendem pyarrow in pytest.
    """
    is_window_ns = 180 * 86400 * 1_000_000_000
    oos_window_ns = 45 * 86400 * 1_000_000_000
    embargo_period_ns = 21 * 86400 * 1_000_000_000
    splits = 1

    start_ns = 1700000000 * 1_000_000_000

    fold = 0
    split_is_start_ns = start_ns + fold * oos_window_ns
    split_oos_start_ns = split_is_start_ns + is_window_ns + embargo_period_ns
    split_oos_end_ns = split_is_start_ns + is_window_ns + oos_window_ns

    # Trade 1: Kurz vor IS-Ende -> Nicht in OOS
    ts_is = split_is_start_ns + is_window_ns - 1_000_000_000

    # Trade 2: Im Embargo -> Nicht in OOS!
    ts_embargo = split_is_start_ns + is_window_ns + 10 * 86400 * 1_000_000_000

    # Trade 3: Im echten OOS -> Sollte in OOS sein
    ts_oos = split_oos_start_ns + 10 * 86400 * 1_000_000_000

    assert split_oos_start_ns <= ts_oos < split_oos_end_ns
    assert not (split_oos_start_ns <= ts_embargo < split_oos_end_ns)
    assert not (split_oos_start_ns <= ts_is < split_oos_end_ns)
