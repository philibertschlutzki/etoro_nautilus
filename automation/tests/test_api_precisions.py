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

    # Mock response without any precisions (Issue #171 Regression Test: shouldn't drop standard equity)
    mock_response = MagicMock()
    mock_response.status = 200
    # Simulate an instrument but missing precisions entirely, e.g., an equity that should fallback to (2,2)
    mock_response.json = AsyncMock(return_value={"instrumentDisplayDatas": [{"instrumentID": 1, "symbolFull": "TSLA"}]})

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)

    res = await fetch_precisions_from_api(mock_session, ["1", "2"], "api", "user")

    # Issue #171: TSLA (standard equity) should resolve to (2, 2) and NOT be dropped, even if missing from API
    assert "1" in res
    assert res["1"] == (2, 2)

    # Check that a warning is logged for missing APIs. Since TSLA resolved via fallback, it is counted as a hit.
    # We requested 2 IDs ("1", "2") but only provided mock display data for "1".
    # Therefore, 1 out of 2 instruments was resolved.
    assert any("Precision-API lieferte nur 1/2 Instrumente" in record.message for record in caplog.records)

@pytest.mark.asyncio
async def test_fetch_precisions_from_api_mismatch_guard(caplog):
    # Pitfall #36 Guard: Mock response returns size_prec=2 for a non-equity (Crypto)
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={
        "instrumentDisplayDatas": [
            {
                "instrumentID": 1,
                "symbolFull": "BTC",
                "pricePrecision": 2,
                "sizePrecision": 2  # Incorrect API response: size=2 for Crypto
            }
        ]
    })

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)

    res = await fetch_precisions_from_api(mock_session, ["1"], "api", "user")

    # It MUST be dropped (skipped) because size_prec==2 but fallback says 8
    assert "1" not in res


@pytest.mark.asyncio
async def test_fetch_precisions_catalog_no_hits(caplog):
    caplog.set_level(logging.WARNING, logger=c_log.name)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"instruments": [{"instrumentId": "1", "symbol": "AAPL"}]})

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        mock_session.__aenter__.return_value = mock_session
        res = await _fetch_precisions("api", "user", {"1": "AAPL", "2": "GOOG"})

    assert any("Precision-API lieferte nur 0/2 Instrumente" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_api_parser_correctness():
    # Provide a static recorded API-response (Fixture) containing valid instrumentDisplayDatas
    raw_response = {
        "instrumentDisplayDatas": [
            {
                "instrumentID": 1,
                "instrumentDisplayName": "Bitcoin",
                "pricePrecision": 2,
                "sizePrecision": 5,
                "symbolFull": "BTC"
            },
            {
                "instrumentID": 2,
                "instrumentDisplayName": "Apple",
                "pricePrecision": 2,
                "sizePrecision": 2,
                "symbolFull": "AAPL"
            }
        ]
    }

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=raw_response)
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)

    res = await fetch_precisions_from_api(mock_session, ["1", "2"], "api", "user")

    # Assert dynamic values are correctly parsed
    assert res == {"1": (2, 5), "2": (2, 2)}
