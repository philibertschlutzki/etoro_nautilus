"""eToro LiveExecutionClient for Nautilus Trader.

Beinhaltet REST-Order-Submission, WebSocket Fill-Streaming,
Rate-Limiting, State-Persistenz, Race-Condition-Puffer und Active PnL-Polling.
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
    "real": "https://public-api.etoro.com/api/v1/trading/execution",
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
        
        # Buffer für WS-Events, die schneller ankommen als der HTTP Response
        self._ws_buffer: list[dict] = []

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

    async def _process_ws_message(self, msg: dict, is_replay: bool = False) -> None:
        msg_type = msg.get("type", "")
        m_type = msg_type.lower()
        
        content = msg.get("content", {})
        if isinstance(content, str):
            with suppress(json.JSONDecodeError): content = json.loads(content)
        if not isinstance(content, dict): return

        if not is_replay:
            self._log.info(f"WS Recv [{msg_type}]: {content}", LogColor.CYAN)

        c_lower = {str(k).lower(): v for k, v in content.items()}
        pos_id  = str(c_lower.get("positionid", ""))
        ord_id  = str(c_lower.get("orderid", ""))
        token   = str(c_lower.get("token", "") or c_lower.get("requestid", ""))

        if not pos_id and not ord_id and not token: return

        all_mappings = self._state.get_all()
        matched_coid: str | None = None
        
        for coid, stored_id in reversed(list(all_mappings.items())):
            req_id = self._order_req_id(coid)
            if (token and token == req_id) or (pos_id and stored_id == pos_id) or (ord_id and stored_id == ord_id):
                matched_coid = coid
                break
                
        # BUFFER LOGIC: Falls Event zu früh ankam, puffern wir es für 50 Durchläufe
        if not matched_coid:
            if not is_replay:
                self._ws_buffer.append(dict(msg))
                if len(self._ws_buffer) > 50: self._ws_buffer.pop(0)
            return

        stored_id = all_mappings[matched_coid]
        if pos_id and pos_id != stored_id and len(pos_id) > 0:
            await self._state.set(matched_coid, pos_id)

        client_order_id = ClientOrderId(matched_coid)
        order = self._cache.order(client_order_id)
        if not order: return

        ts = self._clock.timestamp_ns()

        if m_type in ("trading.position.opened", "position.opened", "orderfilled", "trading.order.filled", "trading.position.closed", "position.closed"):
            fill_px = c_lower.get("openrate") or c_lower.get("closerate") or c_lower.get("fillprice") or c_lower.get("executionprice") or c_lower.get("rate")
            if fill_px and (instr := self._cache.instrument(order.instrument_id)):
                # Vermeide Duplicate-Fills
                if order.status.name != "FILLED":
                    self.generate_order_filled(
                        strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=client_order_id,
                        venue_order_id=VenueOrderId(pos_id or ord_id or matched_coid), venue_position_id=PositionId(pos_id or ord_id or matched_coid),
                        trade_id=TradeId(str(uuid.uuid4())), order_side=order.side, order_type=OrderType.MARKET,
                        last_qty=order.quantity, last_px=Price(float(fill_px), precision=instr.price_precision),
                        quote_currency=USD, commission=Money(0.0, USD), liquidity_side=LiquiditySide.TAKER, ts_event=ts,
                    )
        elif m_type in ("trading.order.accepted", "order.accepted"):
            if order.status.name == "INITIALIZED" or order.status.name == "SUBMITTED":
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

        req_id = self._order_req_id(order.client_order_id.value)

        # Race Condition Pre-Register
        await self._state.set(order.client_order_id.value, etoro_pos_id if is_close else req_id)

        if self._dry_run:
            fake_id = str(uuid.uuid5(uuid.NAMESPACE_OID, order.client_order_id.value))
            await self._state.set(order.client_order_id.value, fake_id)
            self.generate_order_accepted(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(fake_id), ts_event=ts)
            if order.order_type != OrderType.LIMIT:
                self.generate_order_filled(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(fake_id), venue_position_id=PositionId(fake_id), trade_id=TradeId(str(uuid.uuid4())), order_side=order.side, order_type=order.order_type, last_qty=order.quantity, last_px=Price(1.0, precision=2), quote_currency=USD, commission=Money(0.0, USD), liquidity_side=LiquiditySide.TAKER, ts_event=ts)
            return

        try:
            self._log.info(f"REST POST {url} | payload={payload}", LogColor.CYAN)
            async with self._session.post(url, json=payload, headers=self._make_headers(req_id)) as resp:
                status, body_text = resp.status, await resp.text()

                if 200 <= status < 300:
                    body = json.loads(body_text) if body_text else {}
                    new_pos_id = str(body.get("orderForOpen", {}).get("orderID") or body.get("positionId") or body.get("orderId") or (etoro_pos_id if is_close else req_id))
                    await self._state.set(order.client_order_id.value, new_pos_id)
                    
                    self.generate_order_accepted(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(new_pos_id), ts_event=ts)
                    
                    # 1. Puffer checken (Replay), falls Event während REST Call kam
                    for evt in list(self._ws_buffer):
                        content = evt.get("content", {})
                        if isinstance(content, str):
                            with suppress(json.JSONDecodeError): content = json.loads(content)
                        if isinstance(content, dict):
                            c_lower = {str(k).lower(): v for k, v in content.items()}
                            if str(c_lower.get("orderid", "")) == new_pos_id or str(c_lower.get("positionid", "")) == new_pos_id:
                                self._log.info(f"Replaying buffered WS event for {new_pos_id}", LogColor.GREEN)
                                await self._process_ws_message(evt, is_replay=True)

                    # 2. Sicherheitspolling aktivieren, falls WS komplett versagt
                    if order.order_type == OrderType.MARKET:
                        self.create_task(self._poll_for_fill(order, req_id, new_pos_id, is_close), log_msg="poll_fill")

                elif status in (502, 504):
                    self.create_task(self._poll_for_fill(order, req_id, etoro_pos_id if is_close else req_id, is_close), log_msg="poll_fill")
                elif status == 404 and is_close:
                    await self._state.delete(order.client_order_id.value)
                    self.generate_order_canceled(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(etoro_pos_id or "unknown"), ts_event=ts)
                else:
                    self.generate_order_rejected(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, reason=f"etoro_{status}: {body_text[:200]}", ts_event=ts)

        except asyncio.TimeoutError:
            self.create_task(self._poll_for_fill(order, req_id, etoro_pos_id if is_close else req_id, is_close), log_msg="poll_fill")
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
                if resp.status not in range(200, 300) and resp.status != 404: return
            await self._state.delete(coid)
            self.generate_order_canceled(strategy_id=command.strategy_id, instrument_id=command.instrument_id, client_order_id=command.client_order_id, venue_order_id=VenueOrderId(pos_id), ts_event=ts)
        except Exception as exc:
            self._log.error(f"Cancel failed for {coid}: {exc}", LogColor.RED)

    # ── PnL Polling Fallback ──────────────────────────────────────────────────

    async def _poll_for_fill(self, order: object, req_id: str, order_id: str, is_close: bool) -> None:
        """Sicherheitsnetz: Prüft PnL-Daten, falls der WebSocket ausfällt."""
        for attempt in range(4): # 4x alle 2 Sekunden prüfen
            await asyncio.sleep(2.0)
            
            cached_order = self._cache.order(order.client_order_id)
            if cached_order and cached_order.status.name == "FILLED": return

            try:
                if is_close:
                    # Bei Close prüfen, ob Position aus dem Portfolio verschwunden ist
                    async with self._session.get(self._pnl_base, headers=self._make_headers()) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            still_open = any(str(p.get("PositionID") or p.get("positionID") or p.get("positionId") or "") == order_id for p in data.get("positions", []))
                            if not still_open:
                                instr = self._cache.instrument(order.instrument_id)
                                quote = self._cache.quote_tick(order.instrument_id)
                                fill_px = float(quote.bid_price if order.side == OrderSide.SELL else quote.ask_price) if quote else 1.0
                                
                                self.generate_order_filled(
                                    strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id,
                                    venue_order_id=VenueOrderId(order_id), venue_position_id=PositionId(order_id), trade_id=TradeId(str(uuid.uuid4())),
                                    order_side=order.side, order_type=order.order_type, last_qty=order.quantity, last_px=Price(fill_px, precision=instr.price_precision if instr else 5),
                                    quote_currency=USD, commission=Money(0.0, USD), liquidity_side=LiquiditySide.TAKER, ts_event=self._clock.timestamp_ns(),
                                )
                                self._log.info(f"Reconciled via PnL (Closed): {order.client_order_id.value} -> PosID {order_id}", LogColor.GREEN)
                                return
                else:
                    # Bei Open prüfen, ob OrderID in den Positionen auftaucht
                    found = await self._reconcile_via_pnl(order, req_id, order_id)
                    if found: return
            except Exception: pass
            
    async def _reconcile_via_pnl(self, order: object, req_id: str, order_id: str) -> bool:
        """Sucht gezielt im PnL-Report nach einer Ausführung."""
        try:
            async with self._session.get(self._pnl_base, headers=self._make_headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    for item in data.get("positions", []):
                        i_req = str(item.get("token") or item.get("requestId") or "")
                        i_ord = str(item.get("orderID") or item.get("orderId") or "")
                        if (req_id and i_req == req_id) or (order_id and i_ord == order_id):
                            pos_id = str(item.get("PositionID") or item.get("positionID") or item.get("positionId") or i_ord)
                            await self._state.set(order.client_order_id.value, pos_id)
                            
                            fill_px = item.get("OpenRate") or item.get("openRate") or item.get("rate")
                            instr = self._cache.instrument(order.instrument_id)
                            self.generate_order_filled(
                                strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id,
                                venue_order_id=VenueOrderId(pos_id), venue_position_id=PositionId(pos_id), trade_id=TradeId(str(uuid.uuid4())),
                                order_side=order.side, order_type=order.order_type, last_qty=order.quantity, last_px=Price(float(fill_px or 1.0), precision=instr.price_precision if instr else 5),
                                quote_currency=USD, commission=Money(0.0, USD), liquidity_side=LiquiditySide.TAKER, ts_event=self._clock.timestamp_ns(),
                            )
                            self._log.info(f"Reconciled via PnL (Filled): {order.client_order_id.value} -> PosID {pos_id}", LogColor.GREEN)
                            return True
                            
                    for item in data.get("ordersForOpen", []):
                        i_req = str(item.get("token") or item.get("requestId") or "")
                        i_ord = str(item.get("orderID") or item.get("orderId") or "")
                        if (req_id and i_req == req_id) or (order_id and i_ord == order_id):
                            i_id = str(item.get("orderID") or item.get("orderId") or req_id)
                            await self._state.set(order.client_order_id.value, i_id)
                            self.generate_order_accepted(strategy_id=order.strategy_id, instrument_id=order.instrument_id, client_order_id=order.client_order_id, venue_order_id=VenueOrderId(i_id), ts_event=self._clock.timestamp_ns())
                            return True
        except Exception: pass
        return False