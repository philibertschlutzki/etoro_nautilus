"""Issue #616 — Fold-Dispersions-Strafe war ein struktureller Blindgänger (Skalenfehler wie Pre-#597-dd).

``pstdev(fold_returns)`` (0.001–0.05) gegen ``base`` (PSR/asinh-Sortino) — ohne Normierung war der Term
Median 0.036, Maximum 0.100, drei Grössenordnungen zu klein. Fix: ``fold_dispersion_scale`` (analog
``dd_reward_scale``) normiert ihn in den Bereich der übrigen Terme.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer.reward import compute_reward
from automation.optimizer.parsing import TournamentMetrics

_W = {
    "penalty_unevaluable_oos": -20.0, "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001, "evaluable_reward_floor": -12.0,
    "sortino_clip_abs": 5.0, "sortino_soft_scale": 5.0,
    "penalty_overfit_weight": 0.5, "penalty_dd_weight": 1.0, "bonus_coverage_weight": 1.0,
    "fold_dispersion_weight": 0.5, "fold_dispersion_scale": 0.03,
    "missing_fold_penalty_scale": 0.05, "w_ret": 0.0, "penalty_turnover_weight": 0.0,
}
_CFG = {"oos_min_total_return": 0.005, "max_drawdown": 0.3}


def _m(fold_returns):
    # gleicher Gesamt-Return, gleiche Base — nur die Fold-STREUUNG unterscheidet sich.
    return TournamentMetrics(
        oos_evaluated=True, oos_eligible=True, is_sortino_median=1.0, is_sortino_pooled=1.0,
        oos_sortino=1.5, oos_max_drawdown=0.0, oos_total_trades=40, win_count=1,
        fully_eligible_pairs=1, is_total_trades=100, oos_total_return=0.0,
        oos_fold_returns=tuple(fold_returns), oos_folds_total=len(fold_returns))


def _terms(fr):
    _, t = compute_reward(_m(fr), universe_size=1, weights=_W, risk_dd_cap=0.3,
                          tournament_cfg=_CFG, return_terms=True)
    return t


# ── Akzeptanz: alternierende (Sign-Flip-)Folds strikt härter bestraft als konsistente ────────────
def test_alternating_folds_penalized_more_than_consistent():
    inconsistent = _terms([0.05, -0.05, 0.05, -0.05])   # 100 % Vorzeichenwechsel, Gesamt 0
    consistent = _terms([0.01, 0.01, 0.01, 0.01])        # pstdev 0
    assert inconsistent["fold_dispersion"] > consistent["fold_dispersion"]
    assert consistent["fold_dispersion"] == pytest.approx(0.0, abs=1e-12)
    # Reward-Unterschied > 0.5 Einheiten (der Term beisst jetzt, statt 3 Grössenordnungen zu klein).
    assert inconsistent["fold_dispersion"] - consistent["fold_dispersion"] > 0.5


def test_penalty_is_non_negligible_and_normalized():
    """Ein realistischer Dispersions-Fold (±0.03) liefert eine Strafe im Bereich [0.1, 1.0] (nicht 0.03)."""
    t = _terms([0.03, -0.03, 0.03, -0.03])   # pstdev 0.03 ⇒ norm 1.0 ⇒ penalty 0.5
    assert 0.1 <= t["fold_dispersion"] <= 1.0


def test_scale_absent_is_legacy_raw():
    """Fehlt fold_dispersion_scale ⇒ Roh-Skala (bit-identisch, migrations-sicher)."""
    import statistics
    w = dict(_W)
    w.pop("fold_dispersion_scale")
    _, t = compute_reward(_m([0.05, -0.05, 0.05, -0.05]), universe_size=1, weights=w,
                          risk_dd_cap=0.3, tournament_cfg=_CFG, return_terms=True)
    raw = 0.5 * statistics.pstdev([0.05, -0.05, 0.05, -0.05])
    assert t["fold_dispersion"] == pytest.approx(raw, abs=1e-12)


def test_shipped_config_has_fold_dispersion_scale():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg["fold_dispersion_scale"] > 0.0
    assert "fold_dispersion_scale" in cfg["_schema"]["fields"]
