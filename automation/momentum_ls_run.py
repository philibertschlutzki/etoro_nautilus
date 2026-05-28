import argparse
import importlib
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from nautilus_trader.config import TradingNodeConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode

sys.path.append(str(Path(__file__).resolve().parent.parent))

from archive.adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from archive.adapters.etoro_config import EToroExecClientConfig, EToroLiveExecClientFactory
import json
with open("automation/config/instrument_map.json", "r") as f:
    _imap = json.load(f)
    ETORO_INSTRUMENTS = _imap.get("instruments", {})
from automation.momentum_ls_allocator import MomentumLSAllocator

ETORO_EXECUTION = {"environment": "demo", "dry_run": True, "enable_trailing_stop": False}
def _check_live_safety_interlock(log):
    environment = ETORO_EXECUTION.get('environment', 'demo')
    dry_run = ETORO_EXECUTION.get('dry_run', True)
    confirm_live = os.getenv('ETORO_CONFIRM_LIVE', '0').strip() == '1'
    if environment == 'real' and not dry_run and not confirm_live:
        log.critical('SAFETY INTERLOCK TRIGGERED')
        sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Registry for strategies (Only Momentum-LS strategies)
STRATEGY_REGISTRY = {
    "MomentumLSSmaStrategy": ("automation.strategies.momentum_ls_sma", "MomentumLSSmaStrategy", "MomentumLSSmaConfig"),
    # As an example we mapped SMA. To use other strategies they should be migrated to inherit MomentumLSBaseStrategy first.
    # In a full deployment, all strategies from Phase 3 would be ported. Here we map any winner to MomentumLSSmaStrategy
    # to demonstrate the wiring (as per requirements, we only created momentum_ls_sma.py as proof of concept in Phase 4).
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="data/universe/momentum_ls.json")
    parser.add_argument("--tournament", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("ETORO_API_KEY")
    user_key = os.getenv("ETORO_USER_KEY")

    if not api_key or not user_key:
        logger.error("FEHLER: API_KEY oder USER_KEY fehlen in der .env Datei.")
        sys.exit(1)

    # Apply safety interlock using the exact pattern as in run_bot.py
    _check_live_safety_interlock(logger)

    try:
        with open(args.universe, "r") as f:
            universe_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load universe {args.universe}: {e}")
        sys.exit(1)

    try:
        with open(args.tournament, "r") as f:
            tournament_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load tournament {args.tournament}: {e}")
        sys.exit(1)

    # Validate universe freshness
    fetched_at = datetime.fromisoformat(universe_data["fetched_at"])
    if (datetime.now(timezone.utc) - fetched_at).total_seconds() > 24 * 3600:
        logger.warning(f"Universe data is stale (fetched_at > 24 hours ago: {fetched_at})")

    # Reverse lookup for etoro_ids
    symbol_to_etoro_id = {v: k for k, v in ETORO_INSTRUMENTS.items()}

    per_symbol_winners = tournament_data.get("per_symbol_winners", {})

    active_symbols = []
    bots_config = []

    for uni_obj in universe_data.get("universe", []):
        symbol = uni_obj.get("symbol")
        if not symbol:
            continue

        winner = per_symbol_winners.get(symbol)
        if not winner:
            logger.warning(f"No tournament winner for {symbol}. Skipping.")
            continue

        strat_class_name = winner["strategy"]
        etoro_id = symbol_to_etoro_id.get(symbol)

        if not etoro_id:
            logger.warning(f"Could not resolve etoro_id for {symbol}. Skipping.")
            continue

        active_symbols.append(symbol)
        bots_config.append({
            "strategy_class": "MomentumLSSmaStrategy", # Using the PoC strategy for all winners as required by the spec constraints
            "original_winner_class": strat_class_name,
            "etoro_id": etoro_id,
            "symbol": symbol,
            "bar_type": f"{symbol}-1-MINUTE-MID-INTERNAL",
            "params": {"sma_period": 5},
            "max_open_positions": 1
        })

    if not active_symbols:
        logger.error("No valid symbols to trade after cross-referencing universe and tournament.")
        sys.exit(1)

    allocator = MomentumLSAllocator(active_symbols)

    environment = ETORO_EXECUTION["environment"]
    dry_run = True if args.dry_run else ETORO_EXECUTION["dry_run"]
    enable_trailing_stop = ETORO_EXECUTION["enable_trailing_stop"]

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    nautilus_log_name = f"nautilus_mls_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"

    config = TradingNodeConfig(
        trader_id="eToro-Momentum-LS",
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
            log_directory=str(log_dir),
            log_file_name=nautilus_log_name,
            log_colors=False,
        ),
        data_clients={
            "ETORO_WS_CLIENT": EToroDataClientConfig(
                api_key=api_key,
                user_key=user_key,
                instrument_ids=list(set([b["etoro_id"] for b in bots_config])),
            )
        },
        exec_clients={
            "ETORO": EToroExecClientConfig(
                api_key=api_key,
                user_key=user_key,
                environment=environment,
                dry_run=dry_run,
                enable_trailing_stop=enable_trailing_stop,
            )
        },
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("ETORO_WS_CLIENT", EToroLiveDataClientFactory)
    node.add_exec_client_factory("ETORO", EToroLiveExecClientFactory)

    for idx, bot_spec in enumerate(bots_config):
        strategy_class_name = bot_spec.get("strategy_class")

        if strategy_class_name in STRATEGY_REGISTRY:
            module_name, class_name, config_name = STRATEGY_REGISTRY[strategy_class_name]
            try:
                module = importlib.import_module(module_name)
                StrategyClass = getattr(module, class_name)
                ConfigClass = getattr(module, config_name)

                strat_config = ConfigClass(
                    strategy_id=f"MLS_{strategy_class_name}_{bot_spec['symbol']}_{idx}",
                    instrument_id=bot_spec["symbol"],
                    bar_type=bot_spec["bar_type"],
                    max_open_positions=bot_spec.get("max_open_positions", 1),
                    **bot_spec["params"]
                )

                # Momentum-LS specific signature
                strategy = StrategyClass(config=strat_config, allocator=allocator)
                node.trader.add_strategy(strategy)
                logger.info(f"Strategie registriert: {strat_config.strategy_id} (Winner was {bot_spec['original_winner_class']})")
            except Exception as e:
                logger.error(f"FEHLER beim Laden der Strategie {strategy_class_name}: {e}")
        else:
            logger.warning(f"Unbekannte Strategieklasse in Config ignoriert: {strategy_class_name}")

    node.build()
    logger.info(f"Starte Nautilus Momentum-LS Orchestrator mit {len(active_symbols)} Instrumenten...")

    if args.dry_run:
        logger.info("Dry-Run Beendet. Node wurde erfolgreich konfiguriert und gebaut.")
        node.dispose()
        sys.exit(0)

    try:
        node.run()
    except KeyboardInterrupt:
        logger.warning("Herunterfahren eingeleitet (KeyboardInterrupt)...")
    except Exception as e:
        logger.error(f"Laufzeitfehler: {e}\n{traceback.format_exc()}")
    finally:
        node.stop()
        logger.info("Bot erfolgreich beendet.")


if __name__ == "__main__":
    main()
