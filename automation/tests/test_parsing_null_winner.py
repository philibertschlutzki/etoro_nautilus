import json
from pathlib import Path
import pytest
from automation.optimizer.parsing import parse_tournament, TournamentMetrics

def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p

def test_null_aggregate_winner_returns_unevaluable(tmp_path):
    p = _write(tmp_path, {"fully_eligible_pairs": 0, "aggregate_winner": None})
    m = parse_tournament(p)
    assert isinstance(m, TournamentMetrics)
    assert m.oos_evaluated is False
    assert m.oos_eligible is False
    assert m.oos_sortino is None
    assert m.oos_total_trades == 0
    assert m.win_count == 0
    assert m.fully_eligible_pairs == 0

def test_missing_aggregate_winner_key(tmp_path):
    m = parse_tournament(_write(tmp_path, {"fully_eligible_pairs": 3}))
    assert m.oos_evaluated is False
    assert m.fully_eligible_pairs == 3

def test_null_oos_metrics_inside_winner(tmp_path):
    p = _write(tmp_path, {"aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True,
        "oos_metrics": None, "median_is_sortino": 1.2}})
    m = parse_tournament(p)
    assert m.oos_max_drawdown == 0.0
    assert m.oos_sortino is None
    assert m.is_sortino_median == pytest.approx(1.2)

def test_fold_sortinos_take_median(tmp_path):
    p = _write(tmp_path, {"aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True,
        "oos_fold_sortinos": [0.5, 1.5, 1.0],
        "oos_metrics": {"max_drawdown": 0.1, "total_trades": 30}}})
    m = parse_tournament(p)
    assert m.oos_sortino == pytest.approx(1.0)
    assert m.oos_total_trades == 30

def test_null_fully_eligible_pairs(tmp_path):
    m = parse_tournament(_write(tmp_path, {"fully_eligible_pairs": None, "aggregate_winner": None}))
    assert m.fully_eligible_pairs == 0

def test_happy_path_regression(tmp_path):
    p = _write(tmp_path, {"fully_eligible_pairs": 5, "aggregate_winner": {
        "oos_evaluated": True, "oos_eligible": True, "win_count": 4,
        "median_is_sortino": 0.9,
        "oos_metrics": {"sortino_ratio": 0.7, "max_drawdown": 0.18, "total_trades": 41}}})
    m = parse_tournament(p)
    assert m.oos_evaluated and m.oos_eligible
    assert m.oos_sortino == pytest.approx(0.7)
    assert m.win_count == 4 and m.fully_eligible_pairs == 5
