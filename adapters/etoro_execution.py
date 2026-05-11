"""eToro LiveExecutionClient for Nautilus Trader.

Implements full order execution: REST submission, WebSocket fill stream,
token-bucket rate limiting, atomic state persistence, and dry-run mode.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
from nautilus_trader.live.config import LiveExecClientConfig
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.live.factories import LiveExecClientFactory
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
    ClientId,
    ClientOrderId,
    PositionId,
    TradeId,
    VenueOrderId,
    Venue,
    AccountId,
)
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from adapters.instrument_map import ETORO_INSTRUMENTS

# ── Constants ──────────────────────────────────────────────────────────────────

_MAX_CONNECT_ATTEMPTS = 5
_CONNECT_TIMEOUT_S = 30
_REST_TIMEOUT_S = 10
_RATE_LIMIT_CAPACITY = 20
_RATE_LIMIT_REFILL_INTERVAL = 3.0  # seconds per token

_REST_BASE: dict[str, str] = {
    "demo": "https://public-api.etoro.com/api/v1/trading/execution/demo",
    "real": "https://public-api.etoro.com/api/v1/trading/execution/real",
}
_PNL_BASE: dict[str, str] = {
    "demo": "https://public-api.etoro.com/api/v1/trading/execution/demo/pnl",
    "real": "https://public-api.etoro.com/api/v1/trading/execution/real/pnl",
}
_WS_URL = "wss://ws.etoro.com/ws"


# ── Rate Limiter ───────────────────────────────────────────────────────────────

class _RateLimiter:
    """Async token bucket: 20 cap, 1 token / 3 s.

    CLOSE requests queue and wait; OPEN requests fail-fast if no token.
    """

    def __init__(
        self,
        capacity: int = _RATE_LIMIT_CAPACITY,
        refill_interval: float = _RATE_LIMIT_REFILL_INTERVAL,
    ) -> None:
        self._capacity = capacity
        self._tokens = capacity
        self._refill_interval = refill_interval
        self._lock = asyncio.Lock()
        self._close_queue: asyncio.PriorityQueue[tuple[int, int, asyncio.Future[bool]]] = (
            asyncio.PriorityQueue()
        )
        self._seq = 0
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.ensure_future(self._refill_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _refill_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refill_interval)
            await self._tick()

    async def _tick(self) -> None:
        """One refill cycle: add a token if below capacity, drain CLOSE queue."""
        futures_to_resolve: list[asyncio.Future[bool]] = []
        async with self._lock:
            if self._tokens < self._capacity:
                self._tokens += 1
            while not self._close_queue.empty() and self._tokens > 0:
                _, _, future = self._close_queue.get_nowait()
                if not future.done():
                    self._tokens -= 1
                    futures_to_resolve.append(future)
        for future in futures_to_resolve:
            if not future.done():
                future.set_result(True)

    async def acquire(self, priority: str) -> bool:
        """Acquire a token.

        Returns True when the token is granted.
        For CLOSE: queues and awaits until granted (never dropped).
        For OPEN / LIMIT: returns False immediately when capacity is 0.
        """
        if priority == "CLOSE":
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bool] = loop.create_future()
            async with self._lock:
                if self._tokens > 0:
                    self._tokens -= 1
                    return True
                self._seq += 1
                self._close_queue.put_nowait((0, self._seq, future))
            return await future
        else:
            async with self._lock:
                if self._tokens > 0:
                    self._tokens -= 1
                    return True
            return False

    @property
    def tokens(self) -> int:
        return self._tokens


# ── State Manager ─────────────────────────────────────────────────────────────

class _StateManager:
    """Atomic-write JSON persistence for ClientOrderId → eToro positionId mapping."""

    def __init__(self, state_path: str) -> None:
        self._path = Path(state_path)
        self._mapping: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def load(self, warn_fn: object = None) -> None:
        """Load mapping from disk; start with empty dict on any failure."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                data = self._path.read_text(encoding="utf-8")
                loaded = json.loads(data)
                if isinstance(loaded, dict):
                    self._mapping = {str(k): str(v) for k, v in loaded.items()}
                else:
                    self._mapping = {}
            except Exception as exc:
                if warn_fn is not None:
                    warn_fn(
                        f"State load failed ({exc}); starting with empty mapping.",
                        LogColor.YELLOW,
                    )
                self._mapping = {}
        else:
            self._mapping = {}

    async def get(self, client_order_id: str) -> str | None:
        async with self._lock:
            return self._mapping.get(client_order_id)

    async def set(self, client_order_id: str, position_id: str) -> None:
        async with self._lock:
            self._mapping[client_order_id] = position_id
            await self._persist()

    async def delete(self, client_order_id: str) -> None:
        async with self._lock:
            self._mapping.pop(client_order_id, None)
            await self._persist()

    async def _persist(self) -> None:
        tmp = str(self._path) + ".tmp"
        data = json.dumps(self._mapping, indent=2)
        Path(tmp).write_text(data, encoding="utf-8")
        os.replace(tmp, str(self._path))

    def get_all(self) -> dict[str, str]:
        return dict(self._mapping)


# ── Config & Factory ──────────────────────────────────────────────────────────

class EToroExecClientConfig(LiveExecClientConfig, frozen=True, kw_only=True):
    api_key: str
    user_key: str
    environment: Literal["demo", "real"] = "demo"
    dry_run: bool = True
    state_path: str = "data/state/execution_mapping.json"
    enable_trailing_stop: bool = True


class EToroLiveExecClientFactory(LiveExecClientFactory):
    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: EToroExecClientConfig,
        msgbus: object,
        cache: object,
        clock: object,
        **kwargs: object,
    ) -> "EToroExecutionClient":
        return EToroExecutionClient(
            loop=loop,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=InstrumentProvider(),
            api_key=config.api_key,
            user_key=config.user_key,
            environment=config.environment,
            dry_run=config.dry_run,
            state_path=config.state_path,
            enable_trailing_stop=config.enable_trailing_stop,
        )


# ── Execution Client ───────────────────────────────────────────────────────────

class EToroExecutionClient(LiveExecutionClient):
    """Live execution client for eToro broker."""

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
            account_id=AccountId("ETORO-001"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            instrument_provider=instrument_provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )
        self._api_key = api_key
        self._user_key = user_key
        self._environment = environment
        self._dry_run = dry_run
        self._enable_trailing_stop = enable_trailing_stop
        self._rest_base = _REST_BASE[environment]
        self._pnl_base = _PNL_BASE[environment]

        # Reverse lookup: Nautilus InstrumentId string → eToro numeric id string
        self._instrument_to_etoro: dict[str, str] = {
            v: k for k, v in ETORO_INSTRUMENTS.items()
        }

        self._rate_limiter = _RateLimiter()
        self._state = _StateManager(state_path)
        self._session: aiohttp.ClientSession | None = None
        self._ws: object | None = None
        self._ws_task: asyncio.Task[None] | None = None

    async def generate_order_status_reports(
        self,
        instrument_id=None,
        start=None,
        end=None,
        open_only: bool = False,
    ) -> list:
        """
        eToro bietet keinen sauberen Order-Status-Query-Endpoint.
        Reconciliation erfolgt ausschliesslich über den WS-Stream und lokalen State.
        """
        self._log.warning(
            "generate_order_status_reports: Kein Query-Endpoint verfügbar. "
            "Gebe leere Liste zurück.",
            LogColor.YELLOW,
        )
        return []

    async def generate_trade_reports(
        self,
        instrument_id=None,
        venue_order_id=None,
        start=None,
        end=None,
    ) -> list:
        return []

    async def generate_position_status_reports(
        self,
        instrument_id=None,
        start=None,
        end=None,
    ) -> list:
        return []
    
    async def generate_fill_reports(
        self,
        instrument_id=None,
        venue_order_id=None,
        start=None,
        end=None,
    ) -> list:
        return []
    
    async def generate_mass_status(self, *args, **kwargs):
        """
        Workaround für Nautilus 1.226.0: Das Rust-Backend crasht, 
        wenn account_id = None ist. Wir fangen den Status ab und patchen ihn.
        """
        from nautilus_trader.execution.messages import ExecutionMassStatus
        from nautilus_trader.model.identifiers import AccountId
        
        # 1. Originale Nautilus-Logik laufen lassen (sammelt deine leeren Listen)
        res = await super().generate_mass_status(*args, **kwargs)
        
        # 2. Den Report mit einer gültigen AccountId neu zusammensetzen
        return ExecutionMassStatus(
            client_id=res.client_id,
            venue=res.venue,
            account_id=AccountId("ETORO_ACC"), # HIER fixen wir den Bug!
            order_status_reports=res.order_status_reports,
            trade_reports=res.trade_reports,
            position_status_reports=res.position_status_reports,
            fill_reports=res.fill_reports,
            ts_event=res.ts_event,
            ts_init=res.ts_init,
        )
    
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
    
    # ── WebSocket ──────────────────────────────────────────────────────────────

    async def _connect_ws(self) -> None:
        ssl_context = ssl.create_default_context()
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
            delay = min(10 * attempt, 60)
            try:
                self._log.info(
                    f"Execution WS connect attempt {attempt}/{_MAX_CONNECT_ATTEMPTS} ...",
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
                    f"Execution WS connect attempt {attempt}/{_MAX_CONNECT_ATTEMPTS} failed: {exc}",
                    LogColor.YELLOW,
                )
                if attempt < _MAX_CONNECT_ATTEMPTS:
                    self._log.info(
                        f"Retrying in {delay}s ...", LogColor.BLUE
                    )
                    await asyncio.sleep(delay)

        self._log.error(
            f"Execution WS unreachable after {_MAX_CONNECT_ATTEMPTS} attempts "
            f"(last: {last_exc}). Forcing restart via systemd ...",
            LogColor.RED,
        )
        os._exit(1)

    async def _ws_authenticate(self) -> None:
        auth_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Authenticate",
            "data": {"userKey": self._user_key, "apiKey": self._api_key},
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
            "id": str(uuid.uuid4()),
            "operation": "Subscribe",
            "data": {
                "topics": ["trading.notifications", "portfolio.positions"],
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
            content.get("positionId")
            or content.get("PositionId")
            or ""
        )
        order_id = str(
            content.get("OrderID") or content.get("orderId") or ""
        )

        all_mappings = self._state.get_all()
        matched_coid: str | None = None
        for coid, pos_id in all_mappings.items():
            if (position_id and pos_id == position_id) or (
                order_id and coid == order_id
            ):
                matched_coid = coid
                break

        if matched_coid is None:
            return

        client_order_id = ClientOrderId(matched_coid)
        order = self._cache.order(client_order_id)
        if order is None:
            return

        ts = self._clock.timestamp_ns()

        if msg_type in (
            "Trading.Position.Opened",
            "position.opened",
            "OrderFilled",
        ):
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
                venue_order_id=VenueOrderId(order_id or position_id or matched_coid),
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
                venue_order_id=VenueOrderId(order_id or position_id or matched_coid),
                ts_event=ts,
            )

    # ── Command handlers (sync → schedule async task) ─────────────────────────

    def _submit_order(self, command: SubmitOrder) -> None:
        self.create_task(
            self._submit_order_async(command),
            log_msg=f"submit_{command.order.client_order_id.value}",
        )

    def _cancel_order(self, command: CancelOrder) -> None:
        self.create_task(
            self._cancel_order_async(command),
            log_msg=f"cancel_{command.client_order_id.value}",
        )

    def _modify_order(self, command: ModifyOrder) -> None:
        self._log.warning(
            f"ModifyOrder not supported by eToro; ignoring {command.client_order_id.value}",
            LogColor.YELLOW,
        )

    def _submit_order_list(self, command: SubmitOrderList) -> None:
        self._log.warning(
            "SubmitOrderList not supported by eToro; ignoring.", LogColor.YELLOW
        )

    def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        self._log.warning(
            "CancelAllOrders not supported by eToro; ignoring.", LogColor.YELLOW
        )

    def _batch_cancel_orders(self, command: BatchCancelOrders) -> None:
        self._log.warning(
            "BatchCancelOrders not supported by eToro; ignoring.", LogColor.YELLOW
        )

    def _query_order(self, command: QueryOrder) -> None:
        pass  # No query endpoint; rely on WS stream

    # ── Submit order (async) ──────────────────────────────────────────────────

    async def _submit_order_async(self, command: SubmitOrder) -> None:
        order = command.order
        ts = self._clock.timestamp_ns()

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
        """Determine whether an order is a close (and return the eToro positionId)."""
        open_positions = self._cache.positions_open(
            instrument_id=order.instrument_id
        )
        if not open_positions:
            return False, None

        pos = open_positions[0]
        is_close_direction = (
            order.side == OrderSide.SELL and pos.side == PositionSide.LONG
        ) or (
            order.side == OrderSide.BUY and pos.side == PositionSide.SHORT
        )
        if not is_close_direction:
            return False, None

        coid_str = str(pos.opening_order_id)
        etoro_pos_id = await self._state.get(coid_str)
        return True, etoro_pos_id

    async def _handle_dry_run(
        self,
        order: object,
        is_close: bool,
        etoro_position_id: str | None,
    ) -> None:
        fake_pos_id = str(
            uuid.uuid5(uuid.NAMESPACE_OID, order.client_order_id.value)
        )
        req_id = hashlib.md5(
            order.client_order_id.value.encode("utf-8")
        ).hexdigest()
        payload = self._build_payload(order, is_close, etoro_position_id)

        if is_close and etoro_position_id:
            url = f"{self._rest_base}/market-close-orders/positions/{etoro_position_id}"
        elif order.order_type == OrderType.LIMIT:
            url = f"{self._rest_base}/limit-orders"
        elif "AmountInUnits" in payload:
            url = f"{self._rest_base}/market-open-orders/by-units"
        else:
            url = f"{self._rest_base}/market-open-orders/by-amount"

        self._log.info(
            f"DRY-RUN POST {url} | payload={payload} | x-request-id={req_id}",
            LogColor.CYAN,
        )

        await self._state.set(order.client_order_id.value, fake_pos_id)
        ts = self._clock.timestamp_ns()
        instrument = self._cache.instrument(order.instrument_id)

        self.generate_order_accepted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId(fake_pos_id),
            ts_event=ts,
        )

        if order.order_type == OrderType.LIMIT:
            return  # Pending order; wait for fill via WS

        last_quote = self._cache.quote_tick(order.instrument_id)
        if last_quote is not None:
            fill_price = (
                float(last_quote.ask_price)
                if order.side == OrderSide.BUY
                else float(last_quote.bid_price)
            )
        else:
            fill_price = 1.0

        price_precision = instrument.price_precision if instrument is not None else 2

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
            f"DRY-RUN order filled: {order.client_order_id.value} @ {fill_price:.5f}",
            LogColor.GREEN,
        )

    def _build_payload(
        self,
        order: object,
        is_close: bool,
        etoro_position_id: str | None,
    ) -> dict:
        if is_close:
            return {"UnitsToDeduct": None}

        instr_str = str(order.instrument_id)
        etoro_id = int(self._instrument_to_etoro.get(instr_str, "0"))
        is_buy = order.side == OrderSide.BUY

        base: dict = {
            "InstrumentId": etoro_id,
            "IsBuy": is_buy,
            "Leverage": 1,
        }

        if order.order_type == OrderType.LIMIT:
            base["Rate"] = float(order.price)
            if self._enable_trailing_stop:
                base["IsTslEnabled"] = False  # TSL only for market orders
            return base

        qty = float(order.quantity)
        last_quote = self._cache.quote_tick(order.instrument_id)
        if last_quote is not None:
            px = float(last_quote.ask_price if is_buy else last_quote.bid_price)
            base["Amount"] = round(qty * px, 2)
        else:
            base["AmountInUnits"] = qty

        if self._enable_trailing_stop:
            base["IsTslEnabled"] = True

        return base

    async def _send_rest_order(
        self,
        order: object,
        is_close: bool,
        etoro_position_id: str | None,
    ) -> None:
        req_id = hashlib.md5(
            order.client_order_id.value.encode("utf-8")
        ).hexdigest()
        headers = {
            "x-api-key": self._api_key,
            "x-user-key": self._user_key,
            "x-request-id": req_id,
            "Content-Type": "application/json",
        }
        payload = self._build_payload(order, is_close, etoro_position_id)

        if is_close and etoro_position_id:
            url = f"{self._rest_base}/market-close-orders/positions/{etoro_position_id}"
        elif order.order_type == OrderType.LIMIT:
            url = f"{self._rest_base}/limit-orders"
        elif "AmountInUnits" in payload:
            url = f"{self._rest_base}/market-open-orders/by-units"
        else:
            url = f"{self._rest_base}/market-open-orders/by-amount"

        try:
            assert self._session is not None
            async with self._session.post(
                url, json=payload, headers=headers
            ) as resp:
                status = resp.status
                body_text = await resp.text()

                if 200 <= status < 300:
                    try:
                        body: dict = json.loads(body_text)
                    except json.JSONDecodeError:
                        body = {}
                    new_pos_id = str(
                        body.get("positionId")
                        or body.get("PositionId")
                        or body.get("orderId")
                        or body.get("OrderId")
                        or req_id
                    )
                    await self._state.set(order.client_order_id.value, new_pos_id)
                    self.generate_order_accepted(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=order.client_order_id,
                        venue_order_id=VenueOrderId(new_pos_id),
                        ts_event=self._clock.timestamp_ns(),
                    )
                    self._log.info(
                        f"Order accepted: {order.client_order_id.value} → pos {new_pos_id}",
                        LogColor.GREEN,
                    )

                elif status in (502, 504):
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
                        f"Close 404: position {etoro_position_id} already gone (SL/TP?)",
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
                        f"Order rejected ({status}): {order.client_order_id.value} | {body_text[:200]}",
                        LogColor.RED,
                    )

        except asyncio.TimeoutError:
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
        """Check PnL endpoint after timeout to determine if order was placed."""
        try:
            assert self._session is not None
            headers = {
                "x-api-key": self._api_key,
                "x-user-key": self._user_key,
                "x-request-id": str(uuid.uuid4()),
            }
            async with self._session.get(
                self._pnl_base, headers=headers
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    positions = (
                        body
                        if isinstance(body, list)
                        else body.get("positions", [])
                    )
                    for pos in positions:
                        if str(pos.get("requestId")) == req_id:
                            pos_id = str(
                                pos.get("positionId") or req_id
                            )
                            await self._state.set(
                                order.client_order_id.value, pos_id
                            )
                            self.generate_order_accepted(
                                strategy_id=order.strategy_id,
                                instrument_id=order.instrument_id,
                                client_order_id=order.client_order_id,
                                venue_order_id=VenueOrderId(pos_id),
                                ts_event=self._clock.timestamp_ns(),
                            )
                            self._log.info(
                                f"Reconciled via PnL: {order.client_order_id.value} → {pos_id}",
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

    # ── Cancel order (async) ──────────────────────────────────────────────────

    async def _cancel_order_async(self, command: CancelOrder) -> None:
        coid = command.client_order_id.value
        order = self._cache.order(command.client_order_id)
        if order is None:
            return

        pos_id = await self._state.get(coid)
        ts = self._clock.timestamp_ns()

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

        headers = {
            "x-api-key": self._api_key,
            "x-user-key": self._user_key,
            "x-request-id": hashlib.md5(coid.encode("utf-8")).hexdigest(),
            "Content-Type": "application/json",
        }
        url = f"{self._rest_base}/market-close-orders/positions/{pos_id}"

        try:
            assert self._session is not None
            async with self._session.post(
                url, json={"UnitsToDeduct": None}, headers=headers
            ) as _resp:
                pass
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
