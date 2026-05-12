"""eToro LiveExecutionClient for Nautilus Trader.

Implements full order execution: REST submission, WebSocket fill stream,
token-bucket rate limiting, atomic state persistence, and dry-run mode.

API-Spec-konforme URLs (Stand v1.138.0):
  Open by amount : POST /api/v1/trading/execution/{env}/market-open-orders/by-amount
  Open by units  : POST /api/v1/trading/execution/{env}/market-open-orders/by-units
  Close position : POST /api/v1/trading/execution/{env}/market-close-orders/positions/{positionId}
  Limit order    : POST /api/v1/trading/execution/{env}/limit-orders
  PnL / Balance  : GET  /api/v1/trading/info/{env}/pnl
"""

from __future__ import annotations

from adapters.etoro_rate_limiter import _RateLimiter
from adapters.etoro_state_manager import _StateManager
from adapters.etoro_config import EToroExecClientConfig, EToroLiveExecClientFactory

import asyncio
import json
import os
import ssl
import uuid
from contextlib import suppress
from typing import Literal

import aiohttp
import websockets

from nautilus_trader.common.enums import LogColor
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.execution.messages import (
    BatchCancelOrders,
    CancelAllOrders,
    CancelOrder,
    ModifyOrder,
    QueryOrder,
    SubmitOrder,
    SubmitOrderList,
)
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import (
    AccountType,
    LiquiditySide,
    OmsType,
    OrderSide,
    OrderType,
    PositionSide,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    ClientOrderId,
    PositionId,
    TradeId,
    Venue,
    VenueOrderId,
)
from nautilus_trader.model.objects import AccountBalance, Money, Price, Quantity

from adapters.instrument_map import ETORO_INSTRUMENTS

# ── Constants ──────────────────────────────────────────────────────────────────

_MAX_CONNECT_ATTEMPTS = 5
_CONNECT_TIMEOUT_S    = 30
_REST_TIMEOUT_S       = 10

_REST_BASE: dict[str, str] = {
    "demo": "https://public-api.etoro.com/api/v1/trading/execution/demo",
    "real": "https://public-api.etoro.com/api/v1/trading/execution",
}

# FIX #1: Korrekter PnL-Pfad laut API-Doku (/trading/info/, nicht /trading/execution/)
# Wird für Balance-Berechnung und Timeout-Reconciliation verwendet.
_PNL_BASE: dict[str, str] = {
    "demo": "https://public-api.etoro.com/api/v1/trading/info/demo/pnl",
    "real": "https://public-api.etoro.com/api/v1/trading/info/real/pnl",
}

_WS_URL = "wss://ws.etoro.com/ws"


# ── Execution Client ───────────────────────────────────────────────────────────

class EToroExecutionClient(LiveExecutionClient):
    """Live execution client for eToro broker.

    Handles order submission, cancellation, and WS-based fill events.
    All command handlers are async (required by Nautilus >= 1.200).
    URL construction and auth-header creation are centralised to avoid
    duplication across submit / cancel / reconcile paths.

    API-Spec-Fixes vs. Vorversion:
      #1  PnL-URL: /trading/info/{env}/pnl  (war /trading/execution/{env}/pnl)
      #2  Response-Parsing: orderForOpen.orderID extrahieren
      #3  Close-Payload: InstrumentID required laut OpenAPI
      #4  Payload-Schlüssel: InstrumentID  (war InstrumentId, falsches D)
      #5  Balance-Berechnung via PnL-Endpoint mit offizieller Formel
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        msgbus: object,
        cache: object,
        clock: object,
        instrument_provider: InstrumentProvider,
        api_key: str,
        user_key: str,
        environment: Literal["demo", "real"],
        dry_run: bool,
        state_path: str,
        enable_trailing_stop: bool,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId("ETORO"),
            venue=Venue("ETORO"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            instrument_provider=instrument_provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )

        if not environment:
            raise ValueError(
                "Account ID missing in configuration. "
                "Ensure environment is correctly set."
            )
        self._set_account_id(AccountId(f"ETORO-{environment.upper()}-001"))

        self._api_key              = api_key
        self._user_key             = user_key
        self._environment          = environment
        self._dry_run              = dry_run
        self._enable_trailing_stop = enable_trailing_stop
        self._rest_base            = _REST_BASE[environment]
        self._pnl_base             = _PNL_BASE[environment]

        # Reverse lookup: Nautilus InstrumentId string → eToro numeric id string
        self._instrument_to_etoro: dict[str, str] = {
            v: k for k, v in ETORO_INSTRUMENTS.items()
        }

        self._rate_limiter = _RateLimiter()
        self._state        = _StateManager(state_path)
        self._session: aiohttp.ClientSession | None = None
        self._ws: object | None                     = None
        self._ws_task: asyncio.Task[None] | None    = None

    # ── Auth helpers ──────────────────────────────────────────────────────────

    def _make_headers(self, req_id: str | None = None) -> dict[str, str]:
        """Build standard eToro auth headers.

        Pass an explicit req_id for idempotent operations (submit, cancel).
        Omit to auto-generate a random UUID (reconcile, balance fetch).
        """
        return {
            "x-api-key":    self._api_key,
            "x-user-key":   self._user_key,
            "x-request-id": req_id if req_id else str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _order_req_id(client_order_id_value: str) -> str:
        """Deterministic, idempotent request-id derived from client_order_id.

        Uses UUID5 (namespace-OID) so the same order always maps to the same
        x-request-id — enabling eToro-side deduplication on retries.
        """
        return str(uuid.uuid5(uuid.NAMESPACE_OID, client_order_id_value))

    # ── URL construction ──────────────────────────────────────────────────────

    def _build_url(
        self,
        order: object,
        is_close: bool,
        etoro_position_id: str | None,
        payload: dict,
    ) -> str:
        """Single source of truth for REST endpoint URLs."""
        if is_close and etoro_position_id:
            return (
                f"{self._rest_base}/market-close-orders"
                f"/positions/{etoro_position_id}"
            )
        if order.order_type == OrderType.LIMIT:
            return f"{self._rest_base}/limit-orders"
        if "AmountInUnits" in payload:
            return f"{self._rest_base}/market-open-orders/by-units"
        return f"{self._rest_base}/market-open-orders/by-amount"

    # ── Report stubs ──────────────────────────────────────────────────────────

    async def generate_order_status_reports(
        self, instrument_id=None, start=None, end=None, open_only: bool = False
    ) -> list:
        """eToro bietet keinen Order-Status-Query-Endpoint.

        Reconciliation erfolgt ausschliesslich über den WS-Stream und lokalen State.
        """
        self._log.warning(
            "generate_order_status_reports: Kein Query-Endpoint verfügbar. "
            "Gebe leere Liste zurück.",
            LogColor.YELLOW,
        )
        return []

    async def generate_trade_reports(
        self, instrument_id=None, venue_order_id=None, start=None, end=None
    ) -> list:
        return []

    async def generate_position_status_reports(
        self, instrument_id=None, start=None, end=None
    ) -> list:
        return []

    async def generate_fill_reports(
        self, instrument_id=None, venue_order_id=None, start=None, end=None
    ) -> list:
        return []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def _connect(self) -> None:
        if self._dry_run:
            self._log.info(
                "⚠️  DRY-RUN MODE: no real orders will be sent.",
                LogColor.YELLOW,
            )

        await self._state.load(warn_fn=self._log.warning)

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_REST_TIMEOUT_S)
        )
        await self._rate_limiter.start()
        await self._connect_ws()

        # FIX #5: Balance via PnL-Endpoint mit offizieller Formel berechnen.
        balance = await self._fetch_account_balance()
        self.generate_account_state(
            balances=[
                AccountBalance(
                    total=balance,
                    locked=Money(0, USD),
                    free=balance,
                )
            ],
            margins=[],
            reported=False,
            ts_event=self._clock.timestamp_ns(),
        )

    async def _disconnect(self) -> None:
        await self._rate_limiter.stop()

        if self._ws_task is not None:
            self._ws_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None

        if self._ws is not None:
            await self._ws.close()
            self._ws = None

        if self._session is not None:
            await self._session.close()
            self._session = None

        self._log.info("EToroExecutionClient disconnected.", LogColor.BLUE)

    # ── Account balance ───────────────────────────────────────────────────────

    async def _fetch_account_balance(self) -> Money:
        """Fetch available cash from PnL endpoint using official eToro formula.

        Available Cash = credit - (Σ ordersForOpen[mirrorID=0].amount
                                   + Σ orders.amount)

        Returns 0 USD in dry-run mode or on any fetch failure.
        """
        if self._dry_run:
            return Money(0, USD)
        try:
            assert self._session is not None
            async with self._session.get(
                self._pnl_base,
                headers=self._make_headers(),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    credit = float(data.get("credits", data.get("credit", 0)))

                    # Only manual open orders (mirrorID == 0) reduce available cash.
                    orders_for_open_amount = sum(
                        float(o.get("amount", 0))
                        for o in data.get("ordersForOpen", [])
                        if o.get("mirrorID", 0) == 0
                    )
                    orders_amount = sum(
                        float(o.get("amount", 0))
                        for o in data.get("orders", [])
                    )
                    available = credit - (orders_for_open_amount + orders_amount)

                    self._log.info(
                        f"Account balance: credit={credit:.2f} USD  "
                        f"available={available:.2f} USD",
                        LogColor.GREEN,
                    )
                    return Money(max(available, 0.0), USD)

                self._log.warning(
                    f"Balance fetch returned HTTP {resp.status}; using 0 USD.",
                    LogColor.YELLOW,
                )
        except Exception as exc:
            self._log.warning(
                f"Balance fetch failed ({exc}); using 0 USD.", LogColor.YELLOW
            )
        return Money(0, USD)

    # ── WebSocket ──────────────────────────────────────────────────────────────

    async def _connect_ws(self) -> None:
        ssl_context = ssl.create_default_context()
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
            delay = min(10 * attempt, 60)
            try:
                self._log.info(
                    f"Execution WS connect attempt "
                    f"{attempt}/{_MAX_CONNECT_ATTEMPTS} ...",
                    LogColor.BLUE,
                )
                self._ws = await asyncio.wait_for(
                    websockets.connect(
                        _WS_URL,
                        ssl=ssl_context,
                        ping_interval=20,
                        ping_timeout=20,
                    ),
                    timeout=_CONNECT_TIMEOUT_S,
                )
                await self._ws_authenticate()
                self._ws_task = self.create_task(
                    self._ws_message_loop(), log_msg="exec_ws_loop"
                )
                self._log.info(
                    "Execution WS connected and authenticated.", LogColor.GREEN
                )
                return
            except Exception as exc:
                last_exc = exc
                self._log.warning(
                    f"Execution WS attempt {attempt}/{_MAX_CONNECT_ATTEMPTS} "
                    f"failed: {exc}",
                    LogColor.YELLOW,
                )
                if attempt < _MAX_CONNECT_ATTEMPTS:
                    self._log.info(f"Retrying in {delay}s ...", LogColor.BLUE)
                    await asyncio.sleep(delay)

        self._log.error(
            f"Execution WS unreachable after {_MAX_CONNECT_ATTEMPTS} attempts "
            f"(last: {last_exc}). Forcing restart via systemd ...",
            LogColor.RED,
        )
        os._exit(1)

    async def _ws_authenticate(self) -> None:
        auth_payload = {
            "id":        str(uuid.uuid4()),
            "operation": "Authenticate",
            "data":      {"userKey": self._user_key, "apiKey": self._api_key},
        }
        await self._ws.send(json.dumps(auth_payload))
        raw = await self._ws.recv()
        try:
            resp = json.loads(raw)
            self._log.info(f"Exec WS auth response: {resp}", LogColor.CYAN)
            if resp.get("status") not in (None, "OK", "ok", 200):
                raise RuntimeError(f"Auth failed: {resp}")
        except json.JSONDecodeError:
            self._log.warning(
                f"Auth response not JSON: {raw!r}", LogColor.YELLOW
            )

        sub_payload = {
            "id":        str(uuid.uuid4()),
            "operation": "Subscribe",
            "data": {
                "topics":   ["trading.notifications", "portfolio.positions"],
                "snapshot": False,
            },
        }
        await self._ws.send(json.dumps(sub_payload))
        self._log.info(
            "Subscribed to trading notification topics.", LogColor.CYAN
        )

    async def _ws_message_loop(self) -> None:
        try:
            async for raw in self._ws:
                if not raw or raw == b"\x00":
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "messages" in data:
                    for msg in data["messages"]:
                        await self._process_ws_message(msg)

            self._log.warning(
                "Execution WS closed by server. Forcing restart ...",
                LogColor.YELLOW,
            )
            os._exit(1)

        except websockets.exceptions.ConnectionClosedOK:
            self._log.warning(
                "Execution WS closed (OK). Forcing restart ...", LogColor.YELLOW
            )
            os._exit(1)
        except websockets.exceptions.ConnectionClosedError as exc:
            self._log.error(
                f"Execution WS error: {exc}. Forcing restart ...", LogColor.RED
            )
            os._exit(1)
        except asyncio.CancelledError:
            self._log.info("Execution WS loop cancelled.", LogColor.BLUE)
            raise
        except Exception as exc:
            self._log.error(
                f"Unexpected execution WS error: {exc}. Forcing restart ...",
                LogColor.RED,
            )
            os._exit(1)

    async def _process_ws_message(self, msg: dict) -> None:
        msg_type = msg.get("type", "")
        try:
            content_raw = msg.get("content", {})
            content: dict = (
                json.loads(content_raw)
                if isinstance(content_raw, str)
                else content_raw
            )
            if not isinstance(content, dict):
                return
        except (json.JSONDecodeError, TypeError):
            return

        position_id = str(
            content.get("positionId") or content.get("PositionId") or ""
        )
        order_id = str(content.get("OrderID") or content.get("orderId") or "")

        # Match incoming message to a known client order via state mapping.
        all_mappings  = self._state.get_all()
        matched_coid: str | None = None
        for coid, stored_id in all_mappings.items():
            # KORREKTUR: stored_id (eToro ID aus dem State) mit position_id ODER order_id vergleichen
            if (position_id and stored_id == position_id) or (
                order_id and stored_id == order_id
            ):
                matched_coid = coid
                break

        if matched_coid is None:
            return

        # KORREKTUR: Wenn die Position ausgeführt wird und eine neue, finale Position-ID bekommt, 
        # müssen wir den State aktualisieren. Ansonsten schlägt das spätere "Close" fehl.
        stored_id = all_mappings[matched_coid]
        if position_id and position_id != stored_id:
            await self._state.set(matched_coid, position_id)
            self._log.info(
                f"State updated: {matched_coid} -> {position_id} (war {stored_id})", 
                LogColor.CYAN
            )

        client_order_id = ClientOrderId(matched_coid)
        order           = self._cache.order(client_order_id)
        if order is None:
            return

        ts = self._clock.timestamp_ns()

        if msg_type in ("Trading.Position.Opened", "position.opened", "OrderFilled"):
            fill_price_raw = (
                content.get("OpenRate")
                or content.get("fillPrice")
                or content.get("Rate")
            )
            if fill_price_raw:
                instrument = self._cache.instrument(order.instrument_id)
                if instrument is None:
                    return
                self.generate_order_filled(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=client_order_id,
                    venue_order_id=VenueOrderId(position_id or matched_coid),
                    venue_position_id=PositionId(position_id or matched_coid),
                    trade_id=TradeId(str(uuid.uuid4())),
                    order_side=order.side,
                    order_type=OrderType.MARKET,
                    last_qty=order.quantity,
                    last_px=Price(
                        float(fill_price_raw),
                        precision=instrument.price_precision,
                    ),
                    quote_currency=USD,
                    commission=Money(0.0, USD),
                    liquidity_side=LiquiditySide.TAKER,
                    ts_event=ts,
                )

        elif msg_type in ("Trading.Order.Accepted", "order.accepted"):
            self.generate_order_accepted(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=client_order_id,
                venue_order_id=VenueOrderId(
                    order_id or position_id or matched_coid
                ),
                ts_event=ts,
            )

        elif msg_type in (
            "Trading.Order.Canceled",
            "order.cancelled",
            "Trading.Position.Closed",
        ):
            await self._state.delete(matched_coid)
            self.generate_order_canceled(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=client_order_id,
                venue_order_id=VenueOrderId(
                    order_id or position_id or matched_coid
                ),
                ts_event=ts,
            )

    # ── Command handlers ──────────────────────────────────────────────────────
    # All handlers are async: Nautilus >= 1.200 wraps them in create_task(),
    # which requires a coroutine. Sync methods return None → TypeError.

    async def _submit_order(self, command: SubmitOrder) -> None:
        await self._submit_order_async(command)

    async def _cancel_order(self, command: CancelOrder) -> None:
        await self._cancel_order_async(command)

    async def _modify_order(self, command: ModifyOrder) -> None:
        self._log.warning(
            f"ModifyOrder not supported by eToro; ignoring "
            f"{command.client_order_id.value}",
            LogColor.YELLOW,
        )

    async def _submit_order_list(self, command: SubmitOrderList) -> None:
        self._log.warning(
            "SubmitOrderList not supported by eToro; ignoring.", LogColor.YELLOW
        )

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        self._log.warning(
            "CancelAllOrders not supported by eToro; ignoring.", LogColor.YELLOW
        )

    async def _batch_cancel_orders(self, command: BatchCancelOrders) -> None:
        self._log.warning(
            "BatchCancelOrders not supported by eToro; ignoring.", LogColor.YELLOW
        )

    async def _query_order(self, command: QueryOrder) -> None:
        pass  # No query endpoint; rely on WS stream.

    # ── Submit order ──────────────────────────────────────────────────────────

    async def _submit_order_async(self, command: SubmitOrder) -> None:
        order = command.order
        ts    = self._clock.timestamp_ns()

        is_close, etoro_position_id = await self._classify_order(order)
        priority = "CLOSE" if is_close else "OPEN"

        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=ts,
        )

        if not await self._rate_limiter.acquire(priority):
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason="rate_limit_exhausted",
                ts_event=self._clock.timestamp_ns(),
            )
            self._log.warning(
                f"Order rejected (rate limit): {order.client_order_id.value}",
                LogColor.YELLOW,
            )
            return

        if self._dry_run:
            await self._handle_dry_run(order, is_close, etoro_position_id)
            return

        await self._send_rest_order(order, is_close, etoro_position_id)

    async def _classify_order(
        self, order: object
    ) -> tuple[bool, str | None]:
        """Determine if an order closes an existing position.

        Returns (is_close, etoro_position_id).
        """
        open_positions = self._cache.positions_open(
            instrument_id=order.instrument_id
        )
        if not open_positions:
            return False, None

        pos = open_positions[0]
        is_close_direction = (
            order.side == OrderSide.SELL and pos.side == PositionSide.LONG
        ) or (order.side == OrderSide.BUY and pos.side == PositionSide.SHORT)
        if not is_close_direction:
            return False, None

        etoro_pos_id = await self._state.get(str(pos.opening_order_id))
        return True, etoro_pos_id

    def _etoro_instrument_id(self, order: object) -> int:
        """Resolve Nautilus InstrumentId → eToro numeric instrument ID."""
        return int(self._instrument_to_etoro.get(str(order.instrument_id), "0"))

    def _build_payload(
        self,
        order: object,
        is_close: bool,
        etoro_position_id: str | None,
    ) -> dict:
        # FIX #3: InstrumentID is required in close payload (laut OpenAPI spec).
        # FIX #4: Schlüssel heisst InstrumentID (capital D), nicht InstrumentId.
        if is_close:
            return {
                "InstrumentID":  self._etoro_instrument_id(order),
                "UnitsToDeduct": None,
            }

        etoro_id = self._etoro_instrument_id(order)
        is_buy   = order.side == OrderSide.BUY

        base: dict = {
            "InstrumentID": etoro_id,   # FIX #4: capital D
            "IsBuy":        is_buy,
            "Leverage":     1,
        }

        if order.order_type == OrderType.LIMIT:
            base["Rate"] = float(order.price)
            if self._enable_trailing_stop:
                base["IsTslEnabled"] = False  # TSL only for market orders
            return base

        qty        = float(order.quantity)
        last_quote = self._cache.quote_tick(order.instrument_id)
        if last_quote is not None:
            px = float(
                last_quote.ask_price if is_buy else last_quote.bid_price
            )
            base["Amount"] = round(qty * px, 2)
        else:
            base["AmountInUnits"] = qty

        if self._enable_trailing_stop:
            base["IsTslEnabled"] = True

        return base

    async def _handle_dry_run(
        self,
        order: object,
        is_close: bool,
        etoro_position_id: str | None,
    ) -> None:
        fake_pos_id = str(
            uuid.uuid5(uuid.NAMESPACE_OID, order.client_order_id.value)
        )
        req_id  = self._order_req_id(order.client_order_id.value)
        payload = self._build_payload(order, is_close, etoro_position_id)
        url     = self._build_url(order, is_close, etoro_position_id, payload)

        self._log.info(
            f"DRY-RUN POST {url} | payload={payload} | x-request-id={req_id}",
            LogColor.CYAN,
        )

        await self._state.set(order.client_order_id.value, fake_pos_id)
        ts         = self._clock.timestamp_ns()
        instrument = self._cache.instrument(order.instrument_id)

        self.generate_order_accepted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId(fake_pos_id),
            ts_event=ts,
        )

        if order.order_type == OrderType.LIMIT:
            return  # Pending limit order — fill arrives via WS.

        last_quote = self._cache.quote_tick(order.instrument_id)
        fill_price = (
            float(
                last_quote.ask_price
                if order.side == OrderSide.BUY
                else last_quote.bid_price
            )
            if last_quote is not None
            else 1.0
        )
        price_precision = (
            instrument.price_precision if instrument is not None else 2
        )

        self.generate_order_filled(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId(fake_pos_id),
            venue_position_id=PositionId(fake_pos_id),
            trade_id=TradeId(str(uuid.uuid4())),
            order_side=order.side,
            order_type=order.order_type,
            last_qty=order.quantity,
            last_px=Price(fill_price, precision=price_precision),
            quote_currency=USD,
            commission=Money(0.0, USD),
            liquidity_side=LiquiditySide.TAKER,
            ts_event=self._clock.timestamp_ns(),
        )
        self._log.info(
            f"DRY-RUN order filled: {order.client_order_id.value} "
            f"@ {fill_price:.5f}",
            LogColor.GREEN,
        )

    async def _send_rest_order(
        self,
        order: object,
        is_close: bool,
        etoro_position_id: str | None,
    ) -> None:
        req_id  = self._order_req_id(order.client_order_id.value)
        payload = self._build_payload(order, is_close, etoro_position_id)
        url     = self._build_url(order, is_close, etoro_position_id, payload)

        # Log every outgoing REST call — critical for debugging API issues.
        self._log.info(
            f"REST POST {url} | payload={payload} | x-request-id={req_id}",
            LogColor.CYAN,
        )

        try:
            assert self._session is not None
            async with self._session.post(
                url,
                json=payload,
                headers=self._make_headers(req_id),
            ) as resp:
                status    = resp.status
                body_text = await resp.text()

                if 200 <= status < 300:
                    try:
                        body: dict = json.loads(body_text)
                    except json.JSONDecodeError:
                        body = {}

                    # FIX #2: API antwortet mit {"orderForOpen": {"orderID": 13902598}}
                    # Die orderID ist die positionId für spätere Close-Calls.
                    order_for_open = body.get("orderForOpen", {})
                    new_pos_id = str(
                        order_for_open.get("orderID")   # PRIMARY — immer vorhanden
                        or body.get("positionId")        # Fallbacks
                        or body.get("PositionId")
                        or body.get("orderId")
                        or body.get("OrderId")
                        or req_id                        # Last resort
                    )

                    await self._state.set(
                        order.client_order_id.value, new_pos_id
                    )
                    self.generate_order_accepted(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=order.client_order_id,
                        venue_order_id=VenueOrderId(new_pos_id),
                        ts_event=self._clock.timestamp_ns(),
                    )
                    self._log.info(
                        f"Order accepted: {order.client_order_id.value} "
                        f"→ orderID {new_pos_id}",
                        LogColor.GREEN,
                    )

                elif status in (502, 504):
                    self._log.warning(
                        f"Gateway timeout ({status}) for "
                        f"{order.client_order_id.value}; reconciling via PnL.",
                        LogColor.YELLOW,
                    )
                    await self._reconcile_via_pnl(order, req_id)

                elif status == 404 and is_close:
                    await self._state.delete(order.client_order_id.value)
                    self.generate_order_canceled(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=order.client_order_id,
                        venue_order_id=VenueOrderId(
                            etoro_position_id or "unknown"
                        ),
                        ts_event=self._clock.timestamp_ns(),
                    )
                    self._log.warning(
                        f"Close 404: position {etoro_position_id} "
                        f"already gone (SL/TP triggered?)",
                        LogColor.YELLOW,
                    )

                else:
                    reason = f"etoro_{status}: {body_text[:200]}"
                    self.generate_order_rejected(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=order.client_order_id,
                        reason=reason,
                        ts_event=self._clock.timestamp_ns(),
                    )
                    self._log.error(
                        f"Order rejected ({status}): "
                        f"{order.client_order_id.value} | {body_text[:200]}",
                        LogColor.RED,
                    )

        except asyncio.TimeoutError:
            self._log.warning(
                f"REST timeout for {order.client_order_id.value}; "
                f"reconciling via PnL.",
                LogColor.YELLOW,
            )
            await self._reconcile_via_pnl(order, req_id)

        except Exception as exc:
            self._log.error(
                f"REST send error for {order.client_order_id.value}: {exc}",
                LogColor.RED,
            )
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=f"send_error: {exc}",
                ts_event=self._clock.timestamp_ns(),
            )

    async def _reconcile_via_pnl(
        self, order: object, req_id: str
    ) -> None:
        """Check PnL endpoint after a timeout or gateway error.

        Scannt ordersForOpen nach einer Order die per token dem req_id entspricht.
        Falls gefunden: accepted + state gesetzt. Sonst: rejected.
        """
        try:
            assert self._session is not None
            async with self._session.get(
                self._pnl_base,
                headers=self._make_headers(),
            ) as resp:
                if resp.status == 200:
                    data      = await resp.json()
                    # Suche in offenen Positionen und pending orders
                    candidates = (
                        data.get("positions", [])
                        + data.get("ordersForOpen", [])
                    )
                    for item in candidates:
                        # token aus dem open-response entspricht x-request-id
                        if str(item.get("token") or item.get("requestId") or "") == req_id:
                            item_id = str(
                                item.get("positionID")
                                or item.get("positionId")
                                or item.get("orderID")
                                or req_id
                            )
                            await self._state.set(
                                order.client_order_id.value, item_id
                            )
                            self.generate_order_accepted(
                                strategy_id=order.strategy_id,
                                instrument_id=order.instrument_id,
                                client_order_id=order.client_order_id,
                                venue_order_id=VenueOrderId(item_id),
                                ts_event=self._clock.timestamp_ns(),
                            )
                            self._log.info(
                                f"Reconciled via PnL: "
                                f"{order.client_order_id.value} → {item_id}",
                                LogColor.GREEN,
                            )
                            return
        except Exception as exc:
            self._log.error(
                f"PnL reconciliation failed: {exc}", LogColor.RED
            )

        self.generate_order_rejected(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            reason="upstream_timeout_no_position",
            ts_event=self._clock.timestamp_ns(),
        )

    # ── Cancel order ──────────────────────────────────────────────────────────

    async def _cancel_order_async(self, command: CancelOrder) -> None:
        coid  = command.client_order_id.value
        order = self._cache.order(command.client_order_id)
        if order is None:
            return

        pos_id = await self._state.get(coid)
        ts     = self._clock.timestamp_ns()

        if pos_id is None:
            self.generate_order_canceled(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=VenueOrderId("unknown"),
                ts_event=ts,
            )
            return

        if self._dry_run:
            await self._state.delete(coid)
            self.generate_order_canceled(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=VenueOrderId(pos_id),
                ts_event=ts,
            )
            return

        await self._rate_limiter.acquire("CLOSE")

        req_id   = self._order_req_id(coid)
        url      = f"{self._rest_base}/market-close-orders/positions/{pos_id}"
        # FIX #3/#4: InstrumentID required und capital D
        etoro_id = int(
            self._instrument_to_etoro.get(str(command.instrument_id), "0")
        )
        payload  = {"InstrumentID": etoro_id, "UnitsToDeduct": None}

        self._log.info(
            f"REST POST (cancel) {url} | payload={payload} | x-request-id={req_id}",
            LogColor.CYAN,
        )

        try:
            assert self._session is not None
            async with self._session.post(
                url,
                json=payload,
                headers=self._make_headers(req_id),
            ) as resp:
                status = resp.status

                if status == 404:
                    # Position already closed (SL/TP) — treat as canceled.
                    self._log.warning(
                        f"Cancel 404: position {pos_id} already gone.",
                        LogColor.YELLOW,
                    )
                elif status not in range(200, 300):
                    body = await resp.text()
                    self._log.error(
                        f"Cancel failed ({status}) for {coid}: {body[:200]}",
                        LogColor.RED,
                    )
                    return  # Don't update state or fire event on hard failure.

            await self._state.delete(coid)
            self.generate_order_canceled(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=VenueOrderId(pos_id),
                ts_event=self._clock.timestamp_ns(),
            )

        except Exception as exc:
            self._log.error(
                f"Cancel REST failed for {coid}: {exc}", LogColor.RED
            )