import pytest
from unittest.mock import patch, MagicMock
from automation.backtest_runner import run_backtest
import builtins
from io import StringIO
import json
import os

def test_run_backtest_fatal_error_aborts(monkeypatch):
    """
    E2E-Test für Issue #355: Stellt sicher, dass systemische Fehler (z.B. ImportError)
    im ProcessPoolExecutor nicht stillschweigend geschluckt werden, sondern zu einem
    harten Abbruch mit sys.exit(1) führen.
    """
    class DummyFatalFuture:
        def result(self):
            raise ImportError("Simulated Fatal ImportError in Worker")

    with patch('automation.backtest_runner.ProcessPoolExecutor') as MockExecutor, \
         patch('automation.backtest_runner.as_completed') as mock_as_completed_patch, \
         patch('automation.backtest_runner.sys.argv', ['backtest_runner.py', '--config', 'dummy.json']):

        mock_executor_instance = MockExecutor.return_value
        dummy_future = DummyFatalFuture()
        mock_executor_instance.submit.return_value = dummy_future

        def generator_yielding_future(futures):
            for future in futures:
                yield future

        mock_as_completed_patch.side_effect = generator_yielding_future

        original_open = builtins.open
        def mock_open_file(filename, *args, **kwargs):
            if isinstance(filename, str) and ("strategies.json" in filename or "backtest.json" in filename or "dummy.json" in filename):
                return StringIO(json.dumps({
                    "global_settings": {"spread_modeling": False, "span_tolerance_days": 5.0, "tournament": {"n_workers": 2}},
                    "strategies": [
                        {
                            "active": True,
                            "strategy_class": "SmaCrossoverStrategy",
                            "config_class": "TestConfig",
                            "symbols": ["AAPL.NASDAQ"],
                            "params": {}
                        }
                    ]
                }))
            if isinstance(filename, str) and ".log" in filename:
                import io
                class DummyFile(io.StringIO):
                    def close(self): pass
                return DummyFile()
            return original_open(filename, *args, **kwargs)

        original_listdir = os.listdir
        def mock_listdir(path):
            if isinstance(path, str) and "quote_tick" in path:
                return ["AAPL.NASDAQ"]
            return original_listdir(path)

        original_isdir = os.path.isdir
        def mock_isdir(path):
            if isinstance(path, str) and "AAPL.NASDAQ" in path:
                return True
            return original_isdir(path)

        original_exists = os.path.exists
        def mock_exists(path):
            if isinstance(path, str) and ("quote_tick" in path or "dummy.json" in path):
                return True
            return original_exists(path)

        import pyarrow.parquet as pq
        class MockParquetFile:
            def __init__(self, *args, **kwargs):
                self.schema = MagicMock()
                self.schema.names = ["ts_event"]
                self.metadata = MagicMock()
                mock_rg = MagicMock()
                mock_col = MagicMock()
                mock_col.statistics.min = 1000000000000
                mock_rg.column.return_value = mock_col
                self.metadata.row_group.return_value = mock_rg

        with patch('builtins.open', side_effect=mock_open_file), \
             patch('os.path.exists', side_effect=mock_exists), \
             patch('os.makedirs', return_value=None), \
             patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.isdir', side_effect=mock_isdir), \
             patch('automation.backtest_runner.normalize_parquet_metadata', return_value=False), \
             patch('automation.backtest_runner.ParquetDataCatalog') as MockCatalog, \
             patch('automation.backtest_runner.Path.glob', return_value=['dummy.parquet']), \
             patch('automation.backtest_runner.Path.exists', return_value=True), \
             patch('pyarrow.parquet.ParquetFile', MockParquetFile), \
             patch('automation.backtest_runner.read_precisions_from_parquet', return_value=(2, 2)), \
             patch('argparse.ArgumentParser.parse_args') as mock_args, \
             patch('automation.backtest_runner.check_data_span', return_value=(True, 150, 150)):

             mock_args.return_value.config = "dummy.json"
             mock_args.return_value.htmlreport = False
             mock_args.return_value.output = None
             mock_args.return_value.dry_run = False
             mock_args.return_value.strategy = None
             mock_args.return_value.single_symbol = None
             mock_args.return_value.catalog_path = "dummy"
             mock_args.return_value.momentum = False

             mock_catalog_instance = MockCatalog.return_value
             mock_catalog_instance.instruments.return_value = [MagicMock()]

             with patch("sys.exit") as mock_exit, \
                  patch('automation.backtest_runner.write_tournament_json', return_value=None):
                 mock_exit.side_effect = SystemExit(1)

                 with pytest.raises(SystemExit) as excinfo:
                     run_backtest()

                 assert excinfo.value.code == 1, "Der Runner muss bei einem ImportError mit Exit-Code 1 hart abbrechen."
