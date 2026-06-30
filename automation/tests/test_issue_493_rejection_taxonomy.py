import pytest
import tempfile
import json
from pathlib import Path
from automation.optimizer.parsing import parse_tournament
from automation.optimizer.run_optimization import _classify_is_rejection_detail

def test_classify_is_rejection_detail_discarded_by_is_gate():
    # Setup mock data simulating an IS rejection with OOS trades
    manipulated_data = {
        "universe_size": 1,
        "full_results": [
            {
                "symbol": "BTCUSD",
                "strategy": "StratA",
                "is_eligible": False, # IS gate rejects
                "oos_metrics": {"total_trades": 10} # trades happen in OOS
            }
        ],
        "aggregate_winner": None,
        "single_symbol_oos": {
            "oos_evaluated": False,
            "oos_eligible": False,
            "oos_metrics": {"total_trades": 10},
            "oos_rejection_reasons": []
        },
        "data_window": {
            "oos_covered": True,
            "fill_ts_max": 200,
            "oos_window_start_ns": 100
        }
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "tournament_result.json"
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(manipulated_data, f)

        metrics = parse_tournament(out_path)

        # Test the classification
        result = _classify_is_rejection_detail(metrics)

        # Assertions
        assert getattr(metrics, "oos_total_trades", 0) > 0
        assert getattr(metrics, "oos_covered", None) is True
        assert getattr(metrics, "oos_evaluated", None) is False
        assert result == "REJECT_OOS_DISCARDED_BY_IS_GATE"
        assert result != "REJECT_OOS_INACTIVE"

def test_classify_is_rejection_detail_inactive():
    # Setup mock data simulating an IS rejection with no OOS trades
    manipulated_data = {
        "universe_size": 1,
        "full_results": [
            {
                "symbol": "BTCUSD",
                "strategy": "StratA",
                "is_eligible": False, # IS gate rejects
                "oos_metrics": {"total_trades": 0} # NO trades in OOS
            }
        ],
        "aggregate_winner": None,
        "single_symbol_oos": {
            "oos_evaluated": False,
            "oos_eligible": False,
            "oos_metrics": {"total_trades": 0},
            "oos_rejection_reasons": []
        },
        "data_window": {
            "oos_covered": True,
            "fill_ts_max": 200,
            "oos_window_start_ns": 100
        }
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "tournament_result.json"
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(manipulated_data, f)

        metrics = parse_tournament(out_path)

        # Test the classification
        result = _classify_is_rejection_detail(metrics)

        # Assertions
        assert getattr(metrics, "oos_total_trades", 0) == 0
        assert getattr(metrics, "oos_covered", None) is True
        assert getattr(metrics, "oos_evaluated", None) is False
        assert result == "REJECT_OOS_INACTIVE"
