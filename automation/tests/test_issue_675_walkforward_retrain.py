"""Issue #675 — walk_forward.retrain: rollierende statt statisch verankerte IS-Referenz je Fold.

Deckt zwei Ebenen ab:
1. Reine Geometrie (``compute_fold_boundaries``): retrain=False ist bit-identisch zum
   Pre-#675-Verhalten; retrain=True rollt is_start_ns je Fold, embargo-sicher, OHNE die
   OOS-Fold-Grenzen zu veraendern.
2. Die additive Diagnose (``rolling_fold_is_oos_divergence``): auf einer synthetischen
   Zwei-Regime-Preisserie zeigt der rollierende (retrain=True) IS-Bezug fuer den letzten Fold eine
   kleinere IS/OOS-Divergenz als der statische (retrain=False) Bezug, weil die rollierende Referenz
   dasselbe (neue) Regime abbildet wie der zugehoerige OOS-Fold.
"""
import math

import numpy as np
import pandas as pd

from automation.backtest_runner import compute_fold_boundaries, rolling_fold_is_oos_divergence

DAY_NS = 86400 * 1_000_000_000
WF_BASE = {"is_window_days": 180, "oos_window_days": 45, "splits": 4, "embargo_period_days": 21}


def test_retrain_absent_is_bit_identical_to_legacy():
    start_ns = 1_700_000_000 * 1_000_000_000
    folds = compute_fold_boundaries(start_ns, WF_BASE)
    for is_start, _oos_start, _oos_end in folds:
        assert is_start == start_ns


def test_retrain_false_explicit_is_bit_identical_to_legacy():
    start_ns = 1_700_000_000 * 1_000_000_000
    wf = {**WF_BASE, "retrain": False}
    folds = compute_fold_boundaries(start_ns, wf)
    for is_start, _oos_start, _oos_end in folds:
        assert is_start == start_ns


def test_retrain_true_rolls_is_window_per_fold_embargo_safe():
    start_ns = 0
    wf = {**WF_BASE, "retrain": True}
    folds_retrain = compute_fold_boundaries(start_ns, wf)
    folds_static = compute_fold_boundaries(start_ns, {**WF_BASE, "retrain": False})

    # OOS-Fold-Grenzen bleiben IDENTISCH — nur die IS-Referenz je Fold aendert sich.
    for (is_r, oos_s_r, oos_e_r), (is_s, oos_s_s, oos_e_s) in zip(folds_retrain, folds_static):
        assert oos_s_r == oos_s_s
        assert oos_e_r == oos_e_s

    is_window_ns = WF_BASE["is_window_days"] * DAY_NS
    embargo_ns = WF_BASE["embargo_period_days"] * DAY_NS

    prev_is_start = None
    for is_start, oos_start, _oos_end in folds_retrain:
        # Embargo-sicher: die Luecke zwischen (rollierendem) IS-Ende und OOS-Start ist EXAKT das
        # konfigurierte Embargo — kein Leakage-Kollaps auf 0.
        is_end = is_start + is_window_ns
        assert oos_start - is_end == embargo_ns
        if prev_is_start is not None:
            assert is_start > prev_is_start  # rollt strikt vorwaerts
        prev_is_start = is_start

    # Fold 0 unter retrain=True faellt auf denselben Ankerpunkt zurueck wie der statische Modus
    # (es gibt noch keine "juengere" Referenz vor dem allerersten Fold).
    assert folds_retrain[0][0] == folds_static[0][0]
    # Ab Fold 1 divergiert die rollierende Referenz vom statischen Anker.
    assert folds_retrain[1][0] > folds_static[1][0]


def test_single_split_retrain_noop():
    # splits=1 (Holdout-aequivalent): retrain aendert nichts an einem einzigen Fold relativ zum
    # statischen Anker aus derselben Formel (fold=0 ist immer der erste Fold).
    wf = {**WF_BASE, "splits": 1, "retrain": True}
    folds = compute_fold_boundaries(0, wf)
    assert len(folds) == 1
    assert folds[0][0] == 0


def _build_two_regime_mtm(days_a: int, days_b: int) -> pd.Series:
    """Regime A (Tage [0, days_a)): flach/leicht fallend mit Auf-/Ab-Bars (Downside vorhanden).
    Regime B (Tage [days_a, days_a+days_b)): stetiger Aufwaertstrend mit kleinen Ruecksetzern."""
    bars_per_day = 24
    n_a = days_a * bars_per_day
    n_b = days_b * bars_per_day
    t_a = np.arange(n_a)
    t_b = np.arange(n_b)

    price_a = 100.0 - 0.01 * t_a + 1.5 * np.sin(t_a / 3.0)
    last_a = price_a[-1]
    price_b = last_a + 0.05 * t_b + 0.8 * np.sin(t_b / 4.0)

    prices = np.concatenate([price_a, price_b])
    idx = pd.date_range("2020-01-01", periods=len(prices), freq="h")
    return pd.Series(prices, index=idx)


def test_rolling_diagnostic_reduces_stale_regime_divergence_on_last_fold():
    wf_small = {"is_window_days": 5, "oos_window_days": 10, "splits": 3, "embargo_period_days": 1}
    # start_ns MUSS mit dem Epoch der synthetischen mtm-Serie uebereinstimmen (pd.to_datetime(0,
    # unit="ns") == 1970-01-01 Unix-Epoche, NICHT der Serienbeginn "2020-01-01").
    start_ns = pd.Timestamp("2020-01-01").value
    is_window_ns = wf_small["is_window_days"] * DAY_NS

    folds_static = compute_fold_boundaries(start_ns, {**wf_small, "retrain": False})
    folds_retrain = compute_fold_boundaries(start_ns, {**wf_small, "retrain": True})

    # Regime A dauert 20 Tage, Regime B beginnt danach — der letzte Fold (OOS [26, 36)) liegt
    # vollstaendig in Regime B; die rollierende IS-Referenz (Fold 2: [20, 25)) faellt jetzt ebenfalls
    # in Regime B, waehrend die statische Referenz ([0, 5)) in Regime A verbleibt.
    mtm = _build_two_regime_mtm(days_a=20, days_b=16)

    diag_static = rolling_fold_is_oos_divergence(mtm, folds_static, is_window_ns)
    diag_retrain = rolling_fold_is_oos_divergence(mtm, folds_retrain, is_window_ns)

    last_static = diag_static[-1]
    last_retrain = diag_retrain[-1]

    assert last_static["overfit_gap"] is not None
    assert last_retrain["overfit_gap"] is not None

    # Die rollierende (regime-aktuelle) IS-Referenz divergiert WENIGER von der OOS-Performance des
    # letzten Folds als die stale, statisch verankerte Referenz aus Regime A.
    assert abs(last_retrain["overfit_gap"]) < abs(last_static["overfit_gap"])

    # Fold 0 ist unter beiden Modi identisch (siehe Geometrie-Test oben) ⇒ identische Diagnose.
    assert diag_static[0]["overfit_gap"] == diag_retrain[0]["overfit_gap"]


def test_rolling_diagnostic_returns_none_safe_structure_for_empty_series():
    wf = {**WF_BASE, "retrain": True}
    folds = compute_fold_boundaries(0, wf)
    diag = rolling_fold_is_oos_divergence(None, folds, WF_BASE["is_window_days"] * DAY_NS)
    assert len(diag) == len(folds)
    for row in diag:
        assert row["is_sortino_approx"] is None
        assert row["oos_sortino_approx"] is None
        assert row["overfit_gap"] is None
