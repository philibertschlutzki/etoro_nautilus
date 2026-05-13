import asyncio
import os
import sys
import uuid
from decimal import Decimal

import aiohttp
from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity, Price
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from adapters.etoro_config import EToroExecClientConfig, EToroLiveExecClientFactory
from adapters.etoro_execution import EToroExecutionClient
from adapters.instrument_map import ETORO_INSTRUMENTS
from config.setups import ETORO_API_TEST
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")


class ApiFullExecutionTestStrategy(Strategy):
    """Testet alle gängigen Execution Endpoints der eToro API nacheinander."""

    def __init__(self, config: StrategyConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(ETORO_API_TEST["symbol"])
        self.usd_amount = Decimal(str(ETORO_API_TEST["trade_amount_usd"]))

        self.phase = 0
        self.limit_buy_id = None
        self.market_buy_id = None

        self.limit_accepted = False
        self.limit_canceled = False
        self.market_filled = False
        self.position_closed = False
        self._test_aborted = False

    def on_start(self) -> None:
        self.log.info(
            f"Full API Test gestartet. Warte auf Daten für {self.instrument_id} ..."
        )
        self.subscribe_quote_ticks(self.instrument_id)

        tick = self.cache.quote_tick(self.instrument_id)
        if tick:
            self.execute_tests(tick)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.execute_tests(tick)

    def execute_tests(self, tick: QuoteTick) -> None:
        if self.phase == 0:
            self.phase = 1

            # --- TEST 1: POST /limit-orders ---
            limit_price_val = float(tick.ask_price) * 0.5
            limit_price = Price(limit_price_val, precision=5)
            limit_qty = Quantity(
                max(1, round(float(self.usd_amount) / limit_price_val)), precision=0
            )

            self.log.info(
                f"[TEST 1] Sende LIMIT BUY: {limit_qty} Units @ {limit_price}"
            )
            limit_order = self.order_factory.limit(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=limit_qty,
                price=limit_price,
                time_in_force=TimeInForce.GTC,
            )
            self.limit_buy_id = limit_order.client_order_id
            self.submit_order(limit_order)

            # --- TEST 2: POST /market-open-orders/by-amount ---
            raw_qty = float(self.usd_amount) / float(tick.ask_price)
            mkt_qty = Quantity(max(1, round(raw_qty)), precision=0)

            self.log.info(
                f"[TEST 2] Sende MARKET BUY: {mkt_qty} Units (~{self.usd_amount} USD)"
            )
            market_order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=mkt_qty,
                time_in_force=TimeInForce.GTC,
            )
            self.market_buy_id = market_order.client_order_id
            self.submit_order(market_order)

    def on_order_accepted(self, event) -> None:
        if event.client_order_id == self.limit_buy_id and not self.limit_canceled:
            self.limit_accepted = True
            self.log.info("[TEST 3] LIMIT Order wurde akzeptiert. Sende Cancel...")
            self.cancel_order(self.cache.order(self.limit_buy_id))

    def on_order_canceled(self, event) -> None:
        if event.client_order_id == self.limit_buy_id:
            self.log.info("✅ LIMIT Order erfolgreich storniert!")
            self.limit_canceled = True

    def on_order_rejected(self, event) -> None:
        self.log.error(f"❌ ORDER REJECTED: {event.client_order_id} - {event.reason}")
        self.log.error("Aborting test due to order rejection.")
        self._test_aborted = True
        self.stop()

    def on_order_filled(self, event) -> None:
        if event.client_order_id == self.market_buy_id and not self.market_filled:
            self.log.info(
                f"✅ MARKET BUY gefüllt: {event.last_qty} Units @ {event.last_px}"
            )
            self.market_filled = True

            self.log.info("[TEST 4] Sende MARKET SELL zum Schliessen der Position...")
            close_order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=event.last_qty,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(close_order)

    def on_order_closed(self, event) -> None:
        if (
            self.market_filled
            and event.client_order_id != self.market_buy_id
            and event.client_order_id != self.limit_buy_id
        ):
            self.log.info("✅ Position erfolgreich geschlossen!")
            self.position_closed = True

    def is_finished(self) -> bool:
        return self._test_aborted or (self.limit_canceled and self.position_closed)


async def emergency_cleanup(
    api_key: str,
    user_key: str,
    environment: str,
    symbol: str,
    settle_delay_s: float = 3.0,
) -> None:
    """Sicherheitsnetz: Schliesst alle verbleibenden Positionen und Limit-Orders per REST-Call."""  # noqa: E501
    print(f"\n🧹 Emergency Cleanup (warte {settle_delay_s}s auf Settlement)...")
    await asyncio.sleep(settle_delay_s)

    base_url = "https://public-api.etoro.com/api/v1/trading"
    pnl_url = f"{base_url}/info/{environment}/pnl"
    exec_path = "/execution/demo" if environment == "demo" else "/execution"

    headers = {
        "x-api-key": api_key,
        "x-user-key": user_key,
        "Content-Type": "application/json",
    }

    etoro_id = None
    for k, v in ETORO_INSTRUMENTS.items():
        if v == symbol:
            etoro_id = k
            break

    if not etoro_id:
        return

    async with aiohttp.ClientSession() as session:
        h_get = headers.copy()
        h_get["x-request-id"] = str(uuid.uuid4())

        async with session.get(pnl_url, headers=h_get) as resp:
            if resp.status != 200:
                print(f"⚠️ PnL Abruf fehlgeschlagen: HTTP {resp.status}")
                return

            data = await resp.json()
            found_anything = False

            positions = data.get("Positions", data.get("positions", []))
            orders_open = data.get("OrdersForOpen", data.get("ordersForOpen", []))

            # 1. Offene MARKET Positionen (LONG/SHORT) schliessen
            for p in positions:
                p_lower = {str(k).lower(): v for k, v in p.items()}
                if str(p_lower.get("instrumentid", "")) == str(etoro_id):
                    found_anything = True
                    pos_id = p_lower.get("positionid")
                    print(f"   -> Schliesse offene Position {pos_id}...")

                    payload = {
                        "UnitsToDeduct": None
                    }  # FIX 6: Kein InstrumentID bei Close
                    h_close = headers.copy()
                    h_close["x-request-id"] = str(uuid.uuid4())

                    async with session.post(
                        f"{base_url}{exec_path}/market-close-orders/positions/{pos_id}",  # noqa: E501
                        json=payload,
                        headers=h_close,
                    ) as c_resp:
                        if c_resp.status in range(200, 300):
                            print(f"   ✅ Position {pos_id} erfolgreich geschlossen.")
                        else:
                            err = await c_resp.text()
                            print(
                                f"   ❌ Fehler beim Schliessen von {pos_id}: HTTP {c_resp.status} - {err}"  # noqa: E501
                            )

            # 2. Offene LIMIT Orders schliessen
            for o in orders_open:
                o_lower = {str(k).lower(): v for k, v in o.items()}
                if str(o_lower.get("instrumentid", "")) == str(etoro_id):
                    found_anything = True
                    ord_id = o_lower.get("orderid")
                    print(f"   -> Storniere Limit-Order {ord_id}...")

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
                                f"   ❌ Fehler beim Stornieren von {ord_id}: HTTP {d_resp.status} - {err}"  # noqa: E501
                            )

            if not found_anything:
                print(
                    "✅ Keine offenen Positionen oder Limits gefunden. Das Konto ist sauber."  # noqa: E501
                )


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
                enable_trailing_stop=False,
            )
        },
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)
    node.add_exec_client_factory("ETORO", EToroLiveExecClientFactory)

    strategy = ApiFullExecutionTestStrategy(config=StrategyConfig())
    node.trader.add_strategy(strategy)

    node.build()
    print(
        f"Trading Node gestartet (Environment: {ETORO_API_TEST['environment'].upper()}). Führe alle Tests aus (max. 90 s) ..."  # noqa: E501
    )

    loop = asyncio.get_event_loop()
    run_task = loop.run_in_executor(None, node.run)

    timeout = 90
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
        f"Achtung: eToro API-Test ({ETORO_API_TEST['environment'].upper()}) — echte Orders und Limits werden platziert!\n"  # noqa: E501
        "Weiter? (j/N): "
    )
    if confirm.strip().lower() == "j":
        asyncio.run(main())
