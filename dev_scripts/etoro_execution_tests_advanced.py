import asyncio
import os
import sys
import uuid
import aiohttp

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from nautilus_trader.config import LoggingConfig, StrategyConfig, TradingNodeConfig
from nautilus_trader.core.message import Event
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled, OrderRejected, PositionClosed
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.live.node import TradingNode
from nautilus_trader.trading.strategy import Strategy

from adapters.instrument_map import ETORO_INSTRUMENTS
from config.setups import ETORO_API_TEST
from adapters.etoro_config import EToroExecClientConfig, EToroLiveExecClientFactory
from adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory

load_dotenv()
API_KEY = os.environ.get("ETORO_API_KEY")
USER_KEY = os.environ.get("ETORO_USER_KEY")


class ApiAdvancedExecutionTestStrategy(Strategy):
    """
    Testet erweiterte eToro-Execution-Features:
    Short-Eröffnung, Stop Loss, Take Profit, Trailing Stop Loss.
    """

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(ETORO_API_TEST["symbol"])
        self.phase = 0
        self.short_order_id = None
        self.short_filled = False
        self.position_closed = False
        self._test_aborted = False

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.instrument_id)
        self.log.info(
            f"🚀 Starte erweiterte API-Tests für {self.instrument_id} (Phase {self.phase})"
        )

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if self.phase == 0:
            sl_pct = 0.05
            tp_pct = 0.05
            ask_price = float(tick.ask_price)

            raw_qty = float(ETORO_API_TEST["trade_amount_usd"]) / ask_price
            qty = Quantity(max(1, round(raw_qty)), precision=0)

            short_order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=qty,
                time_in_force=TimeInForce.GTC,
                tags=[f"SL:{sl_pct}", f"TP:{tp_pct}", "TSL:1"],
            )
            self.short_order_id = short_order.client_order_id
            self.log.info(f"Sende Market SELL mit SL:{sl_pct}, TP:{tp_pct}, TSL:1")
            self.submit_order(short_order)
            self.phase = 1

    def on_order_filled(self, event: OrderFilled) -> None:
        if self.phase == 1 and event.client_order_id == self.short_order_id:
            self.log.info(f"Short-Order gefüllt: {event.last_qty} @ {event.last_px}")
            self.short_filled = True

            # Start background task to verify TSL field name via PnL
            self.create_task(self._verify_tsl_field(), log_msg="verify_tsl_field")

            close_order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=event.last_qty,
                time_in_force=TimeInForce.GTC,
            )
            self.log.info("Sende Market BUY zum Schließen der Short-Position")
            self.submit_order(close_order)
            self.phase = 2

    async def _verify_tsl_field(self) -> None:
        await asyncio.sleep(3.0)  # Wait for PnL propagation
        base_url = "https://public-api.etoro.com/api/v1/trading"
        env = ETORO_API_TEST["environment"]
        pnl_url = f"{base_url}/info/{env}/pnl"
        headers = {
            "x-api-key": API_KEY,
            "x-user-key": USER_KEY,
            "Content-Type": "application/json",
            "x-request-id": str(uuid.uuid4())
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(pnl_url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        data = data.get("clientPortfolio", data)
                        positions = data.get("Positions", data.get("positions", []))

                        etoro_id = None
                        for k, v in ETORO_INSTRUMENTS.items():
                            if v == ETORO_API_TEST["symbol"]:
                                etoro_id = int(k)
                                break

                        found_short = False
                        for p in positions:
                            p_lower = {str(k).lower(): v for k, v in p.items()}
                            try:
                                p_iid = int(p_lower.get("instrumentid", -1))
                            except (ValueError, TypeError):
                                p_iid = -1

                            if p_iid == etoro_id and p_lower.get("isbuy") is False:
                                found_short = True
                                self.log.debug(f"PnL Position Dict: {p}")

                                has_tsl = False
                                for key in p.keys():
                                    if key.lower() in ("istrailingstop", "istslenabled") and p[key]:
                                        has_tsl = True
                                        self.log.info(f"✅ TSL confirmed using key: {key}")
                                        break

                                if not has_tsl:
                                    self.log.warning("TSL field not confirmed in PnL response - verify IsTrailingStop field name with eToro API docs")
                                break

                        if not found_short:
                            self.log.debug("Short position not found in PnL during TSL verification.")
        except Exception as e:
            self.log.error(f"Failed to verify TSL field in PnL: {e}")

    def on_position_closed(self, event: PositionClosed) -> None:
        self.position_closed = True
        self.log.info(f"Position geschlossen: {event.position_id}")

    def on_order_rejected(self, event: OrderRejected) -> None:
        self.log.error(f"Order abgewiesen: {event.reason}")
        self._test_aborted = True
        self.stop()

    def on_stop(self) -> None:
        self.unsubscribe_quote_ticks(self.instrument_id)

    def is_finished(self) -> bool:
        return self._test_aborted or self.position_closed

async def emergency_cleanup(
    api_key: str,
    user_key: str,
    environment: str,
    symbol: str,
    settle_delay_s: float = 5.0,
    max_retries: int = 3
) -> None:
    """Sicherheitsnetz: Schliesst alle verbleibenden Positionen und Limit-Orders per REST-Call."""

    base_url = "https://public-api.etoro.com/api/v1/trading"
    pnl_url = f"{base_url}/info/{environment}/pnl"
    exec_path = "/execution/demo" if environment == "demo" else "/execution"

    headers = {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "Content-Type": "application/json",
    }

    etoro_id: int | None = None
    for k, v in ETORO_INSTRUMENTS.items():
        if v == symbol:
            etoro_id = int(k)
            break

    if etoro_id is None:
        print(f"⚠️  Symbol {symbol} nicht in ETORO_INSTRUMENTS — Cleanup abgebrochen.")
        return

    def _safe_int(val, default: int = -1) -> int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    async with aiohttp.ClientSession() as session:
        for attempt in range(1, max_retries + 1):
            delay = settle_delay_s * attempt
            print(f"\n🧹 Emergency Cleanup — Versuch {attempt}/{max_retries} (warte {delay}s)...")
            await asyncio.sleep(delay)

            h_get = headers.copy()
            h_get["x-request-id"] = str(uuid.uuid4())

            async with session.get(pnl_url, headers=h_get) as resp:
                if resp.status != 200:
                    print(f"⚠️ PnL Abruf fehlgeschlagen: HTTP {resp.status}")
                    return

                data = await resp.json()
                data = data.get("clientPortfolio", data)  # Unwrap Real-PnL-Envelope
                print(f"   [DEBUG] PnL-Keys: {list(data.keys())}")
                print(f"   [DEBUG] Positionen: {len(data.get('positions', data.get('Positions', [])))}")
                print(f"   [DEBUG] OrdersForOpen: {len(data.get('ordersForOpen', data.get('OrdersForOpen', [])))}")

                found_anything = False

                positions = data.get("Positions", data.get("positions", []))
                orders_open = (
                    data.get("ordersForOpen", data.get("OrdersForOpen", []))
                    + data.get("entryOrders", data.get("EntryOrders", []))
                    + data.get("orders", data.get("Orders", []))
                )

                # 1. Offene MARKET Positionen (LONG/SHORT) schliessen
                for p in positions:
                    p_lower = {str(k).lower(): v for k, v in p.items()}
                    if _safe_int(p_lower.get("instrumentid")) != etoro_id:
                        continue
                    is_settled = p_lower.get("issettled", False)
                    if is_settled:
                        print(f"   ℹ️  Position {p_lower.get('positionid')} ist settled — versuche Close trotzdem...")
                    found_anything = True
                    pos_id = p_lower.get("positionid")
                    print(f"   -> Schliesse {'settled ' if is_settled else ''}Position {pos_id}...")

                    payload = {"InstrumentID": etoro_id, "UnitsToDeduct": None}
                    h_close = headers.copy()
                    h_close["x-request-id"] = str(uuid.uuid4())

                    async with session.post(
                        f"{base_url}{exec_path}/market-close-orders/positions/{pos_id}",
                        json=payload,
                        headers=h_close,
                    ) as c_resp:
                        if c_resp.status in range(200, 300):
                            print(f"   ✅ Position {pos_id} erfolgreich geschlossen.")
                        else:
                            err = await c_resp.text()
                            print(
                                f"   ❌ Fehler beim Schliessen von {pos_id}: HTTP {c_resp.status} - {err}"
                            )

                # 2. Offene LIMIT Orders schliessen
                for o in orders_open:
                    o_lower = {str(k).lower(): v for k, v in o.items()}
                    if _safe_int(o_lower.get("instrumentid")) != etoro_id:
                        continue
                    is_settled = o_lower.get("issettled", False)
                    if is_settled:
                        print(f"   ℹ️  Order {o_lower.get('orderid')} ist settled — versuche Cancel trotzdem...")
                    found_anything = True
                    ord_id = o_lower.get("orderid")
                    print(f"   -> Storniere {'settled ' if is_settled else ''}Limit-Order {ord_id}...")

                    h_del = headers.copy()
                    h_del["x-request-id"] = str(uuid.uuid4())

                    async with session.delete(
                        f"{base_url}{exec_path}/limit-orders/{ord_id}", headers=h_del
                    ) as d_resp:
                        if d_resp.status in range(200, 300):
                            print(f"   ✅ Limit-Order {ord_id} erfolgreich storniert.")
                        else:
                            err = await d_resp.text()
                            print(
                                f"   ❌ Fehler beim Stornieren von {ord_id}: HTTP {d_resp.status} - {err}"
                            )

            if found_anything:
                break
            if attempt < max_retries:
                print(f"   ↩️  Keine Positionen gefunden, erneuter Versuch in {settle_delay_s * (attempt+1)}s...")

        if not found_anything:
            print(
                "⚠️  WARNUNG: Keine offenen Positionen oder Limits für "
                f"InstrumentID {etoro_id} nach {max_retries} Versuchen gefunden.\n"
                "   Manuell auf eToro prüfen!"
            )
        else:
            print("✅ Emergency Cleanup abgeschlossen.")

async def main() -> None:
    if not API_KEY or not USER_KEY:
        print("FEHLER: ETORO_API_KEY oder ETORO_USER_KEY fehlen in der .env.")
        sys.exit(1)

    etoro_id: str | None = None
    for k, v in ETORO_INSTRUMENTS.items():
        if v == ETORO_API_TEST["symbol"]:
            etoro_id = k
            break

    if etoro_id is None:
        print(f"FEHLER: {ETORO_API_TEST['symbol']} nicht in ETORO_INSTRUMENTS.")
        sys.exit(1)

    print("🧹 Pre-Start Cleanup — schließe alle offenen Positionen für Test-Instrument...")
    if not ETORO_API_TEST["dry_run"]:
        await emergency_cleanup(
            API_KEY,
            USER_KEY,
            ETORO_API_TEST["environment"],
            ETORO_API_TEST["symbol"],
            settle_delay_s=2.0,
            max_retries=2,
        )

    config = TradingNodeConfig(
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
            log_directory="logs",
        ),
        data_clients={
            "ETORO_WS_CLIENT": EToroDataClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
                instrument_ids=[etoro_id],
            )
        },
        exec_clients={
            "ETORO": EToroExecClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
                environment=ETORO_API_TEST["environment"],
                dry_run=ETORO_API_TEST["dry_run"],
                enable_trailing_stop=True,
            )
        },
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)
    node.add_exec_client_factory("ETORO", EToroLiveExecClientFactory)

    strategy = ApiAdvancedExecutionTestStrategy(config=StrategyConfig())
    node.trader.add_strategy(strategy)

    node.build()
    print(
        f"Trading Node gestartet (Environment: {ETORO_API_TEST['environment'].upper()}). Führe Tests aus (max. 90 s) ..."
    )

    loop = asyncio.get_event_loop()
    run_task = loop.run_in_executor(None, node.run)

    timeout = 180  # 2 × 90s für zwei sequentielle Fills (Short-Open + Close)
    while timeout > 0 and not strategy.is_finished():
        await asyncio.sleep(1)
        timeout -= 1

    if strategy.is_finished() and not strategy._test_aborted:
        print("\n✅ Alle Execution-Tests erfolgreich abgeschlossen!")
    elif strategy._test_aborted:
        print("\n❌ Test abgebrochen (Order abgewiesen). Cleanup wird ausgeführt.")
    else:
        print("\n⚠️ Timeout — Testlauf unvollständig.")

    # Node sauber herunterfahren
    node.stop()
    try:
        await asyncio.wait_for(run_task, timeout=5.0)
    except asyncio.TimeoutError:
        pass

    # --- Das Sicherheitsnetz ---
    if not ETORO_API_TEST["dry_run"]:
        await emergency_cleanup(
            API_KEY,
            USER_KEY,
            ETORO_API_TEST["environment"],
            ETORO_API_TEST["symbol"],
            settle_delay_s=5.0,
        )


if __name__ == "__main__":
    confirm = input(
        f"Achtung: Erweiterte eToro API-Tests ({ETORO_API_TEST['environment'].upper()}) — "
        "Short-Positionen + SL/TP/TSL werden getestet!\n"
        "Weiter? (j/N): "
    )
    if confirm.strip().lower() == "j":
        asyncio.run(main())
