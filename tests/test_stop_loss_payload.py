import pytest
from unittest.mock import MagicMock
from decimal import Decimal
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from adapters.etoro_execution import EToroExecutionClient

def setup_client():
    exec_client = EToroExecutionClient.__new__(EToroExecutionClient)
    exec_client._rest_base = "https://mock.etoro.com"

    mock_cache = MagicMock()
    mock_quote = MagicMock()
    mock_quote.ask_price = 100.0
    mock_quote.bid_price = 100.0
    mock_cache.quote_tick.return_value = mock_quote

    mock_inst = MagicMock()
    mock_inst.price_precision = 5
    mock_cache.instrument.return_value = mock_inst

    return exec_client, mock_cache

def setup_order():
    mock_order = MagicMock()
    mock_order.instrument_id = InstrumentId.from_str("ADA.ETORO")
    mock_order.side = OrderSide.BUY
    mock_order.quantity = Decimal("10")
    return mock_order

class DummyClient(EToroExecutionClient):
    @property
    def _log(self): return MagicMock()
    @property
    def _cache(self): return getattr(self, "_mock_cache")

def test_sl_payload_none_tag():
    exec_client, mock_cache = setup_client()
    mock_order = setup_order()
    mock_order.tags = None

    dummy = DummyClient.__new__(DummyClient)
    dummy._rest_base = "https://mock.etoro.com"
    object.__setattr__(dummy, "_mock_cache", mock_cache)

    payload, _ = DummyClient._build_market_open_payload(dummy, mock_order, 100017)
    assert "StopLossRate" not in payload

def test_sl_payload_empty_tag():
    exec_client, mock_cache = setup_client()
    mock_order = setup_order()
    mock_order.tags = []

    dummy = DummyClient.__new__(DummyClient)
    dummy._rest_base = "https://mock.etoro.com"
    object.__setattr__(dummy, "_mock_cache", mock_cache)

    payload, _ = DummyClient._build_market_open_payload(dummy, mock_order, 100017)
    assert "StopLossRate" not in payload

def test_sl_payload_malformed_abc():
    exec_client, mock_cache = setup_client()
    mock_order = setup_order()
    mock_order.tags = ["SL:abc"]

    dummy = DummyClient.__new__(DummyClient)
    dummy._rest_base = "https://mock.etoro.com"
    object.__setattr__(dummy, "_mock_cache", mock_cache)

    payload, _ = DummyClient._build_market_open_payload(dummy, mock_order, 100017)
    assert "StopLossRate" not in payload

def test_sl_payload_malformed_empty():
    exec_client, mock_cache = setup_client()
    mock_order = setup_order()
    mock_order.tags = ["SL:"]

    dummy = DummyClient.__new__(DummyClient)
    dummy._rest_base = "https://mock.etoro.com"
    object.__setattr__(dummy, "_mock_cache", mock_cache)

    payload, _ = DummyClient._build_market_open_payload(dummy, mock_order, 100017)
    assert "StopLossRate" not in payload

def test_sl_payload_negative_pct():
    exec_client, mock_cache = setup_client()
    mock_order = setup_order()
    mock_order.tags = ["SL:-0.1"]

    dummy = DummyClient.__new__(DummyClient)
    dummy._rest_base = "https://mock.etoro.com"
    object.__setattr__(dummy, "_mock_cache", mock_cache)

    payload, _ = DummyClient._build_market_open_payload(dummy, mock_order, 100017)
    assert "StopLossRate" not in payload

def test_sl_payload_valid():
    exec_client, mock_cache = setup_client()
    mock_order = setup_order()
    mock_order.tags = ["SL:0.10"]

    dummy = DummyClient.__new__(DummyClient)
    dummy._rest_base = "https://mock.etoro.com"
    object.__setattr__(dummy, "_mock_cache", mock_cache)

    payload, _ = DummyClient._build_market_open_payload(dummy, mock_order, 100017)
    assert "StopLossRate" in payload
    assert payload["StopLossRate"] == 90.0
