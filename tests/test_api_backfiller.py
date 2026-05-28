import pytest
import os
import pyarrow as pa
import pandas as pd
import logging
from unittest.mock import Mock, patch, AsyncMock
from automation.api_backfiller import _encode_fsb16, _merge_and_save, fetch_precisions_from_api

class TestApiBackfiller:
    def test_encode_fsb16(self):
        # 0.0001 with precision 8
        # Scaled value = 0.0001 * 10**8 = 10000
        # 10000 in hex is 0x2710
        encoded = _encode_fsb16(0.0001, 8)
        assert len(encoded) == 16
        # Struct '<q' produces 8 bytes, followed by 8 null bytes
        assert encoded[:8] == (10000).to_bytes(8, byteorder='little', signed=True)
        assert encoded[8:] == b'\x00' * 8

    @pytest.mark.asyncio
    @patch('automation.api_backfiller.aiohttp.ClientSession.get')
    async def test_fetch_precisions_from_api(self, mock_get):
        # Mocking API Integration
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "instruments": [
                {
                    "instrumentId": 100,
                    "symbolFull": "AAPL",
                    "pricePrecision": 3,
                    "sizePrecision": 4
                }
            ]
        }
        mock_get.return_value.__aenter__.return_value = mock_response

        # Need to mock the session
        session_mock = AsyncMock()
        session_mock.get = mock_get
        result = await fetch_precisions_from_api(session_mock, ["100"], "key1", "key2")
        assert result == {"100": (3, 4)}

    @patch('automation.api_backfiller.pq.write_table')
    @patch('automation.api_backfiller.pq.read_table')
    @patch('automation.api_backfiller.Path.exists')
    def test_merge_and_save(self, mock_exists, mock_read_table, mock_write_table):
        # Create a mock for reading tables
        df1 = pd.DataFrame({'ts_event': [100, 200], 'price': [1.0, 2.0]})
        df2 = pd.DataFrame({'ts_event': [200, 300], 'price': [2.0, 3.0]})

        table1 = pa.Table.from_pandas(df1).replace_schema_metadata({b"price_precision": b"5"})
        table2 = pa.Table.from_pandas(df2)

        # First call reads existing, we pass table2 as new_table
        mock_exists.return_value = True
        mock_read_table.return_value = table1

        logger = logging.getLogger("test")
        _merge_and_save(logger, table2, "AAPL", 5, 0)

        assert mock_write_table.called

        # Check written table
        args, kwargs = mock_write_table.call_args
        written_table = args[0]

        # Should have 3 rows (deduplicated by ts_event)
        assert len(written_table) == 3
        # Should retain metadata
        assert written_table.schema.metadata[b"price_precision"] == b"5"
        assert written_table.schema.metadata[b"instrument_id"] == b"AAPL"
