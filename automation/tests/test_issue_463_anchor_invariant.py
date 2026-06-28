import json
import logging
from pathlib import Path
from automation.optimizer.parsing import parse_tournament
from automation.optimizer.trial_config import compute_walk_forward_window
import pytest

def test_oos_anchor_divergence_invariant(tmp_path, caplog):
    """
    Testet die Issue #463 Invariante:
    Wenn oos_covered=True und fill_ts_max in der OOS-Union liegt, aber oos_total_trades == 0,
    muss oos_anchor_divergence = True sein und ein Warning geloggt werden.
    """
    test_json = tmp_path / "tournament.json"

    # Simulate data where fill_ts_max is well into the OOS window but 0 OOS trades
    data = {
        "aggregate_winner": {
            "oos_metrics": {
                "total_trades": 0,
                "sortino_ratio": 0.0
            }
        },
        "data_window": {
            "fill_ts_max": 1700000000000000000,
            "oos_window_start_ns": 1600000000000000000,
            "oos_covered": True
        }
    }

    with open(test_json, "w") as f:
        json.dump(data, f)

    with caplog.at_level(logging.WARNING):
        metrics = parse_tournament(test_json)

    assert metrics.oos_anchor_divergence is True
    assert "OOS Anchor Divergence detected" in caplog.text

def test_oos_anchor_divergence_normal(tmp_path):
    """
    Normalfall: oos_total_trades >= 1
    """
    test_json = tmp_path / "tournament.json"

    # Simulate data where fill_ts_max is well into the OOS window and > 0 OOS trades
    data = {
        "aggregate_winner": {
            "oos_metrics": {
                "total_trades": 5,
                "sortino_ratio": 1.5
            }
        },
        "data_window": {
            "fill_ts_max": 1700000000000000000,
            "oos_window_start_ns": 1600000000000000000,
            "oos_covered": True
        }
    }

    with open(test_json, "w") as f:
        json.dump(data, f)

    metrics = parse_tournament(test_json)

    assert metrics.oos_anchor_divergence is False

def test_grep_gate_no_first_tick_ns_in_oos_boundary():
    """
    Grep-Gate: Ensure `_first_tick_ns` is not used for OOS boundary assignment (select_winners)
    in automation/backtest_runner.py anymore.
    """
    import re
    runner_path = Path("automation/backtest_runner.py")
    with open(runner_path, "r") as f:
        content = f.read()

    # Ensure `_first_tick_ns` is NOT used anywhere to define `start_ns`
    matches = re.findall(r'start_ns\s*=\s*.*_first_tick_ns', content)
    assert len(matches) == 0, f"Found occurrences of dynamic re-anchoring to _first_tick_ns! Matches: {matches}"
