"""Issue #590 — Fold-Degeneration umging die Dispersionsstrafe (Reward-Hacking, reproduziert).

Der Optimierer lernte, die BEWERTUNG zu löschen statt die Performance zu verbessern: ein verlustfreier
Fold setzte ``sortino = None`` (``losses_count == 0``-Ausstieg) ⇒ der Fold verschwand aus
``oos_fold_sortinos`` ⇒ ``len(fold) < 2`` ⇒ Fold-Dispersions-Strafe 0.0, und der verbleibende Fold
clippte auf den Maximal-Reward. Fixes: (1) ``losses_count == 0`` ist KEIN Ausstiegsgrund mehr
(``sortino_downside_floor`` liefert einen endlichen Sortino); (2) fehlende Folds werden als MAXIMALE
Unsicherheit bestraft (Normierung über ``oos_folds_total``); (3) Gate ``min_evaluable_folds``.
"""
import math

import numpy as np
import pandas as pd

from automation.backtest_runner import _calculate_stats, _evaluate_oos_eligibility
from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics


def test_zero_downside_fold_trips_guard_under_614():
    """Issue #614 SUPERSEDET #590 an dieser Stelle: eine STRENG steigende (Zero-Downside-)Equity hat
    eine UNMESSBARE Downside (``dd_dev`` auf ``sortino_downside_floor`` geklemmt) ⇒ der annualisierte
    Sortino explodiert (>> 25) ⇒ der #614-Guard greift ⇒ ``sortino=None`` (ehrlich undefiniert, kein
    Datenfehler-Ergebnis). Das Anti-Reward-Hacking (#590) läuft jetzt über die PSR (T-bewusst) + den
    engen Guard + ``min_evaluable_folds``, NICHT mehr über einen erzwungen-finiten Zero-Downside-Sortino."""
    idx = pd.date_range("2025-01-01", periods=13, freq="1h", tz="UTC")
    series = pd.Series([1000.0 * (1.002 ** i) for i in range(13)], index=idx)  # streng steigend, keine Downside
    pnls = [5.0] * 12
    holds = [(3600 * 10**9, 1.0)] * 12
    stats = _calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=10)
    assert stats["losses_count"] == 0
    assert stats["sortino_ratio"] is None   # #614 — Guard: |annualized| > 25 ⇒ Datenfehler ⇒ None
    assert stats["psr"] is None             # PSR folgt (kein per-Perioden-Sortino)


def test_small_downside_fold_yields_finite_positive_sortino():
    """Ein Fold mit MESSBARER, realer Downside (moderates Signal-Rausch) liefert einen FINITEN,
    positiven Sortino UNTERHALB des #614-Guards (25) — die #590-Intention (ein überwiegend gewinnender
    Fold verschwindet nicht) bleibt für nicht-degenerierte Downside erhalten. Die PSR ist definiert."""
    idx = pd.date_range("2025-01-01", periods=41, freq="1h", tz="UTC")
    # Kleiner positiver Drift mit realer Volatilität ⇒ moderater per-Perioden-Sortino, annualisiert < 25.
    rets = ([0.0015, -0.0011] * 20) + [0.0015]   # 41 Werte ⇒ 40 Perioden, Netto-Drift positiv
    vals = [1000.0]
    for r in rets[1:]:
        vals.append(vals[-1] * (1.0 + r))
    series = pd.Series(vals, index=idx)
    pnls = [3.0] * 25 + [-2.0] * 13
    holds = [(3600 * 10**9, 1.0)] * 38
    stats = _calculate_stats(pnls, holds, 1000.0, mtm_series=series, min_trades_for_sortino=10)
    sortino = stats["sortino_ratio"]
    assert sortino is not None
    assert math.isfinite(sortino) and sortino > 0.0
    assert abs(sortino) <= 25.0             # unter dem #614-Guard
    assert 0.0 <= stats["psr"] <= 1.0       # PSR definiert und beschränkt


_WEIGHTS = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001, "evaluable_reward_floor": -12.0,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
    "fold_dispersion_weight": 0.5, "missing_fold_penalty_scale": 0.05, "w_ret": 0.0,
}
_CFG = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}


def _m(fold_returns, folds_total, oos_sortino=2.0, oos_total_return=0.04):
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=2.0,
        oos_sortino=oos_sortino, oos_max_drawdown=0.02, oos_total_trades=40, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=oos_total_return,
        oos_fold_returns=tuple(fold_returns), oos_folds_total=folds_total,
    )


def test_fold_dispersion_not_gameable_by_degeneration():
    """Identischer gepoolter Return/Sortino; Trial A = 4 valide Folds, Trial B = 1 valide + 3
    degenerierte ⇒ reward_A > reward_B (strikt). Fehlende Folds sind eine Strafe, keine Auslassung."""
    a = compute_reward(_m([0.04, 0.04, 0.04, 0.04], 4), universe_size=1,
                       weights=_WEIGHTS, risk_dd_cap=0.3, tournament_cfg=_CFG)
    b = compute_reward(_m([0.16], 4), universe_size=1,
                       weights=_WEIGHTS, risk_dd_cap=0.3, tournament_cfg=_CFG)
    assert a > b, "der degenerierte Trial (3 fehlende Folds) darf NICHT gleich/besser bewertet werden"


def test_min_evaluable_folds_gate_rejects_degenerate_trial():
    """oos_folds_total > 1, aber < oos_min_evaluable_folds valide Fold-Sortinos ⇒ nicht eligible."""
    cfg = {
        "oos_min_trades": 1, "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0,
        "oos_min_win_rate": 0.0, "oos_min_evaluable_folds": 2, "max_drawdown": 0.3,
        "eligible_requires_all": ["min_trades", "min_evaluable_folds"],
    }
    degenerate = {
        "total_trades": 13, "max_drawdown": 0.0, "win_rate": 1.0, "total_return": 0.5,
        "expectancy": 0.01, "sortino_ratio": 15.0, "profit_factor": None,
        "median_position_notional": 1000.0,
        "oos_folds_total": 4, "oos_fold_sortinos": [15.0],   # nur 1 valider Fold von 4
    }
    ev = _evaluate_oos_eligibility(degenerate, cfg)
    assert ev["oos_eligible"] is False
    assert any("oos_min_evaluable_folds" in r for r in ev["oos_rejection_reasons"])

    healthy = dict(degenerate, oos_fold_sortinos=[2.0, 1.5, 1.0, 1.2])   # 4 valide Folds
    ev2 = _evaluate_oos_eligibility(healthy, cfg)
    assert ev2["oos_eligible"] is True
