import json, statistics
import pytest
from pathlib import Path
from automation.optimizer import parsing, reward

def _write_tournament(tmp_path, **agg):
    data = {"fully_eligible_pairs": 1, "aggregate_winner": agg}
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(data), "utf-8")
    return p

def test_parser_median_from_fold_sortinos(tmp_path):
    p = _write_tournament(tmp_path, oos_evaluated=True, oos_eligible=True, win_count=3,
                          median_is_sortino=2.0, oos_fold_sortinos=[1.0, 3.0, 2.0],
                          oos_metrics={"sortino_ratio": 9.9, "max_drawdown": 0.1})
    m = parsing.parse_tournament(p)
    assert m.oos_sortino == statistics.median([1.0, 3.0, 2.0])   # 2.0, nicht 9.9

def test_reward_uses_config_weights(tmp_path):
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    cap = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))["max_drawdown"]

    p = _write_tournament(tmp_path, oos_evaluated=True, oos_eligible=True, win_count=5,
                          median_is_sortino=3.0, oos_fold_sortinos=[1.0],
                          oos_metrics={"sortino_ratio": 1.0, "max_drawdown": cap + 0.1})
    m = parsing.parse_tournament(p)

    base = max(-cfg["sortino_clip_abs"], min(cfg["sortino_clip_abs"], 1.0))
    expected = (base
                - max(0.0, 3.0 - base) * cfg["penalty_overfit_weight"]
                - 0.1 * cfg["penalty_dd_weight"]
                + (5 / 100) * cfg["bonus_coverage_weight"])

    assert reward.compute_reward(m, universe_size=100) == \
        __import__("pytest").approx(expected, rel=1e-9)

def test_reward_unevaluable_penalty(tmp_path):
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    p = _write_tournament(tmp_path, oos_evaluated=False, win_count=0)
    m = parsing.parse_tournament(p)

    assert reward.compute_reward(m, universe_size=100) == cfg["penalty_unevaluable_oos"]


# --- ISSUE-OPT-375: IS-activity gradient for non-evaluable trials ----------
def _write_full(dir_path, agg, full_results):
    """Tournament JSON including per-pair full_results (carries IS activity)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    data = {"fully_eligible_pairs": 0, "aggregate_winner": agg, "full_results": full_results}
    p = dir_path / "tournament_result.json"
    p.write_text(json.dumps(data), "utf-8")
    return p


def _pairs(*trade_counts):
    # Issue #488 - Activity gradient requires some IS performance (proximity > 0)
    return [{"metrics": {"total_trades": n, "total_return": 0.5, "win_rate": 0.5}} for n in trade_counts]




def test_evaluable_strictly_above_every_unevaluable(tmp_path):
    """The worst-case evaluable trial (clamped to the floor) still strictly beats the
    best-shaped unevaluable trial — the hard reward-ordering invariant."""
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    cap = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))["max_drawdown"]

    # Best-shaped unevaluable: IS activity saturated far past the target.
    saturated = cfg["shaping_trade_target"] * 1000
    m_uneval = parsing.parse_tournament(_write_full(
        tmp_path / "uneval", dict(oos_evaluated=False, win_count=0), _pairs(saturated)))
    r_uneval = reward.compute_reward(m_uneval, universe_size=100)

    # Worst-case evaluable: clipped-negative OOS sortino, high IS sortino, DD over the cap.
    m_eval = parsing.parse_tournament(_write_full(
        tmp_path / "eval",
        dict(oos_evaluated=True, oos_eligible=True, win_count=0,
             median_is_sortino=cfg["sortino_clip_abs"],
             oos_fold_sortinos=[-cfg["sortino_clip_abs"]],
             oos_metrics={"sortino_ratio": -cfg["sortino_clip_abs"],
                          "max_drawdown": cap + 0.99, "total_trades": 20}),
        _pairs(20)))
    r_eval = reward.compute_reward(m_eval, universe_size=100)

    floor = (cfg["penalty_unevaluable_oos"] + cfg["unevaluable_shaping_span"]
             + cfg["evaluable_floor_epsilon"])
    assert r_eval == pytest.approx(floor)   # worst evaluable is clamped to the floor
    assert r_eval > r_uneval                # strictly above every unevaluable trial
