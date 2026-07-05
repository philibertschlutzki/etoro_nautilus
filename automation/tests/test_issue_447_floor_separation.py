"""Issue #447 (P1) — Unevaluable-Floor belohnt kein IS-Overtrading (Per-Symbol-Shaping).

Formalisiert die zwei Invarianten des Per-Symbol-Reward-Pfads, die verhindern, dass Optuna bei
strukturell unevaluierbaren Trials (OOS=0, vgl. #448) faktisch die IS-Trade-Anzahl maximiert:

  1. **Floor-Separation:** der BESTE unevaluierbare Reward
     (`penalty_unevaluable_oos + unevaluable_shaping_span` = −9.75) bleibt strikt KLEINER als der
     SCHLECHTESTE evaluierbare Reward (`-sortino_clip_abs` = −5.0). Ein hyperaktiver
     unevaluierbarer Trial kann niemals einen schwachen evaluierbaren überholen (Rang-Invariante).
  2. **Shaping-Saturation (Anti-Overtrading-Deckel):** im Per-Symbol-Pfad saturiert das
     IS-Aktivitäts-Shaping bei `per_symbol_shaping_trade_target`; jenseits davon liefert „noch mehr
     IS-Trades" KEINEN zusätzlichen Reward — `reward(is=target) == reward(is=10·target)`.

Die Werte spiegeln `automation/config/optimizer.json` (penalty −10, span 0.25, epsilon 0.001,
per_symbol_shaping_trade_target 400), per DI übergeben (kein File-IO im Test).
"""
import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import compute_reward

W = {
    "penalty_unevaluable_oos": -10.0,
    "sortino_clip_abs": 5.0,
    "penalty_overfit_weight": 0.5,
    "penalty_dd_weight": 8.0,
    "bonus_coverage_weight": 1.0,
    "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001,
    "shaping_trade_target": 50,
    "per_symbol_shaping_trade_target": 400,
    "reward_mode": "per_symbol",
    "lambda_reg": 0.25,
    "oos_min_trades": 3,
}

UNEVAL_CEILING = W["penalty_unevaluable_oos"] + W["unevaluable_shaping_span"]            # −9.75
EVAL_FLOOR = -float(W["sortino_clip_abs"])                                               # −5.0


def _m(**kw):
    base = dict(oos_evaluated=False, oos_eligible=False, is_sortino_median=0.0,
                oos_sortino=None, oos_max_drawdown=0.0, oos_total_trades=0,
                win_count=0, fully_eligible_pairs=0, is_total_trades=0, hit_trade_cap=False)
    base.update(kw)
    return TournamentMetrics(**base)


def _reward(m):
    return compute_reward(m, universe_size=1, weights=W, risk_dd_cap=0.30,
                          sampled={}, global_params={}, strategy="X")


def test_best_unevaluable_equals_documented_ceiling():
    """Maximal geshapter unevaluierbarer Trial (IS-Aktivität gesättigt) == penalty + span (−9.75)."""
    best_uneval = _reward(_m(is_total_trades=10 * W["per_symbol_shaping_trade_target"]))
    assert best_uneval == pytest.approx(UNEVAL_CEILING)


def test_per_symbol_floor_separation_is_strict():
    """Der schlechteste evaluierbare Trial schlägt strikt den besten unevaluierbaren (Rang-Invariante)."""
    best_uneval = _reward(_m(is_total_trades=10 * W["per_symbol_shaping_trade_target"]))
    # Schlechtest möglicher evaluierbarer Trial: minimaler Sortino, maximaler Overfit-Gap & DD.
    worst_eval = _reward(_m(oos_evaluated=True, oos_eligible=True,
                            oos_sortino=-W["sortino_clip_abs"], is_sortino_median=W["sortino_clip_abs"],
                            oos_max_drawdown=0.99, oos_total_trades=20))
    assert worst_eval >= EVAL_FLOOR - 1e-12
    assert worst_eval > best_uneval, "Floor-Separation verletzt: evaluable muss IMMER unevaluable schlagen"


def test_is_activity_shaping_saturates_at_target():
    """Anti-Overtrading: jenseits des Targets liefert mehr IS-Aktivität KEINEN Mehr-Reward."""
    target = W["per_symbol_shaping_trade_target"]
    at_target = _reward(_m(is_total_trades=target))
    ten_x = _reward(_m(is_total_trades=10 * target))
    assert at_target == pytest.approx(ten_x)
    # …und beide sitzen exakt auf der Unevaluable-Decke (vollständig gesättigtes Shaping).
    assert at_target == pytest.approx(UNEVAL_CEILING)


def test_is_activity_shaping_has_gradient_below_target():
    """Unterhalb des Targets ist das Shaping ein (schwacher) Tie-Breaker, kein dominanter Gradient."""
    low = _reward(_m(is_total_trades=40))
    high = _reward(_m(is_total_trades=200))
    assert high > low                       # monoton, Gradient Richtung Eligibility vorhanden
    assert high < EVAL_FLOOR                 # aber strikt unter dem Evaluable-Floor (gedeckelt)
