import pytest
import os
import io
import pyarrow as pa
import asyncio
import json
from unittest.mock import Mock, patch, AsyncMock
from automation.catalog_service import _write_zip, CatalogService, _load_etoro_id_map

class TestCatalogService:
    @patch('automation.catalog_service.os._exit')
    @patch('automation.catalog_service.websockets.connect')
    @pytest.mark.asyncio
    async def test_fatal_error_handling(self, mock_connect, mock_exit):
        mock_connect.side_effect = Exception("Fatal Error")

        service = CatalogService(
            etoro_id_to_symbol={"100": "AAPL"},
            api_key="dummy",
            user_key="dummy"
        )

        # Test that _ws_connect handles the error and calls os._exit(1)
        with patch.object(service, '_ws_connect', side_effect=Exception("Fatal Error")):
            try:
                await service.run()
            except Exception:
                pass

        assert mock_exit.called
        mock_exit.assert_called_with(1)

    @patch('automation.catalog_service.zipfile.ZipFile')
    @patch('automation.catalog_service.pq.write_table')
    @patch('automation.catalog_service.IMPORT_PATH')
    def test_do_flush(self, mock_import_path, mock_write_table, mock_zip):
        from automation.catalog_service import RawTick, InstrumentState
        # Setup fake buffer ticks keyed by symbol as the function expects
        buffer = {
            "AAPL": [
                RawTick(1.0, 1.1, 1000000000),
                RawTick(2.0, 2.1, 2000000000)
            ]
        }

        # instrument_states keyed by etoro_id or symbol doesn't matter for the mock here,
        # but let's check the function. It uses instrument_states to lookup by etoro_id or symbol?
        # Actually in _do_flush it calls _write_zip(snapshot, self._states).
        # self._states is keyed by etoro_id.
        states = {
            "100": InstrumentState(symbol="AAPL", etoro_id="100", price_prec=3, size_prec=4)
        }

        from pathlib import Path
        mock_import_path.return_value = Path("/tmp")

        zip_path = _write_zip(buffer, states)

        # Check zip file called
        assert zip_path is not None
        assert mock_zip.called

    @patch('automation.catalog_service.websockets.connect')
    @pytest.mark.asyncio
    async def test_websocket_integration(self, mock_connect):
        from automation.catalog_service import InstrumentState
        # We simulate the websocket context manager
        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws

        service = CatalogService(
            etoro_id_to_symbol={"100": "AAPL"},
            api_key="dummy",
            user_key="dummy"
        )
        service._states["100"] = InstrumentState(symbol="AAPL", etoro_id="100", price_prec=2, size_prec=2)

        # Checking what _process_message expects
        msg = {
            "type": "Trading.Instrument.Rate",
            "content": {
                "InstrumentID": 100,
                "Bid": 1.5,
                "Ask": 1.51
            },
            "topic": "instrument:100"
        }

        await service._process_message(msg)

        assert "AAPL" in service._tick_buffer
        assert len(service._tick_buffer["AAPL"]) == 1
