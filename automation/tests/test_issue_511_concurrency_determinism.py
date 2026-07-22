import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from automation.optimizer.sweep import main

def test_seed_no_longer_forces_n_jobs_to_1_or_raises(tmp_path):
    """Issue #755 (superseded #511): Determinismus wird seither PER STUDY erzwungen
    (``run_optimization.seed_effective``), nicht mehr durch sweep-weite Serialisierung. Ein
    gesetzter Seed darf ``--n-jobs > 1`` weder ablehnen noch stillschweigend auf 1 zwingen — jede
    Study hat eine eigene SQLite-Datei/eigenen Sampler, Studies teilen keinen Sampler-Zustand."""
    base_cfg = tmp_path

    (base_cfg / "strategies.json").write_text(json.dumps({"strategies": [{"strategy_class": "TestStrat", "active": True}]}))
    (base_cfg / "tournament.json").write_text(json.dumps({}))
    (base_cfg / "backtest.json").write_text(json.dumps({
        "walk_forward": {"is_window_days": 100, "oos_window_days": 10, "splits": 2, "holdout_days": 5}
    }))

    # Seed ist in config definiert (wie in optimizer.json) UND --n-jobs > 1 explizit gesetzt.
    (base_cfg / "optimizer.json").write_text(json.dumps({"seed": 42}))
    argv = ["--strategies", "TestStrat", "--symbols", "AAPL", "--n-jobs", "6"]

    with patch("automation.optimizer.sweep.config_dir", return_value=base_cfg), \
         patch("automation.optimizer.sweep.run_per_symbol_sweep") as mock_run:
        main(argv)  # darf NICHT mehr werfen (#755)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["n_jobs"] == 6, "CLI --n-jobs muss trotz gesetztem Seed respektiert werden."
        assert kwargs.get("n_jobs_source") == "CLI"

    # OHNE CLI-Override faellt der Default auf sweep_max_workers/cpu_count-2 zurueck — NICHT mehr
    # zwangsweise auf 1.
    argv_no_override = ["--strategies", "TestStrat", "--symbols", "AAPL"]
    with patch("automation.optimizer.sweep.config_dir", return_value=base_cfg), \
         patch("automation.optimizer.sweep.run_per_symbol_sweep") as mock_run:
        main(argv_no_override)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["n_jobs"] >= 1
        assert kwargs.get("n_jobs_source") in ("CONFIG_SWEEP_MAX_WORKERS", "DEFAULT_CPU_MINUS_2")

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
