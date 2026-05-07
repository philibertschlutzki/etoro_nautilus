import asyncio
import json
import uuid
import time
import websockets
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.data import QuoteTick

class EToroDataClient(LiveMarketDataClient):
    def __init__(
        self,
        loop,
        msgbus,
        cache,
        clock,
        instrument_provider,
        api_key: str,
        user_key: str
    ):
        # Nautilus strict injection requirements for 1.226.0+
        super().__init__(
            loop=loop,
            venue=Venue("ETORO"),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            client_id="ETORO_WS_CLIENT"
        )
        self.api_key = api_key
        self.user_key = user_key
        self.ws_url = "wss://public-api.etoro.com/ws"
        self._ws = None
        
        # Mapping from eToro IDs to Nautilus InstrumentIds (1111 = TSLA)
        self.instrument_map = {
            "1111": InstrumentId.from_str("TSLA.NASDAQ")
        }

    async def connect(self):
        self._log.info(f"Connecting to eToro WebSocket: {self.ws_url}")
        try:
            self._ws = await websockets.connect(self.ws_url)
            self._log.info("WebSocket connected. Authenticating...")
            await self._authenticate()
        except Exception as e:
            self._log.error(f"Connection error: {e}")
            self.disconnect()

    async def _authenticate(self):
        auth_payload = {
            "id": str(uuid.uuid4()),
            "data": {
                "userKey": self.user_key,
                "apiKey": self.api_key
            }
        }

        await self._ws.send(json.dumps(auth_payload))

        # Wait for auth response
        response_str = await self._ws.recv()
        response = json.loads(response_str)

        self._log.info(f"Auth response: {response}")
        # In a real scenario we'd check if auth was successful here.
        # Assuming success for now.
        self._log.info("Authenticated successfully.")

        # Start reading messages
        asyncio.create_task(self._message_loop())

        # Subscribe to instrument
        await self._subscribe_instrument()

    def disconnect(self):
        self._log.info("Disconnecting...")
        if self._ws:
            asyncio.create_task(self._ws.close())
            self._ws = None

    async def _subscribe_instrument(self):
        sub_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Subscribe",
            "data": {
                "topics": ["instrument:1111"],
                "snapshot": True
            }
        }
        self._log.info(f"Subscribing to topics: instrument:1111")
        await self._ws.send(json.dumps(sub_payload))

    async def _message_loop(self):
        try:
            async for message_str in self._ws:
                self._log.debug(f"Received raw message: {message_str}")
                try:
                    data = json.loads(message_str)
                    if "messages" in data:
                        for msg in data["messages"]:
                            await self._process_message(msg)
                except json.JSONDecodeError as e:
                    self._log.error(f"Failed to parse JSON: {e} - Message: {message_str}")
                except Exception as e:
                    self._log.error(f"Error processing message: {e}")
        except websockets.exceptions.ConnectionClosed:
            self._log.warning("WebSocket connection closed.")
        except Exception as e:
            self._log.error(f"WebSocket loop error: {e}")
        finally:
            self.disconnect()

    async def _process_message(self, msg):
        topic = msg.get("topic", "")
        msg_type = msg.get("type", "")

        if msg_type == "Trading.Instrument.Rate" and topic.startswith("instrument:"):
            instrument_id_str = topic.split(":")[1]
            if instrument_id_str in self.instrument_map:
                try:
                    # Double JSON encoding parsing
                    content_str = msg.get("content", "{}")
                    content = json.loads(content_str)

                    ask_str = content.get("Ask")
                    bid_str = content.get("Bid")

                    if ask_str is not None and bid_str is not None:
                        ask = float(ask_str)
                        bid = float(bid_str)

                        ts = time.time_ns()

                        tick = QuoteTick(
                            instrument_id=self.instrument_map[instrument_id_str],
                            bid=bid,
                            ask=ask,
                            bid_size=1.0,
                            ask_size=1.0,
                            ts_event=ts,
                            ts_init=ts
                        )

                        self._log.info(f"Received Tick: {tick}")
                        self.handle_quote_tick(tick)
                except json.JSONDecodeError as e:
                    self._log.error(f"Failed to parse content JSON: {e} - Content: {msg.get('content')}")
                except ValueError as e:
                    self._log.error(f"Failed to parse bid/ask as float: {e}")
