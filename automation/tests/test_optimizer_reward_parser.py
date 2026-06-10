import json, statistics
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
