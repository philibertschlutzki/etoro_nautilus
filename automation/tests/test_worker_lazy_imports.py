import unittest.mock
import pandas as pd
from automation.backtest_runner import run_single_backtest_worker

def test_worker_lazy_import_no_exception():
    """
    Testet den Lazy-Import in run_single_backtest_worker, wenn 'check_data_span'
    fehlschlägt und ein WALK_FORWARD_INSUFFICIENT_DATA Event emittiert werden muss.
    Stellt sicher, dass kein ImportError (Issue #354) auftritt.
    """
    with unittest.mock.patch('automation.backtest_runner.ParquetDataCatalog') as mock_catalog:
        mock_instance = mock_catalog.return_value

        with unittest.mock.patch('automation.backtest_runner.load_ticks_from_catalog') as mock_load:
            class DummyTick:
                def __init__(self, ts_init):
                    self.ts_event = pd.Timestamp(ts_init, unit='ns')

            mock_load.return_value = [
                DummyTick(0),
                DummyTick(86400 * 1_000_000_000) # 1 day span
            ]

            with unittest.mock.patch('automation.log_manager.emit_execution_event') as mock_emit:
                # This should not raise any exceptions.
                result = run_single_backtest_worker(
                    inst_id_str="BTC.ETORO",
                    bar_type="1-HOUR",
                    strat={"name": "test", "strategy_module": "automation.strategies.mock", "strategy_class": "MockStrategy", "config_class": "MockConfig", "params": {}, "_walk_forward_days": 90},
                    catalog_path="/tmp/mock_catalog",
                    start_ns=0,
                    end_ns=None,
                    start_capital=10000,
                    generate_html_report=False,
                    reports_dir="/tmp/reports",
                    worker_log_file="/tmp/worker_log.log",
                    span_tolerance_days=3.0,
                    commission_bps=0.0,
                    spread_bps_by_asset_class=None
                )

                # verify that the result reflects the skipped execution
                assert "error" in result
                assert result["error"] == "insufficient_data"

                # verify that emit_execution_event was called correctly. Issue #898 Fix 4 added an
                # unconditional COST_MODEL_RESOLVED event right after the spread/asset-class
                # resolution (before the walk-forward check runs). Issue #1015/#1167 (Katalog
                # #1170) added a symmetric INVARIANT_STREAM_RESULT event for check_data_span's own
                # PASS/FAIL judgement (source="worker"), emitted right before the pre-existing
                # WALK_FORWARD_INSUFFICIENT_DATA event on the FAIL branch — this path now emits
                # three times.
                assert mock_emit.call_count == 3
                event_names = [call.args[1] for call in mock_emit.call_args_list]
                assert "COST_MODEL_RESOLVED" in event_names
                assert "INVARIANT_STREAM_RESULT" in event_names
                assert "WALK_FORWARD_INSUFFICIENT_DATA" in event_names
                args, kwargs = mock_emit.call_args_list[-1]
                assert args[1] == "WALK_FORWARD_INSUFFICIENT_DATA"
                assert "symbol" in args[2]
                assert args[2]["symbol"] == "BTC.ETORO"
