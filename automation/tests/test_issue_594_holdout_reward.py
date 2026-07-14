"""Issue #594 — Holdout-Reward aus einem Platzhalter und einem Frankenstein-Metrikvektor.

(A) ``is_sortino_median=0.0`` als Platzhalter erzeugte bei negativem base eine FIKTIVE Overfit-Strafe
    von ``0.5·|base|``, die in die Promotion-Entscheidung floss. Fix: ``holdout=True`` schaltet die
    IS-abhängigen Terme AB (kein Platzhalter); ohne ``holdout=True`` ist ``is_sortino_median is None``
    ein ``ValueError`` (kein stiller 0.0-Default).
(B) Komponentenweiser Median über Top-k erzeugte einen Vektor, der zu KEINEM real gelaufenen Backtest
    gehörte. Fix: der Lauf mit dem medianen Rang liefert seinen VOLLSTÄNDIGEN, kohärenten Vektor
    (``_median_rank_index``, dokumentierte Tie-Break-Regel).
"""
import pytest

from automation.optimizer.reward import compute_reward
from automation.optimizer.confirm import _median_rank_index
from automation.optimizer.parsing import TournamentMetrics


_W = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001, "evaluable_reward_floor": -12.0,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0, "w_ret": 0.0,
}
_CFG = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}


def test_none_is_sortino_without_holdout_raises():
    """compute_reward wirft ValueError, wenn is_sortino_median is None UND kein holdout=True."""
    m = TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
        oos_sortino=1.5, oos_max_drawdown=0.02, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.03)
    with pytest.raises(ValueError, match="is_sortino_median is None"):
        compute_reward(m, universe_size=1, weights=_W, risk_dd_cap=0.3, tournament_cfg=_CFG)


def test_holdout_mode_disables_is_terms_no_placeholder():
    """Mit holdout=True liefert derselbe (None-IS) Vektor einen endlichen Reward OHNE fiktive
    Overfit-Strafe; der Divergenz-Term ist abgeschaltet (nicht mit 0.0 gefüttert)."""
    m = TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
        oos_sortino=1.5, oos_max_drawdown=0.02, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.03)
    r = compute_reward(m, universe_size=1, weights=_W, risk_dd_cap=0.3, tournament_cfg=_CFG, holdout=True)
    # base (soft-scaled 1.5) − dd_penalty; keine Divergenz-, keine Fold-Dispersions-Strafe.
    import math
    base = 5.0 * math.asinh(1.5 / 5.0)
    dd = 1.0 * ((0.02 / 0.3) ** 2)   # kein dd_reward_scale in _W ⇒ Gate-Cap-Fallback
    assert r == pytest.approx(base - dd, abs=1e-9)


def test_holdout_negative_base_has_no_fictitious_overfit_penalty():
    """Bei negativem base erzeugte der Platzhalter 0.0 vorher eine Overfit-Strafe 0.5·|base|. Mit
    holdout=True existiert dieser Term nicht."""
    m = TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=None,
        oos_sortino=-3.0, oos_max_drawdown=0.0, oos_total_trades=30, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=-0.01)
    r = compute_reward(m, universe_size=1, weights=_W, risk_dd_cap=0.0, tournament_cfg=_CFG, holdout=True)
    import math
    base = 5.0 * math.asinh(-3.0 / 5.0)
    assert r == pytest.approx(base, abs=1e-9)   # reward == base, keine fiktive 0.5·|base|-Strafe


def test_median_rank_index_selects_a_real_run():
    """_median_rank_index adressiert EINEN real gelaufenen Lauf (kohärenter Vektor), kein
    komponentenweiser Median."""
    returns = [0.10, -0.05, 0.02, 0.07, 0.01]   # 5 Läufe
    idx = _median_rank_index(returns)
    # medianer Rang (unterer Median): sortiert [-0.05, 0.01, 0.02, 0.07, 0.10] ⇒ Median 0.02 ⇒ Index 2.
    assert idx == 2
    assert returns[idx] == 0.02
    # Der ausgewählte Wert ist EINER der Eingaben (kein neu zusammengesetzter).
    assert returns[idx] in returns


def test_median_rank_index_lower_median_and_none_handling():
    assert _median_rank_index([3.0, 1.0]) == 1          # gerade ⇒ unterer Median (1.0 an Index 1)
    assert _median_rank_index([0.5, None, 0.9]) == 0    # None rankt als −inf ⇒ Median-Rang = 0.5
    with pytest.raises(ValueError):
        _median_rank_index([])
