import asyncio
import os
import sys
from decimal import Decimal

from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from archive.adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from archive.adapters.etoro_execution import EToroLiveExecClientFactory
from archive.adapters.etoro_config import EToroExecClientConfig
from archive.adapters.instrument_map import ETORO_INSTRUMENTS
from archive.config.setups import ETORO_API_TEST
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")


class ApiOrderTestStrategy(Strategy):
    """Ping-Pong-Test: kauft einmal, schliesst sofort nach dem Fill."""

    def __init__(self, config: StrategyConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(ETORO_API_TEST["symbol"])
        self.usd_amount = Decimal(str(ETORO_API_TEST["trade_amount_usd"]))
        self.buy_submitted = False
        self.position_closed = False
        self.buy_order_id = None

    def on_start(self) -> None:
        self.log.info(
            f"Test gestartet. Warte auf ersten Tick für {self.instrument_id} ..."
        )
        self.subscribe_quote_ticks(self.instrument_id)

    def on_stop(self) -> None:
        self.unsubscribe_quote_ticks(self.instrument_id)
        self.log.info("Strategie gestoppt.")

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if self.buy_submitted:
            return

        # eToro Live-API: Equity-Instrumente akzeptieren nur ganzzahlige Quantities (precision=0).
        # Crypto-Instrumente (BTC, ETH etc.) unterstützen Fraktionen — hier nicht relevant
        # da ETORO_API_TEST auf ein Equity-Instrument zeigt.
        # Backtest-Mock-Instrumente verwenden asset-klassenspezifische size_precision
        # (0 für Equities, 8 für Crypto) — siehe create_mock_instrument() in run_backtest.py.
        raw_qty = float(self.usd_amount) / float(tick.ask_price)
        quantity = Quantity(max(1, round(raw_qty)), precision=0)

        self.log.info(
            f"Sende BUY: {quantity} Units @ Ask {tick.ask_price} "
            f"(~{self.usd_amount} USD)"
        )
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
        )
        self.buy_order_id = order.client_order_id
        self.submit_order(order)
        self.buy_submitted = True

    def on_order_filled(self, event) -> None:
        self.log.info(
            f"Fill: {event.last_qty} Units @ {event.last_px} "
            f"(client_order_id={event.client_order_id})"
        )
        if event.client_order_id == self.buy_order_id and not self.position_closed:
            self.log.info("BUY gefüllt — sende SELL zum Schliessen ...")
            close_order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=event.last_qty,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(close_order)
            self.position_closed = True

    def on_order_denied(self, event) -> None:
        self.log.error(f"Order DENIED: {event.client_order_id} — {event.reason}")

    def on_order_rejected(self, event) -> None:
        self.log.error(f"Order REJECTED: {event.client_order_id} — {event.reason}")

    def on_order_closed(self, event) -> None:
        if self.position_closed and event.client_order_id != self.buy_order_id:
            self.log.info("SELL abgeschlossen. Ping-Pong-Test erfolgreich!")


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

    strategy = ApiOrderTestStrategy(config=StrategyConfig())
    node.trader.add_strategy(strategy)

    node.build()
    print("Trading Node gestartet. Warte auf Trade-Abschluss (max. 90 s) ...")

    loop = asyncio.get_event_loop()
    run_task = loop.run_in_executor(None, node.run)

    timeout = 90
    while timeout > 0 and not strategy.position_closed:
        await asyncio.sleep(1)
        timeout -= 1

    if strategy.position_closed:
        print("✅  Ping-Pong-Test erfolgreich abgeschlossen.")
    else:
        print("⚠️  Timeout — Position wurde nicht innerhalb von 90 s geschlossen.")

    node.stop()
    try:
        await asyncio.wait_for(run_task, timeout=5.0)
    except asyncio.TimeoutError:
        pass


if __name__ == "__main__":
    confirm = input(
        "Achtung: LIVE eToro API-Test — echte Orders werden platziert!\n"
        "Weiter? (j/N): "
    )
    if confirm.strip().lower() == "j":
        asyncio.run(main())