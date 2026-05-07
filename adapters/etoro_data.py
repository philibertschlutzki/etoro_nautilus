import asyncio
import json
import uuid
import time
import websockets
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.identifiers import InstrumentId, Venue, ClientId
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
        # Initialisierung mit strikten Nautilus Typen
        super().__init__(
            loop=loop,
            venue=Venue("ETORO"),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            client_id=ClientId("ETORO_WS_CLIENT")
        )
        self.api_key = api_key
        self.user_key = user_key
        
        # URL-Korrektur: Nutze den Endpunkt aus dem erfolgreichen Tracker
        self.ws_url = "wss://ws.etoro.com/ws" 
        self._ws = None
        
        # Mapping: eToro ID -> Nautilus InstrumentId
        # ID "1" wurde in deinem Tracker-Log für TSLA (oder EUR/USD) empfangen
        self.instrument_map = {
            "1": InstrumentId.from_str("TSLA.NASDAQ") 
        }

    def connect(self):
        """
        Synchrone Schnittstelle für die Nautilus Engine.
        Delegiert den Verbindungsaufbau an den Kernel-Loop.
        """
        self._log.info(f"Connecting to eToro WebSocket: {self.ws_url}")
        # Zugriff auf den internen Loop der Nautilus-Basisklasse
        self._loop.create_task(self._run_connect())

    async def _run_connect(self):
        """Interne asynchrone Methode für den Handshake."""
        try:
            # Header hinzugefügt, um HTTP 422 Fehler zu vermeiden
            extra_headers = {
                "User-Agent": "NautilusTrader/1.226.0",
                "Origin": "https://www.etoro.com"
            }
            
            self._ws = await websockets.connect(
                self.ws_url,
                extra_headers=extra_headers
            )
            self._log.info("WebSocket connected. Authenticating...")
            await self._authenticate()
        except Exception as e:
            self._log.error(f"Connection error: {e}")
            self.disconnect()

    async def _authenticate(self):
        auth_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Authenticate",
            "data": {
                "userKey": self.user_key,
                "apiKey": self.api_key
            }
        }
        await self._ws.send(json.dumps(auth_payload))
        
        # Bestätigung vom Server abwarten
        response_str = await self._ws.recv()
        self._log.info(f"Auth response: {response_str}")
        
        # Nachrichten-Schleife und Abonnements starten
        asyncio.create_task(self._message_loop())
        await self._subscribe_instrument()

    def disconnect(self):
        self._log.info("Disconnecting from eToro...")
        if self._ws:
            self._loop.create_task(self._ws.close())
            self._ws = None

    async def _subscribe_instrument(self):
        # Abonniere Instrument ID 1
        sub_payload = {
            "id": str(uuid.uuid4()),
            "operation": "Subscribe",
            "data": {
                "topics": ["instrument:1"],
                "snapshot": True
            }
        }
        self._log.info("Subscribing to topic 'instrument:1'")
        await self._ws.send(json.dumps(sub_payload))

    async def _message_loop(self):
        try:
            async for message_str in self._ws:
                # Heartbeat-Handling (Null-Bytes ignorieren)
                if not message_str or message_str == b'\x00':
                    continue
                    
                data = json.loads(message_str)
                if "messages" in data:
                    for msg in data["messages"]:
                        await self._process_message(msg)
        except Exception as e:
            self._log.error(f"WebSocket loop error: {e}")
        finally:
            self.disconnect()

    async def _process_message(self, msg):
        msg_type = msg.get("type")
        
        # Akzeptiert Snapshots und Live-Updates
        if msg_type in ("Trading.Instrument.Rate", "Snapshot"):
            try:
                content_str = msg.get("content", "{}")
                
                # eToro sendet 'content' oft als String, der erneut geparst werden muss
                if isinstance(content_str, str):
                    content = json.loads(content_str)
                else:
                    content = content_str
                
                # ID-Extraktion (eToro nutzt verschiedene Schreibweisen)
                instr_id = content.get("InstrumentID") or content.get("InstrumentId")
                
                if str(instr_id) in self.instrument_map:
                    ask = content.get("Ask")
                    bid = content.get("Bid")
                    
                    if ask is not None and bid is not None:
                        ts = time.time_ns()
                        tick = QuoteTick(
                            instrument_id=self.instrument_map[str(instr_id)],
                            bid=float(bid),
                            ask=float(ask),
                            bid_size=1.0,
                            ask_size=1.0,
                            ts_event=ts,
                            ts_init=ts
                        )
                        self._log.info(f"Tick received: {tick}")
                        self.handle_quote_tick(tick)
            except (json.JSONDecodeError, ValueError) as e:
                self._log.error(f"Error parsing message content: {e}")
