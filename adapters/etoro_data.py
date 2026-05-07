import asyncio
import json
import uuid
import ssl
import websockets
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.config import LiveDataClientConfig
from nautilus_trader.model.identifiers import InstrumentId, Venue, ClientId
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.common.providers import InstrumentProvider


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
        self.instrument_map = {"1": InstrumentId.from_str("TSLA.NASDAQ")}
        # Cache für letzte bekannte Bid/Ask pro Instrument-ID
        self._last_bid: dict[str, float] = {}
        self._last_ask: dict[str, float] = {}

    def connect(self):
        self._loop.create_task(self._run_connect())

    async def _run_connect(self):
        try:
            ssl_context = ssl.create_default_context()
            headers = {
                "User-Agent": "NautilusTrader/1.226.0",
                "Origin": "https://www.etoro.com",
            }
            try:

                self._ws = await websockets.connect(
                    self.ws_url, extra_headers=headers, ssl=ssl_context
                )
            except TypeError:
                self._log.warning("Retrying connection without extra_headers...")
                self._ws = await websockets.connect(self.ws_url, ssl=ssl_context)

            self._log.info("WebSocket connected. Authenticating...")
            await self._authenticate()
        except Exception as e:
            self._log.error(f"Connection error: {e}")
            self.disconnect()

    async def _authenticate(self):
        auth_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Authenticate",
            "data": {"userKey": self.user_key, "apiKey": self.api_key},
        }
        await self._ws.send(json.dumps(auth_payload))
        await self._ws.recv()
        asyncio.create_task(self._message_loop())
        await self._subscribe_instrument()

    async def _subscribe_instrument(self):
        sub_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Subscribe",
            "data": {"topics": ["instrument:1"], "snapshot": True},
        }
        self._log.info("Subscribing to TSLA (ID: 1)...")
        await self._ws.send(json.dumps(sub_payload))

    async def _message_loop(self):
        try:
            async for message_str in self._ws:
                if not message_str or message_str == b"\x00":
                    continue
                data = json.loads(message_str)
                if "messages" in data:
                    for msg in data["messages"]:
                        await self._process_message(msg)
        except Exception as e:
            self._log.error(f"WebSocket error: {e}")
        finally:
            self.disconnect()

    async def _process_message(self, msg):
        if msg.get("type") not in ("Trading.Instrument.Rate", "Snapshot"):
            return
        try:
            content = (
                json.loads(msg["content"])
                if isinstance(msg.get("content"), str)
                else msg.get("content")
            )
            instr_id = str(
                content.get("InstrumentID") or content.get("InstrumentId")
            )

            # Partielle Updates: Cache aktualisieren, fehlende Seite aus Cache ergänzen
            if content.get("Bid") is not None:
                self._last_bid[instr_id] = float(content["Bid"])
            if content.get("Ask") is not None:
                self._last_ask[instr_id] = float(content["Ask"])

            bid = self._last_bid.get(instr_id)
            ask = self._last_ask.get(instr_id)

            # Noch kein vollständiges Bild (vor dem ersten Snapshot)
            if bid is None or ask is None:
                self._log.debug(
                    f"Waiting for initial Bid/Ask snapshot (instr={instr_id}, "
                    f"bid={bid}, ask={ask})"
                )
                return

            ts = self._clock.utc_now()

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
            self.handle_quote_tick(tick)
        except Exception as e:
            self._log.error(f"Error parsing message: {e}")

    def disconnect(self):
        self._log.info("Closing eToro connection...")
        if self._ws:
            self._loop.create_task(self._ws.close())
            self._ws = None
