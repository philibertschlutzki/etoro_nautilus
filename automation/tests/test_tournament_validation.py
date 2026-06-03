from automation.backtest_runner import load_tournament_config, _is_eligible
import json
import tempfile
import os
import io
import sys

def test_oos_eligibility():
    tournament_cfg = {
        "min_trades": 20,
        "min_sortino": 0.3,
        "min_profit_factor": 1.1,
        "max_drawdown": 0.30,
        "min_win_rate": 0.35,
        "min_total_return": 0.0,
        "oos_min_trades": 5,
        "oos_min_total_return": 0.0,
        "eligible_requires_all": ["min_trades", "min_total_return", "min_win_rate", "max_drawdown"],
        "eligible_requires_any": ["min_sortino", "min_profit_factor"],
        "scoring": {
            "sortino_weight": 0.4,
            "profit_factor_weight": 0.3,
            "win_rate_weight": 0.2,
            "drawdown_penalty_weight": 0.1
        }
    }

    metrics = {
        "oos_metrics": {
            "total_trades": 2, # Less than 5! Should fail!
            "total_return": 0.1,
            "win_rate": 0.5,
            "max_drawdown": 0.1,
            "sortino_ratio": 1.0,
            "profit_factor": 1.5
        }
    }

    assert not _is_eligible(metrics, tournament_cfg, check_oos=True), "Should fail with 2 OOS trades"

    metrics["oos_metrics"]["total_trades"] = 10
    assert _is_eligible(metrics, tournament_cfg, check_oos=True), "Should pass with 10 OOS trades"

def test_load_tournament_config_validation(monkeypatch):
    tournament_cfg = {
        "oos_min_trades": 5,
        "eligible_requires_all": ["min_trades"],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "automation", "config"))
        cfg_path = os.path.join(tmpdir, "automation", "config", "tournament.json")
        with open(cfg_path, "w") as f:
            json.dump(tournament_cfg, f)

        monkeypatch.setattr("automation.backtest_runner._get_project_root", lambda: tmpdir)

        captured_output = io.StringIO()
        sys.stdout = captured_output
        load_tournament_config(tmpdir)
        sys.stdout = sys.__stdout__

        output = captured_output.getvalue()
        assert "ist nicht definiert" not in output
        assert "ist definiert, aber nicht in" not in output
