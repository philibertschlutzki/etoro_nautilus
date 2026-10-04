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

from automation.adapters.etoro_data import EToroDataClientConfig, EToroLiveDataClientFactory
from automation.adapters.etoro_config import EToroExecClientConfig, EToroLiveExecClientFactory
import json
with open("automation/config/instrument_map.json", "r") as f:
    _imap = json.load(f)
    ETORO_INSTRUMENTS = _imap.get("instruments", {})
from automation.momentum_ls_allocator import MomentumLSAllocator
from automation.live_risk import LiveCircuitBreakerWatchdog, LiveShutdownCoordinator
from automation.live_equity_state import (
    DEFAULT_DAILY_LOSS_TZ, HWM_PATH, HwmEnvironmentMismatch, PersistentEquityState,
    distribution_references, exchange_day_key,
)
from automation.live_bot_lock import (
    EXIT_ALREADY_RUNNING, LOCK_PATH, LiveBotAlreadyRunning, LiveBotLock, compute_whitelist_sha256,
)
from automation.log_manager import emit_execution_event
from automation.live_params import live_params_sha256, mismatching_live_params, resolve_live_params
from automation.disaster_stop import DISASTER_STOP_MODE_BROKER, resolve_disaster_stop_params

ETORO_EXECUTION = {
    "environment": os.getenv("ETORO_ENV", "demo"),
    "dry_run": os.getenv("ETORO_DRY_RUN", "1") == "1",
    "enable_trailing_stop": os.getenv("ETORO_ENABLE_TSL", "0") == "1"
}

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

        # Type-safe casting für etoro_id um Mismatches zu vermeiden
        etoro_id = symbol_to_etoro_id.get(symbol)
        if etoro_id:
            etoro_id = str(etoro_id)

        # Issue #1360 (GH #1256, P0) — Zulassung AUSSCHLIESSLICH über die Deployment-Grenze
        # (``whitelist[symbol]["deployment_gate"]["admitted"]``, deployment_gate.py #993), nicht über
        # die Phase-4-Felder ``oos_eligible``/``oos_evaluated``: Phase 5 lässt Paare über die Grenze
        # zu, deren Phase-4-Felder False sind — sie waren zugelassen, wurden aber nie gehandelt; und
        # umgekehrt entschied ein Einzelfenster-Gate OHNE Multiplizitätskorrektur über Kapitaleinsatz.
        gate = winner.get("deployment_gate")
        if not (isinstance(gate, dict) and gate.get("admitted") is True):
            logger.info(f"[DEPLOY-GATE-REJECT] Skipping symbol {symbol}: winner strategy {winner.get('strategy')} ist nicht über die Deployment-Grenze zugelassen (deployment_gate.admitted != True).")
            continue

        strat_class_name = winner["strategy"]

        if not etoro_id:
            continue

        if strat_class_name not in registry:
            continue

        # Issue #1360 Fix Punkt 1 — EINE Quelle der Live-Parameter für Bot UND Gate.
        merged_params = resolve_live_params(strat_class_name, symbol, defaults, strategies_raw)
        params_sha256 = live_params_sha256(merged_params)

        # Issue #1360 Fix Punkt 3 — dieselbe Prüfung wie die Klausel live_params_match_promotion, je
        # Paar VOR add_strategy: jeder Key des promovierten Overrides muss live exakt so aufgelöst
        # werden. Ein Whitelist-Eintrag ohne Override/Fingerabdruck ist nicht prüfbar ⇒ fail-closed.
        mismatching = mismatching_live_params(merged_params, winner.get("proposed_instrument_override"))
        whitelist_sha = winner.get("live_params_sha256")
        if mismatching is None or whitelist_sha is None:
            reason = "unverifiable_whitelist_entry"
            mismatching = mismatching or []
        elif mismatching:
            reason = "promoted_override_not_in_live_params"
        elif whitelist_sha != params_sha256:
            reason = "live_params_sha256_changed_since_whitelist"
        else:
            reason = None
        if reason is not None:
            emit_execution_event(logger, "LIVE_PARAMS_MISMATCH", {
                "symbol": symbol, "strategy": strat_class_name, "reason": reason,
                "mismatching_keys": mismatching,
                "whitelist_live_params_sha256": whitelist_sha, "live_params_sha256": params_sha256,
            }, level=logging.ERROR)
            logger.error(
                f"[LIVE_PARAMS_MISMATCH] {symbol}/{strat_class_name} übersprungen ({reason}; "
                f"Keys: {mismatching}) — der Bot handelt nur die validierten Parameter.")
            continue

        # A4.8/#1360: merged_params = defaults < params < instrument_overrides[symbol] (reine Funktion
        # ``resolve_live_params``; ``trade_amount_usd`` ist bereits entfernt).

        bot_spec = {
            "strategy_class": strat_class_name,
            "etoro_id": etoro_id,
            "symbol": symbol,
            "bar_type": f"{symbol}-1-HOUR-MID-INTERNAL",
            "params": merged_params,
            "live_params_sha256": params_sha256,
        }

        if "max_open_positions" in merged_params:
            bot_spec["max_open_positions"] = merged_params["max_open_positions"]
            del merged_params["max_open_positions"]

        active_symbols.append(symbol)
        bots_config.append(bot_spec)

    return active_symbols, bots_config

def _instantiate_strategy(bot_spec: dict, registry: dict[str, tuple[str, str, str]], allocator: MomentumLSAllocator, idx: int):
    import importlib
    strat_class_name = bot_spec.get("strategy_class")
    if strat_class_name not in registry:
        raise ValueError(f"Unknown strategy class {strat_class_name}")

    module_name, class_name, config_name = registry[strat_class_name]
    module = importlib.import_module(module_name)
    StrategyClass = getattr(module, class_name)
    ConfigClass = getattr(module, config_name)

    # Issue #1359 (GH #1255, P0) — Live: der Broker hält den Katastrophen-Stop (nur der ``SL:``-Tag
    # der Entry-Order, keine separate Order); Parameter aus derselben Quelle wie der Backtest.
    cfg_kwargs = dict(
        strategy_id=f"MLS_{strat_class_name}_{bot_spec['symbol']}_{idx}",
        instrument_id=bot_spec["symbol"],
        bar_type=bot_spec["bar_type"],
        **{**resolve_disaster_stop_params(Path(__file__).resolve().parent / "config"),
           **bot_spec["params"]},
        disaster_stop_mode=DISASTER_STOP_MODE_BROKER,
    )
    if "max_open_positions" in bot_spec:
        cfg_kwargs["max_open_positions"] = bot_spec["max_open_positions"]

    strat_config = ConfigClass(**cfg_kwargs)
    return StrategyClass(config=strat_config, allocator=allocator)

def _reset_hwm(environment: str, *, hwm_path: Path = HWM_PATH, lock_path: Path = LOCK_PATH) -> int:
    """Issue #1362 (GH #1258) Fix Punkt 1 — ``momentum_ls_run --reset-hwm``: der EINZIGE Weg, den
    persistenten Hochwasserstand zurückzusetzen (Event ``LIVE_HWM_RESET``). Nimmt die exklusive
    Bot-Sperre (ein laufender Bot würde den alten Stand sonst bei der nächsten Änderung wieder
    persistieren) — gehaltene Sperre ⇒ Exit-Code 4, nichts zurückgesetzt. Exit-Code 0 bei Erfolg."""
    lock = LiveBotLock(lock_path)
    try:
        lock.acquire(environment=environment, whitelist_sha256=None)
    except LiveBotAlreadyRunning as exc:
        logger.critical(
            f"[LIVE_HWM_RESET] abgelehnt: ein Bot läuft ({exc.info}) — erst stoppen, dann zurücksetzen.")
        return EXIT_ALREADY_RUNNING
    try:
        previous = PersistentEquityState(hwm_path, environment=environment).reset()
        emit_execution_event(logger, "LIVE_HWM_RESET", {
            "environment": environment, "hwm_path": str(hwm_path), "previous_state": previous,
        }, level=logging.WARNING)
        logger.warning(f"[LIVE_HWM_RESET] Hochwasserstand zurückgesetzt (vorher: {previous}).")
        return 0
    finally:
        lock.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="data/universe/momentum_ls.json")
    parser.add_argument("--tournament", default=None,
                        help="Whitelist-/Turnier-JSON (Pflicht, ausser mit --reset-hwm)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset-hwm", action="store_true",
                        help="Setzt den persistenten Equity-Hochwasserstand zurück und beendet sich "
                             "(Event LIVE_HWM_RESET; Issue #1362).")
    args = parser.parse_args()

    if args.reset_hwm:
        sys.exit(_reset_hwm(ETORO_EXECUTION["environment"]))
    if not args.tournament:
        parser.error("--tournament ist erforderlich (ausser mit --reset-hwm)")

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

    # Issue #999 (P0, HEADLINE) — Budget-/Circuit-Breaker-Parameter aus backtest.json["live_risk"]
    # (Defaults der Allocator-/Watchdog-Konstruktoren gelten nur, falls der Key fehlt).
    live_risk_cfg = {}
    backtest_cfg_path = Path(__file__).resolve().parent / "config" / "backtest.json"
    try:
        with open(backtest_cfg_path, "r", encoding="utf-8") as f:
            live_risk_cfg = (json.load(f) or {}).get("live_risk", {}) or {}
    except Exception as e:
        logger.warning(f"live_risk-Konfiguration konnte nicht geladen werden ({e}) — Allocator/Watchdog nutzen Defaults.")

    allocator = MomentumLSAllocator(
        active_symbols,
        max_total_exposure_fraction=live_risk_cfg.get("max_total_exposure_fraction", 0.60),
        max_symbol_exposure_fraction=live_risk_cfg.get("max_symbol_exposure_fraction", 0.10),
        dd_halt_fraction=live_risk_cfg.get("dd_halt_fraction", 0.10),
        psi_min=live_risk_cfg.get("psi_min", 0.2),
    )

    environment = ETORO_EXECUTION["environment"]
    dry_run = True if args.dry_run else ETORO_EXECUTION["dry_run"]
    enable_trailing_stop = ETORO_EXECUTION["enable_trailing_stop"]

    # Issue #1358 (GH #1254) Fix Punkt 1 — exklusive Sperre VOR dem Aufbau des TradingNode: ein
    # zweiter Start gegen dasselbe Konto beendet sich mit Exit-Code 4, ohne einen Node zu bauen.
    # ``--dry-run`` handelt nie und nimmt die Sperre daher nicht (er darf neben dem Live-Bot laufen).
    bot_lock = LiveBotLock(LOCK_PATH)
    if not args.dry_run:
        try:
            bot_lock.acquire(
                environment=environment,
                whitelist_sha256=compute_whitelist_sha256(tournament_data.get("per_symbol_winners", {})),
            )
        except LiveBotAlreadyRunning as exc:
            emit_execution_event(logger, "LIVE_BOT_ALREADY_RUNNING", {
                "lock_path": str(LOCK_PATH), "holder": exc.info, "exit_code": EXIT_ALREADY_RUNNING,
            }, level=logging.CRITICAL)
            logger.critical(
                f"[LIVE_BOT_ALREADY_RUNNING] {LOCK_PATH} wird bereits gehalten ({exc.info}) — "
                f"zweiter Bot gegen dasselbe Konto verweigert (Exit-Code {EXIT_ALREADY_RUNNING})."
            )
            sys.exit(EXIT_ALREADY_RUNNING)

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

    successful_strategies = 0
    failed_strategies = []

    for idx, bot_spec in enumerate(bots_config):
        strat_class_name = bot_spec.get("strategy_class")
        try:
            strategy = _instantiate_strategy(bot_spec, registry, allocator, idx)
            node.trader.add_strategy(strategy)
            logger.info(
                f"Strategie registriert: {strategy.config.strategy_id} (Winner: {strat_class_name}, "
                f"live_params_sha256={bot_spec.get('live_params_sha256')})")
            successful_strategies += 1
        except Exception as e:
            logger.error(f"FEHLER beim Laden der Strategie {strat_class_name} auf {bot_spec.get('symbol')}: {e}")
            failed_strategies.append((bot_spec.get("symbol"), strat_class_name))

    if successful_strategies == 0:
        logger.error("Keine einzige Strategie konnte erfolgreich registriert werden. Breche Start ab (Fail-Fast).")
        sys.exit(1)
    elif len(failed_strategies) > 0:
        logger.warning(f"Warnung: {len(failed_strategies)} Strategie(n) konnten nicht registriert werden: {failed_strategies}")

    node.build()
    logger.info(f"Starte Nautilus Momentum-LS Orchestrator mit {len(active_symbols)} Instrumenten...")

    if args.dry_run:
        logger.info("Dry-Run Beendet. Node wurde erfolgreich konfiguriert und gebaut.")
        node.dispose()
        sys.exit(0)

    # Issue #999 Fix Punkt 2 — Live-Circuit-Breaker-Wächter: unabhängig vom Backtest-seitigen
    # max_drawdown-Gate (das im Livebetrieb keine Entsprechung hatte) überwacht dieser Thread den
    # Equity-Verlauf des laufenden Nodes und flattet+stoppt bei Auslöser A/B (automation/live_risk.py).
    # Issue #1362 (GH #1258) — persistentes Drawdown-Gedächtnis (Hochwasserstand + Tagesbasis), der
    # Allocator-Dämpfer ψ(DD) startet mit dem persistierten Drawdown, und der Verteilungs-Auslöser B
    # läuft je Paar gegen die Holdout-Round-Trip-Statistik der Whitelist.
    equity_state = PersistentEquityState(HWM_PATH, environment=environment)
    try:
        equity_state.load()
    except HwmEnvironmentMismatch as exc:
        logger.critical(f"[LIVE_HWM] {exc}")
        bot_lock.release()
        sys.exit(5)
    if equity_state.persisted_drawdown() is not None:
        allocator.update_risk_state(current_drawdown=equity_state.persisted_drawdown())
    daily_loss_tz = live_risk_cfg.get("daily_loss_tz", DEFAULT_DAILY_LOSS_TZ)
    distribution_refs, distribution_disabled = distribution_references(
        tournament_data.get("per_symbol_winners", {}))
    distribution_refs = {s: r for s, r in distribution_refs.items() if s in set(active_symbols)}
    distribution_disabled = {s: r for s, r in distribution_disabled.items() if s in set(active_symbols)}
    emit_execution_event(logger, "LIVE_CIRCUIT_BREAKER_CONFIGURED", {
        "dd_halt_fraction": live_risk_cfg.get("dd_halt_fraction", 0.10),
        "daily_loss_halt_fraction": live_risk_cfg.get("daily_loss_halt_fraction", 0.03),
        "daily_loss_tz": daily_loss_tz,
        "persisted_hwm": equity_state.hwm,
        "persisted_drawdown": equity_state.persisted_drawdown(),
        "circuit_breaker_n_min_round_trips": live_risk_cfg.get("circuit_breaker_n_min_round_trips", 30),
        "distribution_breaker_pairs": sorted(distribution_refs),
        # Kein stiller toter Pfad: je Paar ohne Holdout-Statistik der Grund.
        "distribution_breaker_disabled_reason": distribution_disabled or None,
    })
    watchdog = LiveCircuitBreakerWatchdog(
        node,
        venue="ETORO",
        poll_interval_s=live_risk_cfg.get("poll_interval_s", 30.0),
        dd_halt_fraction=live_risk_cfg.get("dd_halt_fraction", 0.10),
        z_halt=live_risk_cfg.get("distribution_z_halt", 2.5),
        n_min_periods=live_risk_cfg.get("circuit_breaker_n_min_periods", 30),
        on_update=lambda d: allocator.update_risk_state(current_drawdown=d.dd_live),
        on_trip=lambda d: allocator.update_risk_state(tripped=True),
        equity_state=equity_state,
        daily_loss_halt_fraction=live_risk_cfg.get("daily_loss_halt_fraction", 0.03),
        day_key_fn=lambda now: exchange_day_key(now, daily_loss_tz),
        distribution_refs=distribution_refs,
        n_min_round_trips=live_risk_cfg.get("circuit_breaker_n_min_round_trips", 30),
    )
    watchdog.start()

    # Issue #1358 Fix Punkt 2 — SIGTERM/SIGINT: Entry-Sperre, node.stop() über den Event-Loop,
    # Policy live_risk.on_shutdown (keep | flatten), Event LIVE_BOT_SHUTDOWN.
    coordinator = LiveShutdownCoordinator(
        node,
        policy=live_risk_cfg.get("on_shutdown", "keep"),
        block_entries=lambda: allocator.update_risk_state(tripped=True),
        emit=lambda event, payload: emit_execution_event(logger, event, payload),
    )
    try:
        coordinator.install(loop=node.get_event_loop())
    except Exception as e:
        logger.warning(f"Signal-Handler konnten nicht installiert werden ({e}) — Fallback signal.signal.")
        coordinator.install()

    try:
        node.run()
    except KeyboardInterrupt:
        logger.warning("Herunterfahren eingeleitet (KeyboardInterrupt)...")
    except Exception as e:
        logger.error(f"Laufzeitfehler: {e}\n{traceback.format_exc()}")
    finally:
        coordinator.uninstall()
        watchdog.stop()
        if not coordinator.requested.is_set():
            node.stop()
        bot_lock.release()
        logger.info("Bot erfolgreich beendet.")

    if watchdog.tripped_event.is_set():
        logger.critical("[Circuit-Breaker] Bot ueber LIVE_CIRCUIT_BREAKER_TRIPPED beendet (Exit-Code 3).")
        sys.exit(3)


if __name__ == "__main__":
    main()
