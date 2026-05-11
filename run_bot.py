import os
import importlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode

# Adapter Importe
from adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from adapters.instrument_map import ETORO_INSTRUMENTS

# Config und Strategien Importe
from config.setups import ACTIVE_BOTS

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

LOG_DIR = Path(__file__).parent / "logs"

# Registry for strategies
STRATEGY_REGISTRY = {
    "SmaCrossoverStrategy": ("strategies.sma_crossover", "SmaCrossoverStrategy", "SmaCrossoverConfig"),
    "ComboTrendVwapStrategy": ("strategies.tesla_combo_strategy", "ComboTrendVwapStrategy", "ComboTrendVwapConfig"),
    "VwapExhaustionStrategy": ("strategies.vwap_exhaustion", "VwapExhaustionStrategy", "VwapExhaustionConfig"),
    "DynamicBreakoutStrategy": ("strategies.dynamic_breakout", "DynamicBreakoutStrategy", "DynamicBreakoutConfig"),
    "AdxAtrMomentumStrategy": ("strategies.adx_atr_momentum", "AdxAtrMomentumStrategy", "AdxAtrMomentumConfig"),
}


def _setup_logging() -> tuple[Path, logging.Logger]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = LOG_DIR / f"bot_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return log_file, logging.getLogger(__name__)


def _cleanup_old_logs(max_age_hours: int = 24) -> None:
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    for log_file in LOG_DIR.glob("bot_*.log"):
        if datetime.fromtimestamp(log_file.stat().st_mtime) < cutoff:
            log_file.unlink(missing_ok=True)


def main():
    log_file, log = _setup_logging()
    _cleanup_old_logs()

    if not API_KEY or not USER_KEY:
        log.error("FEHLER: API_KEY oder USER_KEY fehlen in der .env Datei.")
        return

    log.info(f"Bot gestartet. Logfile: {log_file}")

    # 1. Sammle und validiere alle einzigartigen eToro-IDs aus den Konfigurationen
    required_etoro_ids = []
    valid_bots = []
    for bot in ACTIVE_BOTS:
        eid = bot.get("etoro_id")
        if eid not in ETORO_INSTRUMENTS:
            log.error(
                f"CRITICAL WARNUNG: etoro_id {eid} (Symbol: {bot.get('symbol')}) "
                "in setups.py ist NICHT in instrument_map.py definiert. Ueberspringe Bot!"
            )
            continue
        required_etoro_ids.append(eid)
        valid_bots.append(bot)

    required_etoro_ids = list(set(required_etoro_ids))

    if not required_etoro_ids:
        log.error("FEHLER: Keine gueltigen Instrumente fuer den Start gefunden.")
        return

    # 2. Node Config & Data Client initialisieren
    nautilus_log_name = f"nautilus_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
    config = TradingNodeConfig(
        trader_id="eToro-MultiBot",
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
            log_directory=str(LOG_DIR),
            log_file_name=nautilus_log_name,
            log_colors=False,
        ),
        data_clients={
            "ETORO_WS_CLIENT": EToroDataClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
                instrument_ids=required_etoro_ids,
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
                log.info(f"Strategie registriert: {strat_config.strategy_id}")
            except Exception as e:
                log.error(f"FEHLER beim Laden der Strategie {strategy_class_name}: {e}")
        else:
            log.warning(f"Unbekannte Strategieklasse in setups.py ignoriert: {strategy_class_name}")

    # 4. Node starten
    node.build()
    log.info(f"Starte Nautilus eToro-Orchestrator mit {len(required_etoro_ids)} Instrumenten...")
    log.info("Druecke Ctrl+C zum Beenden")

    try:
        node.run()
    except KeyboardInterrupt:
        log.warning("Herunterfahren eingeleitet (KeyboardInterrupt)...")
    except Exception as e:
        log.error(f"Laufzeitfehler: {e}")
    finally:
        node.stop()
        log.info("Bot erfolgreich beendet.")


if __name__ == "__main__":
    main()
