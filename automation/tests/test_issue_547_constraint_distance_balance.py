"""Issue #547 — Expectancy-Distanzterm entkoppeln, damit er die Constraint-Penalty nicht dominiert.

Vor dem Fix normierte ``_shortfall_distance`` die Expectancy-Distanz auf das (mikroskopische)
Target ``oos_min_expectancy`` (5e-05). Ein winziger absoluter Miss erzeugte damit eine Distanz
≫ 1, die den Aktiv-Mittelwert der sechs Distanz-Terme dominierte. ``expectancy_penalty_scale``
(analog ``return_penalty_scale`` aus #467) bringt den Term auf eine vergleichbare Skala; ein
optionaler ``distance_term_cap`` deckelt jede Dimension robust gegen Kalibrierfehler.
"""
import pytest

from automation.optimizer.parsing import TournamentMetrics
from automation.optimizer.reward import (
    _constraint_distance_penalty,
    _shortfall_distance,
    _excess_distance,
    _any_condition_distance,
    compute_reward,
)

WEIGHTS = {
    "penalty_unevaluable_oos": -10.0,
    "unevaluable_shaping_span": 0.25,
    "evaluable_floor_epsilon": 0.001,
    "constraint_distance_penalty_weight": 0.25,
    "sortino_clip_abs": 5.0,
    "penalty_overfit_weight": 0.5,
    "penalty_dd_weight": 8.0,
    "bonus_coverage_weight": 1.0,
}

# --- Trial #3 aus dem Sweep-Log (rekonstruiert aus den im Issue genannten Distanzen) ----------
#   return 0.281, expectancy 8.4, win_rate 0.824, any 0.952 (unter der ALTEN Kalibrierung).
TRIAL3 = dict(
    oos_evaluated=True, oos_eligible=False, is_sortino_median=0.0,
    oos_sortino=0.0144, oos_max_drawdown=0.05, oos_total_trades=100,
    win_count=0, fully_eligible_pairs=0, is_total_trades=0,
    oos_total_return=-0.0231, oos_expectancy=-0.00037, oos_win_rate=0.044,
    oos_profit_factor=0.05,
)

OLD_CFG = {
    "oos_min_trades": 1, "oos_min_total_return": 0.005, "oos_min_expectancy": 5e-05,
    "oos_min_win_rate": 0.25, "oos_min_sortino": 0.3, "oos_min_profit_factor": 1.1,
    "return_penalty_scale": 0.1, "max_drawdown": 0.3,
}
NEW_CFG = dict(OLD_CFG, oos_min_expectancy=0.001,
               expectancy_penalty_scale=0.002, distance_term_cap=3.0)


def _active_distances(m, cfg):
    """Repliziert die Distanz-Liste aus ``_constraint_distance_penalty`` (aktive Terme > 0)."""
    dists = {
        "trades": _shortfall_distance(float(m.oos_total_trades), cfg.get("oos_min_trades")),
        "return": _shortfall_distance(m.oos_total_return, cfg.get("oos_min_total_return"),
                                      scale=cfg.get("return_penalty_scale")),
        "expectancy": _shortfall_distance(m.oos_expectancy, cfg.get("oos_min_expectancy"),
                                          scale=cfg.get("expectancy_penalty_scale")),
        "win_rate": _shortfall_distance(m.oos_win_rate, cfg.get("oos_min_win_rate")),
        "drawdown": _excess_distance(m.oos_max_drawdown, cfg.get("max_drawdown")),
        "any": _any_condition_distance(m, {}, cfg),
    }
    cap = cfg.get("distance_term_cap")
    if cap:
        dists = {k: min(v, float(cap)) for k, v in dists.items()}
    return {k: v for k, v in dists.items() if v > 0.0}


def test_expectancy_share_drops_from_dominant_to_balanced():
    """Akzeptanzkriterium 1: Expectancy-Anteil am mean_distance von ~80 % auf < 40 %."""
    m = TournamentMetrics(**TRIAL3)

    old = _active_distances(m, OLD_CFG)
    old_share = old["expectancy"] / sum(old.values())
    assert old_share > 0.7, f"Erwartet Dominanz unter alter Kalibrierung, war {old_share:.2%}"

    new = _active_distances(m, NEW_CFG)
    new_share = new["expectancy"] / sum(new.values())
    assert new_share < 0.4, f"Expectancy dominiert weiterhin: {new_share:.2%}"


def test_all_dimensions_on_comparable_scale():
    """Akzeptanzkriterium 2: alle aktiven Distanzen in [0, ~cap], keine > 5× einer anderen."""
    m = TournamentMetrics(**TRIAL3)
    new = _active_distances(m, NEW_CFG)
    assert all(0.0 < d <= NEW_CFG["distance_term_cap"] for d in new.values())
    assert max(new.values()) / min(new.values()) < 5.0


def test_missing_scale_uses_legacy_target_normalization():
    """Akzeptanzkriterium 4 (Zero-Hardcoding): ohne expectancy_penalty_scale ⇒ scale=target."""
    m = TournamentMetrics(**TRIAL3)
    legacy_cfg = dict(OLD_CFG)  # kein expectancy_penalty_scale
    d = _shortfall_distance(m.oos_expectancy, legacy_cfg["oos_min_expectancy"],
                            scale=legacy_cfg.get("expectancy_penalty_scale"))
    expected = (legacy_cfg["oos_min_expectancy"] - m.oos_expectancy) / legacy_cfg["oos_min_expectancy"]
    assert d == pytest.approx(expected)


def test_rank_invariant_failed_below_evaluable_floor():
    """Akzeptanzkriterium 3: jeder Constraint-Failure-Reward strikt < Evaluable-Floor."""
    m = TournamentMetrics(**TRIAL3)
    r = compute_reward(m, universe_size=1, weights=WEIGHTS,
                       risk_dd_cap=NEW_CFG["max_drawdown"], tournament_cfg=NEW_CFG)
    assert r < -float(WEIGHTS["sortino_clip_abs"])


def test_cap_only_lowers_penalty():
    """Der Cap darf die Strafe nur senken (Rang-Invariante bleibt erhalten)."""
    m = TournamentMetrics(**TRIAL3)
    capped = _constraint_distance_penalty(m, WEIGHTS, risk_dd_cap=NEW_CFG["max_drawdown"],
                                          tournament_cfg=NEW_CFG)
    uncapped_cfg = dict(NEW_CFG); uncapped_cfg.pop("distance_term_cap")
    uncapped = _constraint_distance_penalty(m, WEIGHTS, risk_dd_cap=uncapped_cfg["max_drawdown"],
                                            tournament_cfg=uncapped_cfg)
    assert capped <= uncapped + 1e-12
