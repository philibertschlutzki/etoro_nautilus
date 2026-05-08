import json
import ssl
import uuid
import websockets

from nautilus_trader.common.enums import LogColor
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import LiveDataClientConfig
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import ClientId, InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity


# ── Config ────────────────────────────────────────────────────────────────────

class EToroDataClientConfig(LiveDataClientConfig, frozen=True, kw_only=True):
    api_key: str
    user_key: str


# ── Factory ───────────────────────────────────────────────────────────────────

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
        )


# ── Client ────────────────────────────────────────────────────────────────────

class EToroDataClient(LiveMarketDataClient):
    def __init__(self, loop, msgbus, cache, clock, instrument_provider, api_key, user_key):
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
        self.ws_url = "wss://ws.etoro.com/ws"
        self._ws = None
        self.instrument_map = {"1": InstrumentId.from_str("TSLA.ETORO")}
        self._last_bid: dict[str, float] = {}
        self._last_ask: dict[str, float] = {}

    # -------------------------------------------------------------------------
    # Nautilus lifecycle hooks
    # -------------------------------------------------------------------------

    async def _connect(self) -> None:
        ssl_context = ssl.create_default_context()
        self._ws = await websockets.connect(self.ws_url, ssl=ssl_context)
        self._log.info("WebSocket connected. Authenticating...", LogColor.GREEN)
        self._register_instruments()
        await self._authenticate()
        self.create_task(self._message_loop(), log_msg="message_loop")

    async def _disconnect(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
            self._log.info("WebSocket closed.", LogColor.BLUE)

    # -------------------------------------------------------------------------
    # Abstract subscription stubs required by LiveMarketDataClient
    # The strategy calls subscribe_quote_ticks(); the framework dispatches to
    # _subscribe_quote_ticks(). For eToro the WS subscription is already
    # established in _connect(), so these are intentional no-ops.
    # -------------------------------------------------------------------------

    async def _subscribe_quote_ticks(self, command) -> None:
        self._log.info(
            f"QuoteTick subscription acknowledged for {command.instrument_id} "
            f"(WS already subscribed via _connect)",
            LogColor.BLUE,
        )

    async def _unsubscribe_quote_ticks(self, command) -> None:
        self._log.info(f"Unsubscribe quote_ticks {command.instrument_id} (no-op)")

    async def _subscribe_trade_ticks(self, command) -> None:
        self._log.debug(f"subscribe_trade_ticks not supported: {command.instrument_id}")

    async def _unsubscribe_trade_ticks(self, command) -> None:
        pass

    async def _subscribe_bars(self, command) -> None:
        self._log.debug(f"subscribe_bars not supported: {command.bar_type}")

    async def _unsubscribe_bars(self, command) -> None:
        pass

    async def _subscribe_instrument(self, command) -> None:
        self._log.debug(f"subscribe_instrument not supported: {command.instrument_id}")

    async def _unsubscribe_instrument(self, command) -> None:
        pass

    async def _subscribe_instruments(self, command) -> None:
        pass

    async def _unsubscribe_instruments(self, command) -> None:
        pass

    async def _subscribe_order_book_deltas(self, command) -> None:
        pass

    async def _unsubscribe_order_book_deltas(self, command) -> None:
        pass

    async def _subscribe_instrument_status(self, command) -> None:
        pass

    async def _unsubscribe_instrument_status(self, command) -> None:
        pass

    async def _subscribe_instrument_close(self, command) -> None:
        pass

    async def _unsubscribe_instrument_close(self, command) -> None:
        pass

    async def _request_instrument(self, request) -> None:
        pass

    async def _request_instruments(self, request) -> None:
        pass

    async def _request_quote_ticks(self, request) -> None:
        pass

    async def _request_trade_ticks(self, request) -> None:
        pass

    async def _request_bars(self, request) -> None:
        pass

    # -------------------------------------------------------------------------
    # Instrument registration
    # -------------------------------------------------------------------------

    def _register_instruments(self) -> None:
        ts = self._clock.timestamp_ns()
        tsla = Equity(
            instrument_id=InstrumentId.from_str("TSLA.ETORO"),
            raw_symbol=Symbol("TSLA"),
            currency=USD,
            price_precision=5,
            price_increment=Price(0.00001, precision=5),
            lot_size=Quantity(1, precision=0),
            ts_event=ts,
            ts_init=ts,
        )
        self._instrument_provider.add(tsla)
        self._cache.add_instrument(tsla)
        self._msgbus.publish(topic=f"data.instrument.ETORO.{tsla.id}", msg=tsla)
        self._log.info("Instrument TSLA.ETORO registriert.", LogColor.GREEN)

    # -------------------------------------------------------------------------
    # WebSocket authentication & subscription
    # -------------------------------------------------------------------------

    async def _authenticate(self) -> None:
        auth_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Authenticate",
            "data": {"userKey": self.user_key, "apiKey": self.api_key},
        }
        await self._ws.send(json.dumps(auth_payload))
        resp = await self._ws.recv()
        self._log.debug(f"Auth response: {resp}")
        await self._subscribe_etoro_instrument()

    async def _subscribe_etoro_instrument(self) -> None:
        sub_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Subscribe",
            "data": {"topics": ["instrument:1"], "snapshot": True},
        }
        self._log.info("Subscribing to TSLA (ID: 1)...")
        await self._ws.send(json.dumps(sub_payload))

    # -------------------------------------------------------------------------
    # Message loop
    # -------------------------------------------------------------------------

    async def _message_loop(self) -> None:
        try:
            async for raw in self._ws:
                if not raw or raw == b"\x00":
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    self._log.debug(f"Non-JSON frame: {raw}")
                    continue
                if "messages" in data:
                    for msg in data["messages"]:
                        self._process_message(msg)
        except websockets.exceptions.ConnectionClosed as e:
            self._log.warning(f"WebSocket connection closed: {e}")
        except Exception as e:
            self._log.error(f"WebSocket error: {e}")

    def _process_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type not in ("Trading.Instrument.Rate", "Snapshot"):
            return
        
        try:
            content = (
                json.loads(msg["content"])
                if isinstance(msg.get("content"), str)
                else msg.get("content")
            )
            
            # --- START DER ÄNDERUNG ---
            # Wir ziehen die ID primär aus dem 'topic' (z.B. "instrument:1")
            topic = msg.get("topic", "")
            if topic.startswith("instrument:"):
                instr_id = topic.split(":")[1]
            else:
                # Fallback, falls das topic unerwartet formatiert ist
                instr_id = str(content.get("InstrumentID") or content.get("InstrumentId") or "")
            # --- ENDE DER ÄNDERUNG ---

            if instr_id not in self.instrument_map:
                self._log.debug(f"Unbekannte InstrumentID '{instr_id}', überspringe.")
                return

            bid_changed = False
            ask_changed = False

            if content.get("Bid") is not None:
                self._last_bid[instr_id] = float(content["Bid"])
                bid_changed = True
            if content.get("Ask") is not None:
                self._last_ask[instr_id] = float(content["Ask"])
                ask_changed = True

            # Wenn sich weder Bid noch Ask geändert haben und es kein Snapshot ist, 
            # überspringen wir dieses Update, um Ressourcen zu sparen.
            if not bid_changed and not ask_changed and msg_type != "Snapshot":
                return

            bid = self._last_bid.get(instr_id)
            ask = self._last_ask.get(instr_id)

            if bid is None or ask is None:
                self._log.debug(
                    f"Waiting for initial snapshot (instr={instr_id}, bid={bid}, ask={ask})"
                )
                return

            ts = self._clock.timestamp_ns()
            tick = QuoteTick(
                instrument_id=self.instrument_map[instr_id],
                bid_price=Price(bid, precision=5),
                ask_price=Price(ask, precision=5),
                bid_size=Quantity(1.0, precision=0),
                ask_size=Quantity(1.0, precision=0),
                ts_event=ts,
                ts_init=ts,
            )
            self._log.info(f"Tick: bid={bid:.5f} ask={ask:.5f} [{tick.instrument_id}]")
            # Route QuoteTick in den Nautilus-Datenpfad
            self._handle_data(tick)
            
        except Exception as e:
            self._log.error(f"Error parsing message: {e}")
