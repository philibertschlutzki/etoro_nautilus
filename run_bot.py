import os
import importlib
from dotenv import load_dotenv
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode

# Adapter Importe
from adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from adapters.instrument_map import ETORO_INSTRUMENTS

# Config und Strategien Importe
from config.setups import ACTIVE_BOTS

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

# Registry for strategies
STRATEGY_REGISTRY = {
    "SmaCrossoverStrategy": ("strategies.sma_crossover", "SmaCrossoverStrategy", "SmaCrossoverConfig"),
    "ComboTrendVwapStrategy": ("strategies.tesla_combo_strategy", "ComboTrendVwapStrategy", "ComboTrendVwapConfig"),
    "VwapExhaustionStrategy": ("strategies.vwap_exhaustion", "VwapExhaustionStrategy", "VwapExhaustionConfig"),
    "DynamicBreakoutStrategy": ("strategies.dynamic_breakout", "DynamicBreakoutStrategy", "DynamicBreakoutConfig"),
    "AdxAtrMomentumStrategy": ("strategies.adx_atr_momentum", "AdxAtrMomentumStrategy", "AdxAtrMomentumConfig"),
}

def main():
    if not API_KEY or not USER_KEY:
        print("❌ FEHLER: API_KEY oder USER_KEY fehlen in der .env Datei.")
        return

    # 1. Sammle und validiere alle einzigartigen eToro-IDs aus den Konfigurationen
    required_etoro_ids = []
    valid_bots = []
    for bot in ACTIVE_BOTS:
        eid = bot.get("etoro_id")
        if eid not in ETORO_INSTRUMENTS:
            print(f"❌ CRITICAL WARNUNG: etoro_id {eid} (Symbol: {bot.get('symbol')}) in setups.py ist NICHT in instrument_map.py definiert. Überspringe Bot!")
            continue
        required_etoro_ids.append(eid)
        valid_bots.append(bot)

    required_etoro_ids = list(set(required_etoro_ids))

    if not required_etoro_ids:
        print("❌ FEHLER: Keine gültigen Instrumente für den Start gefunden.")
        return

    # 2. Node Config & Data Client initialisieren
    config = TradingNodeConfig(
        trader_id="eToro-MultiBot",
        data_clients={
            "ETORO_WS_CLIENT": EToroDataClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
                instrument_ids=required_etoro_ids  # Übergebe alle validierten IDs an den Adapter
            )
        },
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)

    # 3. Strategien dynamisch aus der Config registrieren
    for idx, bot_spec in enumerate(valid_bots):
        strategy_class_name = bot_spec.get("strategy_class")
        
        if strategy_class_name in STRATEGY_REGISTRY:
            module_name, class_name, config_name = STRATEGY_REGISTRY[strategy_class_name]
            try:
                module = importlib.import_module(module_name)
                StrategyClass = getattr(module, class_name)
                ConfigClass = getattr(module, config_name)

                strat_config = ConfigClass(
                    strategy_id=f"{strategy_class_name}_{bot_spec['symbol']}_{idx}",
                    instrument_id=bot_spec["symbol"],
                    bar_type=bot_spec["bar_type"],
                    **bot_spec["params"]
                )
                strategy = StrategyClass(config=strat_config)
                node.trader.add_strategy(strategy)
                print(f"✅ Strategie registriert: {strat_config.strategy_id}")
            except Exception as e:
                print(f"❌ FEHLER beim Laden der Strategie {strategy_class_name}: {e}")
        else:
            print(f"⚠️ Unbekannte Strategieklasse in setups.py ignoriert: {strategy_class_name}")

    # 4. Node starten
    node.build()
    print(f"\n🚀 Starte Nautilus eToro-Orchestrator mit {len(required_etoro_ids)} Instrumenten...")
    print("Drücke Ctrl+C zum Beenden\n")

    try:
        node.run()
    except KeyboardInterrupt:
        print("\n⚠️ Herunterfahren eingeleitet (KeyboardInterrupt)...")
    except Exception as e:
        print(f"\n⚠️ Laufzeitfehler: {e}")
    finally:
        node.stop()
        print("🤖 Bot erfolgreich beendet.")


if __name__ == "__main__":
    main()
