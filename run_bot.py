import os
from dotenv import load_dotenv
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from adapters.etoro_data import (
    EToroDataClientConfig,
    EToroLiveDataClientFactory,
)
from strategies.etoro_strategy import EToroStrategy, EToroStrategyConfig

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")


def main():
    strategy_config = EToroStrategyConfig(
        strategy_id="EToroStrategy-001",
        instrument_id="TSLA.ETORO",
    )

    config = TradingNodeConfig(
        trader_id="eToro-Bot-01",
        data_clients={
            "ETORO_WS_CLIENT": EToroDataClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
            )
        },
    )

    node = TradingNode(config=config)

    # Factory registrieren – node.build() instantiiert den Client automatisch
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)

    # Strategy registrieren
    strategy = EToroStrategy(config=strategy_config)
    node.add_strategy(strategy)

    node.build()

    print(f"✅ System für {config.trader_id} erfolgreich konfiguriert.")
    print("🚀 Starte eToro-Bot... (Drücke Ctrl+C zum Beenden)")

    try:
        node.run()
    except KeyboardInterrupt:
        print("\n⚠️ Herunterfahren eingeleitet...")
    finally:
        node.stop()
        print("🤖 Bot erfolgreich beendet.")


if __name__ == "__main__":
    main()
