import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientSession

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.common.component import MessageBus, Logger, LiveClock
from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import (
    ClientId,
    ClientOrderId,
    InstrumentId,
    TraderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity
from unittest.mock import MagicMock
from nautilus_trader.model.enums import OrderType

from adapters.etoro_execution import EToroExecutionClient


@pytest.fixture
def exec_client():
    from nautilus_trader.live.execution_client import LiveExecutionClient

    # In nautilus versions we just patch super().__init__
    # to avoid needing all actual C extension objects instantiated correctly in tests.
    with patch.object(LiveExecutionClient, "__init__", return_value=None), patch.object(
        LiveExecutionClient, "_set_account_id", return_value=None
    ):
        client = EToroExecutionClient(
            loop=MagicMock(),
            msgbus=MagicMock(),
            cache=MagicMock(),
            clock=MagicMock(),
            instrument_provider=MagicMock(),
            api_key="TEST_API",
            user_key="TEST_USER",
            environment="demo",
            dry_run=False,
            state_path=":memory:",
            enable_trailing_stop=False,
        )
        # For cython extension types, setting attributes dynamically is hard.
        # Instead, we patch them at the class level.
        EToroExecutionClient._log = MagicMock()
        EToroExecutionClient._clock = MagicMock()
        EToroExecutionClient._cache = MagicMock()
        client._clock.timestamp_ns.return_value = 1234567890

        client.generate_order_submitted = MagicMock()
        client.generate_order_accepted = MagicMock()
        client.generate_order_rejected = MagicMock()
        client.generate_order_canceled = MagicMock()
        client.generate_order_filled = MagicMock()
        client.generate_account_state = MagicMock()

        client._session = MagicMock(spec=ClientSession)
        client._state = AsyncMock()
        client._state.get.return_value = "mock_pos_id"
        client._state.get_all.return_value = {}
        client._rate_limiter = AsyncMock()
        client._rate_limiter.acquire.return_value = True
        return client


@pytest.mark.asyncio
async def test_limit_order_payload_has_is_no_stop_loss(exec_client):
    order = MagicMock()
    order.client_order_id = ClientOrderId("test_limit_1")
    order.instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
    order.strategy_id = ClientId("TEST_STRATEGY")
    order.side = OrderSide.BUY
    order.quantity = Quantity.from_int(10)
    order.price = Price(150.0, precision=2)
    order.time_in_force = TimeInForce.GTC
    order.order_type = OrderType.LIMIT

    mock_post_ctx = AsyncMock()
    mock_post_ctx.__aenter__.return_value.status = 200
    mock_post_ctx.__aenter__.return_value.text.return_value = '{"token": "mock-token"}'
    exec_client._session.post.return_value = mock_post_ctx

    mock_command = MagicMock()
    mock_command.order = order

    # Mock acquire so it continues
    exec_client._rate_limiter.acquire = AsyncMock(return_value=True)

    await exec_client._submit_order_async(mock_command)

    # Verify the payload
    exec_client._session.post.assert_called_once()
    args, kwargs = exec_client._session.post.call_args
    assert "limit-orders" in args[0]
    payload = kwargs["json"]
    assert payload["Rate"] == 150.0
    assert payload["Amount"] == 1500.0
    assert payload["IsNoStopLoss"] is True
    assert payload["IsNoTakeProfit"] is True
    assert "StopLossRate" not in payload
    assert "TakeProfitRate" not in payload


@pytest.mark.asyncio
async def test_cancel_limit_order_calls_delete_endpoint(exec_client):
    order = MagicMock()
    order.client_order_id = ClientOrderId("test_limit_cancel")
    order.instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
    order.strategy_id = ClientId("TEST_STRATEGY")
    order.side = OrderSide.BUY
    order.quantity = Quantity.from_int(10)
    order.price = Price(150.0, precision=2)
    order.time_in_force = TimeInForce.GTC
    order.order_type = OrderType.LIMIT
    exec_client._cache.order.return_value = order
    exec_client._state.get.return_value = "mock_order_id"

    mock_delete_ctx = AsyncMock()
    mock_delete_ctx.__aenter__.return_value.status = 200
    exec_client._session.delete.return_value = mock_delete_ctx

    mock_command = MagicMock()
    mock_command.client_order_id = ClientOrderId("test_limit_cancel")

    exec_client._rate_limiter.acquire = AsyncMock(return_value=True)

    await exec_client._cancel_order_async(mock_command)

    exec_client._session.delete.assert_called_once()
    args, kwargs = exec_client._session.delete.call_args
    assert "limit-orders/mock_order_id" in args[0]
    exec_client._session.post.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_market_position_calls_post_close_endpoint(exec_client):
    order = MagicMock()
    order.client_order_id = ClientOrderId("test_market_cancel")
    order.instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
    order.strategy_id = ClientId("TEST_STRATEGY")
    order.side = OrderSide.BUY
    order.quantity = Quantity.from_int(10)
    order.time_in_force = TimeInForce.GTC
    order.order_type = OrderType.MARKET
    exec_client._cache.order.return_value = order
    exec_client._state.get.return_value = "mock_pos_id"

    mock_post_ctx = AsyncMock()
    mock_post_ctx.__aenter__.return_value.status = 200
    exec_client._session.post.return_value = mock_post_ctx

    mock_command = MagicMock()
    mock_command.client_order_id = ClientOrderId("test_market_cancel")

    exec_client._rate_limiter.acquire = AsyncMock(return_value=True)

    await exec_client._cancel_order_async(mock_command)

    exec_client._session.post.assert_called_once()
    args, kwargs = exec_client._session.post.call_args
    assert "market-close-orders/positions/mock_pos_id" in args[0]
    payload = kwargs.get("json")
    assert payload == {"UnitsToDeduct": None}
    assert "InstrumentID" not in payload


@pytest.mark.asyncio
async def test_balance_fetch_parses_credit_key(exec_client):
    mock_get_ctx = AsyncMock()
    mock_get_ctx.__aenter__.return_value.status = 200
    mock_get_ctx.__aenter__.return_value.json.return_value = {
        "credit": 9921.62,
        "positions": [],
        "ordersForOpen": [],
    }
    exec_client._session.get.return_value = mock_get_ctx

    balance = await exec_client._fetch_account_balance()

    assert isinstance(balance, Money)
    assert balance.as_double() == 9921.62
    assert balance.currency == USD


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("aiohttp.ClientSession")
async def test_emergency_cleanup_waits_for_settle_delay(mock_session, mock_sleep):
    from dev_scripts.etoro_execution_tests_all_orders import emergency_cleanup


    mock_session_inst = MagicMock()
    mock_session.return_value.__aenter__.return_value = mock_session_inst

    mock_get_ctx = AsyncMock()
    mock_get_ctx.__aenter__.return_value.status = 200
    mock_get_ctx.__aenter__.return_value.json.return_value = {
        "Positions": [],
        "OrdersForOpen": [],
    }
    mock_session_inst.get.return_value = mock_get_ctx


    await emergency_cleanup(
        api_key="API_KEY",
        user_key="USER_KEY",
        environment="demo",
        symbol="ADA.ETORO",
        settle_delay_s=3.0,
    )

    assert mock_sleep.call_count == 3

@pytest.mark.asyncio
async def test_limit_order_calls_generate_order_accepted_immediately(exec_client):
    order = MagicMock()
    order.client_order_id = ClientOrderId("test_limit_2")
    order.instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
    order.strategy_id = ClientId("TEST_STRATEGY")
    order.side = OrderSide.BUY
    order.quantity = Quantity.from_int(10)
    order.price = Price(150.0, precision=2)
    order.time_in_force = TimeInForce.GTC
    order.order_type = OrderType.LIMIT

    mock_post_ctx = AsyncMock()
    mock_post_ctx.__aenter__.return_value.status = 200
    mock_post_ctx.__aenter__.return_value.text.return_value = '{"token": "mock-token"}'
    exec_client._session.post.return_value = mock_post_ctx

    mock_command = MagicMock()
    mock_command.order = order

    exec_client._rate_limiter.acquire = AsyncMock(return_value=True)

    await exec_client._submit_order_async(mock_command)

    # Assert generate_order_accepted is called immediately
    exec_client.generate_order_accepted.assert_called_once()
