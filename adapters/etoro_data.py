import asyncio
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
    # Nautilus lifecycle hooks (async, called by the framework)
    # -------------------------------------------------------------------------

    async def _connect(self) -> None:
        """Called by LiveMarketDataClient.connect() — framework sets connected after this returns."""
        ssl_context = ssl.create_default_context()
        self._ws = await websockets.connect(self.ws_url, ssl=ssl_context)
        self._log.info("WebSocket connected. Authenticating...", LogColor.GREEN)
        self._register_instruments()
        await self._authenticate()
        # Start receive loop as background task
        self.create_task(self._message_loop(), log_msg="message_loop")

    async def _disconnect(self) -> None:
        """Called by LiveMarketDataClient.disconnect() — framework sets disconnected after this returns."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
            self._log.info("WebSocket closed.", LogColor.BLUE)

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
        await self._subscribe_instrument()

    async def _subscribe_instrument(self) -> None:
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
        if msg.get("type") not in ("Trading.Instrument.Rate", "Snapshot"):
            return
        try:
            content = (
                json.loads(msg["content"])
                if isinstance(msg.get("content"), str)
                else msg.get("content")
            )
            instr_id = str(
                content.get("InstrumentID") or content.get("InstrumentId") or ""
            )
            if instr_id not in self.instrument_map:
                self._log.debug(f"Unbekannte InstrumentID '{instr_id}', überspringe.")
                return

            if content.get("Bid") is not None:
                self._last_bid[instr_id] = float(content["Bid"])
            if content.get("Ask") is not None:
                self._last_ask[instr_id] = float(content["Ask"])

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
            self._handle_quote_tick(tick)
        except Exception as e:
            self._log.error(f"Error parsing message: {e}")
