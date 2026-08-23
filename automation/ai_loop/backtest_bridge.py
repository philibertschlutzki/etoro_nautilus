"""
automation/ai_loop/backtest_bridge.py
=======================================
Issue #1106 (backtest-execution decision) / used by Issue #1107's orchestrator — executes a
Path-B candidate (a strategy file that lives ONLY under
``automation/ai_loop/workspace/candidates/``, never ``automation/strategies/``) against the
SAME NautilusTrader backtest engine building blocks the production optimizer uses, without ever
writing to ``automation/strategies/``, ``automation/config/``, ``data/optimizer/``, or touching
the production CLI (``automation/optimizer/run_optimization.py``, ``automation/backtest_runner.py``)
on disk.

Design decision — option (b) of the two allowed mechanisms (see the Issue #1106 task text):
    ``automation.backtest_runner.run_single_backtest_worker`` is ALREADY a pure,
    side-effect-scoped function ("Isolierter Worker-Prozess (1 Instrument x 1 Strategie)",
    see its docstring) that resolves its strategy class via
    ``importlib.import_module(strat["strategy_module"])`` + ``getattr(module,
    strat["strategy_class"])`` — every filesystem write it performs is confined to the
    ``worker_log_file``/``reports_dir`` paths its CALLER supplies.

    This module registers the candidate ``.py`` file into ``sys.modules`` under an
    ``automation.ai_loop.workspace.candidates.<stem>``-namespaced key (via
    ``importlib.util.spec_from_file_location`` + a manual ``sys.modules`` insertion) BEFORE
    calling ``run_single_backtest_worker`` — Python's import machinery checks ``sys.modules``
    for the exact dotted name FIRST, before ever touching the filesystem, so
    ``importlib.import_module(module_name)`` inside ``backtest_runner.py`` resolves the
    candidate straight from that in-memory cache. The candidate is therefore never written
    to, or read from, ``automation/strategies/`` — and the module name itself is namespaced
    under our own package tree, never masquerading as a real ``automation.strategies.*`` module.

    ``worker_log_file``/``reports_dir`` are always pointed at
    ``automation/ai_loop/workspace/backtest_runs/<run_id>/`` — the only writes this bridge
    triggers.

    Option (a) (an additive CLI flag on ``run_optimization.py``/``backtest_runner.py``) was
    explicitly NOT taken: those two files are under heavy, unrelated concurrent edits right
    now, and option (b) needed ZERO changes to either file — see the top-level task's
    instructions to prefer (b) when it avoids touching them entirely.

    ``automation.backtest_runner`` imports ``nautilus_trader``/``pyarrow`` at MODULE level —
    every import of it in this module is therefore LAZY (inside the function body), matching
    an already-documented project convention for exactly this situation
    (``automation/AGENTS.md`` Pitfall #63 — "Lazy Import Crash im Worker-Prozess").

Scope note: this is a deliberately LIGHTER single-shot evaluation than the production
walk-forward tournament (one continuous window, no multi-fold walk-forward geometry, no
Optuna study) — appropriate for evaluating ONE AI-Loop candidate, not a substitute for the full
sweep. ``orchestrator.py``'s deployment-gate check treats the result accordingly (informative
only, see that module's docstring).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(__file__).resolve().parent / "workspace"
BACKTEST_RUNS_DIR = WORKSPACE_ROOT / "backtest_runs"

_MODULE_NAMESPACE_PREFIX = "automation.ai_loop.workspace.candidates"


class BacktestBridgeError(RuntimeError):
    """Raised when a candidate cannot be loaded, or its backtest cannot be executed/produces no
    usable result."""


def load_candidate_module(candidate_path: Path) -> types.ModuleType:
    """Loads ``candidate_path`` as a standalone module WITHOUT writing/reading anything under
    ``automation/strategies/``. Registered into ``sys.modules`` under an ai_loop-namespaced key
    (see module docstring) so that ``importlib.import_module(module_name)`` — used internally by
    ``backtest_runner.run_single_backtest_worker`` — resolves it from the in-memory cache."""
    candidate_path = Path(candidate_path).resolve()
    if not candidate_path.is_file():
        raise BacktestBridgeError(f"Kandidat nicht gefunden: {candidate_path}")

    module_name = f"{_MODULE_NAMESPACE_PREFIX}.{candidate_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, candidate_path)
    if spec is None or spec.loader is None:
        raise BacktestBridgeError(f"Konnte kein Modul-Spec fuer {candidate_path} erzeugen.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise BacktestBridgeError(f"Kandidat {candidate_path} konnte nicht geladen werden: {exc}") from exc
    return module


def find_strategy_and_config_names(
    module: types.ModuleType, strategy_class_hint: str | None
) -> tuple[str, str]:
    """Introspects ``module`` for the strategy class and its config class. Prefers an exact
    ``strategy_class_hint`` match (the R1 hypothesis' original strategy name); falls back to the
    naming-convention heuristic shared by every file under ``automation/strategies/`` today
    (``*Strategy`` / ``<Strategy>Config``)."""
    names = [n for n in vars(module) if not n.startswith("_")]

    strategy_name: str | None = None
    if strategy_class_hint and hasattr(module, strategy_class_hint):
        strategy_name = strategy_class_hint
    else:
        strategy_candidates = [n for n in names if n.endswith("Strategy") and n != "HourlyStrategyBase"]
        if len(strategy_candidates) == 1:
            strategy_name = strategy_candidates[0]

    if strategy_name is None:
        raise BacktestBridgeError(
            f"Konnte keine eindeutige Strategie-Klasse in {module.__name__} bestimmen "
            f"(gefundene *Strategy-Namen: {[n for n in names if n.endswith('Strategy')]})."
        )

    config_name = f"{strategy_name}Config"
    if not hasattr(module, config_name):
        config_candidates = [n for n in names if n.endswith("Config") and n != "HourlyStrategyConfig"]
        if len(config_candidates) == 1:
            config_name = config_candidates[0]
        else:
            raise BacktestBridgeError(
                f"Konnte keine eindeutige Config-Klasse zu '{strategy_name}' in {module.__name__} finden."
            )
    return strategy_name, config_name


def run_candidate_backtest(
    candidate_path: Path,
    *,
    symbol: str,
    params: dict[str, Any],
    run_id: str,
    strategy_class_hint: str | None = None,
    worker_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Runs ONE (candidate strategy x symbol) backtest via
    ``automation.backtest_runner.run_single_backtest_worker`` and returns its raw metrics dict.

    Every filesystem write this function triggers (worker log, optional HTML report) is scoped
    to ``automation/ai_loop/workspace/backtest_runs/<run_id>/`` — see module docstring.

    ``worker_fn`` is dependency-injectable (defaults to the real
    ``run_single_backtest_worker``, imported lazily) — tests/CI environments without
    ``nautilus_trader``/``pyarrow`` installed (both heavy, optional-at-import-time deps of
    ``automation.backtest_runner``) can inject a stub to exercise the wiring around it."""
    module = load_candidate_module(candidate_path)
    strategy_class_name, config_class_name = find_strategy_and_config_names(module, strategy_class_hint)
    module_name = module.__name__

    run_dir = BACKTEST_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    worker_log_file = run_dir / "worker.log"
    worker_log_file.touch(exist_ok=True)
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    bt_data = _read_backtest_config()
    catalog_path = bt_data.get("catalog_path", "data/nautilus")
    start_capital = float(bt_data.get("start_capital", 10000.0))
    commission_bps = float(bt_data.get("commission_bps", 0.0))
    span_tolerance_days = float(bt_data.get("span_tolerance_days", 5.0))

    strat = {
        "strategy_class": strategy_class_name,
        "strategy_module": module_name,
        "config_class": config_class_name,
        "params": dict(params),
    }
    bar_type = f"{symbol}-1-HOUR-MID-INTERNAL"

    try:
        if worker_fn is not None:
            run_single_backtest_worker = worker_fn
        else:
            # Lazy: automation.backtest_runner imports nautilus_trader/pyarrow at module level
            # (AGENTS.md Pitfall #63 convention) — this import must not happen at module load
            # time of backtest_bridge.py / orchestrator.py.
            from automation.backtest_runner import run_single_backtest_worker  # noqa: PLC0415

        result = run_single_backtest_worker(
            inst_id_str=symbol,
            bar_type=bar_type,
            strat=strat,
            catalog_path=str(catalog_path),
            start_ns=None,
            end_ns=None,
            start_capital=start_capital,
            generate_html_report=False,
            reports_dir=str(reports_dir),
            worker_log_file=str(worker_log_file),
            span_tolerance_days=span_tolerance_days,
            commission_bps=commission_bps,
        )
    except ImportError as exc:
        raise BacktestBridgeError(
            "automation.backtest_runner konnte nicht importiert werden (nautilus_trader/pyarrow "
            f"fehlen?) — Kandidat kann in dieser Umgebung nicht gebacktestet werden: {exc}"
        ) from exc
    except Exception as exc:
        raise BacktestBridgeError(f"Backtest fuer Kandidat {candidate_path} fehlgeschlagen: {exc}") from exc
    finally:
        sys.modules.pop(module_name, None)

    if not isinstance(result, dict):
        raise BacktestBridgeError(f"run_single_backtest_worker lieferte kein dict: {result!r}")
    return result


def _read_backtest_config() -> dict[str, Any]:
    """Read-only: ``automation/config/backtest.json`` (catalog_path/start_capital/commission —
    the same settings the production optimizer reads). Missing/unreadable ⇒ ``{}`` (fail-open,
    the caller applies its own defaults)."""
    try:
        from automation.optimizer.trial_config import config_dir  # lazy, avoids import cost when unused
    except ImportError:
        return {}
    path = config_dir() / "backtest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("backtest_bridge: %s konnte nicht gelesen werden: %s", path, exc)
        return {}
