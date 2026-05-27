HEAD Commit: 1bcf781

- Welche Funktionen existieren in `backtesting/run_backtest.py`?
  - `discover_instruments_from_catalog`
  - `ts_to_ns`
  - `_cleanup_worker_logs`
  - `_flush_worker_log`
  - `_run_remaining_sequentially`
  - `run_backtest`
  - (diverse importierte Funktionen aus `adapters/` oder lokal definiert, u.a. `run_single_backtest_worker`, `create_mock_instrument`, `select_winners`, `print_tournament_table`, `write_tournament_json`, `validate_strategy_params`, `normalize_parquet_metadata` - diese stammen teilweise aus Submodulen, wir haben den Code gelesen).

- Importiert irgendeines der `strategies/*.py` aus `adapters/` (ausser `momentum_ls_base.py`)?
  - Nein. Nur `momentum_ls_base.py` und `momentum_ls_sma.py` importieren `MomentumLSAllocator` aus `adapters/`.

- Welche CLI-Flags hat `backtesting/run_backtest.py` aktuell?
  - `--momentum`, `--catalog-path`, `--output`, `--htmlreport`, `--dry-run`.

- Wie löst `backtesting/run_backtest.py` Strategy-Module auf (importlib, direkt, Registry)?
  - Dynamisch mit `importlib.import_module`.
