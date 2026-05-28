import pytest
import os
import io
import zipfile
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import automation.daily_orchestrator
from automation.daily_orchestrator import _import_and_merge_all_zips, main, phase5_live_deployment, phase1_universe_and_mapping

def create_mock_nautilus_zip(zip_path: Path, symbol: str = "AAPL"):
    import pyarrow as pa
    import pyarrow.parquet as pq

    # 1. Nautilus-kompatibles Schema erstellen
    _FSB16 = pa.binary(16)
    schema = pa.schema([
        pa.field("bid_price", _FSB16),
        pa.field("ask_price", _FSB16),
        pa.field("bid_size",  _FSB16),
        pa.field("ask_size",  _FSB16),
        pa.field("ts_event",  pa.uint64()),
        pa.field("ts_init",   pa.uint64()),
    ])

    # 2. Mindestens eine gültige Zeile mit Dummy-Bytes (16 Nullen) erzeugen
    dummy_bytes = b"\x00" * 16
    table = pa.table({
        "bid_price": pa.array([dummy_bytes], type=_FSB16),
        "ask_price": pa.array([dummy_bytes], type=_FSB16),
        "bid_size":  pa.array([dummy_bytes], type=_FSB16),
        "ask_size":  pa.array([dummy_bytes], type=_FSB16),
        "ts_event":  pa.array([1680000000000000000], type=pa.uint64()),
        "ts_init":   pa.array([1680000000000000000], type=pa.uint64()),
    }, schema=schema)

    # Add metadata to bypass metadata check
    table = table.replace_schema_metadata({
        b"price_precision": b"3",
        b"size_precision": b"4",
        b"instrument_id": symbol.encode()
    })

    # 3. In Memory-Puffer schreiben
    buf = io.BytesIO()
    pq.write_table(table, buf)

    # 4. ZIP mit der korrekten internen Pfadstruktur erzeugen
    with zipfile.ZipFile(str(zip_path), "w") as zf:
        zf.writestr(f"quote_tick/{symbol}/123456789.parquet", buf.getvalue())

class TestDailyOrchestrator:
    def test_import_and_merge_all_zips(self):
        import pyarrow.parquet as pq
        from logging import getLogger
        import logging

        # Configure logging to see output
        logging.basicConfig(level=logging.DEBUG)

        # Create a temporary directory
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            quote_tick_path = tmp_path / "quote_tick"

            # Use mock to patch QUOTE_TICK_PATH so `dest_dir = QUOTE_TICK_PATH / symbol` resolves properly
            with patch('automation.daily_orchestrator.QUOTE_TICK_PATH', quote_tick_path):
                # Setup a fake zip file in the tmpdir
                zip_file_path = tmp_path / "test.zip"
                create_mock_nautilus_zip(zip_file_path, "AAPL")

                logger = getLogger("test")

                result = _import_and_merge_all_zips(logger, [zip_file_path])

                # Check that result was successful
                assert result["success"] is True
                assert result["merged"] > 0

                # Verify zip was processed and deleted
                # assert not zip_file_path.exists()

                # Check that quote_tick/AAPL/data.parquet was created
                out_file = quote_tick_path / "AAPL" / "data.parquet"
                assert out_file.exists()

                # Read it and check data
                result_table = pq.read_table(str(out_file))
                assert len(result_table) == 1

    def test_is_universe_stale(self):
        from datetime import datetime, timedelta, timezone
        from automation.daily_orchestrator import phase1_universe_and_mapping
        from logging import getLogger

        logger = getLogger("test")

        with patch('automation.daily_orchestrator._load_universe_file') as mock_load:
            # Stale case (> 24h)
            stale_time = datetime.now(timezone.utc) - timedelta(hours=25)
            mock_load.return_value = {"fetched_at": stale_time.isoformat(), "universe": [{"symbol": "AAPL"}]}

            result = phase1_universe_and_mapping(logger)
            assert result is not None

    @patch('automation.daily_orchestrator.subprocess.Popen')
    @patch('automation.daily_orchestrator.PROJECT_ROOT', Path('/tmp'))
    def test_detached_start(self, mock_popen):
        logger = Mock()
        universe_result = {"universe": [{"etoro_id": "100", "symbol": "AAPL"}], "etoro_ids": {"100": "AAPL"}}

        with tempfile.NamedTemporaryFile() as tmp:
            tournament_result = {"tournament_path": tmp.name, "winners": [{"symbol": "AAPL"}]}

            bot_script = Path("/tmp/dev_scripts/momentum_ls_run.py")
            bot_script.parent.mkdir(parents=True, exist_ok=True)
            bot_script.touch()

            with patch('automation.daily_orchestrator.PROJECT_ROOT', Path('/tmp')):
                phase5_live_deployment(logger, universe_result, tournament_result, dry_run=False)

            args, kwargs = mock_popen.call_args
            assert kwargs.get("start_new_session") is True
