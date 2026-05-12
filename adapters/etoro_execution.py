"""eToro LiveExecutionClient for Nautilus Trader.

Beinhaltet REST-Order-Submission, WebSocket Fill-Streaming,
Rate-Limiting, State-Persistenz und Dry-Run-Unterstützung.
"""

from __future__ import annotations

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
    BatchCancelOrders, CancelAllOrders, CancelOrder,
    ModifyOrder, QueryOrder, SubmitOrder, SubmitOrderList
)
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import (
    AccountType, LiquiditySide, OmsType, OrderSide, OrderType, PositionSide
)
from nautilus_trader.model.identifiers import (
    AccountId, ClientId, ClientOrderId, PositionId, TradeId, Venue, VenueOrderId
)
from nautilus_trader.model.objects import AccountBalance, Money, Price

from adapters.etoro_config import EToroExecClientConfig, EToroLiveExecClientFactory
from adapters.etoro_rate_limiter import _RateLimiter
from adapters.etoro_state_manager import _StateManager
from adapters.instrument_map import ETORO_INSTRUMENTS

# ── Konfiguration & Konstanten ────────────────────────────────────────────────

_MAX_CONNECT_ATTEMPTS = 5
_CONNECT_TIMEOUT_S    = 30
_REST_TIMEOUT_S       = 10

_REST_BASE: dict[str, str] = {
    "demo": "https://public-api.etoro.com/api/v1/trading/execution/demo",
    "real": "https://public-api.etoro.com/api/v1/trading/execution", # FIX: Kein /real im Live-Handel
}

_PNL_BASE: dict[str, str] = {
    "demo": "https://public-api.etoro.com/api/v1/trading/info/demo/pnl",
    "real": "https://public-api.etoro.com/api/v1/trading/info/real/pnl",
}

_WS_URL = "wss://ws.etoro.com/ws"


# ── Execution Client ──────────────────────────────────────────────────────────

class EToroExecutionClient(LiveExecutionClient):
    
    def __init__(
        self, loop: asyncio.AbstractEventLoop, msgbus: object, cache: object, clock: object,
        instrument_provider: InstrumentProvider, api_key: str, user_key: str,
        environment: Literal["demo", "real"], dry_run: bool, state_path: str, enable_trailing_stop: bool
    ) -> None:
        super().__init__(
            loop=loop, client_id=ClientId("ETORO"), venue=Venue("ETORO"),
            oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
            base_currency=USD, instrument_provider=instrument_provider,
            msgbus=msgbus, cache=cache, clock=clock,
        )

        self._set_account_id(AccountId(f"ETORO-{environment.upper()}-001"))
        self._api_key              = api_key
        self._user_key             = user_key
        self._dry_run              = dry_run
        self._enable_trailing_stop = enable_trailing_stop
        self._rest_base            = _REST_BASE[environment]
        self._pnl_base             = _PNL_BASE[environment]

        self._instrument_to_etoro = {v: k for k, v in ETORO_INSTRUMENTS.items()}
        self._rate_limiter        = _RateLimiter()
        self._state               = _StateManager(state_path)
        self._session: aiohttp.ClientSession | None = None
        self._ws: object | None                     = None
        self._ws_task: asyncio.Task | None          = None

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    def _make_headers(self, req_id: str | None = None) -> dict[str, str]:
        return {
            "x-api-key":    self._api_key,
            "x-user-key":   self._user_key,
            "x-request-id": req_id or str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _order_req_id(client_order_id_value: str) -> str:
        """Deterministische Request-ID zur Deduplizierung."""
        return str(uuid.uuid5(uuid.NAMESPACE_OID, client_order_id_value))

    # ── Report Stubs ──────────────────────────────────────────────────────────

    async def generate_order_status_reports(self, *args, **kwargs) -> list: return []
    async def generate_trade_reports(self, *args, **kwargs) -> list: return []
    async def generate_position_status_reports(self, *args, **kwargs) -> list: return []
    async def generate_fill_reports(self, *args, **kwargs) -> list: return []

    # ── Lifecycle & Balance ───────────────────────────────────────────────────

    async def _connect(self) -> None:
        if self._dry_run:
            self._log.info("⚠️  DRY-RUN MODE: no real orders will be sent.", LogColor.YELLOW)

        await self._state.load(warn_fn=self._log.warning)
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_REST_TIMEOUT_S))
        await self._rate_limiter.start()
        await self._connect_ws()

        balance = await self._fetch_account_balance()
        self.generate_account_state(
            balances=[AccountBalance(total=balance, locked=Money(0, USD), free=balance)],
            margins=[], reported=False, ts_event=self._clock.timestamp_ns(),
        )

    async def _disconnect(self) -> None:
        await self._rate_limiter.stop()
        if self._ws_task:
            self._ws_task.cancel()
            with suppress(asyncio.CancelledError): await self._ws_task
        if self._ws: await self._ws.close()
        if self._session: await self._session.close()
        self._log.info("EToroExecutionClient disconnected.", LogColor.BLUE)

    async def _fetch_account_balance(self) -> Money:
        """Holt das verfügbare Cash aus dem PnL Endpoint."""
        if self._dry_run: return Money(0, USD)
        try:
            async with self._session.get(self._pnl_base, headers=self._make_headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    credit = float(data.get("credits", data.get("credit", 0)))
                    pending_amount = sum(float(o.get("amount", 0)) for o in data.get("ordersForOpen", []) if o.get("mirrorID", 0) == 0)
                    open_amount = sum(float(o.get("amount", 0)) for o in data.get("orders", []))
                    available = credit - (pending_amount + open_amount)
                    return Money(max(available, 0.0), USD)
        except Exception as exc:
            self._log.warning(f"Balance fetch failed: {exc}", LogColor.YELLOW)
        return Money(0, USD)

    # ── WebSocket Handler ─────────────────────────────────────────────────────

    async def _connect_ws(self) -> None:
        for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
            try:
                self._ws = await asyncio.wait_for(
                    websockets.connect(_WS_URL, ssl=ssl.create_default_context(), ping_interval=20),
                    timeout=_CONNECT_TIMEOUT_S
                )
                await self._ws.send(json.dumps({
                    "id": str(uuid.uuid4()), "operation": "Authenticate",
                    "data": {"userKey": self._user_key, "apiKey": self._api_key}
                }))
                await self._ws.recv() # Wait for Auth ack
                
                await self._ws.send(json.dumps({
                    "id": str(uuid.uuid4()), "operation": "Subscribe",
                    "data": {"topics": ["trading.notifications", "portfolio.positions"], "snapshot": False}
                }))
                
                self._ws_task = self.create_task(self._ws_message_loop(), log_msg="exec_ws_loop")
                self._log.info("Execution WS connected and authenticated.", LogColor.GREEN)
                return
            except Exception as exc:
                self._log.warning(f"WS connect failed ({attempt}/{_MAX_CONNECT_ATTEMPTS}): {exc}", LogColor.YELLOW)
                await asyncio.sleep(min(10 * attempt, 60))
        os._exit(1)

    async def _ws_message_loop(self) -> None:
        try:
            async for raw in self._ws:
                if not raw or raw == b"\x00": continue
                try: data = json.loads(raw)
                except json.JSONDecodeError: continue
                
                # Tolerantes Parsing für verschiedene eToro-JSON Strukturen
                if isinstance(data, dict):
                    if "messages" in data and isinstance(data["messages"], list):
                        for msg in data["messages"]:
                            if isinstance(msg, dict): await self._process_ws_message(msg)
                    elif "type" in data and "content" in data:
                        await self._process_ws_message(data)
                elif isinstance(data, list):
                    for msg in data:
                        if isinstance(msg, dict): await self._process_ws_message(msg)
        except Exception as exc:
            self._log.error(f"WS error or closure: {exc}. Forcing restart.", LogColor.RED)
            os._exit(1)

    async def _process_ws_message(self, msg: dict) -> None:
        msg_type = msg.get("type", "")
        m_type = msg_type.lower()
        
        content = msg.get("content", {})
        if isinstance(content, str):
            with suppress(json.JSONDecodeError): content = json.loads(content)
        if not isinstance(content, dict): return

        # Optionales WS-Logging zur Fehlersuche
        if "trading" in m_type or "order" in m_type or "position" in m_type:
            self._log.info(f"WS Recv [{msg_type}]: {content}", LogColor.CYAN)

        # Case-Insensitive Extrahierung
        c_lower = {str(k).lower(): v for k, v in content.items()}
        pos_id  = str(c_lower.get("positionid", ""))
        ord_id  = str(c_lower.get("orderid", ""))
        token   = str(c_lower.get("token", "") or c_lower.get("requestid", ""))

        if not pos_id and not ord_id and not token: return

        # 1. Zuordnung zu einer Nautilus ClientOrderId
        all_mappings = self._state.get_all()
        matched_coid: str | None = None
        
        # Prio 1: Eindeutiger Request-Token (perfekt für Race Conditions)
        for coid, stored_id in all_mappings.items():
            req_id = self._order_req_id(coid)
            if token and token == req_id:
                matched_coid = coid
                break
                
        # Prio 2: Mapping über PositionId/OrderId (Rückwärts, damit Close-Orders vor Open-Orders matchen)
        if not matched_coid:
            for coid, stored_id in reversed(list(all_mappings.items())):
                if (pos_id and stored_id == pos_id) or (ord_id and stored_id == ord_id):
                    matched_coid = coid
                    break
                    
        if not matched_coid: return

        # 2. State-Synchronisierung (Aktualisierung von temporärer OrderID auf finale PositionID)
        stored_id = all_mappings[matched_coid]
        if pos_id and pos_id != stored_id and len(pos_id) > 0:
            await self._state.set(matched_coid, pos_id)

        client_order_id = ClientOrderId(matched_coid)
        order = self._cache.order(client_order_id)
        if not order: return

        ts = self._clock.timestamp_ns()

        # 3. Nautilus Events feuern
        if m_type in ("trading.position.opened", "position.opened", "orderfilled", "trading.order.filled", "trading.position.closed", "position.closed"):
            fill_px = c_lower.get("openrate") or c_lower.get("closerate") or c_lower.get("fillprice") or c_lower.get("executionprice") or c_lower.get("rate")
            if fill_px and (instr := self._cache.instrument(order.instrument_id)):
                self.generate_order_filled(
                    strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=client_order_id,
                    venue_order_id=VenueOrderId(pos_id or ord_id or matched_coid), venue_position_id=PositionId(pos_id or ord_id or matched_coid),
                    trade_id=TradeId(str(uuid.uuid4())), order_side=order.side, order_type=OrderType.MARKET,
                    last_qty=order.quantity, last_px=Price(float(fill_px), precision=instr.price_precision),
                    quote_currency=USD, commission=Money(0.0, USD), liquidity_side=LiquiditySide.TAKER, ts_event=ts,
                )
        elif m_type in ("trading.order.accepted", "order.accepted"):
            self.generate_order_accepted(
                strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=client_order_id,
                venue_order_id=VenueOrderId(ord_id or pos_id or matched_coid), ts_event=ts,
            )
        elif m_type in ("trading.order.canceled", "order.cancelled"):
            await self._state.delete(matched_coid)
            self.generate_order_canceled(
                strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=client_order_id,
                venue_order_id=VenueOrderId(pos_id or ord_id or matched_coid), ts_event=ts,
            )

    # ── Command Handlers ──────────────────────────────────────────────────────

    async def _submit_order(self, command: SubmitOrder) -> None: await self._submit_order_async(command)
    async def _cancel_order(self, command: CancelOrder) -> None: await self._cancel_order_async(command)
    async def _modify_order(self, c: ModifyOrder) -> None: pass
    async def _submit_order_list(self, c: SubmitOrderList) -> None: pass
    async def _cancel_all_orders(self, c: CancelAllOrders) -> None: pass
    async def _batch_cancel_orders(self, c: BatchCancelOrders) -> None: pass
    async def _query_order(self, c: QueryOrder) -> None: pass

    # ── REST Submission Logic ─────────────────────────────────────────────────

    async def _submit_order_async(self, command: SubmitOrder) -> None:
        order = command.order
        ts = self._clock.timestamp_ns()
        
        # Determine if closing an existing position
        is_close, etoro_pos_id = False, None
        open_positions = self._cache.positions_open(instrument_id=order.instrument_id)
        if open_positions:
            pos = open_positions[0]
            if (order.side == OrderSide.SELL and pos.side == PositionSide.LONG) or (order.side == OrderSide.BUY and pos.side == PositionSide.SHORT):
                is_close, etoro_pos_id = True, await self._state.get(str(pos.opening_order_id))

        self.generate_order_submitted(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, ts_event=ts)

        if not await self._rate_limiter.acquire("CLOSE" if is_close else "OPEN"):
            self.generate_order_rejected(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, reason="rate_limit", ts_event=ts)
            return

        etoro_id = int(self._instrument_to_etoro.get(str(order.instrument_id), "0"))
        if is_close:
            payload = {"InstrumentID": etoro_id, "UnitsToDeduct": None}
            url = f"{self._rest_base}/market-close-orders/positions/{etoro_pos_id}"
        else:
            payload = {"InstrumentID": etoro_id, "IsBuy": order.side == OrderSide.BUY, "Leverage": 1}
            if order.order_type == OrderType.LIMIT:
                payload["Rate"] = float(order.price)
                url = f"{self._rest_base}/limit-orders"
            else:
                last_quote = self._cache.quote_tick(order.instrument_id)
                if last_quote:
                    payload["Amount"] = round(float(order.quantity) * float(last_quote.ask_price if order.side == OrderSide.BUY else last_quote.bid_price), 2)
                    url = f"{self._rest_base}/market-open-orders/by-amount"
                else:
                    payload["AmountInUnits"] = float(order.quantity)
                    url = f"{self._rest_base}/market-open-orders/by-units"
            if self._enable_trailing_stop and order.order_type != OrderType.LIMIT:
                payload["IsTslEnabled"] = True

        req_id = self._order_req_id(order.client_order_id.value)

        # RACE CONDITION FIX: Pre-register im State! 
        # Falls WS schneller feuert als HTTP antwortet, kennt das System die Order bereits.
        pre_mapped_id = etoro_pos_id if is_close else req_id
        await self._state.set(order.client_order_id.value, pre_mapped_id)

        # Dry Run
        if self._dry_run:
            fake_id = str(uuid.uuid5(uuid.NAMESPACE_OID, order.client_order_id.value))
            await self._state.set(order.client_order_id.value, fake_id)
            self.generate_order_accepted(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(fake_id), ts_event=ts)
            if order.order_type != OrderType.LIMIT:
                self.generate_order_filled(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(fake_id), venue_position_id=PositionId(fake_id), trade_id=TradeId(str(uuid.uuid4())), order_side=order.side, order_type=order.order_type, last_qty=order.quantity, last_px=Price(1.0, precision=2), quote_currency=USD, commission=Money(0.0, USD), liquidity_side=LiquiditySide.TAKER, ts_event=ts)
            return

        # Real Execution
        try:
            self._log.info(f"REST POST {url} | payload={payload}", LogColor.CYAN)
            async with self._session.post(url, json=payload, headers=self._make_headers(req_id)) as resp:
                status, body_text = resp.status, await resp.text()

                if 200 <= status < 300:
                    body = json.loads(body_text) if body_text else {}
                    new_pos_id = str(body.get("orderForOpen", {}).get("orderID") or body.get("positionId") or body.get("orderId") or req_id)
                    await self._state.set(order.client_order_id.value, new_pos_id)
                    self.generate_order_accepted(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(new_pos_id), ts_event=ts)

                elif status in (502, 504):
                    await self._reconcile_via_pnl(order, req_id)
                elif status == 404 and is_close:
                    await self._state.delete(order.client_order_id.value)
                    self.generate_order_canceled(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(etoro_pos_id or "unknown"), ts_event=ts)
                else:
                    self.generate_order_rejected(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, reason=f"etoro_{status}: {body_text[:200]}", ts_event=ts)

        except asyncio.TimeoutError:
            await self._reconcile_via_pnl(order, req_id)
        except Exception as exc:
            self.generate_order_rejected(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, reason=f"error: {exc}", ts_event=ts)

    async def _cancel_order_async(self, command: CancelOrder) -> None:
        coid = command.client_order_id.value
        if not (order := self._cache.order(command.client_order_id)): return
        ts = self._clock.timestamp_ns()
        pos_id = await self._state.get(coid)

        if not pos_id or self._dry_run:
            await self._state.delete(coid)
            self.generate_order_canceled(strategy_id=command.strategy_id, instrument_id=command.instrument_id, client_order_id=command.client_order_id, venue_order_id=VenueOrderId(pos_id or "unknown"), ts_event=ts)
            return

        await self._rate_limiter.acquire("CLOSE")
        etoro_id = int(self._instrument_to_etoro.get(str(command.instrument_id), "0"))
        url = f"{self._rest_base}/market-close-orders/positions/{pos_id}"
        
        try:
            async with self._session.post(url, json={"InstrumentID": etoro_id, "UnitsToDeduct": None}, headers=self._make_headers(self._order_req_id(coid))) as resp:
                if resp.status not in range(200, 300) and resp.status != 404: return # Hard fail, do not clean state
            await self._state.delete(coid)
            self.generate_order_canceled(strategy_id=command.strategy_id, instrument_id=command.instrument_id, client_order_id=command.client_order_id, venue_order_id=VenueOrderId(pos_id), ts_event=ts)
        except Exception as exc:
            self._log.error(f"Cancel failed for {coid}: {exc}", LogColor.RED)

    async def _reconcile_via_pnl(self, order: object, req_id: str) -> None:
        """Fallback: Sucht nach Timeouts über den PnL Endpunkt ob die Order doch ausgeführt wurde."""
        try:
            async with self._session.get(self._pnl_base, headers=self._make_headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get("positions", []) + data.get("ordersForOpen", []):
                        if str(item.get("token") or item.get("requestId") or "") == req_id:
                            item_id = str(item.get("positionID") or item.get("orderID") or req_id)
                            await self._state.set(order.client_order_id.value, item_id)
                            self.generate_order_accepted(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(item_id), ts_event=self._clock.timestamp_ns())
                            return
        except Exception: pass
        self.generate_order_rejected(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, reason="timeout_no_position", ts_event=self._clock.timestamp_ns())