import asyncio
import json
import ssl
import uuid
import websockets
import os
import traceback
from datetime import datetime

from nautilus_trader.common.enums import LogColor
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import LiveDataClientConfig
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import ClientId, InstrumentId, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity

from adapters.instrument_map import ETORO_INSTRUMENTS
from adapters.instrument_utils import get_size_precision

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_CONNECT_ATTEMPTS = 5
_CONNECT_TIMEOUT_S = 30
_HEARTBEAT_INTERVAL = 60

# ── Config & Factory ──────────────────────────────────────────────────────────

class EToroDataClientConfig(LiveDataClientConfig, frozen=True, kw_only=True):
    api_key: str
    user_key: str
    instrument_ids: list[str]


class EToroLiveDataClientFactory(LiveDataClientFactory):
    @staticmethod
    def create(loop, msgbus, cache, clock, config: EToroDataClientConfig, **kwargs):
        return EToroDataClient(
            loop=loop,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=InstrumentProvider(),
            api_key=config.api_key,
            user_key=config.user_key,
            instrument_ids=config.instrument_ids,
        )


# ── Client ────────────────────────────────────────────────────────────────────

class EToroDataClient(LiveMarketDataClient):
    def __init__(self, loop, msgbus, cache, clock, instrument_provider,
                 api_key, user_key, instrument_ids):
        super().__init__(
            loop=loop,
            venue=Venue("ETORO"),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            client_id=ClientId("ETORO_WS_CLIENT"),
        )
        self.api_key = api_key
        self.user_key = user_key
        self.instrument_ids = instrument_ids
        self.ws_url = "wss://ws.etoro.com/ws"
        self._ws = None
        self._tick_counter: int = 0
        self._instrument_precision: dict[str, int] = {}  # price precision per eid

        self.instrument_map: dict[str, InstrumentId] = {}
        for eid in self.instrument_ids:
            if eid in ETORO_INSTRUMENTS:
                self.instrument_map[eid] = InstrumentId.from_str(ETORO_INSTRUMENTS[eid])
            else:
                self._log.warning(f"eToro ID {eid} nicht in ETORO_INSTRUMENTS gefunden!")

        self._last_bid: dict[str, float] = {}
        self._last_ask: dict[str, float] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def _connect(self) -> None:
        ssl_context = ssl.create_default_context()
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_CONNECT_ATTEMPTS + 1):
            delay = min(10 * attempt, 60)
            try:
                self._log.info(
                    f"WebSocket Verbindungsversuch {attempt}/{_MAX_CONNECT_ATTEMPTS} "
                    f"zu {self.ws_url} ...",
                    LogColor.BLUE,
                )
                self._ws = await asyncio.wait_for(
                    websockets.connect(
                        self.ws_url,
                        ssl=ssl_context,
                        ping_interval=20,
                        ping_timeout=20,
                    ),
                    timeout=_CONNECT_TIMEOUT_S,
                )
                self._log.info("WebSocket verbunden. Authentifiziere...", LogColor.GREEN)
                self._register_instruments()
                self._last_bid.clear()
                self._last_ask.clear()
                await self._authenticate()
                self.create_task(self._message_loop(), log_msg="message_loop")
                return

            except Exception as e:
                last_exc = e
                self._log.warning(
                    f"Verbindungsversuch {attempt}/{_MAX_CONNECT_ATTEMPTS} "
                    f"fehlgeschlagen: {e}",
                    LogColor.YELLOW,
                )
                if attempt < _MAX_CONNECT_ATTEMPTS:
                    self._log.info(
                        f"Warte {delay}s vor nächstem Versuch...", LogColor.BLUE
                    )
                    await asyncio.sleep(delay)

        self._log.error(
            f"WebSocket nach {_MAX_CONNECT_ATTEMPTS} Versuchen nicht erreichbar "
            f"(letzter Fehler: {last_exc}). Erzwinge Neustart via systemd...",
            LogColor.RED,
        )
        os._exit(1)

    async def _disconnect(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
            self._log.info("WebSocket geschlossen.", LogColor.BLUE)

    # ── Boilerplate-Methoden ──────────────────────────────────────────────────

    async def _subscribe_quote_ticks(self, command) -> None: pass
    async def _unsubscribe_quote_ticks(self, command) -> None: pass
    async def _subscribe_trade_ticks(self, command) -> None: pass
    async def _unsubscribe_trade_ticks(self, command) -> None: pass
    async def _subscribe_bars(self, command) -> None: pass
    async def _unsubscribe_bars(self, command) -> None: pass
    async def _subscribe_instrument(self, command) -> None: pass
    async def _unsubscribe_instrument(self, command) -> None: pass
    async def _subscribe_instruments(self, command) -> None: pass
    async def _unsubscribe_instruments(self, command) -> None: pass
    async def _subscribe_order_book_deltas(self, command) -> None: pass
    async def _unsubscribe_order_book_deltas(self, command) -> None: pass
    async def _subscribe_instrument_status(self, command) -> None: pass
    async def _unsubscribe_instrument_status(self, command) -> None: pass
    async def _subscribe_instrument_close(self, command) -> None: pass
    async def _unsubscribe_instrument_close(self, command) -> None: pass
    async def _request_instrument(self, request) -> None: pass
    async def _request_instruments(self, request) -> None: pass
    async def _request_quote_ticks(self, request) -> None: pass
    async def _request_trade_ticks(self, request) -> None: pass
    async def _request_bars(self, request) -> None: pass

    # ── Instruments ───────────────────────────────────────────────────────────

    def _register_instruments(self) -> None:
        ts = self._clock.timestamp_ns()
        for eid, instr_id in self.instrument_map.items():
            sym = str(instr_id.symbol).upper()

            # ── Price precision ───────────────────────────────────────────────
            if "SHIB" in sym or "PEPE" in sym:
                price_prec, price_incr = 8, 1e-8
            elif "BTC" in sym or "ETH" in sym:
                price_prec, price_incr = 2, 0.01
            else:
                price_prec, price_incr = 5, 0.00001

            # ── Instrument-Typ ────────────────────────────────────────────────
            # Die Live-Strategie verlässt sich auf die korrekte size_precision
            # um Quantities vor dem API-Call via instrument.make_qty() sauber zu runden.
            size_prec = get_size_precision(str(instr_id))
            is_crypto = size_prec > 0

            size_inc_val = round(10 ** (-size_prec), size_prec) if size_prec > 0 else 1.0

            inst = Equity(
                instrument_id=instr_id,
                raw_symbol=instr_id.symbol,
                currency=USD,
                price_precision=price_prec,
                price_increment=Price(price_incr, precision=price_prec),
                size_precision=size_prec,
                size_increment=Quantity(size_inc_val, precision=size_prec),
                lot_size=Quantity(size_inc_val, precision=size_prec),
                ts_event=ts,
                ts_init=ts,
            )

            self._instrument_precision[eid] = price_prec

            self._instrument_provider.add(inst)
            self._cache.add_instrument(inst)
            self._msgbus.publish(topic=f"data.instrument.ETORO.{inst.id}", msg=inst)
            self._log.info(
                f"Instrument registriert: {instr_id} "
                f"[{'Crypto' if is_crypto else 'Equity'}] "
                f"price_prec={price_prec}, size_prec={inst.size_precision}",
                LogColor.BLUE,
            )

    # ── Auth & Subscribe ──────────────────────────────────────────────────────

    async def _authenticate(self) -> None:
        auth_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Authenticate",
            "data": {"userKey": self.user_key, "apiKey": self.api_key},
        }
        await self._ws.send(json.dumps(auth_payload))

        while True:
            raw_resp = await self._ws.recv()
            if raw_resp not in (b"", b"\x00", ""):
                break

        try:
            resp = json.loads(raw_resp)
            self._log.info(f"Auth-Antwort: {resp}", LogColor.CYAN)
            if resp.get("status") not in (None, "OK", "ok", 200):
                raise RuntimeError(f"Authentifizierung fehlgeschlagen: {resp}")
        except json.JSONDecodeError:
            self._log.warning(
                f"Auth-Antwort konnte nicht geparst werden: {raw_resp!r}",
                LogColor.YELLOW,
            )
        await self._subscribe_etoro_instruments()

    async def _subscribe_etoro_instruments(self) -> None:
        topics = [f"instrument:{eid}" for eid in self.instrument_map.keys()]
        sub_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Subscribe",
            "data": {"topics": topics, "snapshot": True},
        }
        await self._ws.send(json.dumps(sub_payload))
        self._log.info(f"Abonniert: {topics}", LogColor.CYAN)

    # ── Message Loop ──────────────────────────────────────────────────────────

    async def _message_loop(self) -> None:
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
                        self._process_message(msg)

            self._log.warning(
                "WebSocket vom Server sauber geschlossen. "
                "Erzwinge Neustart via systemd...",
                LogColor.YELLOW,
            )
            os._exit(1)

        except websockets.exceptions.ConnectionClosedOK:
            self._log.warning(
                "WebSocket sauber getrennt (CloseOK). Erzwinge Neustart...",
                LogColor.YELLOW,
            )
            os._exit(1)
        except websockets.exceptions.ConnectionClosedError as e:
            self._log.error(
                f"WebSocket Verbindungsfehler: {e}. Erzwinge Neustart...",
                LogColor.RED,
            )
            os._exit(1)
        except asyncio.CancelledError:
            self._log.info("Message Loop beendet (Cancelled).", LogColor.BLUE)
            raise
        except Exception as e:
            self._log.error(
                f"Unerwarteter WebSocket-Fehler: {e}. Erzwinge Neustart...",
                LogColor.RED,
            )
            os._exit(1)

    # ── Message Processing ────────────────────────────────────────────────────

    def _process_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type not in ("Trading.Instrument.Rate", "Snapshot"):
            return

        try:
            content_raw = msg.get("content", {})
            content: dict = (
                json.loads(content_raw)
                if isinstance(content_raw, str)
                else content_raw
            )
            if not isinstance(content, dict):
                return

            topic = msg.get("topic", "")
            instr_id = (
                topic.split(":")[1]
                if topic.startswith("instrument:")
                else str(content.get("InstrumentID", ""))
            )

            if instr_id not in self.instrument_map:
                return

            if msg_type == "Snapshot":
                is_open = content.get("IsMarketOpen") == "true"
                allow_buy = content.get("AllowBuy") == "true"
                self._log.info(
                    f"Snapshot {self.instrument_map[instr_id]}: "
                    f"MarketOpen={is_open}, AllowBuy={allow_buy}",
                    LogColor.MAGENTA,
                )

            bid_changed = ask_changed = False

            if "Bid" in content:
                self._last_bid[instr_id] = float(content["Bid"])
                bid_changed = True
            if "Ask" in content:
                self._last_ask[instr_id] = float(content["Ask"])
                ask_changed = True

            if not bid_changed and not ask_changed and msg_type != "Snapshot":
                return

            bid = self._last_bid.get(instr_id)
            ask = self._last_ask.get(instr_id)
            if bid is None or ask is None:
                return

            date_str = content.get("Date")
            if date_str:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                ts = int(dt.timestamp() * 1e9)
            else:
                ts = self._clock.timestamp_ns()

            price_prec = self._instrument_precision.get(instr_id, 5)
            tick = QuoteTick(
                instrument_id=self.instrument_map[instr_id],
                bid_price=Price(bid, precision=price_prec),
                ask_price=Price(ask, precision=price_prec),
                bid_size=Quantity(1.0, precision=0),
                ask_size=Quantity(1.0, precision=0),
                ts_event=ts,
                ts_init=self._clock.timestamp_ns(),
            )
            self._handle_data(tick)

            self._tick_counter += 1
            if self._tick_counter % _HEARTBEAT_INTERVAL == 0:
                self._log.info(
                    f"Heartbeat: {self._tick_counter} Ticks verarbeitet.",
                    LogColor.BLUE,
                )

        except Exception as e:
            self._log.error(
                f"Fehler beim Verarbeiten der Nachricht: {e}\n"
                f"{traceback.format_exc()}\n"
                f"Nachricht: {msg}"
            )