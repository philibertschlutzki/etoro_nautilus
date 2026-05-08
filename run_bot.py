import os
from dotenv import load_dotenv
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode

# Adapter Importe
from adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory

# Config und Strategien Importe
from config.setups import ACTIVE_BOTS
from strategies.sma_crossover import SmaCrossoverStrategy, SmaCrossoverConfig

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")


def main():
    if not API_KEY or not USER_KEY:
        print("❌ FEHLER: API_KEY oder USER_KEY fehlen in der .env Datei.")
        return

    # 1. Sammle alle einzigartigen eToro-IDs aus den Konfigurationen
    required_etoro_ids = list(set([bot["etoro_id"] for bot in ACTIVE_BOTS]))

    # 2. Node Config & Data Client initialisieren
    config = TradingNodeConfig(
        trader_id="eToro-MultiBot",
        data_clients={
            "ETORO_WS_CLIENT": EToroDataClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
                instrument_ids=required_etoro_ids  # Übergebe alle IDs an den Adapter
            )
        },
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)

    # 3. Strategien dynamisch aus der Config registrieren
    for idx, bot_spec in enumerate(ACTIVE_BOTS):
        strategy_class_name = bot_spec.get("strategy_class")
        
        if strategy_class_name == "SmaCrossoverStrategy":
            strat_config = SmaCrossoverConfig(
                strategy_id=f"SMA_{bot_spec['symbol']}_{idx}",
                instrument_id=bot_spec["symbol"],
                bar_type=bot_spec["bar_type"],
                **bot_spec["params"]
            )
            strategy = SmaCrossoverStrategy(config=strat_config)
            node.trader.add_strategy(strategy)
            print(f"✅ Strategie registriert: {strat_config.strategy_id}")
        else:
            print(f"⚠️ Unbekannte Strategieklasse in setups.py ignoriert: {strategy_class_name}")

    # 4. Node starten
    node.build()
    print(f"\n🚀 Starte Nautilus eToro-Orchestrator mit {len(required_etoro_ids)} Instrumenten...")
    print("Drücke Ctrl+C zum Beenden\n")

    try:
        node.run()
    except KeyboardInterrupt:
        print("\n⚠️ Herunterfahren eingeleitet...")
    finally:
        node.stop()
        print("🤖 Bot erfolgreich beendet.")


if __name__ == "__main__":
    main()