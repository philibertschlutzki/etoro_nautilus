import pytest
import json
from unittest.mock import patch, MagicMock
import os
import io

from automation.backtest_runner import run_backtest

@patch("automation.backtest_runner.argparse.ArgumentParser.parse_args")
@patch("automation.backtest_runner.os.path.exists")
@patch("automation.backtest_runner.open")
def test_tournament_observability_logs_all_keys(mock_open, mock_exists, mock_parse_args, capsys):
    mock_parse_args.return_value = MagicMock(dry_run=True, config=None, catalog_path=None, output=None, strategy=None, single_symbol=None, momentum=False, htmlreport=False)

    mock_exists.return_value = True

    mock_tournament_json = json.dumps({
        "min_trades": 25,
        "min_sortino": 0.5,
        "min_profit_factor": 1.2,
        "max_drawdown": 0.5,
        "min_win_rate": 0.4,
        "min_total_return": 0.01,
        "min_expectancy": 0.0001,
        "eligible_requires_all": ["min_trades", "max_drawdown", "min_expectancy"],
        "eligible_requires_any": ["min_sortino", "min_profit_factor", "min_win_rate"],
        "oos_min_trades": 10,
        "oos_min_total_return": 0.005,
        "oos_min_expectancy": 0.00005,
        "scoring": {}
    })

    mock_backtest_json = json.dumps({"spread_modeling": True})
    mock_strategies_json = json.dumps({"global_settings": {}, "strategies": []})

    def mock_open_side_effect(path, *args, **kwargs):
        if "tournament.json" in path:
            return io.StringIO(mock_tournament_json)
        if "backtest.json" in path:
            return io.StringIO(mock_backtest_json)
        if "backtesting_config.json" in path:
            return io.StringIO(mock_strategies_json)
        return io.StringIO("{}")

    mock_open.side_effect = mock_open_side_effect

    # Run in dry run mode to just test the output header
    run_backtest()

    captured = capsys.readouterr()
    output = captured.out

    # Assert that all keys required are present in the output
    assert "🏆 Tournament Configuration:" in output
    assert "min_trades: 25" in output
    assert "min_win_rate: 0.4" in output
    assert "max_drawdown: 0.5" in output
    assert "min_expectancy: 0.0001" in output
    assert "min_sortino: 0.5" in output
    assert "min_profit_factor: 1.2" in output

    # Verify OOS Defaults are present
    assert "[OOS DEFAULTS]" in output
    assert "min_trades: 10" in output
    assert "min_return: 0.005" in output
    assert "min_expectancy: 5e-05" in output or "min_expectancy: 0.00005" in output
