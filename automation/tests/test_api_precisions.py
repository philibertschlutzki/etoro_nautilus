import pytest
import aiohttp
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import logging

from automation.api_backfiller import fetch_precisions_from_api, log
from automation.catalog_service import _fetch_precisions, log as c_log

@pytest.mark.asyncio
async def test_fetch_precisions_from_api_no_hits(caplog):
    # Capture the specific logger used in api_backfiller
    caplog.set_level(logging.WARNING, logger=log.name)

    # Mock response without any precisions
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"instruments": [{"instrumentId": "1", "symbol": "AAPL"}]})

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_response

    res = await fetch_precisions_from_api(mock_session, ["1", "2"], "api", "user")

    # check that a warning is logged
    assert any("Precision-API lieferte 0/2 Instrumente" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_fetch_precisions_catalog_no_hits(caplog):
    caplog.set_level(logging.WARNING, logger=c_log.name)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"instruments": [{"instrumentId": "1", "symbol": "AAPL"}]})

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_response

    with patch("aiohttp.ClientSession", return_value=mock_session):
        mock_session.__aenter__.return_value = mock_session
        res = await _fetch_precisions("api", "user", {"1": "AAPL", "2": "GOOG"})

    assert any("Precision-API lieferte 0/2 Instrumente" in record.message for record in caplog.records)
