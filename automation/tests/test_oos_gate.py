from unittest import mock
import pathlib
import pytest
import logging
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, mock_open
from automation.daily_orchestrator import phase5_live_deployment

def setup_logger():
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.INFO)
    return logger

def test_phase5_oos_eligible():
    log = setup_logger()
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        json.dump({
            "aggregate_winner": {
                "strategy": "MeanReversionStrategy",
                "oos_evaluated": True,
                "oos_eligible": True,
                "oos_metrics": {"total_trades": 10},
                "oos_rejection_reasons": []
            }
        }, tf)
        tournament_path = tf.name

    tournament_result = {"tournament_path": tournament_path}

    with patch('subprocess.Popen') as mock_popen, \
         patch('automation.daily_orchestrator.emit_json_event') as mock_emit, \
         patch('automation.daily_orchestrator.PROJECT_ROOT') as mock_root, \
         patch('automation.daily_orchestrator.LOGS_DIR', new=pathlib.Path(tempfile.mkdtemp())):

        # mock bot_script.exists()
        from pathlib import Path
        mock_script = pathlib.Path("fake_script.py")

        with patch('pathlib.Path.exists', return_value=True):
            res = phase5_live_deployment(log, {}, tournament_result, dry_run=True)

        assert res == 0 # Dry run should return 0
        mock_emit.assert_called_with(log, "BOT_START_INITIATED", mock.ANY)

def test_phase5_oos_not_evaluable():
    log = setup_logger()
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        json.dump({
            "aggregate_winner": {
                "strategy": "MeanReversionStrategy",
                "oos_evaluated": False,
                "oos_eligible": False,
                "oos_metrics": None,
                "oos_rejection_reasons": ["oos_not_evaluable"]
            }
        }, tf)
        tournament_path = tf.name

    tournament_result = {"tournament_path": tournament_path}

    with patch('automation.daily_orchestrator.emit_json_event') as mock_emit:
        res = phase5_live_deployment(log, {}, tournament_result, dry_run=True)
        assert res == 0
        mock_emit.assert_called_with(log, "OOS_GATE_NOT_EVALUABLE", {
            "strategy": "MeanReversionStrategy",
            "oos_metrics": None,
            "reasons": ["oos_not_evaluable"]
        })

def test_phase5_oos_failed():
    log = setup_logger()
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        json.dump({
            "aggregate_winner": {
                "strategy": "MeanReversionStrategy",
                "oos_evaluated": True,
                "oos_eligible": False,
                "oos_metrics": {"total_trades": 2},
                "oos_rejection_reasons": ["oos_min_trades: 2 < 5"]
            }
        }, tf)
        tournament_path = tf.name

    tournament_result = {"tournament_path": tournament_path}

    with patch('automation.daily_orchestrator.emit_json_event') as mock_emit:
        res = phase5_live_deployment(log, {}, tournament_result, dry_run=True)
        assert res == 0
        mock_emit.assert_called_with(log, "OOS_GATE_FAILED", {
            "strategy": "MeanReversionStrategy",
            "reasons": ["oos_min_trades: 2 < 5"],
            "oos_metrics": {"total_trades": 2}
        })

from automation.backtest_runner import select_winners

def test_select_winners_issue_192_regression():
    """
    Regression Test for Issue #192: Critical Error in Tournament Gating.
    Ensure that pairs meeting both IS and OOS thresholds are correctly evaluated and eligible,
    while those failing OOS thresholds are rejected.
    """
    tournament_cfg = {
        "min_trades": 20,
        "min_sortino": 0.3,
        "min_profit_factor": 1.1,
        "oos_min_trades": 20,
        "oos_min_sortino": 0.3,
        "oos_min_profit_factor": 1.1,
        "eligible_requires_all": ["min_trades", "min_sortino", "min_profit_factor"],
        "scoring": {
            "sortino_weight": 0.5,
            "profit_factor_weight": 0.5
        }
    }

    pairs = [
        {"symbol": "CHTR.ETORO", "strategy": "FlashCrashReversalStrategy"},
        {"symbol": "CHTR.ETORO", "strategy": "MeanReversionStrategy"},
        {"symbol": "FDX.ETORO", "strategy": "MeanReversionStrategy"},
        {"symbol": "FDX.ETORO", "strategy": "FlashCrashReversalStrategy"},
        {"symbol": "HAL.ETORO", "strategy": "MeanReversionStrategy"},
        {"symbol": "HAL.ETORO", "strategy": "HourlyMeanReversionStrategy"},
        {"symbol": "CAT.ETORO", "strategy": "FlashCrashReversalStrategy"}
    ]

    all_results = []
    # Add valid pairs
    for p in pairs:
        all_results.append({
            "symbol": p["symbol"],
            "strategy": p["strategy"],
            "metrics": {
                "total_trades": 30,
                "sortino_ratio": 0.5,
                "profit_factor": 1.5,
                "max_drawdown": 0.1,
                "win_rate": 0.6,
                "total_return": 0.1
            },
            "oos_metrics": {
                "total_trades": 25,
                "sortino_ratio": 0.4,
                "profit_factor": 1.3,
                "max_drawdown": 0.1,
                "win_rate": 0.5,
                "total_return": 0.05
            }
        })

    # Add a negative fixture that fails OOS min_trades
    all_results.append({
        "symbol": "AAPL.ETORO",
        "strategy": "MeanReversionStrategy",
        "metrics": {
            "total_trades": 30,
            "sortino_ratio": 0.5,
            "profit_factor": 1.5,
            "max_drawdown": 0.1,
            "win_rate": 0.6,
            "total_return": 0.1
        },
        "oos_metrics": {
            "total_trades": 10,  # Fails min_trades (20)
            "sortino_ratio": 0.4,
            "profit_factor": 1.3,
            "max_drawdown": 0.1,
            "win_rate": 0.5,
            "total_return": 0.05
        }
    })

    # Add a negative fixture that fails OOS min_sortino
    all_results.append({
        "symbol": "TSLA.ETORO",
        "strategy": "MeanReversionStrategy",
        "metrics": {
            "total_trades": 30,
            "sortino_ratio": 0.5,
            "profit_factor": 1.5,
            "max_drawdown": 0.1,
            "win_rate": 0.6,
            "total_return": 0.1
        },
        "oos_metrics": {
            "total_trades": 25,
            "sortino_ratio": 0.1,  # Fails min_sortino (0.3)
            "profit_factor": 1.3,
            "max_drawdown": 0.1,
            "win_rate": 0.5,
            "total_return": 0.05
        }
    })

    per_symbol_winners, aggregate_winner, warnings = select_winners(all_results, tournament_cfg)

    # We expect the valid pairs to pass. We check the number of eligible pairs.
    # Note: select_winners performs cross-sectional scoring and only returns the top winner per symbol
    # and the aggregate winner. However, we can inspect per_symbol_winners.
    # Since there are 4 unique symbols (CHTR, FDX, HAL, CAT) that have valid pairs, there should be 4 winners.
    assert len(per_symbol_winners) == 4

    # Check that AAPL and TSLA are not in winners
    assert "AAPL.ETORO" not in per_symbol_winners
    assert "TSLA.ETORO" not in per_symbol_winners

    # Check that aggregate winner is one of the valid ones
    assert aggregate_winner is not None
    assert aggregate_winner["strategy"] in ["FlashCrashReversalStrategy", "MeanReversionStrategy", "HourlyMeanReversionStrategy"]
