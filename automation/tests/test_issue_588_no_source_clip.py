"""Issue #588 — RATIO_CAP klemmte den Sortino an der Quelle und vernichtete die Rangordnung.

Der Hard-Clip ``max(-15, min(sortino_raw, 15))`` war eine stückweise konstante Funktion (Gradient 0
oberhalb der Grenze): zwei Trials mit ``sortino_raw`` 18 und 47 waren danach ununterscheidbar, und die
weiche #559-asinh-Sättigung im Reward konnte die gelöschte Information nicht rekonstruieren. Fix: KEIN
Clip mehr in ``_calculate_stats`` — nur ein reiner Numerik-/Datenfehler-Guard (``sortino_numeric_guard``,
fail-loud als ``SORTINO_GUARD_TRIPPED``). Die semantische Sättigung passiert ausschliesslich in
``reward.py`` via ``sortino_soft_scale`` (streng monoton).
"""
import logging
import math
from unittest.mock import patch

import numpy as np
import pandas as pd

from automation.backtest_runner import _calculate_stats, _read_sortino_numeric_guard
from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics


def _sortino_for_target(target_ratio: float) -> float:
    """Baut eine MtM-Serie, deren (mean/dd_dev) ≈ target_ratio ist (ann=1.0 gepatcht), und liefert
    den von _calculate_stats zurückgegebenen (UNGEKLEMMTEN) Sortino."""
    # returns: konstantes Plus + ein kleiner Verlust ⇒ definierte Downside-Deviation.
    up = 0.001 * target_ratio
    rets = [up] * 30 + [-0.001]
    mtm = [1000.0]
    for r in rets:
        mtm.append(mtm[-1] * (1 + r))
    idx = pd.date_range("2025-01-01", periods=len(mtm), freq="1h", tz="UTC")
    series = pd.Series(mtm, index=idx)
    # Issue #614 — dieser #588-Test prüft die NO-CLIP-Rangordnung oberhalb der alten Grenze (15), nicht
    # den Datenfehler-Guard (25, separat getestet). Guard hochsetzen, damit die Ordnung sichtbar bleibt.
    with patch("automation.backtest_runner._get_annualization_factor", return_value=1.0), \
         patch("automation.backtest_runner._read_sortino_numeric_guard", return_value=1e9):
        stats = _calculate_stats([1.0] * 30 + [-1.0], [(3600 * 10**9, 1.0)] * 31, 1000.0,
                                 mtm_series=series, min_trades_for_sortino=5)
    return stats["sortino_ratio"]


def test_source_sortino_is_unclamped_and_ordered():
    """sortino 18 und 47 sind NACH _calculate_stats verschieden und BEIDE > 15 (kein Hard-Clip, #588).
    (Der #614-Datenfehler-Guard bei 25 ist hier hochgesetzt — er ist in test_issue_614/#588 separat getestet.)"""
    s18 = _sortino_for_target(18.0)
    s47 = _sortino_for_target(47.0)
    assert s18 is not None and s47 is not None
    assert s18 > 15.0, "kein Hard-Clip mehr: ein Sortino > 15 bleibt ungeklemmt"
    assert s47 > 15.0
    assert s18 < s47, "die Rangordnung oberhalb der alten Clip-Grenze bleibt erhalten"


def _reward(oos_sortino: float) -> float:
    weights = {
        "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
        "evaluable_floor_epsilon": 0.001, "evaluable_reward_floor": -12.0,
        "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
        "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
        "w_ret": 2.0,
    }
    m = TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=oos_sortino,
        oos_sortino=oos_sortino, oos_max_drawdown=0.01, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.02,
    )
    return compute_reward(m, universe_size=1, weights=weights, risk_dd_cap=0.3,
                          tournament_cfg={"oos_min_total_return": 0.005, "max_drawdown": 0.3})


def test_reward_strictly_monotone_past_old_clip():
    """Nach compute_reward gilt reward_18 < reward_47 (strikt) — die #559-asinh-Sättigung trägt die
    Ordnung, die der Hard-Clip vorher gelöscht hätte."""
    assert _reward(18.0) < _reward(47.0)


def test_numeric_guard_trips_and_logs():
    """|sortino_raw| jenseits von sortino_numeric_guard ⇒ None + SORTINO_GUARD_TRIPPED (WARNING mit
    sortino_raw/n_periods/dd_dev)."""
    guard = _read_sortino_numeric_guard()
    idx = pd.date_range("2025-01-01", periods=12, freq="1h", tz="UTC")
    # near-zero Downside × riesiger Faktor ⇒ sortino_raw ≫ guard.
    series = pd.Series([1000.0 * (1.05 ** i) for i in range(11)] + [1000.0 * 1.05 ** 10 * 0.999999999],
                       index=idx)
    caplog = _CapturingHandler()
    logging.getLogger("optimizer").addHandler(caplog)
    try:
        with patch("automation.backtest_runner._get_annualization_factor", return_value=1e10):
            stats = _calculate_stats([1.0] * 10 + [-1e-9], [(3600 * 10**9, 1.0)] * 11, 1000.0,
                                     mtm_series=series, min_trades_for_sortino=5)
    finally:
        logging.getLogger("optimizer").removeHandler(caplog)
    assert stats["sortino_ratio"] is None, "Datenfehler jenseits des Guards ⇒ None (kein still-Clip)"
    # Issue #614 — der Guard ist von 1e6 auf 25.0 gesenkt (bei T≈200 ist |annualized|>25 ein
    # Datenfehler); er bleibt ein fail-loud Datenfehler-Guard, NICHT die Reward-Sättigung (soft_scale).
    assert guard == 25.0
    assert any("SORTINO_GUARD_TRIPPED" in r.getMessage() for r in caplog.records)


class _CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)
