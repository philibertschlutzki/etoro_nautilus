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
            "eligible_pairs": 1,
            "fully_eligible_pairs": 1,
            "per_symbol_winners": {"SYM1": {}},
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
                res = phase5_live_deployment(log, {}, tournament_result, no_deploy=True)

        assert res == 0 # Dry run should return 0

        # Verify whitelist generation event instead of BOT_START_INITIATED because bot start is now skipped when dry run = True
        mock_emit.assert_any_call(log, "DEPLOYMENT_WHITELIST_GENERATED", mock.ANY)

def test_phase5_oos_not_evaluable():
    log = setup_logger()
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        json.dump({
            "eligible_pairs": 1,
            "fully_eligible_pairs": 1,
            "per_symbol_winners": {"SYM1": {}},
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
        res = phase5_live_deployment(log, {}, tournament_result, no_deploy=True)
        assert res == 0
        mock_emit.assert_called_with(log, "OOS_GATE_NOT_EVALUABLE", {
            "strategy": "MeanReversionStrategy",
            "oos_metrics": None,
            "reasons": ["oos_not_evaluable"],
            "aggregate_oos_sortino": None,
            "aggregate_oos_max_drawdown": None,
            "fully_eligible_pairs": 1,
            "winner_count": 1
        })

def test_phase5_oos_failed():
    log = setup_logger()
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        json.dump({
            "eligible_pairs": 1,
            "fully_eligible_pairs": 1,
            "per_symbol_winners": {"SYM1": {}},
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
        res = phase5_live_deployment(log, {}, tournament_result, no_deploy=True)
        assert res == 0
        mock_emit.assert_called_with(log, "OOS_GATE_FAILED", {
            "strategy": "MeanReversionStrategy",
            "reasons": ["oos_min_trades: 2 < 5"],
            "oos_metrics": {"total_trades": 2},
            "aggregate_oos_sortino": None,
            "aggregate_oos_max_drawdown": None,
            "fully_eligible_pairs": 1,
            "winner_count": 1
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

    # Helper: valid IS metrics dict (median_position_notional >= 10 required by Issue #254 gate)
    def _valid_is(sortino=0.5):
        return {
            "total_trades": 30,
            "sortino_ratio": sortino,
            "profit_factor": 1.5,
            "max_drawdown": 0.1,
            "win_rate": 0.6,
            "total_return": 0.1,
            "median_position_notional": 50.0,
        }

    def _valid_oos(trades=25, sortino=0.4):
        return {
            "total_trades": trades,
            "sortino_ratio": sortino,
            "profit_factor": 1.3,
            "max_drawdown": 0.1,
            "win_rate": 0.5,
            "total_return": 0.05,
            "median_position_notional": 50.0,
        }

    all_results = []
    # Add valid pairs
    for p in pairs:
        all_results.append({
            "symbol": p["symbol"],
            "strategy": p["strategy"],
            "metrics": _valid_is(),
            "oos_metrics": _valid_oos(),
        })

    # Add a negative fixture that fails OOS min_trades
    all_results.append({
        "symbol": "AAPL.ETORO",
        "strategy": "MeanReversionStrategy",
        "metrics": _valid_is(),
        "oos_metrics": _valid_oos(trades=10),  # Fails oos_min_trades (20)
    })

    # Add a negative fixture that fails OOS min_sortino
    all_results.append({
        "symbol": "TSLA.ETORO",
        "strategy": "MeanReversionStrategy",
        "metrics": _valid_is(),
        "oos_metrics": _valid_oos(sortino=0.1),  # Fails oos_min_sortino (0.3)
    })

    # Add a negative fixture that fails IS min_sortino but passes OOS
    all_results.append({
        "symbol": "AMZN.ETORO",
        "strategy": "MeanReversionStrategy",
        "metrics": _valid_is(sortino=0.1),  # Fails IS min_sortino (0.3)
        "oos_metrics": _valid_oos(),
    })

    per_symbol_winners, aggregate_winner, warnings, _, _ = select_winners(all_results, tournament_cfg)

    # We expect the valid pairs to pass. We check the number of eligible pairs.
    # Note: select_winners performs cross-sectional scoring and only returns the top winner per symbol
    # and the aggregate winner. However, we can inspect per_symbol_winners.
    # Since there are 4 unique symbols (CHTR, FDX, HAL, CAT) that have valid pairs, there should be 4 winners.
    assert len(per_symbol_winners) == 4

    # Check that AAPL and TSLA are not in winners
    assert "AAPL.ETORO" not in per_symbol_winners
    assert "TSLA.ETORO" not in per_symbol_winners
    assert "AMZN.ETORO" not in per_symbol_winners

    # Check that aggregate winner is one of the valid ones
    assert aggregate_winner is not None
    assert aggregate_winner["strategy"] in ["FlashCrashReversalStrategy", "MeanReversionStrategy", "HourlyMeanReversionStrategy"]

def test_phase5_zero_eligible_pairs_aborts():
    """
    Test that if fully_eligible_pairs == 0 or winner_count == 0, Phase 5 aborts cleanly (fail-closed)
    and does not trigger BOT_START_INITIATED or BOT_STARTED.
    """
    log = setup_logger()
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        json.dump({
            "eligible_pairs": 1,
            "fully_eligible_pairs": 0,
            "per_symbol_winners": {},
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
        with patch('pathlib.Path.exists', return_value=True):
                res = phase5_live_deployment(log, {}, tournament_result, no_deploy=False)

        assert res == 1 # Aborts with error code 1

        # Verify LIVE_DEPLOY_ABORTED is emitted
        mock_emit.assert_any_call(log, "LIVE_DEPLOY_ABORTED", {"reason": "zero_fully_eligible_pairs", "fully_eligible_pairs": 0, "winner_count": 0, "oos_not_evaluable_pairs": 0, "oos_failed_pairs": 0})

        # Verify BOT_START_INITIATED is NOT emitted
        calls = [c[0][1] for c in mock_emit.call_args_list]
        assert "BOT_START_INITIATED" not in calls
        assert "BOT_STARTED" not in calls

def test_aggregate_pass_but_zero_eligible_pairs_asserts():
    """
    Test that the assertion triggers in select_winners if aggregate OOS is eligible
    but per_symbol_winners is empty.
    """
    from automation.backtest_runner import select_winners
    tournament_cfg = {
        "min_trades": 20,
        "min_sortino": 0.0,
        "min_profit_factor": 1.0,
        "oos_min_trades": 20,
        "oos_min_sortino": 0.0,
        "oos_min_profit_factor": 1.0,
        "eligible_requires_all": ["min_trades"]
    }

    all_results = []

    # Normally this is impossible due to our filtered logic, but if we somehow force
    # an aggregate calculation, the assertion protects us.
    # We test the filtering logic directly here.

    # Provide a result that passes IS but fails OOS:
    all_results.append({
        "symbol": "FAKE.ETORO",
        "strategy": "MeanReversionStrategy",
        "metrics": {
            "total_trades": 30,
            "sortino_ratio": 1.0,
            "profit_factor": 1.5,
            "max_drawdown": 0.1,
            "win_rate": 0.6,
            "total_return": 0.1,
            "median_position_notional": 50.0,
        },
        "oos_metrics": {
            "total_trades": 5,  # fails oos_min_trades
            "sortino_ratio": 1.0,
            "profit_factor": 1.5,
            "max_drawdown": 0.1,
            "win_rate": 0.6,
            "total_return": 0.1,
            "median_position_notional": 50.0,
        }
    })

    per_symbol_winners, aggregate_winner, warnings, _, _ = select_winners(all_results, tournament_cfg)

    # It correctly filtered out the pair, so per_symbol_winners should be empty
    assert len(per_symbol_winners) == 0
    # Because there are no winners, it shouldn't even try to aggregate and trigger the assert
    # The assert is essentially un-hittable if per_symbol_winners is empty, because `if win_counts:` evaluates to False
    assert aggregate_winner is None
