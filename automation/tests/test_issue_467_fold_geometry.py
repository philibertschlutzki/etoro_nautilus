"""Issue #466 (Audit #467) / Issue #490 (Single-Pass Fragmented Holdout)

Geprüft wird die reine Single-Source-of-Truth-Geometrie ``backtest_runner.compute_fold_boundaries``
unter dem in #490 eingeführten Regime:
- Statisches IS-Fenster (startet immer bei start_ns)
- Kontiguierliche OOS-Folds direkt im Anschluss an das Embargo.
"""
import pytest
from automation.backtest_runner import compute_fold_boundaries

DAY_NS = 86400 * 1_000_000_000
WF = {"is_window_days": 180, "oos_window_days": 45, "splits": 4, "embargo_period_days": 21}

def test_static_is_window_with_contiguous_oos():
    start_ns = 1_700_000_000 * 1_000_000_000
    folds = compute_fold_boundaries(start_ns, WF)

    assert len(folds) == 4, "splits=4 muss vier distinkte Folds liefern"

    expected_purge_end = start_ns + (WF["is_window_days"] + WF["embargo_period_days"]) * DAY_NS

    for k, (is_start, oos_start, oos_end) in enumerate(folds):
        assert is_start == start_ns  # IS-Start ist statisch!

        expected_oos_start = expected_purge_end + k * (WF["oos_window_days"] * DAY_NS)
        expected_oos_end = expected_oos_start + WF["oos_window_days"] * DAY_NS

        assert oos_start == expected_oos_start
        assert oos_end == expected_oos_end

def test_trade_classification_excludes_embargo_and_is():
    start_ns = 1_700_000_000 * 1_000_000_000
    folds = compute_fold_boundaries(start_ns, WF)
    is_start, oos_start, oos_end = folds[0]

    ts_is = is_start + (WF["is_window_days"] - 1) * DAY_NS          # kurz vor IS-Ende
    ts_embargo = is_start + WF["is_window_days"] * DAY_NS + 5 * DAY_NS  # innerhalb der 21d-Purge
    ts_oos = oos_start + 5 * DAY_NS                                  # echtes OOS

    def _is_oos(ts):
        return any(s <= ts < e for _is, s, e in folds)

    assert _is_oos(ts_oos) is True
    assert _is_oos(ts_embargo) is False
    assert _is_oos(ts_is) is False

def test_single_split_is_not_silently_assumed():
    base = dict(is_window_days=180, oos_window_days=45, embargo_period_days=0)
    for n in (1, 2, 4, 6):
        folds = compute_fold_boundaries(0, {**base, "splits": n})
        assert len(folds) == n
