import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from automation.optimizer.sweep import main

def test_determinism_guard_forces_n_jobs_to_1(tmp_path):
    """Prüft Issue #511: Sweep erzwingt n_jobs=1 bei aktivem Seed zur Determinismus-Garantie."""
    base_cfg = tmp_path

    # Setup Mock-Configs
    (base_cfg / "strategies.json").write_text(json.dumps({"strategies": [{"strategy_class": "TestStrat", "active": True}]}))
    (base_cfg / "tournament.json").write_text(json.dumps({}))
    (base_cfg / "backtest.json").write_text(json.dumps({
        "walk_forward": {"is_window_days": 100, "oos_window_days": 10, "splits": 2, "holdout_days": 5}
    }))

    # 1. Fall: Seed ist in config definiert (wie in optimizer.json)
    (base_cfg / "optimizer.json").write_text(json.dumps({"seed": 42}))

    # CLI args
    argv = ["--strategies", "TestStrat", "--symbols", "AAPL", "--n-jobs", "6"]

    # Test execution
    with patch("automation.optimizer.sweep.config_dir", return_value=base_cfg), \
         patch("automation.optimizer.sweep.run_per_symbol_sweep") as mock_run:

        main(argv)

        # Verify that n_jobs was forced to 1 despite the CLI asking for 6
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["n_jobs"] == 1, "Determinism guard failed: n_jobs not forced to 1 when seed=42."
        assert kwargs.get("n_jobs_source") == "ENFORCED_BY_SEED"

def test_determinism_guard_respects_n_jobs_without_seed(tmp_path):
    """Prüft Issue #511: Ohne Seed darf n_jobs > 1 von der CLI bestehen bleiben."""
    base_cfg = tmp_path

    # Setup Mock-Configs (Kein Seed definiert)
    (base_cfg / "strategies.json").write_text(json.dumps({"strategies": [{"strategy_class": "TestStrat", "active": True}]}))
    (base_cfg / "tournament.json").write_text(json.dumps({}))
    (base_cfg / "backtest.json").write_text(json.dumps({
        "walk_forward": {"is_window_days": 100, "oos_window_days": 10, "splits": 2, "holdout_days": 5}
    }))
    (base_cfg / "optimizer.json").write_text(json.dumps({})) # No seed

    # CLI args
    argv = ["--strategies", "TestStrat", "--symbols", "AAPL", "--n-jobs", "4"]

    with patch("automation.optimizer.sweep.config_dir", return_value=base_cfg), \
         patch("automation.optimizer.sweep.run_per_symbol_sweep") as mock_run:

        main(argv)

        # Verify that n_jobs remains 4
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["n_jobs"] == 4, "CLI n_jobs should be respected if no seed is set."
        assert kwargs.get("n_jobs_source") == "CLI"
