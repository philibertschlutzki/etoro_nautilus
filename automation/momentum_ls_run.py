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
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.data import BarType

sys.path.append(str(Path(__file__).resolve().parent.parent))

from automation.adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from automation.adapters.etoro_config import EToroExecClientConfig, EToroLiveExecClientFactory
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

def _build_strategy_registry(strategies_cfg_path: str) -> dict[str, tuple[str, str, str]]:
    """Baut {strategy_class: (module, class, config_class)} aus strategies.json (active=true)."""
    with open(strategies_cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    registry: dict[str, tuple[str, str, str]] = {}
    for s in data.get("strategies", []):
        if s.get("active", True) is not False:
            registry[s["strategy_class"]] = (
                s["strategy_module"], s["strategy_class"], s["config_class"]
            )
    return registry

def _load_strategy_defaults(defaults_cfg_path: str) -> dict[str, dict]:
    with open(defaults_cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}

def _build_bots_config(
    universe_data: dict,
    tournament_data: dict,
    registry: dict[str, tuple[str, str, str]],
    defaults: dict[str, dict],
    strategies_raw: list[dict],
    symbol_to_etoro_id: dict[str, str]
) -> tuple[list[str], list[dict]]:
    """Mappt Tournament-Gewinner pro Symbol auf bot_spec-Dicts.
    Reine Funktion ohne I/O / Logging-Seiteneffekte (für Unit-Tests).
    Symbole ohne registrierten Gewinner werden übersprungen (kein SMA-Fallback).
    """
    per_symbol_winners = tournament_data.get("per_symbol_winners", {})
    active_symbols = []
    bots_config = []

    for uni_obj in universe_data.get("universe", []):
        symbol = uni_obj.get("symbol")
        if not symbol:
            continue

        winner = per_symbol_winners.get(symbol)
        if not winner:
            continue

        strat_class_name = winner["strategy"]
        etoro_id = symbol_to_etoro_id.get(symbol)

        if not etoro_id:
            continue

        if strat_class_name not in registry:
            continue

        # Merge params
        strat_defaults = defaults.get(strat_class_name, {})
        strat_override = {}
        for s in strategies_raw:
            if s.get("strategy_class") == strat_class_name:
                strat_override = s.get("params", {})
                break

        merged_params = {**strat_defaults, **strat_override}

        # Remove trade_amount_usd
        if "trade_amount_usd" in merged_params:
            del merged_params["trade_amount_usd"]

        bot_spec = {
            "strategy_class": strat_class_name,
            "etoro_id": etoro_id,
            "symbol": symbol,
            "bar_type": f"{symbol}-1-HOUR-MID-INTERNAL",
            "params": merged_params
        }

        if "max_open_positions" in merged_params:
            bot_spec["max_open_positions"] = merged_params["max_open_positions"]
            del merged_params["max_open_positions"]

        active_symbols.append(symbol)
        bots_config.append(bot_spec)

    return active_symbols, bots_config

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

    # Load configuration files
    project_root = Path(__file__).resolve().parent
    strategies_cfg_path = project_root / "config" / "strategies.json"
    defaults_cfg_path = project_root / "config" / "strategy_defaults.json"

    registry = _build_strategy_registry(str(strategies_cfg_path))
    defaults = _load_strategy_defaults(str(defaults_cfg_path))

    with open(strategies_cfg_path, "r", encoding="utf-8") as f:
        strategies_raw = json.load(f).get("strategies", [])

    # Reverse lookup for etoro_ids
    symbol_to_etoro_id = {v["symbol"]: k for k, v in ETORO_INSTRUMENTS.items() if isinstance(v, dict) and "symbol" in v}

    active_symbols, bots_config = _build_bots_config(
        universe_data,
        tournament_data,
        registry,
        defaults,
        strategies_raw,
        symbol_to_etoro_id
    )

    # Log skipped ones (to mimic the original behavior)
    per_symbol_winners = tournament_data.get("per_symbol_winners", {})
    for uni_obj in universe_data.get("universe", []):
        symbol = uni_obj.get("symbol")
        if symbol:
            winner = per_symbol_winners.get(symbol)
            if not winner:
                logger.warning(f"No tournament winner for {symbol}. Skipping.")
            elif not symbol_to_etoro_id.get(symbol):
                logger.warning(f"Could not resolve etoro_id for {symbol}. Skipping.")
            elif winner["strategy"] not in registry:
                logger.warning(f"Winner strategy {winner['strategy']} not in active registry for {symbol}. Skipping.")

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
        strat_class_name = bot_spec.get("strategy_class")

        module_name, class_name, config_name = registry[strat_class_name]
        try:
            module = importlib.import_module(module_name)
            StrategyClass = getattr(module, class_name)
            ConfigClass = getattr(module, config_name)

            cfg_kwargs = dict(
                strategy_id=f"MLS_{strat_class_name}_{bot_spec['symbol']}_{idx}",
                instrument_id=InstrumentId.from_str(bot_spec["symbol"]),
                bar_type=BarType.from_str(bot_spec["bar_type"]),
                **bot_spec["params"],
            )
            if "max_open_positions" in bot_spec:
                cfg_kwargs["max_open_positions"] = bot_spec["max_open_positions"]

            strat_config = ConfigClass(**cfg_kwargs)
            strategy = StrategyClass(config=strat_config, allocator=allocator)
            node.trader.add_strategy(strategy)
            logger.info(f"Strategie registriert: {strat_config.strategy_id} (Winner: {strat_class_name})")
        except Exception as e:
            logger.error(f"FEHLER beim Laden der Strategie {strat_class_name}: {e}")

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
