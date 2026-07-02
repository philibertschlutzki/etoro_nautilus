from automation.backtest_runner import load_tournament_config, _is_eligible, _evaluate_oos_eligibility
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
        "eligible_requires_all": ["min_trades", "min_total_return", "max_drawdown"],
        "eligible_requires_any": ["min_sortino", "min_profit_factor", "min_win_rate"],
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
            "profit_factor": 1.5,
            "median_position_notional": 50.0,  # above $10 floor (Issue #254)
        }
    }

    assert not _evaluate_oos_eligibility(metrics["oos_metrics"], tournament_cfg).get("oos_eligible"), "Should fail with 2 OOS trades"

    metrics["oos_metrics"]["total_trades"] = 10
    assert _evaluate_oos_eligibility(metrics["oos_metrics"], tournament_cfg).get("oos_eligible"), "Should pass with 10 OOS trades"

def test_total_return_eligibility_hard_gate():
    tournament_cfg = {
        "min_trades": 20,
        "min_sortino": 0.3,
        "min_profit_factor": 1.1,
        "max_drawdown": 0.30,
        "min_win_rate": 0.35,
        "min_total_return": 0.005,
        "min_expectancy": 0.0005,
        "eligible_requires_all": ["min_trades", "min_total_return", "max_drawdown", "min_expectancy"],
        "eligible_requires_any": ["min_sortino", "min_profit_factor", "min_win_rate"]
    }

    metrics = {
        "total_trades": 50,
        "win_rate": 0.60,
        "max_drawdown": 0.10,
        "sortino_ratio": 5.0,  # Extremely high
        "profit_factor": 3.0,  # Extremely high
        "total_return": 0.001,  # < 0.005 (Fails hard gate!)
        "median_position_notional": 50.0,  # above $10 floor (Issue #254)
    }

    assert not _is_eligible({'metrics': metrics}, tournament_cfg), "Should reject due to total_return < 0.005 despite high sortino/pf"

    metrics["total_return"] = 0.026  # Pass
    metrics["expectancy"] = 0.0006  # Pass
    assert _is_eligible({'metrics': metrics}, tournament_cfg), "Should pass with total_return > 0.005 and expectancy > 0.0005"

def test_rejection_reasons_in_is_eligible():
    tournament_cfg = {}
    metrics = {
        "sortino_ratio": None,
        "profit_factor": None,
        "total_trades": 0,
        "losses_count": 0,
        "win_rate": 0.0
    }

    _is_eligible({'metrics': metrics}, tournament_cfg, log_rejections=True)
    assert metrics.get("rejection_reason") == "no trades executed"

    metrics["total_trades"] = 10
    metrics["win_rate"] = 0.0
    metrics["losses_count"] = 10
    _is_eligible({'metrics': metrics}, tournament_cfg, log_rejections=True)
    assert metrics.get("rejection_reason") == "all-loss"

    metrics["total_trades"] = 10
    metrics["win_rate"] = 1.0
    metrics["losses_count"] = 0
    _is_eligible({'metrics': metrics}, tournament_cfg, log_rejections=True)
    assert metrics.get("rejection_reason") == "all-win (no losses)"

    metrics["total_trades"] = 4
    metrics["win_rate"] = 0.5
    metrics["losses_count"] = 2
    _is_eligible({'metrics': metrics}, tournament_cfg, log_rejections=True)
    assert metrics.get("rejection_reason") == "insufficient sample (n=4)"

    metrics["total_trades"] = 10
    metrics["win_rate"] = 0.9
    metrics["losses_count"] = 1
    _is_eligible({'metrics': metrics}, tournament_cfg, log_rejections=True)
    assert metrics.get("rejection_reason") == "insufficient loss data (losses=1 for n=10)"

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

def test_zero_trades_no_micro_sizing_rejection(capfd):
    """
    Test that when total_trades == 0, the Micro-Sizing rejection reason
    is not appended to the rejection list, avoiding the 0.0 value artifact.
    """
    tournament_cfg = {
        "min_trades": 10,
        "eligible_requires_all": ["min_trades"],
    }

    metrics = {
        "total_trades": 0,
        "win_rate": 0.0,
        "max_drawdown": 0.0,
        "sortino_ratio": 0.0,
        "profit_factor": 0.0,
        "total_return": 0.0,
        "median_position_notional": 0.0,
    }

    # Wir setzen sortino_ratio und profit_factor explizit auf 0.0 statt None,
    # um das early return bei None zu umgehen und sicherzustellen, dass die
    # Rejection-Logik am Ende der Funktion erreicht wird.
    result = {'metrics': metrics}

    is_eligible = _is_eligible(result, tournament_cfg, log_rejections=True)

    assert not is_eligible, "Should be rejected because min_trades (0 < 10) failed"

    # Check the standard output
    out, _ = capfd.readouterr()

    # We expect min_trades failed, but NOT Micro-Sizing
    assert "no trades executed" in out, "Should reject explicitly because no trades executed"
    assert "Micro-Sizing" not in out, "Micro-Sizing rejection should NOT be present when total_trades == 0"

    # Test Out-of-Sample path
    oos_metrics = {
        "total_trades": 0,
        "win_rate": 0.0,
        "max_drawdown": 0.0,
        "sortino_ratio": 0.0,
        "profit_factor": 0.0,
        "total_return": 0.0,
        "median_position_notional": 0.0,
    }
    # Note: _evaluate_oos_eligibility has an early exit for total_trades <= 0.
    oos_result = _evaluate_oos_eligibility(oos_metrics, tournament_cfg)
    assert not oos_result["oos_eligible"]
    # The reasons list should NOT have Micro-Sizing
    reasons = " ".join(oos_result["oos_rejection_reasons"])
    assert "Micro-Sizing" not in reasons, "Micro-Sizing rejection should NOT be present in OOS when total_trades == 0"
