import os
import sys
import importlib
import logging
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode

# Adapter imports
from archive.adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from archive.adapters.etoro_config import EToroExecClientConfig, EToroLiveExecClientFactory
from archive.adapters.instrument_map import ETORO_INSTRUMENTS

# Config imports
from archive.config.setups import ACTIVE_BOTS, ETORO_EXECUTION

load_dotenv()
API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

LOG_DIR = Path(__file__).parent / "logs"

# Registry for strategies
STRATEGY_REGISTRY = {
    "SmaCrossoverStrategy":           ("strategies.sma_crossover",          "SmaCrossoverStrategy",           "SmaCrossoverConfig"),
    "ComboTrendVwapStrategy":         ("strategies.tesla_combo_strategy",    "ComboTrendVwapStrategy",         "ComboTrendVwapConfig"),
    "VwapExhaustionStrategy":         ("strategies.vwap_exhaustion",         "VwapExhaustionStrategy",         "VwapExhaustionConfig"),
    "DynamicBreakoutStrategy":        ("strategies.dynamic_breakout",        "DynamicBreakoutStrategy",        "DynamicBreakoutConfig"),
    "AdxAtrMomentumStrategy":         ("strategies.adx_atr_momentum",        "AdxAtrMomentumStrategy",         "AdxAtrMomentumConfig"),
    "TrendPullbackStrategy":          ("strategies.trend_pullback",           "TrendPullbackStrategy",          "TrendPullbackConfig"),
    "MeanReversionStrategy":          ("strategies.mean_reversion",           "MeanReversionStrategy",          "MeanReversionConfig"),
    "FlashCrashReversalStrategy":     ("strategies.flash_crash_reversal",     "FlashCrashReversalStrategy",     "FlashCrashReversalConfig"),
    "VolatilityBreakoutPumpStrategy": ("strategies.volatility_breakout",      "VolatilityBreakoutPumpStrategy", "VolatilityBreakoutConfig"),
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


def _check_live_safety_interlock(log: logging.Logger) -> None:
    """Refuse to start if real trading is requested without the explicit env-var confirmation."""
    environment = ETORO_EXECUTION.get("environment", "demo")
    dry_run = ETORO_EXECUTION.get("dry_run", True)
    confirm_live = os.getenv("ETORO_CONFIRM_LIVE", "0").strip() == "1"

    if environment == "real" and not dry_run and not confirm_live:
        log.critical(
            "SAFETY INTERLOCK: environment='real' and dry_run=False but "
            "ETORO_CONFIRM_LIVE=1 is not set. "
            "Set ETORO_CONFIRM_LIVE=1 in your .env to confirm live trading."
        )
        sys.exit(1)


def main():
    log_file, log = _setup_logging()
    _cleanup_old_logs()

    if not API_KEY or not USER_KEY:
        log.error("FEHLER: API_KEY oder USER_KEY fehlen in der .env Datei.")
        return

    # Safety interlock must run before constructing the node
    _check_live_safety_interlock(log)

    log.info(f"Bot gestartet. Logfile: {log_file}")

    # 1. Collect and validate all unique eToro IDs from configuration
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

    # 2. Node config
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
        exec_clients={
            "ETORO": EToroExecClientConfig(
                api_key=API_KEY,
                user_key=USER_KEY,
                environment=ETORO_EXECUTION["environment"],
                dry_run=ETORO_EXECUTION["dry_run"],
                enable_trailing_stop=ETORO_EXECUTION["enable_trailing_stop"],
            )
        },
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)
    node.add_exec_client_factory("ETORO", EToroLiveExecClientFactory)

    # 3. Dynamically register strategies from config
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
                    trade_amount_usd=bot_spec.get("trade_amount_usd", 100.0),
                    max_open_positions=bot_spec.get("max_open_positions", 1),
                    **bot_spec["params"]
                )
                strategy = StrategyClass(config=strat_config)
                node.trader.add_strategy(strategy)
                log.info(f"Strategie registriert: {strat_config.strategy_id}")
            except Exception as e:
                log.error(f"FEHLER beim Laden der Strategie {strategy_class_name}: {e}")
        else:
            log.warning(f"Unbekannte Strategieklasse in setups.py ignoriert: {strategy_class_name}")

    # 4. Start node
    node.build()
    log.info(f"Starte Nautilus eToro-Orchestrator mit {len(required_etoro_ids)} Instrumenten...")
    log.info("Druecke Ctrl+C zum Beenden")

    try:
        node.run()
    except KeyboardInterrupt:
        log.warning("Herunterfahren eingeleitet (KeyboardInterrupt)...")
    except Exception as e:
        log.error(f"Laufzeitfehler: {e}\n{traceback.format_exc()}")
    finally:
        node.stop()
        log.info("Bot erfolgreich beendet.")


if __name__ == "__main__":
    main()
