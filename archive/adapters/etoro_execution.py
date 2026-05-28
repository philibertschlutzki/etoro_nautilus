"""eToro LiveExecutionClient for Nautilus Trader."""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
import uuid
import random
from contextlib import suppress
from typing import Literal

import aiohttp
import websockets

__all__ = ["EToroExecutionClient"]

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
from nautilus_trader.model.objects import AccountBalance, Money, Price

from archive.adapters.etoro_rate_limiter import _RateLimiter
from archive.adapters.etoro_state_manager import _StateManager
from archive.adapters.instrument_map import ETORO_INSTRUMENTS

_MAX_CONNECT_ATTEMPTS = 5
_CONNECT_TIMEOUT_S = 30
_REST_TIMEOUT_S = 10

_POLL_ATTEMPTS_OPEN = 20
_POLL_ATTEMPTS_CLOSE = 12
_POLL_INTERVAL_S = 5.0

# TTL for the WS event buffer: events older than this are not replayed
_WS_BUFFER_TTL_S = 30.0
_WS_BUFFER_MAX = 50

_REST_BASE: dict[str, str] = {
    "demo": "https://public-api.etoro.com/api/v1/trading/execution/demo",
    "real": "https://public-api.etoro.com/api/v1/trading/execution",
}

_PNL_BASE: dict[str, str] = {
    "demo": "https://public-api.etoro.com/api/v1/trading/info/demo/pnl",
    "real": "https://public-api.etoro.com/api/v1/trading/info/real/pnl",
}

_WS_URL = "wss://ws.etoro.com/ws"


class EToroExecutionClient(LiveExecutionClient):

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

        self._set_account_id(AccountId(f"ETORO-{environment.upper()}-001"))
        self._api_key = api_key
        self._user_key = user_key
        self._dry_run = dry_run
        self._enable_trailing_stop = enable_trailing_stop
        self._rest_base = _REST_BASE[environment]
        self._pnl_base = _PNL_BASE[environment]

        self._instrument_to_etoro = {v: k for k, v in ETORO_INSTRUMENTS.items()}
        self._rate_limiter = _RateLimiter()
        self._state = _StateManager(state_path)
        self._session: aiohttp.ClientSession | None = None
        self._ws: object | None = None
        self._ws_task: asyncio.Task | None = None

        # [WS-2] TTL-based WS event buffer: list of (monotonic_timestamp, event_dict)
        # Events older than _WS_BUFFER_TTL_S are silently dropped on replay.
        self._ws_buffer: list[tuple[float, dict]] = []

    def _make_headers(self, req_id: str | None = None) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "x-user-key": self._user_key,
            "x-request-id": req_id or str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _order_req_id(client_order_id_value: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_OID, client_order_id_value))

    async def generate_order_status_reports(self, *args, **kwargs) -> list:
        return []

    async def generate_trade_reports(self, *args, **kwargs) -> list:
        return []

    async def generate_position_status_reports(self, *args, **kwargs) -> list:
        return []

    async def generate_fill_reports(self, *args, **kwargs) -> list:
        return []

    async def _connect(self) -> None:
        if self._dry_run:
            self._log.info(
                "⚠️  DRY-RUN MODE: no real orders will be sent.", LogColor.YELLOW
            )

        await self._state.load(warn_fn=self._log.warning)
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_REST_TIMEOUT_S)
        )
        await self._rate_limiter.start()
        await self._connect_ws()

        balance = await self._fetch_account_balance()
        self.generate_account_state(
            balances=[
                AccountBalance(total=balance, locked=Money(0, USD), free=balance)
            ],
            margins=[],
            reported=False,
            ts_event=self._clock.timestamp_ns(),
        )

    async def _disconnect(self) -> None:
        await self._rate_limiter.stop()
        if self._ws_task:
            self._ws_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._ws_task
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        # Flush any pending state writes before exit
        if self._state._flush_task and not self._state._flush_task.done():
            self._state._flush_task.cancel()
            await self._state._persist_now()
        self._log.info("EToroExecutionClient disconnected.", LogColor.BLUE)

    async def _fetch_account_balance(self) -> Money:
        """Fetch available cash balance.

        [EX-3] Fix: uses the official eToro formula:
            available = credit - pending_non_mirror_orders_for_open

        The previous implementation also subtracted `invested` (open position amounts),
        causing a double-deduction because eToro's `credit` field already reflects
        deployed capital. See: https://api-portal.etoro.com/guides/calculate-available-cash.md
        """
        if self._dry_run:
            return Money(0, USD)
        try:
            async with self._session.get(
                self._pnl_base, headers=self._make_headers()
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    data = data.get("clientPortfolio", data)
                    self._log.debug(f"PnL raw keys: {list(data.keys())}", LogColor.CYAN)

                    credit = float(
                        data.get("credit")
                        or data.get("credits")
                        or data.get("availableCash")
                        or data.get("equity")
                        or data.get("netEquity")
                        or 0
                    ) or 0.0

                    # Deduct only non-mirror pending open orders (official eToro formula)
                    orders_open = data.get("ordersForOpen", data.get("OrdersForOpen", []))
                    pending = sum(
                        float(o.get("amount", o.get("Amount", 0)))
                        for o in orders_open
                        if not o.get("mirrorID", o.get("MirrorID"))
                    )

                    available = max(credit - pending, 0.0)
                    self._log.info(
                        f"Balance: credit={credit:.2f}, pending={pending:.2f}, "
                        f"available={available:.2f}",
                        LogColor.CYAN,
                    )
                    return Money(available, USD)
                else:
                    self._log.warning(
                        f"Balance fetch HTTP {resp.status}", LogColor.YELLOW
                    )
        except Exception as exc:
            self._log.warning(f"Balance fetch failed: {exc}", LogColor.YELLOW)
        return Money(0, USD)

    async def _connect_ws(self) -> None:
        for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
            try:
                # [WS-1] ping_timeout added — mirrors etoro_data.py configuration.
                # Without this, silent TCP drops leave the socket in a "connected"
                # state indefinitely; ping_timeout=20 forces a hard reconnect within
                # ping_interval + ping_timeout = 40 seconds.
                self._ws = await asyncio.wait_for(
                    websockets.connect(
                        _WS_URL,
                        ssl=ssl.create_default_context(),
                        ping_interval=20,
                        ping_timeout=20,
                    ),
                    timeout=_CONNECT_TIMEOUT_S,
                )
                await self._ws.send(
                    json.dumps(
                        {
                            "id": str(uuid.uuid4()),
                            "operation": "Authenticate",
                            "data": {
                                "userKey": self._user_key,
                                "apiKey": self._api_key,
                            },
                        }
                    )
                )
                await self._ws.recv()

                await self._ws.send(
                    json.dumps(
                        {
                            "id": str(uuid.uuid4()),
                            "operation": "Subscribe",
                            "data": {
                                "topics": [
                                    "trading.notifications",
                                    "portfolio.positions",
                                    "trading.orders",
                                    "trading.executions",
                                ],
                                "snapshot": False,
                            },
                        }
                    )
                )

                self._ws_task = self.create_task(
                    self._ws_message_loop(), log_msg="exec_ws_loop"
                )
                self._log.info(
                    "Execution WS connected and authenticated.", LogColor.GREEN
                )
                return
            except Exception as exc:
                self._log.warning(
                    f"WS connect failed ({attempt}/{_MAX_CONNECT_ATTEMPTS}): {exc}",
                    LogColor.YELLOW,
                )
                await asyncio.sleep(min(10 * attempt, 60) + random.uniform(0, 2))
        os._exit(1)

    async def _ws_message_loop(self) -> None:
        try:
            async for raw in self._ws:
                if not raw or raw == b"\x00":
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if isinstance(data, dict):
                    if "messages" in data and isinstance(data["messages"], list):
                        for msg in data["messages"]:
                            if isinstance(msg, dict):
                                await self._process_ws_message(msg)
                    elif "type" in data and "content" in data:
                        await self._process_ws_message(data)
                elif isinstance(data, list):
                    for msg in data:
                        if isinstance(msg, dict):
                            await self._process_ws_message(msg)
        except Exception as exc:
            self._log.error(
                f"WS error or closure: {exc}. Forcing restart.", LogColor.RED
            )
            os._exit(1)

    async def _process_ws_message(self, msg: dict, is_replay: bool = False) -> None:
        msg_type = msg.get("type", "")
        m_type = msg_type.lower()

        content = msg.get("content", {})
        if isinstance(content, str):
            with suppress(json.JSONDecodeError):
                content = json.loads(content)
        if not isinstance(content, dict):
            return

        if not is_replay and (
            "trading" in m_type or "order" in m_type or "position" in m_type
        ):
            self._log.info(f"WS Recv [{msg_type}]: {content}", LogColor.CYAN)

        c_lower = {str(k).lower(): v for k, v in content.items()}
        pos_id = str(c_lower.get("positionid", ""))
        ord_id = str(c_lower.get("orderid", ""))
        token = str(c_lower.get("token", "") or c_lower.get("requestid", ""))

        if not pos_id and not ord_id and not token:
            return

        # [EX-1] O(1) COID resolution via pre-computed reverse-lookup caches.
        # Replaces the former O(N) loop that also recomputed uuid5() on every
        # iteration. Cache is maintained synchronously in _StateManager.set/delete.
        matched_coid: str | None = None

        if token:
            matched_coid = self._state._req_id_to_coid.get(token)

        if matched_coid is None and pos_id:
            matched_coid = self._state._venue_id_to_coid.get(pos_id)

        if matched_coid is None and ord_id:
            matched_coid = self._state._venue_id_to_coid.get(ord_id)

        if not matched_coid:
            if not is_replay:
                # [WS-2] TTL-based buffer: evict stale events before inserting
                now = time.monotonic()
                self._ws_buffer = [
                    (t, m) for (t, m) in self._ws_buffer
                    if now - t < _WS_BUFFER_TTL_S
                ]
                if len(self._ws_buffer) < _WS_BUFFER_MAX:
                    self._ws_buffer.append((now, dict(msg)))
            return

        stored_id = self._state.get_all().get(matched_coid, "")
        real_id_from_ws = pos_id or ord_id
        if (
            real_id_from_ws
            and real_id_from_ws != stored_id
            and len(real_id_from_ws) > 0
        ):
            await self._state.set(matched_coid, real_id_from_ws)

        client_order_id = ClientOrderId(matched_coid)
        order = self._cache.order(client_order_id)
        if not order:
            return

        ts = self._clock.timestamp_ns()

        if m_type in (
            "trading.position.opened",
            "position.opened",
            "orderfilled",
            "trading.order.filled",
            "trading.position.closed",
            "position.closed",
        ):
            fill_px = (
                c_lower.get("openrate")
                or c_lower.get("closerate")
                or c_lower.get("fillprice")
                or c_lower.get("executionprice")
                or c_lower.get("rate")
            )
            if fill_px and (instr := self._cache.instrument(order.instrument_id)):
                if order.status.name != "FILLED":
                    self.generate_order_filled(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=client_order_id,
                        venue_order_id=VenueOrderId(real_id_from_ws or matched_coid),
                        venue_position_id=PositionId(real_id_from_ws or matched_coid),
                        trade_id=TradeId(str(uuid.uuid4())),
                        order_side=order.side,
                        order_type=OrderType.MARKET,
                        last_qty=order.quantity,
                        last_px=Price(float(fill_px), precision=instr.price_precision),
                        quote_currency=USD,
                        commission=Money(0.0, USD),
                        liquidity_side=LiquiditySide.TAKER,
                        ts_event=ts,
                    )
            self.create_task(self._refresh_balance(), log_msg="balance_refresh")

        elif m_type in ("trading.order.accepted", "order.accepted"):
            real_order_id = str(
                c_lower.get("orderid")
                or c_lower.get("order_id")
                or ord_id
                or pos_id
                or matched_coid
            )
            if real_order_id and real_order_id != matched_coid:
                await self._state.set(matched_coid, real_order_id)
            if order.status.name in ("INITIALIZED", "SUBMITTED"):
                self.generate_order_accepted(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=client_order_id,
                    venue_order_id=VenueOrderId(real_order_id),
                    ts_event=ts,
                )
        elif m_type in ("trading.order.canceled", "order.cancelled"):
            await self._state.delete(matched_coid)
            self.generate_order_canceled(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=client_order_id,
                venue_order_id=VenueOrderId(real_id_from_ws or matched_coid),
                ts_event=ts,
            )

    async def _submit_order(self, command: SubmitOrder) -> None:
        await self._submit_order_async(command)

    async def _cancel_order(self, command: CancelOrder) -> None:
        await self._cancel_order_async(command)

    async def _modify_order(self, c: ModifyOrder) -> None:
        pass

    async def _submit_order_list(self, c: SubmitOrderList) -> None:
        pass

    async def _cancel_all_orders(self, c: CancelAllOrders) -> None:
        pass

    async def _batch_cancel_orders(self, c: BatchCancelOrders) -> None:
        pass

    async def _query_order(self, command: QueryOrder) -> None:
        coid = command.client_order_id.value
        order = self._cache.order(command.client_order_id)
        if not order or order.status.name not in ("INITIALIZED", "SUBMITTED", "ACCEPTED"):
            return
        self._log.info(f"Query ClientOrderId('{coid}')", LogColor.BLUE)
        try:
            found = await self._reconcile_via_pnl(
                order,
                self._order_req_id(coid),
                await self._state.get(coid) or "",
            )
            if found:
                self._log.info(
                    f"Query resolved order {coid} via PnL reconciliation.", LogColor.GREEN
                )
        except Exception as exc:
            self._log.warning(f"Query failed for {coid}: {exc}", LogColor.YELLOW)

    def _build_limit_payload(self, order, etoro_id: int) -> dict:
        limit_rate = float(order.price)
        if order.side == OrderSide.BUY:
            sl_rate = max(round(limit_rate * 0.5, 5), 1e-05)
        else:
            sl_rate = round(limit_rate * 2.0, 5)
        return {
            "InstrumentID": etoro_id,
            "IsBuy": order.side == OrderSide.BUY,
            "Leverage": 1,
            "Rate": limit_rate,
            "Amount": round(float(order.quantity) * limit_rate, 2),
            "StopLossRate": sl_rate,
            "IsNoStopLoss": False,
            "IsNoTakeProfit": True,
        }

    def _build_market_open_payload(self, order, etoro_id: int) -> tuple[dict, str]:
        payload = {
            "InstrumentID": etoro_id,
            "IsBuy": order.side == OrderSide.BUY,
            "Leverage": 1,
            "IsNoStopLoss": True,
            "IsNoTakeProfit": True,
        }

        last_quote = self._cache.quote_tick(order.instrument_id)
        if last_quote:
            exec_price = float(
                last_quote.ask_price
                if order.side == OrderSide.BUY
                else last_quote.bid_price
            )
            payload["Amount"] = round(float(order.quantity) * exec_price, 2)
            url = f"{self._rest_base}/market-open-orders/by-amount"
        else:
            exec_price = None
            payload["AmountInUnits"] = float(order.quantity)
            url = f"{self._rest_base}/market-open-orders/by-units"

        sl_pct = 0.0
        tp_pct = 0.0

        for tag in (getattr(order, "tags", None) or []):
            if isinstance(tag, str):
                if tag.startswith("SL:"):
                    try:
                        sl_pct = float(tag[3:])
                    except ValueError:
                        pass
                elif tag.startswith("TP:"):
                    try:
                        tp_pct = float(tag[3:])
                    except ValueError:
                        pass

        if sl_pct > 0 or tp_pct > 0:
            if exec_price is not None:
                instr = self._cache.instrument(order.instrument_id)
                if instr:
                    if sl_pct > 0:
                        if order.side == OrderSide.BUY:
                            sl_rate = exec_price * (1.0 - sl_pct)
                        else:
                            sl_rate = exec_price * (1.0 + sl_pct)
                        payload["StopLossRate"] = round(sl_rate, instr.price_precision)
                        payload["IsNoStopLoss"] = False

                    if tp_pct > 0:
                        if order.side == OrderSide.BUY:
                            tp_rate = exec_price * (1.0 + tp_pct)
                        else:
                            tp_rate = exec_price * (1.0 - tp_pct)
                        payload["TakeProfitRate"] = round(tp_rate, instr.price_precision)
                        payload["IsNoTakeProfit"] = False
                        self._log.info(
                            f"[{order.instrument_id}] Take-Profit gesetzt: "
                            f"rate={tp_rate:.5f} (TP:{tp_pct*100:.1f}%)",
                            LogColor.CYAN,
                        )
                else:
                    self._log.warning(
                        f"[{order.instrument_id}] Instrument not in cache. "
                        "Skipping StopLossRate/TakeProfitRate.",
                        LogColor.YELLOW,
                    )
            else:
                if sl_pct > 0:
                    self._log.warning(
                        f"[{order.instrument_id}] SL-Tag gefunden, aber kein Quote-Tick. "
                        "StopLossRate wird übersprungen.",
                        LogColor.YELLOW,
                    )
                if tp_pct > 0:
                    self._log.warning(
                        f"[{order.instrument_id}] TP-Tag gefunden, aber kein Quote-Tick. "
                        "TP wird übersprungen.",
                        LogColor.YELLOW,
                    )

        tsl_requested = any(
            isinstance(tag, str) and tag == "TSL:1"
            for tag in (getattr(order, "tags", None) or [])
        )

        if tsl_requested:
            if not self._enable_trailing_stop:
                self._log.warning(
                    f"[{order.instrument_id}] TSL:1 Tag: globale TSL-Erlaubnis deaktiviert.",
                    LogColor.YELLOW,
                )
            elif "StopLossRate" in payload:
                payload["IsTrailingStop"] = True
                self._log.info(
                    f"[{order.instrument_id}] Trailing Stop aktiviert (TSL:1 Tag).",
                    LogColor.CYAN,
                )
            else:
                self._log.warning(
                    f"[{order.instrument_id}] TSL:1 Tag ohne SL:<pct> — TSL ignoriert.",
                    LogColor.YELLOW,
                )

        return payload, url

    def _build_close_payload(self, etoro_id: int) -> dict:
        return {
            "InstrumentID": etoro_id,
            "UnitsToDeduct": None,
        }

    async def _submit_order_async(self, command: SubmitOrder) -> None:
        order = command.order
        ts = self._clock.timestamp_ns()

        is_close, etoro_pos_id = False, None
        open_positions = self._cache.positions_open(instrument_id=order.instrument_id)
        if open_positions:
            pos = open_positions[0]
            if (order.side == OrderSide.SELL and pos.side == PositionSide.LONG) or (
                order.side == OrderSide.BUY and pos.side == PositionSide.SHORT
            ):
                is_close, etoro_pos_id = True, await self._state.get(
                    str(pos.opening_order_id)
                )

        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=ts,
        )

        if not await self._rate_limiter.acquire("CLOSE" if is_close else "OPEN"):
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason="rate_limit",
                ts_event=ts,
            )
            return

        etoro_id = int(self._instrument_to_etoro.get(str(order.instrument_id), "0"))

        if is_close:
            payload = self._build_close_payload(etoro_id)
            url = f"{self._rest_base}/market-close-orders/positions/{etoro_pos_id}"
        elif order.order_type == OrderType.LIMIT:
            payload = self._build_limit_payload(order, etoro_id)
            url = f"{self._rest_base}/limit-orders"
        else:
            payload, url = self._build_market_open_payload(order, etoro_id)

        req_id = self._order_req_id(order.client_order_id.value)
        await self._state.set(
            order.client_order_id.value, etoro_pos_id if is_close else req_id
        )

        if self._dry_run:
            fake_id = str(uuid.uuid5(uuid.NAMESPACE_OID, order.client_order_id.value))
            await self._state.set(order.client_order_id.value, fake_id)
            self.generate_order_accepted(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=VenueOrderId(fake_id),
                ts_event=ts,
            )
            if order.order_type != OrderType.LIMIT:
                self.generate_order_filled(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    venue_order_id=VenueOrderId(fake_id),
                    venue_position_id=PositionId(fake_id),
                    trade_id=TradeId(str(uuid.uuid4())),
                    order_side=order.side,
                    order_type=order.order_type,
                    last_qty=order.quantity,
                    last_px=Price(1.0, precision=2),
                    quote_currency=USD,
                    commission=Money(0.0, USD),
                    liquidity_side=LiquiditySide.TAKER,
                    ts_event=ts,
                )
            return

        try:
            self._log.info(f"REST POST {url} | payload={payload}", LogColor.CYAN)
            async with self._session.post(
                url, json=payload, headers=self._make_headers(req_id)
            ) as resp:
                status, body_text = resp.status, await resp.text()

                if 200 <= status < 300:
                    self._log.debug(
                        f"REST response body for {order.client_order_id.value}: "
                        f"{body_text[:2000]}",
                        LogColor.CYAN,
                    )
                    body = json.loads(body_text) if body_text else {}
                    if order.order_type == OrderType.LIMIT:
                        self._log.info(
                            f"Limit order REST response body: {body}", LogColor.CYAN
                        )
                    new_pos_id = str(
                        body.get("orderForOpen", {}).get("orderID")
                        or body.get("positionId")
                        or body.get("orderId")
                        or body.get("token")
                        or (etoro_pos_id if is_close else req_id)
                    )
                    await self._state.set(order.client_order_id.value, new_pos_id)

                    self.generate_order_accepted(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=order.client_order_id,
                        venue_order_id=VenueOrderId(new_pos_id),
                        ts_event=ts,
                    )
                    if order.order_type == OrderType.LIMIT:
                        self._log.info(
                            f"Limit order accepted (venue_id={new_pos_id}, token={req_id}). "
                            "State will be updated when WS delivers real orderId.",
                            LogColor.GREEN,
                        )

                    # [WS-2] Replay buffered events, evicting stale ones first
                    now = time.monotonic()
                    valid_buffer = [
                        (t, m) for (t, m) in self._ws_buffer
                        if now - t < _WS_BUFFER_TTL_S
                    ]
                    self._ws_buffer = valid_buffer

                    for _ts, evt in valid_buffer:
                        content = evt.get("content", {})
                        if isinstance(content, str):
                            with suppress(json.JSONDecodeError):
                                content = json.loads(content)
                        if isinstance(content, dict):
                            c_lower = {str(k).lower(): v for k, v in content.items()}
                            if (
                                str(c_lower.get("orderid", "")) == new_pos_id
                                or str(c_lower.get("positionid", "")) == new_pos_id
                            ):
                                self._log.info(
                                    f"Replaying buffered WS event for {new_pos_id}",
                                    LogColor.GREEN,
                                )
                                await self._process_ws_message(evt, is_replay=True)

                    if order.order_type == OrderType.MARKET:
                        self.create_task(
                            self._poll_for_fill(order, req_id, new_pos_id, is_close),
                            log_msg="poll_fill",
                        )

                elif status in (502, 504):
                    self.create_task(
                        self._poll_for_fill(
                            order,
                            req_id,
                            etoro_pos_id if is_close else req_id,
                            is_close,
                        ),
                        log_msg="poll_fill",
                    )
                elif status == 404 and is_close:
                    await self._state.delete(order.client_order_id.value)
                    self.generate_order_canceled(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=order.client_order_id,
                        venue_order_id=VenueOrderId(etoro_pos_id or "unknown"),
                        ts_event=ts,
                    )
                else:
                    self._log.warning(
                        f"HTTP {status} for {order.client_order_id.value}: "
                        f"{body_text[:500]}",
                        LogColor.YELLOW,
                    )
                    self.generate_order_rejected(
                        strategy_id=order.strategy_id,
                        instrument_id=order.instrument_id,
                        client_order_id=order.client_order_id,
                        reason=f"etoro_{status}: {body_text[:500]}",
                        ts_event=ts,
                    )

        except asyncio.TimeoutError:
            self.create_task(
                self._poll_for_fill(
                    order, req_id, etoro_pos_id if is_close else req_id, is_close
                ),
                log_msg="poll_fill",
            )
        except Exception as exc:
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=f"error: {exc}",
                ts_event=ts,
            )

    async def _resolve_limit_order_id(
        self,
        coid: str,
        venue_token: str | None = None,
        order: object | None = None,
    ) -> str | None:
        req_id = self._order_req_id(coid)

        limit_rate: float | None = None
        etoro_instr_id: str | None = None
        is_buy: bool | None = None
        if order is not None:
            try:
                limit_rate = float(order.price)
                etoro_instr_id = self._instrument_to_etoro.get(str(order.instrument_id))
                from nautilus_trader.model.enums import OrderSide
                is_buy = order.side == OrderSide.BUY
            except Exception:
                pass

        try:
            async with self._session.get(
                self._pnl_base, headers=self._make_headers()
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                data = data.get("clientPortfolio", data)

                all_orders: list[dict] = []
                for key in ("ordersForOpen", "OrdersForOpen",
                            "entryOrders", "EntryOrders",
                            "orders", "Orders"):
                    all_orders.extend(data.get(key, []))

                # Strategy 1: Token match
                for item in all_orders:
                    item_lower = {str(k).lower(): v for k, v in item.items()}
                    item_token = str(
                        item_lower.get("token", "")
                        or item_lower.get("requestid", "")
                    )
                    if item_token and (
                        item_token == req_id
                        or (venue_token and item_token == venue_token)
                    ):
                        order_id = str(
                            item_lower.get("orderid", "")
                            or item_lower.get("order_id", "")
                        )
                        if order_id:
                            self._log.info(
                                f"Resolved limit orderID {order_id} for {coid} via token.",
                                LogColor.GREEN,
                            )
                            return order_id

                # Strategy 2: Rate + InstrumentID + IsBuy
                if limit_rate is not None and etoro_instr_id is not None:
                    rate_matches: list[str] = []
                    for item in all_orders:
                        item_lower = {str(k).lower(): v for k, v in item.items()}
                        if str(item_lower.get("instrumentid", "")) != str(etoro_instr_id):
                            continue
                        if is_buy is not None:
                            item_is_buy = bool(
                                item_lower.get("isbuy", item_lower.get("is_buy", True))
                            )
                            if item_is_buy != is_buy:
                                continue
                        item_rate = float(
                            item_lower.get("rate", item_lower.get("Rate", 0)) or 0
                        )
                        if item_rate > 0 and abs(item_rate - limit_rate) / limit_rate < 0.005:
                            order_id = str(
                                item_lower.get("orderid", "")
                                or item_lower.get("order_id", "")
                            )
                            if order_id:
                                rate_matches.append(order_id)

                    if len(rate_matches) == 1:
                        self._log.info(
                            f"Resolved limit orderID {rate_matches[0]} for {coid} "
                            f"via rate match (rate={limit_rate}).",
                            LogColor.GREEN,
                        )
                        return rate_matches[0]
                    elif len(rate_matches) > 1:
                        self._log.warning(
                            f"Rate match: {len(rate_matches)} candidates for {coid} "
                            f"at rate={limit_rate}: {rate_matches}. Using first.",
                            LogColor.YELLOW,
                        )
                        return rate_matches[0]

                # Strategy 3: Single-order fallback
                if etoro_instr_id is not None:
                    instr_orders = [
                        item for item in all_orders
                        if str(
                            {str(k).lower(): v for k, v in item.items()}.get(
                                "instrumentid", ""
                            )
                        ) == str(etoro_instr_id)
                    ]
                    if len(instr_orders) == 1:
                        item_lower = {str(k).lower(): v for k, v in instr_orders[0].items()}
                        order_id = str(
                            item_lower.get("orderid", "")
                            or item_lower.get("order_id", "")
                        )
                        if order_id:
                            self._log.info(
                                f"Resolved limit orderID {order_id} for {coid} "
                                "via single-order fallback.",
                                LogColor.GREEN,
                            )
                            return order_id

        except Exception as exc:
            self._log.warning(
                f"_resolve_limit_order_id failed for {coid}: {exc}", LogColor.YELLOW
            )
        return None

    async def _background_cancel_limit(
        self,
        coid: str,
        pos_id: str,
        order: object,
    ) -> None:
        delays = [2.0, 3.0, 5.0]

        for attempt, delay in enumerate(delays, start=1):
            await asyncio.sleep(delay)

            real_id = await self._resolve_limit_order_id(
                coid, venue_token=pos_id, order=order
            )

            if real_id:
                self._log.info(
                    f"Background cancel: found real orderID {real_id} for {coid} "
                    f"(attempt {attempt}/{len(delays)}).",
                    LogColor.CYAN,
                )
                try:
                    retry_url = f"{self._rest_base}/limit-orders/{real_id}"
                    async with self._session.delete(
                        retry_url,
                        headers=self._make_headers(self._order_req_id(coid)),
                    ) as retry_resp:
                        if retry_resp.status in range(200, 300) or retry_resp.status == 404:
                            self._log.info(
                                f"✅ Background cancel: limit {real_id} deleted.",
                                LogColor.GREEN,
                            )
                            return
                        else:
                            err = await retry_resp.text()
                            self._log.warning(
                                f"Background cancel DELETE failed for {real_id}: "
                                f"HTTP {retry_resp.status} — {err[:200]}",
                                LogColor.YELLOW,
                            )
                except Exception as exc:
                    self._log.warning(
                        f"Background cancel exception for {coid}: {exc}", LogColor.YELLOW
                    )
                return

        self._log.warning(
            f"Background cancel exhausted retries for {coid}.",
            LogColor.YELLOW,
        )

    async def _cancel_order_async(self, command: CancelOrder) -> None:
        coid = command.client_order_id.value
        if not (order := self._cache.order(command.client_order_id)):
            return
        ts = self._clock.timestamp_ns()
        pos_id = await self._state.get(coid)

        if not pos_id or self._dry_run:
            await self._state.delete(coid)
            self.generate_order_canceled(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=VenueOrderId(pos_id or "unknown"),
                ts_event=ts,
            )
            return

        await self._rate_limiter.acquire("CLOSE")

        if order.order_type == OrderType.LIMIT:
            url = f"{self._rest_base}/limit-orders/{pos_id}"

            try:
                async with self._session.delete(
                    url,
                    headers=self._make_headers(self._order_req_id(coid)),
                ) as resp:
                    if resp.status in range(200, 300) or resp.status == 404:
                        self._log.info(
                            f"Cancel {resp.status} for {coid} — direct success.",
                            LogColor.GREEN,
                        )
                    elif resp.status == 400:
                        self._log.info(
                            f"Cancel 400 for {coid} (UUID token). "
                            "Launching background cancel task...",
                            LogColor.CYAN,
                        )
                        self.create_task(
                            self._background_cancel_limit(coid, pos_id, order),
                            log_msg="bg_cancel_limit",
                        )
                    else:
                        body = await resp.text()
                        self._log.error(
                            f"Cancel failed for {coid}: HTTP {resp.status} {body[:500]}",
                            LogColor.RED,
                        )
            except Exception as exc:
                self._log.error(f"Cancel exception for {coid}: {exc}", LogColor.RED)

            await self._state.delete(coid)
            self.generate_order_canceled(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=VenueOrderId(pos_id),
                ts_event=ts,
            )
            return

        else:
            url = f"{self._rest_base}/market-close-orders/positions/{pos_id}"
            payload = {"UnitsToDeduct": None}

            try:
                async with self._session.post(
                    url,
                    json=payload,
                    headers=self._make_headers(self._order_req_id(coid)),
                ) as resp:
                    if resp.status not in range(200, 300) and resp.status != 404:
                        body = await resp.text()
                        self._log.error(
                            f"Cancel failed for {coid}: HTTP {resp.status} {body[:500]}",
                            LogColor.RED,
                        )
                        return
                await self._state.delete(coid)
                self.generate_order_canceled(
                    strategy_id=command.strategy_id,
                    instrument_id=command.instrument_id,
                    client_order_id=command.client_order_id,
                    venue_order_id=VenueOrderId(pos_id),
                    ts_event=ts,
                )
            except Exception as exc:
                self._log.error(f"Cancel exception for {coid}: {exc}", LogColor.RED)

    async def _poll_for_fill(
        self, order: object, req_id: str, order_id: str, is_close: bool
    ) -> None:
        max_attempts = _POLL_ATTEMPTS_CLOSE if is_close else _POLL_ATTEMPTS_OPEN
        for attempt in range(max_attempts):
            delay = min(2.0 * (1.5 ** attempt), 10.0)
            await asyncio.sleep(delay)
            cached_order = self._cache.order(order.client_order_id)
            if cached_order and cached_order.status.name == "FILLED":
                return

            try:
                if is_close:
                    async with self._session.get(
                        self._pnl_base, headers=self._make_headers()
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            data = data.get("clientPortfolio", data)
                            positions = data.get("Positions", data.get("positions", []))
                            still_open = any(
                                str(
                                    p.get(
                                        "PositionID",
                                        p.get("positionID", p.get("positionId", "")),
                                    )
                                )
                                == order_id
                                for p in positions
                            )
                            if not still_open:
                                instr = self._cache.instrument(order.instrument_id)
                                quote = self._cache.quote_tick(order.instrument_id)
                                fill_px = (
                                    float(
                                        quote.bid_price
                                        if order.side == OrderSide.SELL
                                        else quote.ask_price
                                    )
                                    if quote
                                    else 1.0
                                )
                                self.generate_order_filled(
                                    strategy_id=order.strategy_id,
                                    instrument_id=order.instrument_id,
                                    client_order_id=order.client_order_id,
                                    venue_order_id=order.venue_order_id
                                    or VenueOrderId(order_id),
                                    venue_position_id=PositionId(order_id),
                                    trade_id=TradeId(str(uuid.uuid4())),
                                    order_side=order.side,
                                    order_type=order.order_type,
                                    last_qty=order.quantity,
                                    last_px=Price(
                                        fill_px,
                                        precision=instr.price_precision if instr else 5,
                                    ),
                                    quote_currency=USD,
                                    commission=Money(0.0, USD),
                                    liquidity_side=LiquiditySide.TAKER,
                                    ts_event=self._clock.timestamp_ns(),
                                )
                                self._log.info(
                                    f"Reconciled via PnL (Closed): "
                                    f"{order.client_order_id.value} -> PosID {order_id}",
                                    LogColor.GREEN,
                                )
                                self.create_task(
                                    self._refresh_balance(), log_msg="balance_refresh"
                                )
                                return
                else:
                    found = await self._reconcile_via_pnl(order, req_id, order_id)
                    if found:
                        return
            except Exception:
                pass

        self._log.warning(
            f"_poll_for_fill exhausted all attempts for "
            f"{order.client_order_id.value}. Order remains ACCEPTED.",
            LogColor.YELLOW,
        )
        try:
            async with self._session.get(
                self._pnl_base, headers=self._make_headers()
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    data = data.get("clientPortfolio", data)
                    self._log.warning(
                        f"[Diagnostic] PnL keys: {list(data.keys())} | "
                        f"positions={len(data.get('positions', data.get('Positions', [])))} | "
                        f"ordersForOpen={len(data.get('ordersForOpen', data.get('OrdersForOpen', [])))} | "
                        f"exitOrders={len(data.get('exitOrders', []))} | "
                        f"ordersForClose={len(data.get('ordersForClose', []))}",
                        LogColor.YELLOW,
                    )
                    for item in data.get("exitOrders", []):
                        self._log.warning(
                            f"[Diagnostic] exitOrder: {item}", LogColor.YELLOW
                        )
        except Exception as exc:
            self._log.warning(
                f"[Diagnostic] PnL fetch failed: {exc}", LogColor.YELLOW
            )

    async def _reconcile_via_pnl(
        self, order: object, req_id: str, order_id: str
    ) -> bool:
        try:
            async with self._session.get(
                self._pnl_base, headers=self._make_headers()
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    data = data.get("clientPortfolio", data)

                    positions = data.get("Positions", data.get("positions", []))
                    orders_for_open = (
                        data.get("OrdersForOpen", data.get("ordersForOpen", []))
                        + data.get("entryOrders", data.get("EntryOrders", []))
                        + data.get("orders", data.get("Orders", []))
                    )

                    for item in positions:
                        i_req = str(item.get("token", item.get("requestId", "")))
                        i_ord = str(
                            item.get("OrderID", item.get("orderID", item.get("orderId", "")))
                        )
                        i_pos = str(
                            item.get(
                                "PositionID",
                                item.get("positionID", item.get("positionId", "")),
                            )
                        )
                        if (
                            (req_id and i_req == req_id)
                            or (order_id and i_ord == order_id)
                            or (order_id and i_pos == order_id)
                        ):
                            pos_id = str(
                                item.get(
                                    "PositionID",
                                    item.get("positionID", item.get("positionId", i_ord)),
                                )
                            )
                            await self._state.set(order.client_order_id.value, pos_id)

                            fill_px = item.get(
                                "OpenRate", item.get("openRate", item.get("rate", 1.0))
                            )
                            instr = self._cache.instrument(order.instrument_id)
                            self.generate_order_filled(
                                strategy_id=order.strategy_id,
                                instrument_id=order.instrument_id,
                                client_order_id=order.client_order_id,
                                venue_order_id=order.venue_order_id
                                or VenueOrderId(pos_id),
                                venue_position_id=PositionId(pos_id),
                                trade_id=TradeId(str(uuid.uuid4())),
                                order_side=order.side,
                                order_type=order.order_type,
                                last_qty=order.quantity,
                                last_px=Price(
                                    float(fill_px),
                                    precision=instr.price_precision if instr else 5,
                                ),
                                quote_currency=USD,
                                commission=Money(0.0, USD),
                                liquidity_side=LiquiditySide.TAKER,
                                ts_event=self._clock.timestamp_ns(),
                            )
                            self._log.info(
                                f"Reconciled via PnL (Filled): "
                                f"{order.client_order_id.value} -> PosID {pos_id}",
                                LogColor.GREEN,
                            )
                            self.create_task(
                                self._refresh_balance(), log_msg="balance_refresh"
                            )
                            return True

                    for item in orders_for_open:
                        i_req = str(item.get("token", item.get("requestId", "")))
                        i_ord = str(
                            item.get("OrderID", item.get("orderID", item.get("orderId", "")))
                        )
                        i_pos = str(
                            item.get(
                                "PositionID",
                                item.get("positionID", item.get("positionId", "")),
                            )
                        )
                        if (
                            (req_id and i_req == req_id)
                            or (order_id and i_ord == order_id)
                            or (order_id and i_pos == order_id)
                        ):
                            i_id = str(
                                item.get(
                                    "OrderID",
                                    item.get("orderID", item.get("orderId", req_id)),
                                )
                            )
                            await self._state.set(order.client_order_id.value, i_id)
                            if order.order_type != OrderType.LIMIT:
                                instr = self._cache.instrument(order.instrument_id)
                                self.generate_order_filled(
                                    strategy_id=order.strategy_id,
                                    instrument_id=order.instrument_id,
                                    client_order_id=order.client_order_id,
                                    venue_order_id=order.venue_order_id
                                    or VenueOrderId(i_id),
                                    venue_position_id=PositionId(i_id),
                                    trade_id=TradeId(str(uuid.uuid4())),
                                    order_side=order.side,
                                    order_type=order.order_type,
                                    last_qty=order.quantity,
                                    last_px=Price(
                                        1.0,
                                        precision=instr.price_precision if instr else 5,
                                    ),
                                    quote_currency=USD,
                                    commission=Money(0.0, USD),
                                    liquidity_side=LiquiditySide.TAKER,
                                    ts_event=self._clock.timestamp_ns(),
                                )
                                self.create_task(
                                    self._refresh_balance(), log_msg="balance_refresh"
                                )
                            else:
                                self.generate_order_accepted(
                                    strategy_id=order.strategy_id,
                                    instrument_id=order.instrument_id,
                                    client_order_id=order.client_order_id,
                                    venue_order_id=VenueOrderId(i_id),
                                    ts_event=self._clock.timestamp_ns(),
                                )
                            return True

                    # [EX-2] exitOrders branch: TSL/SL/TP was triggered by eToro before
                    # Nautilus had a chance to see the open position.
                    #
                    # PREVIOUS BEHAVIOUR (broken): two generate_order_filled() calls with
                    # the same client_order_id — open leg (BUY) + synthetic close leg (SELL).
                    # This caused Nautilus InvalidStateTrigger because an already-FILLED
                    # order cannot be filled again on a different side.
                    #
                    # FIXED BEHAVIOUR: generate only the open-leg fill, which correctly
                    # creates the Nautilus position. Balance refresh is triggered. The
                    # resulting phantom position (open in Nautilus, closed at eToro) is
                    # flagged in the log so the operator can reconcile manually or via
                    # etoro_close_orphans.py. A proper autonomous-close mechanism (synthetic
                    # close order via a separate ClientOrderId) should be implemented as a
                    # follow-up task [EX-2-followup].
                    exit_orders = (
                        data.get("exitOrders", data.get("ExitOrders", []))
                        + data.get("ordersForClose", data.get("OrdersForClose", []))
                        + data.get(
                            "ordersForCloseMultiple",
                            data.get("OrdersForCloseMultiple", []),
                        )
                    )

                    for item in exit_orders:
                        i_req = str(item.get("token", item.get("requestId", "")))
                        i_ord = str(
                            item.get("OrderID", item.get("orderID", item.get("orderId", "")))
                        )
                        i_pos = str(
                            item.get(
                                "PositionID",
                                item.get("positionID", item.get("positionId", "")),
                            )
                        )

                        if (
                            (req_id and i_req == req_id)
                            or (order_id and i_ord == order_id)
                            or (order_id and i_pos == order_id)
                        ):
                            pos_id = i_pos or i_ord or order_id
                            await self._state.set(order.client_order_id.value, pos_id)
                            instr = self._cache.instrument(order.instrument_id)

                            fill_px = item.get(
                                "closeRate",
                                item.get(
                                    "CloseRate", item.get("rate", item.get("Rate"))
                                ),
                            )
                            if fill_px is None:
                                quote = self._cache.quote_tick(order.instrument_id)
                                fill_px = (
                                    float(
                                        quote.bid_price
                                        if order.side == OrderSide.SELL
                                        else quote.ask_price
                                    )
                                    if quote
                                    else 1.0
                                )

                            # Generate only the open-leg fill (safe for Nautilus state machine)
                            self.generate_order_filled(
                                strategy_id=order.strategy_id,
                                instrument_id=order.instrument_id,
                                client_order_id=order.client_order_id,
                                venue_order_id=order.venue_order_id
                                or VenueOrderId(pos_id),
                                venue_position_id=PositionId(pos_id),
                                trade_id=TradeId(str(uuid.uuid4())),
                                order_side=order.side,
                                order_type=order.order_type,
                                last_qty=order.quantity,
                                last_px=Price(
                                    float(fill_px),
                                    precision=instr.price_precision if instr else 5,
                                ),
                                quote_currency=USD,
                                commission=Money(0.0, USD),
                                liquidity_side=LiquiditySide.TAKER,
                                ts_event=self._clock.timestamp_ns(),
                            )

                            # [EX-2] Phantom position warning: eToro has autonomously
                            # closed this position (TSL/SL/TP). Nautilus now has an open
                            # position that no longer exists at the broker. Run
                            # dev_scripts/etoro_close_orphans.py --symbol <sym> to audit.
                            self._log.warning(
                                f"[EX-2] Autonomous TSL/SL/TP close detected for "
                                f"coid={order.client_order_id.value} posId={pos_id}. "
                                f"Nautilus position is now a phantom — reconcile via "
                                f"etoro_close_orphans.py or next strategy signal.",
                                LogColor.YELLOW,
                            )

                            self.create_task(
                                self._refresh_balance(), log_msg="balance_refresh"
                            )
                            return True

        except Exception:
            pass
        return False

    async def _refresh_balance(self) -> None:
        """Refresh account balance after a fill (2s delay for eToro PnL propagation)."""
        await asyncio.sleep(2.0)
        balance = await self._fetch_account_balance()
        self.generate_account_state(
            balances=[
                AccountBalance(total=balance, locked=Money(0, USD), free=balance)
            ],
            margins=[],
            reported=True,
            ts_event=self._clock.timestamp_ns(),
        )