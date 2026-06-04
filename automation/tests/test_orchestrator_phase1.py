import pytest
import asyncio
import aiohttp
import logging
from unittest.mock import patch, MagicMock

# The function we are testing
from automation.daily_orchestrator import phase1_universe_and_mapping, INSTRUMENT_MAP_PATH

@pytest.fixture
def mock_logger():
    return logging.getLogger("test_logger")

from unittest.mock import AsyncMock

@patch("automation.daily_orchestrator._load_universe_file")
@patch("automation.universe_fetcher.run_fetch", new_callable=AsyncMock)
def test_phase1_stale_universe_auto_fetch(mock_run_fetch, mock_load_universe, mock_logger):
    """
    Test A: Simulate >24h old universe and mock a successful API fetch.
    Assert that run_fetch is called with correct INSTRUMENT_MAP_PATH.
    """
    # First call to _load_universe_file returns stale data
    # Second call (after successful fetch) returns fresh data
    mock_load_universe.side_effect = [
        {
            "fetched_at": "2020-01-01T00:00:00Z", # Very old
            "universe": [{"symbol": "AAPL", "etoro_id": "1001"}]
        },
        {
            "fetched_at": "2024-01-01T00:00:00Z", # Fresh (doesn't matter for the logic here, just checking it reloads)
            "universe": [{"symbol": "AAPL", "etoro_id": "1001"}]
        }
    ]

    # Mock run_fetch to return True
    mock_run_fetch.return_value = True

    result = phase1_universe_and_mapping(mock_logger, api_key="test_key", user_key="test_user")

    # Assert run_fetch was called with correct arguments
    mock_run_fetch.assert_called_once()
    kwargs = mock_run_fetch.call_args.kwargs
    assert kwargs["api_key"] == "test_key"
    assert kwargs["user_key"] == "test_user"
    assert "output_path" in kwargs
    assert kwargs["instrument_map_path"] == INSTRUMENT_MAP_PATH

    # Assert it returns the expected dictionary structure
    assert len(result["universe"]) > 0


@patch("automation.daily_orchestrator._load_universe_file")
@patch("automation.universe_fetcher.run_fetch", new_callable=AsyncMock)
def test_phase1_code_error_fail_fast(mock_run_fetch, mock_load_universe, mock_logger):
    """
    Test B: Simulate a code error (NameError) during fetch.
    Assert that the function raises the exception and does NOT fallback to stale data.
    """
    mock_load_universe.return_value = {
        "fetched_at": "2020-01-01T00:00:00Z", # Very old
        "universe": [{"symbol": "AAPL", "etoro_id": "1001"}]
    }

    # Mock run_fetch to raise NameError
    mock_run_fetch.side_effect = NameError("INSTRUMENT_MAP_PATH is not defined")

    # Assert that NameError is raised and not caught by the try-except
    with pytest.raises(NameError, match="INSTRUMENT_MAP_PATH is not defined"):
        phase1_universe_and_mapping(mock_logger, api_key="test_key", user_key="test_user")


@patch("automation.daily_orchestrator._load_universe_file")
@patch("automation.universe_fetcher.run_fetch", new_callable=AsyncMock)
def test_phase1_network_error_fallback(mock_run_fetch, mock_load_universe, mock_logger):
    """
    Test C: Simulate an aiohttp.ClientError (network failure).
    Assert that fallback logic is triggered and no exception is raised.
    """
    stale_universe_data = {
        "fetched_at": "2020-01-01T00:00:00Z", # Very old
        "universe": [{"symbol": "AAPL", "etoro_id": "1001"}]
    }
    mock_load_universe.return_value = stale_universe_data

    # Mock run_fetch to raise aiohttp.ClientError
    mock_run_fetch.side_effect = aiohttp.ClientConnectionError("Connection refused")

    # This should not raise an exception, it should fallback
    result = phase1_universe_and_mapping(mock_logger, api_key="test_key", user_key="test_user")

    # The result should contain the data from the stale universe
    assert len(result["universe"]) > 0
    assert result["universe"][0]["symbol"] == "AAPL"
