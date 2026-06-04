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
